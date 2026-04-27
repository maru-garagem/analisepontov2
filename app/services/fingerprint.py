"""
Fingerprint do layout de um cartão de ponto.

A ideia: dois PDFs da MESMA empresa no MESMO sistema de ponto têm o mesmo
fingerprint; PDFs de layouts diferentes têm fingerprints diferentes. O
fingerprint é computado SEM já ter um esqueleto (é a chave de lookup), então
não pode depender de identificação de tabela semântica.

Estratégia v2 (estabilizada):

1. Extrai texto da 1ª página.
2. **Restringe ao texto ACIMA da primeira tabela detectada** — todo o texto
   abaixo é dado variável (datas, horários, ocorrências do mês), que polui
   o fingerprint. Se não há tabela detectável, usa a página inteira.
3. Normaliza (NFKC, lowercase, remove dígitos).
4. Tokeniza em palavras de 3+ caracteres.
5. Mantém apenas tokens da WHITELIST de termos estruturais — agora **enxuta**
   para excluir palavras que aparecem como conteúdo de dados (`feriado`,
   `folga`, `falta`, `extra`, `abono`, `ano`, etc).
6. Soma os tokens com dimensões da página + número de colunas da maior
   tabela detectada + cabeçalhos das tabelas (que são labels estruturais).
7. Hash SHA-256 dos primeiros 16 chars, com prefixo de versão `v2:` para
   permitir invalidações futuras se a heurística evoluir.

Por que mudou de v1 → v2 (ver DECISIONS.md):
- v1 tinha token `ano` na whitelist; PDFs de janeiro tinham `Feriado: Ano novo`
  no corpo da tabela, então `ano` aparecia. PDFs de outros meses não tinham,
  e o fingerprint mudava. Resultado: a mesma empresa virava esqueletos
  diferentes mês a mês.
- v2 corta termos que ocorrem em conteúdo + restringe ao "topo" da página
  (cabeçalho do documento, não dados).

Limitações conhecidas:
- PDFs 100% imagem (escaneados) caem em fingerprint baseado só em dimensões
  e nº de colunas — pouco assertivo. Matching primário nesses casos passa
  a ser CNPJ.
- Tabelas detectadas como "página inteira" pelo pdfplumber (raras) fazem
  o "texto pré-tabela" ficar vazio. Aceitável: caímos no fallback de
  página inteira.
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


# Versão do algoritmo. Bump aqui invalida fingerprints antigos no matching
# (forçando re-cadastro), então só mude se realmente quiser.
FINGERPRINT_VERSION = "v2"


# Labels ESTRUTURAIS de cartão de ponto (aparecem em cabeçalhos do documento
# ou como header de coluna — NÃO como conteúdo de dados).
#
# IMPORTANTE: o critério para entrar é "aparece em todo PDF do mesmo layout,
# independente do mês". Termos como `feriado`, `folga`, `falta`, `extra`,
# `abono` aparecem como labels de coluna OU como dado dentro da linha — e
# como o fingerprint se baseia apenas no texto pré-tabela, esses ainda
# podem entrar quando aparecem em headers de coluna acima da tabela
# (caso do CENEGED). Mantemos os que são tipicamente label de seção,
# removemos os que são tipicamente conteúdo.
WHITELIST: frozenset[str] = frozenset({
    # Identificação do documento
    "cartao", "cartão", "ponto", "espelho", "registro", "folha",
    "empresa", "empregador", "cnpj", "inscricao", "inscrição",
    "empregado", "colaborador", "funcionario", "funcionário",
    "matricula", "matrícula", "codigo", "código", "cpf",
    "departamento", "setor", "cargo", "funcao", "função",
    "admissao", "admissão", "demissao", "demissão",
    # Período (labels — não os meses)
    "periodo", "período", "competencia", "competência",
    "inicio", "início", "fim", "termino", "término",
    # Batidas / horas (labels de coluna que aparecem em todo cartão)
    "entrada", "saida", "saída", "pausa", "intervalo", "almoco", "almoço",
    "horario", "horário", "hora", "horas", "batida", "batidas",
    "jornada", "trabalhada", "trabalhadas", "efetiva", "efetivo",
    "noturno", "diurno",
    "dsr",
    # Totais (labels)
    "total", "totais", "subtotal", "saldo", "resumo",
    "previsto", "realizado", "acumulado", "banco",
    # Observações (labels)
    "observacao", "observação", "observacoes", "observações", "obs",
    "ocorrencia", "ocorrência", "ocorrencias", "ocorrências",
    # Rodapé / assinaturas
    "assinatura", "ciente", "gestor", "responsavel", "responsável",
})

# Tokens removidos da v1 — todos aparecem como CONTEÚDO de dados em algum
# layout comum, contaminando o fingerprint:
#   ano       — "Feriado: Ano novo"
#   mes, mês  — "Mês/Ano Base" cabe em ano também
#   dia, dias — labels de coluna OU dados (`Dia normal`, `Dia compensado`)
#   semana    — `seg/ter/qua/...` ou `Semana 1`
#   data      — pode ser label ou conteúdo; varia
#   feriado, feriados — `Feriado: Ano novo`, `Feriado` na linha
#   folga, folgas — `Folga` na linha
#   falta, faltas — `Falta` repetido nas células
#   extra, extras — labels de coluna mas também `Hora Extra` em rodapé
#   adicional     — pode ser conteúdo
#   abono, abonos — pode ser conteúdo
#   atestado, atestados — conteúdo
#   desconto, descontos — conteúdo
#
# Comparado com v1, a quantidade de termos cai ~25% mas a estabilidade
# inter-mês sobe.

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
    versao: str


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


def _texto_acima_da_primeira_tabela(pagina) -> str:
    """
    Retorna o texto da página que aparece ACIMA do topo da primeira tabela
    detectada. Se não detecta tabela, retorna o texto completo.

    Por quê: o cabeçalho do documento (empresa, funcionário, período, etc)
    e os labels das colunas estão tipicamente acima da grade de dados. Os
    valores variáveis (datas, horários do mês) ficam nas linhas. Cortar
    abaixo do topo da tabela mantém só estrutura no fingerprint.
    """
    try:
        tabelas = pagina.find_tables() or []
    except Exception:
        tabelas = []

    if not tabelas:
        return pagina.extract_text() or ""

    # bbox = (x0, top, x1, bottom). "top" cresce de cima pra baixo no pdfplumber.
    topo = min((t.bbox[1] for t in tabelas), default=None)
    if topo is None:
        return pagina.extract_text() or ""

    # Se a tabela começa muito perto do topo da página, não há cabeçalho útil
    # para fingerprint — usa a página inteira como fallback.
    if topo < 50:  # ~70 pontos = 1 polegada; abaixo disso é praticamente o topo
        return pagina.extract_text() or ""

    try:
        crop = pagina.crop((0, 0, pagina.width, topo))
        texto = crop.extract_text() or ""
    except Exception:
        # Crop pode falhar em PDFs malformados. Cai no texto completo.
        texto = pagina.extract_text() or ""
    return texto


def _header_da_maior_tabela(pagina) -> str:
    """
    Pega o header (1ª linha) da MAIOR tabela detectada — onde "maior" é
    "mais colunas" (desempate por mais linhas). Para cartão de ponto
    digital, essa é a tabela de batidas, e o header dela é o sinal de
    layout mais estável que existe — vem invariável do gerador do PDF
    (ex: `DIA|PREVISTO|ENT.1|SAÍ.1|...`).

    Por que NÃO pegar a 1ª tabela: a detecção do pdfplumber varia
    sutilmente entre PDFs do mesmo layout (linhas finas no PDF que ele
    "vê" em alguns e ignora em outros), o que muda quem é a "1ª tabela".
    A maior é mais robusta.
    """
    try:
        tabelas = pagina.extract_tables() or []
    except Exception:
        return ""

    melhor: list[str | None] = []
    melhor_score = (-1, -1)  # (n_colunas, n_linhas)
    for tbl in tabelas:
        if not tbl or not tbl[0]:
            continue
        n_colunas = max((len(linha) for linha in tbl if linha), default=0)
        n_linhas = len(tbl)
        score = (n_colunas, n_linhas)
        if score > melhor_score:
            melhor_score = score
            melhor = tbl[0]

    if not melhor:
        return ""
    celulas = [(c or "").strip() for c in melhor]
    return "|".join(celulas)


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
        texto_estrutural = _texto_acima_da_primeira_tabela(pagina)
        header_tabela_principal = _header_da_maior_tabela(pagina)
        width = round(pagina.width)
        height = round(pagina.height)

        try:
            tabelas = pagina.extract_tables() or []
        except Exception:
            tabelas = []
        max_colunas = max(
            (len(linha) for tbl in tabelas for linha in tbl if linha),
            default=0,
        )

    tokens = extrair_tokens_estruturais(texto_estrutural)

    if not tokens and max_colunas == 0 and not header_tabela_principal:
        # PDF provavelmente só imagem; fingerprint cai em dimensões apenas.
        logger.warning(
            "fingerprint_degradado sem_tokens sem_tabela width=%d height=%d",
            width, height,
        )

    # Header da tabela principal entra normalizado (mesma whitelist), assim
    # `BANCO\nTOTAL` ou `BANCO TOTAL` viram o mesmo conjunto. E variações de
    # acentuação no header também colapsam.
    tokens_do_header = sorted(extrair_tokens_estruturais(header_tabela_principal))

    canonical = (
        f"{FINGERPRINT_VERSION}|"
        + "|".join(tokens)
        + "||header_principal="
        + ",".join(tokens_do_header)
        + f"||dim={width}x{height}|cols={max_colunas}"
    )
    return FingerprintInfo(
        hash=_gerar_hash(canonical),
        tokens=tokens,
        page_size=(width, height),
        max_colunas=max_colunas,
        raw_canonical=canonical,
        versao=FINGERPRINT_VERSION,
    )


def gerar_fingerprint_hash(pdf_bytes: bytes) -> str:
    """Atalho: retorna apenas a string hash (uso comum em matching)."""
    return gerar_fingerprint(pdf_bytes).hash
