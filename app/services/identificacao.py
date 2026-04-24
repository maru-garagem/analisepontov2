"""
Identificação de empresa a partir de um PDF de cartão de ponto.

Combina dois sinais:
  - CNPJ: extraído via regex + validação de dígitos verificadores.
  - Fingerprint: assinatura estrutural do layout (services/fingerprint).

Matching:
  1. Fingerprint bate em um esqueleto ativo → fluxo rápido.
  2. CNPJ bate em uma empresa existente mas fingerprint não → empresa
     pode ter trocado de sistema; recomenda cadastro de nova versão.
  3. Nem CNPJ nem fingerprint batem → empresa nova, cadastro assistido.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy.orm import Session

from app.models.empresa import Empresa, EmpresaCNPJ
from app.models.enums import StatusEsqueleto
from app.models.esqueleto import Esqueleto
from app.services.fingerprint import FingerprintInfo, gerar_fingerprint
from app.utils.pdf import extrair_texto_todo

logger = logging.getLogger(__name__)


# --- CNPJ ---------------------------------------------------------------

_PADRAO_CNPJ_FORMATADO = re.compile(
    r"(\d{2})[\.\s]?(\d{3})[\.\s]?(\d{3})[\/\s]?(\d{4})[\-\s]?(\d{2})"
)
_PADRAO_CNPJ_CRU = re.compile(r"(?<!\d)(\d{14})(?!\d)")


def validar_cnpj(cnpj: str) -> bool:
    """
    Valida CNPJ brasileiro conferindo os dois dígitos verificadores.
    Aceita entrada formatada ou não.
    """
    digits = re.sub(r"\D", "", cnpj)
    if len(digits) != 14:
        return False
    if digits == digits[0] * 14:  # 00000000000000, 11111111111111, etc
        return False

    pesos_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma = sum(int(d) * p for d, p in zip(digits[:12], pesos_1))
    resto = soma % 11
    dv1 = 0 if resto < 2 else 11 - resto
    if int(digits[12]) != dv1:
        return False

    pesos_2 = [6] + pesos_1
    soma = sum(int(d) * p for d, p in zip(digits[:13], pesos_2))
    resto = soma % 11
    dv2 = 0 if resto < 2 else 11 - resto
    return int(digits[13]) == dv2


def extrair_cnpjs(texto: str) -> list[str]:
    """
    Retorna CNPJs únicos encontrados no texto, em formato de apenas dígitos.
    Apenas CNPJs com dígito verificador válido são retornados.
    """
    encontrados: set[str] = set()
    for match in _PADRAO_CNPJ_FORMATADO.finditer(texto):
        cnpj = "".join(match.groups())
        if validar_cnpj(cnpj):
            encontrados.add(cnpj)
    for match in _PADRAO_CNPJ_CRU.finditer(texto):
        cnpj = match.group(1)
        if validar_cnpj(cnpj):
            encontrados.add(cnpj)
    return sorted(encontrados)


def formatar_cnpj(cnpj: str) -> str:
    """Formata como XX.XXX.XXX/XXXX-XX. Aceita entrada com ou sem pontuação."""
    d = re.sub(r"\D", "", cnpj)
    if len(d) != 14:
        return cnpj
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def normalizar_cnpj(cnpj: str) -> str:
    """Remove tudo que não é dígito — forma canônica para armazenamento."""
    return re.sub(r"\D", "", cnpj)


# --- Identificação -------------------------------------------------------

MatchType = Literal["exato", "fingerprint_somente", "cnpj_somente", "nenhum"]


@dataclass
class ResultadoIdentificacao:
    empresa: Optional[Empresa]
    esqueleto: Optional[Esqueleto]
    cnpj_detectado: Optional[str]       # apenas dígitos
    cnpjs_todos: list[str]              # caso múltiplos sejam encontrados
    fingerprint: FingerprintInfo
    match_type: MatchType

    @property
    def tem_esqueleto_utilizavel(self) -> bool:
        return self.esqueleto is not None

    @property
    def eh_empresa_nova(self) -> bool:
        return self.empresa is None


def identificar_empresa(pdf_bytes: bytes, db: Session) -> ResultadoIdentificacao:
    """
    Roda classificação combinada (CNPJ + fingerprint) contra a base de
    empresas/esqueletos. **Não** modifica banco.
    """
    textos = extrair_texto_todo(pdf_bytes)
    texto_completo = "\n".join(textos)
    cnpjs = extrair_cnpjs(texto_completo)
    cnpj_principal = cnpjs[0] if cnpjs else None

    fp = gerar_fingerprint(pdf_bytes)

    # 1) Matching por fingerprint (esqueleto ativo)
    esqueleto_por_fp = (
        db.query(Esqueleto)
        .filter(Esqueleto.fingerprint == fp.hash)
        .filter(Esqueleto.status == StatusEsqueleto.ATIVO.value)
        .order_by(Esqueleto.versao.desc())
        .first()
    )

    # 2) Matching por CNPJ
    empresa_por_cnpj: Optional[Empresa] = None
    if cnpj_principal:
        ec = (
            db.query(EmpresaCNPJ)
            .filter(EmpresaCNPJ.cnpj == cnpj_principal)
            .first()
        )
        if ec:
            empresa_por_cnpj = ec.empresa

    # 3) Combina
    if esqueleto_por_fp is not None:
        empresa = empresa_por_cnpj or esqueleto_por_fp.empresa
        match_type: MatchType = (
            "exato"
            if empresa_por_cnpj is not None and empresa_por_cnpj.id == esqueleto_por_fp.empresa_id
            else "fingerprint_somente"
        )
        return ResultadoIdentificacao(
            empresa=empresa,
            esqueleto=esqueleto_por_fp,
            cnpj_detectado=cnpj_principal,
            cnpjs_todos=cnpjs,
            fingerprint=fp,
            match_type=match_type,
        )

    if empresa_por_cnpj is not None:
        # Empresa conhecida, layout desconhecido — precisa de nova versão de esqueleto.
        return ResultadoIdentificacao(
            empresa=empresa_por_cnpj,
            esqueleto=None,
            cnpj_detectado=cnpj_principal,
            cnpjs_todos=cnpjs,
            fingerprint=fp,
            match_type="cnpj_somente",
        )

    return ResultadoIdentificacao(
        empresa=None,
        esqueleto=None,
        cnpj_detectado=cnpj_principal,
        cnpjs_todos=cnpjs,
        fingerprint=fp,
        match_type="nenhum",
    )
