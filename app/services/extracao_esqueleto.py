"""
Aplica um esqueleto a um PDF e retorna dados estruturados (cabeçalho + linhas).

Métodos suportados em v2.0:
  - `plumber_direto` (principal): pdfplumber extrai texto + tabelas;
    cabeçalho é preenchido via regex-âncora; linhas vêm das tabelas detectadas.
  - `ia_barata_com_exemplos` (fallback): envia texto do PDF + exemplos
    validados (few-shot) para o LLM barato e parseia o JSON retornado.
    Acionado automaticamente quando `plumber_direto` retorna 0 linhas.

Entrada: bytes do PDF + esqueleto ORM.
Saída: ResultadoExtracao (cabeçalho, linhas, método efetivo, tempo, avisos).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import MetodoExtracao
from app.models.esqueleto import Esqueleto
from app.schemas.esqueleto import EstruturaEsqueleto, ExemploValidado
from app.services.identificacao import extrair_cnpjs, formatar_cnpj
from app.services.llm import get_llm_client
from app.config import get_settings
from app.utils.errors import LLMUnavailableError, PontoExtractError
from app.utils.ocr import ocr_tabela_por_bbox, ocr_todo
from app.utils.pdf import abrir_pdf, extrair_texto_todo, parece_pdf_escaneado

logger = logging.getLogger(__name__)


@dataclass
class ResultadoExtracao:
    cabecalho: dict[str, Any]
    linhas: list[dict[str, Any]]
    metodo_efetivo: str
    tempo_ms: int
    avisos: list[str] = field(default_factory=list)
    custo_estimado_usd: float | None = None


# --- Parsing de células ------------------------------------------------

_RE_HORA = re.compile(r"^\s*(\d{1,2})[:h](\d{2})\s*$")
_RE_DATA_DDMMAAAA = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$")
_RE_DATA_DDMM = re.compile(r"^\s*(\d{1,2})/(\d{1,2})\s*$")


def _parse_hora(celula: str) -> str | None:
    if not celula or not celula.strip():
        return None
    m = _RE_HORA.match(celula.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"


def _parse_data(celula: str, ano_default: int | None = None) -> str | None:
    if not celula or not celula.strip():
        return None
    texto = celula.strip()
    m = _RE_DATA_DDMMAAAA.match(texto)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        return f"{d:02d}/{mo:02d}/{y:04d}"
    m = _RE_DATA_DDMM.match(texto)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if ano_default is None:
            return f"{d:02d}/{mo:02d}"
        return f"{d:02d}/{mo:02d}/{ano_default:04d}"
    return None


def _parse_numero(celula: str) -> float | None:
    if not celula or not celula.strip():
        return None
    texto = celula.strip().replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def parse_celula(tipo: str, valor: str | None, parsing_spec: dict[str, Any]) -> Any:
    if valor is None or not str(valor).strip():
        return parsing_spec.get("celula_vazia_valor")
    v = str(valor).strip()
    if tipo == "hora":
        return _parse_hora(v) or v
    if tipo == "data":
        return _parse_data(v, parsing_spec.get("ano_default")) or v
    if tipo == "numero":
        n = _parse_numero(v)
        return n if n is not None else v
    return v


# --- Cabeçalho ---------------------------------------------------------

def extrair_campo_cabecalho(texto: str, regra: dict[str, Any]) -> Any:
    tipo = regra.get("tipo")
    if tipo == "ancora_regex":
        pattern = regra.get("regex")
        if not pattern:
            return None
        try:
            m = re.search(pattern, texto, flags=re.MULTILINE)
        except re.error:
            logger.warning("regex_cabecalho_invalida pattern=%s", pattern)
            return None
        if not m:
            return None
        return (m.group(1).strip() if m.groups() else m.group(0).strip()) or None
    if tipo == "regex_cnpj":
        cnpjs = extrair_cnpjs(texto)
        return formatar_cnpj(cnpjs[0]) if cnpjs else None
    if tipo == "literal":
        return regra.get("valor")
    logger.warning("tipo_cabecalho_desconhecido tipo=%s", tipo)
    return None


# --- Tabela ------------------------------------------------------------

def eh_linha_header(linha: list[str | None], header_regex: str | None) -> bool:
    if not header_regex:
        return False
    concatenada = " ".join((c or "") for c in linha)
    try:
        return bool(re.search(header_regex, concatenada, flags=re.IGNORECASE))
    except re.error:
        return False


def eh_linha_descartavel(linha: list[str | None], descartar_regex: list[str]) -> bool:
    concatenada = " ".join((c or "") for c in linha).strip()
    if not concatenada:
        return True
    for pattern in descartar_regex:
        try:
            if re.search(pattern, concatenada, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def processar_linha(
    linha: list[str | None],
    colunas: list[dict[str, Any]],
    parsing_spec: dict[str, Any],
) -> dict[str, Any]:
    resultado: dict[str, Any] = {}
    for idx, col in enumerate(colunas):
        nome = col.get("nome", f"coluna_{idx}")
        tipo = col.get("tipo", "texto")
        valor = linha[idx] if idx < len(linha) else None
        resultado[nome] = parse_celula(tipo, valor, parsing_spec)
    return resultado


# --- Métodos de extração -----------------------------------------------

def _plumber_direto(
    pdf_bytes: bytes, estrutura: dict[str, Any], avisos: list[str]
) -> dict[str, Any]:
    textos = extrair_texto_todo(pdf_bytes)
    texto_completo = "\n".join(textos)

    cabecalho: dict[str, Any] = {}
    for campo, regra in (estrutura.get("cabecalho") or {}).items():
        cabecalho[campo] = extrair_campo_cabecalho(texto_completo, regra)

    tabela_spec = estrutura.get("tabela") or {}
    colunas = tabela_spec.get("colunas") or []
    parsing = estrutura.get("parsing") or {}
    descartar = tabela_spec.get("linhas_descartar_regex") or []
    header_regex = tabela_spec.get("header_row_regex")
    num_esperado = tabela_spec.get("num_colunas_esperado")

    linhas: list[dict[str, Any]] = []

    with abrir_pdf(pdf_bytes) as pdf:
        for pagina in pdf.pages:
            tabelas = pagina.extract_tables() or []
            for tbl in tabelas:
                for linha in tbl:
                    if not linha:
                        continue
                    if eh_linha_header(linha, header_regex):
                        continue
                    if eh_linha_descartavel(linha, descartar):
                        continue
                    if num_esperado and len(linha) < num_esperado:
                        avisos.append(
                            f"linha com {len(linha)} colunas < esperado {num_esperado}"
                        )
                    linhas.append(processar_linha(linha, colunas, parsing))

    return {"cabecalho": cabecalho, "linhas": linhas}


def _ocr_guiado(
    pdf_bytes: bytes, estrutura: dict[str, Any], avisos: list[str]
) -> dict[str, Any]:
    """
    OCR + aplicação das regras do esqueleto. Usado para PDFs escaneados
    (sem texto digital extraível pelo pdfplumber).

    Cabeçalho: regex contra o texto OCRizado completo.
    Tabela: reconstrução via bounding boxes do Tesseract (ocr_tabela_por_bbox).
    """
    # Cabeçalho usa texto livre do OCR (concatenado).
    textos_ocr = ocr_todo(pdf_bytes)
    texto_completo = "\n".join(textos_ocr)

    cabecalho: dict[str, Any] = {}
    for campo, regra in (estrutura.get("cabecalho") or {}).items():
        cabecalho[campo] = extrair_campo_cabecalho(texto_completo, regra)

    # Tabela via bounding boxes.
    tabela_spec = estrutura.get("tabela") or {}
    colunas = tabela_spec.get("colunas") or []
    parsing = estrutura.get("parsing") or {}
    descartar = tabela_spec.get("linhas_descartar_regex") or []
    header_regex = tabela_spec.get("header_row_regex")
    num_esperado = tabela_spec.get("num_colunas_esperado")

    linhas: list[dict[str, Any]] = []
    paginas_linhas = ocr_tabela_por_bbox(pdf_bytes)

    for linhas_pagina in paginas_linhas:
        for linha_bruta in linhas_pagina:
            if not linha_bruta:
                continue
            if eh_linha_header(linha_bruta, header_regex):
                continue
            if eh_linha_descartavel(linha_bruta, descartar):
                continue
            if num_esperado and len(linha_bruta) < max(2, num_esperado // 2):
                # Linha muito fragmentada ou texto solto — ignora (evita
                # poluir resultado com linhas de "assinatura", rodapé, etc.)
                continue
            if num_esperado and len(linha_bruta) < num_esperado:
                avisos.append(
                    f"ocr_linha_com_{len(linha_bruta)}_colunas (esperado {num_esperado})"
                )
            linhas.append(processar_linha(linha_bruta, colunas, parsing))

    return {"cabecalho": cabecalho, "linhas": linhas}


def _ia_barata_com_exemplos(
    pdf_bytes: bytes,
    estrutura: dict[str, Any],
    exemplos: list[dict[str, Any]],
    avisos: list[str],
    texto_override: str | None = None,
) -> dict[str, Any]:
    """
    Fallback usando LLM barato com few-shot dos exemplos validados.
    Envia apenas texto (não imagem) para ser econômico. Se o PDF não tiver
    texto digital, `texto_override` pode ser passado (p.ex. resultado do OCR).

    O modelo usado é `estrutura.modelo_fallback` se definido, senão o default
    das settings (OPENROUTER_MODEL_BARATO).
    """
    if texto_override is not None:
        texto_pdf = texto_override.strip()
    else:
        textos = extrair_texto_todo(pdf_bytes)
        texto_pdf = "\n".join(textos).strip()

    if not texto_pdf:
        # Tenta OCR como último recurso pra ter texto.
        try:
            textos_ocr = ocr_todo(pdf_bytes)
            texto_pdf = "\n".join(textos_ocr).strip()
        except Exception:
            texto_pdf = ""

    if not texto_pdf:
        avisos.append("pdf_sem_texto_extraivel")
        raise PontoExtractError(
            "PDF sem texto extraível nem por pdfplumber nem por OCR."
        )

    settings = get_settings()
    modelo_custom = estrutura.get("modelo_fallback")
    if modelo_custom and modelo_custom in settings.modelos_baratos_permitidos:
        modelo_efetivo = modelo_custom
    else:
        modelo_efetivo = settings.OPENROUTER_MODEL_BARATO

    exemplos_txt = "\n\n".join(
        f"### Exemplo {i+1}\nTrecho:\n{e.get('trecho_pdf','')}\n\nSaída:\n{e.get('saida_esperada')}"
        for i, e in enumerate(exemplos[:3])  # máx 3 exemplos few-shot
    )

    system = (
        "Você é um extrator de cartões de ponto brasileiros. "
        "Siga EXATAMENTE o mesmo formato JSON dos exemplos. "
        "Retorne apenas o JSON, sem comentários."
    )
    user = (
        f"Estrutura esperada:\n{estrutura}\n\n"
        f"Exemplos validados:\n{exemplos_txt}\n\n"
        f"Texto do PDF a extrair:\n{texto_pdf[:15000]}\n\n"
        f"Retorne JSON com chaves 'cabecalho' (dict) e 'linhas' (lista de dicts)."
    )

    client = get_llm_client()
    try:
        resultado = client.chat_json(
            model=modelo_efetivo,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=4000,
        )
    except LLMUnavailableError as exc:
        avisos.append(f"llm_indisponivel: {exc}")
        raise

    cabecalho = resultado.get("cabecalho") or {}
    linhas = resultado.get("linhas") or []
    if not isinstance(linhas, list):
        avisos.append("llm_linhas_invalidas")
        linhas = []

    return {"cabecalho": cabecalho, "linhas": linhas}


# --- Entry point -------------------------------------------------------

def aplicar_esqueleto(
    pdf_bytes: bytes,
    esqueleto: Esqueleto,
    *,
    permitir_fallback_llm: bool = True,
    permitir_fallback_ocr: bool = True,
) -> ResultadoExtracao:
    """
    Aplica o esqueleto em cascata:
      1. Método preferencial do esqueleto (plumber_direto / ocr_guiado /
         ia_barata_com_exemplos).
      2. Se retornou 0 linhas e o PDF parece escaneado → OCR guiado.
      3. Se ainda 0 linhas e há exemplos_validados → IA barata.

    Flags permitem desligar cada fallback individualmente (testes).
    """
    inicio = time.monotonic()
    avisos: list[str] = []
    estrutura = dict(esqueleto.estrutura or {})
    exemplos = list(esqueleto.exemplos_validados or [])

    try:
        EstruturaEsqueleto.model_validate(estrutura)
    except Exception as exc:
        avisos.append(f"estrutura_nao_conforme: {exc}")

    metodo_preferencial = estrutura.get(
        "metodo_preferencial", MetodoExtracao.ESQUELETO_PLUMBER.value
    )

    # --- Tentativa principal ---
    if metodo_preferencial in (MetodoExtracao.ESQUELETO_PLUMBER.value, "plumber_direto"):
        dados = _plumber_direto(pdf_bytes, estrutura, avisos)
        metodo_efetivo = MetodoExtracao.ESQUELETO_PLUMBER.value
    elif metodo_preferencial in (MetodoExtracao.ESQUELETO_OCR.value, "ocr_guiado"):
        dados = _ocr_guiado(pdf_bytes, estrutura, avisos)
        metodo_efetivo = MetodoExtracao.ESQUELETO_OCR.value
    elif metodo_preferencial in (MetodoExtracao.ESQUELETO_IA_BARATA.value, "ia_barata_com_exemplos"):
        dados = _ia_barata_com_exemplos(pdf_bytes, estrutura, exemplos, avisos)
        metodo_efetivo = MetodoExtracao.ESQUELETO_IA_BARATA.value
    else:
        raise PontoExtractError(f"Método '{metodo_preferencial}' não suportado.")

    # --- Cascata 1: plumber sem linhas e PDF escaneado → OCR ---
    if (
        permitir_fallback_ocr
        and not dados.get("linhas")
        and metodo_efetivo == MetodoExtracao.ESQUELETO_PLUMBER.value
        and parece_pdf_escaneado(pdf_bytes)
    ):
        logger.info("fallback_ocr_guiado esqueleto_id=%s", esqueleto.id)
        avisos.append("plumber_vazio_tentando_ocr")
        dados = _ocr_guiado(pdf_bytes, estrutura, avisos)
        metodo_efetivo = MetodoExtracao.ESQUELETO_OCR.value

    # --- Cascata 2: se ainda vazio e há exemplos → IA barata ---
    if (
        permitir_fallback_llm
        and not dados.get("linhas")
        and metodo_efetivo != MetodoExtracao.ESQUELETO_IA_BARATA.value
        and exemplos
    ):
        logger.info("fallback_ia_barata esqueleto_id=%s", esqueleto.id)
        avisos.append(f"{metodo_efetivo}_vazio_tentando_ia_barata")
        # Se já OCRizamos, reusa o texto pra evitar OCR duplicado.
        texto_override = None
        if metodo_efetivo == MetodoExtracao.ESQUELETO_OCR.value:
            try:
                texto_override = "\n".join(ocr_todo(pdf_bytes))
            except Exception:
                texto_override = None
        dados = _ia_barata_com_exemplos(
            pdf_bytes, estrutura, exemplos, avisos, texto_override=texto_override
        )
        metodo_efetivo = MetodoExtracao.ESQUELETO_IA_BARATA.value

    tempo_ms = int((time.monotonic() - inicio) * 1000)
    return ResultadoExtracao(
        cabecalho=dados.get("cabecalho", {}),
        linhas=dados.get("linhas", []),
        metodo_efetivo=metodo_efetivo,
        tempo_ms=tempo_ms,
        avisos=avisos,
    )
