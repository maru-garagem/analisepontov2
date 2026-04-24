"""
Fingerprint do layout de um cartão de ponto.

A ideia: dois PDFs da MESMA empresa no MESMO sistema de ponto têm o mesmo
fingerprint; PDFs de layouts diferentes têm fingerprints diferentes. O
fingerprint é computado SEM já ter um esqueleto (é a chave de lookup), então
não pode depender de identificação de tabela semântica.

Estratégia:
1. Extrai texto da 1ª página.
2. Normaliza (NFKC, lowercase, remove dígitos).
3. Tokeniza em palavras de 3+ caracteres.
4. Mantém apenas tokens que fazem parte da WHITELIST de termos estruturais
   de cartão de ponto (labels que aparecem em cabeçalhos). Isso descarta
   nome do funcionário, razão social, departamento específico, etc.
5. Combina o conjunto ordenado de tokens + dimensões da página + número de
   colunas da maior tabela detectada.
6. Hash SHA-256 dos primeiros 16 chars.

Limitações conhecidas (documentadas em DECISIONS.md):
- PDFs 100% imagem (escaneados) caem em fingerprint baseado só em dimensões,
  que é pouco assertivo. O matching primário nesses casos passa a ser CNPJ.
- Duas empresas com sistemas de ponto do MESMO fornecedor (mesmos labels,
  mesmas dimensões) colidem. Matching secundário por CNPJ resolve.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass

from app.utils.errors import FingerprintError
from app.utils.pdf import abrir_pdf

logger = logging.getLogger(__name__)


# Labels estruturais comuns em cartões de ponto brasileiros. Mantenha
# singular e plural, acentuado e sem acento, para que o fingerprint seja
# estável mesmo quando o PDF usa uma variante.
WHITELIST: frozenset[str] = frozenset({
    # Identificação do documento
    "cartao", "cartão", "ponto", "espelho", "registro", "folha",
    "empresa", "empregador", "cnpj", "inscricao", "inscrição",
    "empregado", "colaborador", "funcionario", "funcionário",
    "matricula", "matrícula", "codigo", "código", "cpf",
    "departamento", "setor", "cargo", "funcao", "função",
    "admissao", "admissão", "demissao", "demissão",
    # Período
    "periodo", "período", "competencia", "competência",
    "mes", "mês", "ano", "semana", "dia", "data",
    "inicio", "início", "fim", "termino", "término",
    # Batidas / horas
    "entrada", "saida", "saída", "pausa", "intervalo", "almoco", "almoço",
    "horario", "horário", "hora", "horas", "batida", "batidas",
    "jornada", "trabalhada", "trabalhadas", "efetiva", "efetivo",
    "extra", "extras", "adicional", "noturno", "diurno",
    "falta", "faltas", "feriado", "feriados", "atestado", "atestados",
    "folga", "folgas", "dsr", "abono", "abonos", "desconto", "descontos",
    # Totais
    "total", "totais", "subtotal", "saldo", "resumo",
    "previsto", "realizado", "acumulado", "banco",
    # Observações
    "observacao", "observação", "observacoes", "observações", "obs",
    "ocorrencia", "ocorrência", "ocorrencias", "ocorrências",
    # Rodapé / assinaturas
    "assinatura", "ciente", "gestor", "responsavel", "responsável",
})

# Remove acentuação para normalização complementar — mantida separadamente
# porque muitos tokens da whitelist já têm variantes acentuada e sem.
_RE_DIGITS = re.compile(r"\d+")
_RE_WORD = re.compile(r"[a-záéíóúâêîôûãõç]{3,}")


@dataclass
class FingerprintInfo:
    """Informação estruturada do fingerprint, útil para debug e drift detection."""
    hash: str
    tokens: list[str]
    page_size: tuple[int, int]
    max_colunas: int
    raw_canonical: str


def _normalizar_texto(texto: str) -> str:
    """Lowercase + NFKC + remove dígitos. Preserva acentos para casar com WHITELIST."""
    normalizado = unicodedata.normalize("NFKC", texto).lower()
    return _RE_DIGITS.sub("", normalizado)


def extrair_tokens_estruturais(texto: str) -> list[str]:
    """
    Extrai tokens estruturais de um texto livre, descartando tudo fora da
    WHITELIST. Retorna lista ordenada e sem duplicatas — pura, testável.
    """
    normalizado = _normalizar_texto(texto)
    tokens = _RE_WORD.findall(normalizado)
    estruturais = {t for t in tokens if t in WHITELIST}
    return sorted(estruturais)


def _gerar_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def gerar_fingerprint(pdf_bytes: bytes) -> FingerprintInfo:
    """
    Gera fingerprint do layout. Levanta `FingerprintError` se o PDF não
    permitir identificação útil (sem texto e sem tabela).
    """
    with abrir_pdf(pdf_bytes) as pdf:
        if not pdf.pages:
            raise FingerprintError("PDF sem páginas.")

        pagina = pdf.pages[0]
        texto = pagina.extract_text() or ""
        width = round(pagina.width)
        height = round(pagina.height)

        tabelas = pagina.extract_tables() or []
        max_colunas = max(
            (len(linha) for tbl in tabelas for linha in tbl if linha),
            default=0,
        )

    tokens = extrair_tokens_estruturais(texto)

    if not tokens and max_colunas == 0:
        # PDF provavelmente só imagem; fingerprint cai em dimensões apenas.
        logger.warning("fingerprint_degradado sem_tokens sem_tabela width=%d height=%d", width, height)

    canonical = "|".join(tokens) + f"||dim={width}x{height}|cols={max_colunas}"
    return FingerprintInfo(
        hash=_gerar_hash(canonical),
        tokens=tokens,
        page_size=(width, height),
        max_colunas=max_colunas,
        raw_canonical=canonical,
    )


def gerar_fingerprint_hash(pdf_bytes: bytes) -> str:
    """Atalho: retorna apenas a string hash (uso comum em matching)."""
    return gerar_fingerprint(pdf_bytes).hash
