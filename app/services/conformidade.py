"""
Score de conformidade de uma extração.

Design (rebalanceado para não penalizar extrações corretas):
  1. Presença de linhas na tabela (peso 40%, binário 0 ou 1)
  2. Fração de campos de cabeçalho preenchidos (peso 30%)
  3. Qualidade de células tipadas hora/data — peso 30% apenas se o esqueleto
     declara colunas tipadas. Se não declara, esse componente vale 1.0
     (não penaliza esqueletos que só extraem texto).
  4. Penalização por avisos — 0.02 cada, máx 0.10 (era 0.05/0.20).

Validação de célula é permissiva: aceita HH:MM, HH:MM:SS, HHhMM, datas
em vários formatos. Rejeita apenas valores claramente não-horários/data.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import StatusEsqueleto
from app.models.esqueleto import Esqueleto
from app.services.extracao_esqueleto import ResultadoExtracao

logger = logging.getLogger(__name__)

# Permissive: aceita H:MM, HH:MM, HH:MM:SS, HHhMM.
_RE_HORA_PERMISSIVA = re.compile(r"^\d{1,2}[:h]\d{2}(?::\d{2})?$", re.IGNORECASE)
# Permissive: aceita DD/MM, DD/MM/YY, DD/MM/YYYY, DD-MM-YYYY.
_RE_DATA_PERMISSIVA = re.compile(r"^\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?$")

MIN_HISTORICO_PARA_FLAG = 5
PENALIDADE_POR_AVISO = 0.02
PENALIDADE_AVISOS_MAX = 0.10


@dataclass
class ScoreBreakdown:
    frac_cabecalho: float
    tem_linhas: float
    frac_celulas: float
    tem_colunas_tipadas: bool
    num_avisos: int
    penalidade_avisos: float
    score_final: float


def _celula_bem_parseada(tipo: str, valor: Any) -> bool:
    if valor is None:
        return True
    s = str(valor).strip()
    if not s:
        return True
    if tipo == "hora":
        return bool(_RE_HORA_PERMISSIVA.match(s))
    if tipo == "data":
        return bool(_RE_DATA_PERMISSIVA.match(s))
    # número e texto são permissivos
    return True


def calcular_score_detalhado(
    resultado: ResultadoExtracao, esqueleto: Esqueleto
) -> ScoreBreakdown:
    estrutura = esqueleto.estrutura or {}
    cabecalho_spec = estrutura.get("cabecalho") or {}
    tabela_spec = estrutura.get("tabela") or {}
    colunas_spec = tabela_spec.get("colunas") or []

    # 1. Cabeçalho
    num_campos = max(1, len(cabecalho_spec))
    preenchidos = sum(
        1 for v in resultado.cabecalho.values() if v not in (None, "", [])
    )
    frac_cabecalho = preenchidos / num_campos

    # 2. Linhas: tem ou não tem
    tem_linhas = 1.0 if resultado.linhas else 0.0

    # 3. Qualidade de células tipadas
    cols_tipadas = [c for c in colunas_spec if c.get("tipo") in ("hora", "data")]
    tem_colunas_tipadas = bool(cols_tipadas)
    total_celulas = 0
    celulas_validas = 0
    if cols_tipadas and resultado.linhas:
        for linha in resultado.linhas:
            for col in cols_tipadas:
                nome = col.get("nome")
                tipo = col.get("tipo")
                if not nome or not tipo:
                    continue
                valor = linha.get(nome)
                if valor in (None, ""):
                    continue
                total_celulas += 1
                if _celula_bem_parseada(tipo, valor):
                    celulas_validas += 1
    # Se não há colunas tipadas OU nenhuma célula preenchida, esse componente
    # vale 1.0 — não penaliza layouts cuja tabela não tem horas/datas tipadas.
    frac_celulas = 1.0 if total_celulas == 0 else celulas_validas / total_celulas

    num_avisos = len(resultado.avisos)
    penalidade = min(PENALIDADE_AVISOS_MAX, num_avisos * PENALIDADE_POR_AVISO)

    score = (
        0.40 * tem_linhas
        + 0.30 * frac_cabecalho
        + 0.30 * frac_celulas
        - penalidade
    )
    score = max(0.0, min(1.0, score))

    return ScoreBreakdown(
        frac_cabecalho=frac_cabecalho,
        tem_linhas=tem_linhas,
        frac_celulas=frac_celulas,
        tem_colunas_tipadas=tem_colunas_tipadas,
        num_avisos=num_avisos,
        penalidade_avisos=penalidade,
        score_final=score,
    )


def calcular_score(resultado: ResultadoExtracao, esqueleto: Esqueleto) -> float:
    breakdown = calcular_score_detalhado(resultado, esqueleto)
    logger.info(
        "score esqueleto=%s final=%.3f cabecalho=%.3f linhas=%.1f celulas=%.3f avisos=%d penal=%.3f",
        getattr(esqueleto, "id", "?"),
        breakdown.score_final,
        breakdown.frac_cabecalho,
        breakdown.tem_linhas,
        breakdown.frac_celulas,
        breakdown.num_avisos,
        breakdown.penalidade_avisos,
    )
    return breakdown.score_final


def breakdown_como_dict(breakdown: ScoreBreakdown) -> dict[str, Any]:
    """Converte o breakdown para dict JSON-serializável (para resultado_json)."""
    return asdict(breakdown)


def atualizar_metricas_esqueleto(
    db: Session, esqueleto: Esqueleto, score: float
) -> None:
    total_antigo = esqueleto.total_extracoes or 0
    taxa_antiga = esqueleto.taxa_sucesso or 0.0
    novo_total = total_antigo + 1
    nova_taxa = ((taxa_antiga * total_antigo) + score) / novo_total

    esqueleto.total_extracoes = novo_total
    esqueleto.taxa_sucesso = nova_taxa

    settings = get_settings()
    if (
        novo_total >= MIN_HISTORICO_PARA_FLAG
        and nova_taxa < settings.TAXA_SUCESSO_MIN_ESQUELETO
        and esqueleto.status == StatusEsqueleto.ATIVO.value
    ):
        logger.warning(
            "esqueleto_em_revisao id=%s taxa=%.3f total=%d",
            esqueleto.id, nova_taxa, novo_total,
        )
        esqueleto.status = StatusEsqueleto.EM_REVISAO.value

    db.commit()


def classificar_status_por_score(score: float) -> str:
    from app.models.enums import StatusProcessamento
    settings = get_settings()
    if score >= settings.SCORE_CONFORMIDADE_MIN:
        return StatusProcessamento.SUCESSO.value
    return StatusProcessamento.SUCESSO_COM_AVISO.value
