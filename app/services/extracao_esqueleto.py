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

import json
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


def _monta_prompt_ia(
    estrutura: dict[str, Any],
    exemplos: list[dict[str, Any]],
    texto_pdf: str,
) -> tuple[str, str]:
    """
    Prompt estruturado: lista EXPLICITAMENTE os campos do cabeçalho e as
    colunas esperadas para a IA não inventar chaves. Exemplos são incluídos
    apenas se tiverem trecho_pdf + saida_esperada preenchidos (few-shot só
    útil quando tem o par completo).
    """
    cabecalho_spec = estrutura.get("cabecalho") or {}
    campos_cabecalho = list(cabecalho_spec.keys())

    tabela_spec = estrutura.get("tabela") or {}
    colunas = tabela_spec.get("colunas") or []
    linhas_desc = []
    for c in colunas:
        nome = c.get("nome")
        tipo = c.get("tipo", "texto")
        if nome:
            linhas_desc.append(f"  - {nome} ({tipo})")

    parsing = estrutura.get("parsing") or {}
    formato_hora = parsing.get("formato_hora", "HH:MM")
    formato_data = parsing.get("formato_data", "DD/MM/YYYY")
    ano_default = parsing.get("ano_default")
    celula_vazia = parsing.get("celula_vazia_valor")

    exemplos_uteis = [
        e for e in (exemplos or [])[:3]
        if e.get("trecho_pdf") and e.get("saida_esperada")
    ]

    blocos: list[str] = [
        "Extraia os dados de um cartão de ponto trabalhista brasileiro.",
        "",
        "CAMPOS DO CABEÇALHO (use EXATAMENTE estas chaves):",
    ]
    if campos_cabecalho:
        blocos.extend(f"  - {k}" for k in campos_cabecalho)
    else:
        blocos.append("  (não especificado — extraia empresa, funcionário, período se achar)")

    blocos.append("")
    blocos.append("COLUNAS DA TABELA DE BATIDAS (cada linha é um objeto com EXATAMENTE estas chaves):")
    if linhas_desc:
        blocos.extend(linhas_desc)
    else:
        blocos.append("  (não especificado — identifique as colunas pelo cabeçalho da tabela)")

    blocos.append("")
    blocos.append("REGRAS DE FORMATAÇÃO:")
    blocos.append(f"  - Horas: formato {formato_hora} (24h)")
    blocos.append(
        f"  - Datas: formato {formato_data}"
        + (f" (use ano {ano_default} se a data no PDF omitir o ano)" if ano_default else "")
    )
    blocos.append(f"  - Células vazias devem ser: {json.dumps(celula_vazia)}")
    blocos.append("  - NÃO invente valores. Se não achar, deixe como vazio.")

    if exemplos_uteis:
        blocos.append("")
        blocos.append("EXEMPLOS JÁ VALIDADOS POR HUMANO (siga este mesmo formato):")
        for i, ex in enumerate(exemplos_uteis):
            blocos.append(f"\n--- Exemplo {i + 1} ---")
            blocos.append("Trecho do PDF:")
            blocos.append(str(ex.get("trecho_pdf", ""))[:1500])
            blocos.append("Saída esperada:")
            blocos.append(
                json.dumps(ex.get("saida_esperada") or {}, ensure_ascii=False, indent=2)
            )

    blocos.append("")
    blocos.append("TEXTO DO PDF A EXTRAIR:")
    blocos.append("```")
    blocos.append(texto_pdf[:20000])
    blocos.append("```")
    blocos.append("")
    blocos.append("Retorne APENAS JSON válido neste formato (sem texto fora do JSON):")
    blocos.append('{"cabecalho": {<chaves listadas acima>}, "linhas": [{<chaves listadas acima>}, ...]}')

    user = "\n".join(blocos)
    system = (
        "Você é um extrator estrito de dados. Responde APENAS com um JSON válido, "
        "sem markdown, sem comentários, sem texto antes ou depois."
    )
    return system, user


def _normaliza_linhas_ia(
    linhas_raw: Any,
    colunas: list[dict[str, Any]],
    parsing: dict[str, Any],
    avisos: list[str],
) -> list[dict[str, Any]]:
    """
    Valida o retorno da IA: deve ser lista de dicts. Filtra tipos fora disso,
    restringe às chaves declaradas no esqueleto (se houver) e aplica parsing
    (hora/data/número) — mesma normalização do plumber_direto, garantindo
    consistência independente de quem extraiu.
    """
    if not isinstance(linhas_raw, list):
        avisos.append("llm_linhas_nao_e_lista")
        return []

    nomes_colunas = [c.get("nome") for c in colunas if c.get("nome")]
    tipos = {c["nome"]: c.get("tipo", "texto") for c in colunas if c.get("nome")}

    linhas: list[dict[str, Any]] = []
    for item in linhas_raw:
        if not isinstance(item, dict):
            continue
        if nomes_colunas:
            # Filtra apenas chaves declaradas + aplica parsing.
            linha: dict[str, Any] = {}
            for nome in nomes_colunas:
                linha[nome] = parse_celula(tipos[nome], item.get(nome), parsing)
            # Descarta linhas totalmente vazias (IA às vezes emite placeholders)
            if any(v not in (None, "", parsing.get("celula_vazia_valor")) for v in linha.values()):
                linhas.append(linha)
        else:
            # Sem colunas declaradas, aceita o dict como veio.
            linhas.append(item)
    return linhas


def _ia_barata_com_exemplos(
    pdf_bytes: bytes,
    estrutura: dict[str, Any],
    exemplos: list[dict[str, Any]],
    avisos: list[str],
    texto_override: str | None = None,
) -> dict[str, Any]:
    """
    Fallback usando LLM barato. Funciona **com ou sem** exemplos validados —
    se não houver exemplos, usa só a estrutura declarada como guia.

    Modelo usado: `estrutura.modelo_fallback` (se na whitelist), senão o
    default das settings.
    """
    # 1. Obtém texto — override > pdfplumber > OCR.
    if texto_override is not None and texto_override.strip():
        texto_pdf = texto_override.strip()
    else:
        try:
            textos = extrair_texto_todo(pdf_bytes)
            texto_pdf = "\n".join(textos).strip()
        except Exception:
            texto_pdf = ""

    if not texto_pdf:
        try:
            texto_pdf = "\n".join(ocr_todo(pdf_bytes)).strip()
            avisos.append("ia_texto_via_ocr")
        except Exception:
            texto_pdf = ""

    if not texto_pdf:
        avisos.append("pdf_sem_texto_extraivel")
        raise PontoExtractError(
            "PDF sem texto extraível nem por pdfplumber nem por OCR."
        )

    # 2. Monta prompt estruturado.
    system, user = _monta_prompt_ia(estrutura, exemplos, texto_pdf)

    # 3. Modelo efetivo.
    settings = get_settings()
    modelo_custom = estrutura.get("modelo_fallback")
    if modelo_custom and modelo_custom in settings.modelos_baratos_permitidos:
        modelo_efetivo = modelo_custom
    else:
        modelo_efetivo = settings.OPENROUTER_MODEL_BARATO
    logger.info("ia_barata chamando modelo=%s exemplos_uteis=%d texto_len=%d",
                modelo_efetivo,
                sum(1 for e in (exemplos or []) if e.get("trecho_pdf") and e.get("saida_esperada")),
                len(texto_pdf))

    # 4. Chama.
    client = get_llm_client()
    try:
        resultado = client.chat_json(
            model=modelo_efetivo,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=6000,
        )
    except LLMUnavailableError as exc:
        avisos.append(f"llm_indisponivel: {exc}")
        raise

    # 5. Valida retorno.
    cabecalho = resultado.get("cabecalho")
    if not isinstance(cabecalho, dict):
        avisos.append("llm_cabecalho_invalido")
        cabecalho = {}

    tabela_spec = estrutura.get("tabela") or {}
    colunas = tabela_spec.get("colunas") or []
    parsing = estrutura.get("parsing") or {}
    linhas = _normaliza_linhas_ia(resultado.get("linhas"), colunas, parsing, avisos)

    return {"cabecalho": cabecalho, "linhas": linhas}


# --- Heurística de fallback -------------------------------------------

def _diagnostica_extracao(
    dados: dict[str, Any], estrutura: dict[str, Any]
) -> str | None:
    """
    Retorna None se a extração parece OK, ou um string com o motivo
    se há sinais de que precisa de IA para completar/refazer.

    Critérios (ordenados do mais forte ao mais fraco):
      1. Zero linhas extraídas.
      2. Tabela tem colunas tipadas hora/data declaradas, mas NENHUMA
         célula dessas colunas está preenchida em nenhuma linha → plumber
         provavelmente quebrou a tabela.
      3. Mais de 70% das linhas têm apenas 1 célula populada (ruído).
    """
    linhas = dados.get("linhas") or []
    if not linhas:
        return "zero_linhas"

    tabela = estrutura.get("tabela") or {}
    colunas = tabela.get("colunas") or []
    parsing = estrutura.get("parsing") or {}
    valor_vazio = parsing.get("celula_vazia_valor")

    cols_tipadas = [c for c in colunas if c.get("tipo") in ("hora", "data")]
    if cols_tipadas:
        total_celulas_tipadas_preenchidas = 0
        for linha in linhas:
            for c in cols_tipadas:
                nome = c.get("nome")
                if not nome:
                    continue
                valor = linha.get(nome)
                if valor not in (None, "", valor_vazio):
                    total_celulas_tipadas_preenchidas += 1
        if total_celulas_tipadas_preenchidas == 0:
            return "colunas_tipadas_todas_vazias"

    # Linhas-ruído: muitas linhas com só 1 célula significativa
    if colunas:
        def _celulas_preenchidas(linha):
            return sum(
                1 for v in linha.values()
                if v not in (None, "", valor_vazio)
            )
        ruidosas = sum(1 for l in linhas if _celulas_preenchidas(l) <= 1)
        if ruidosas / len(linhas) > 0.7 and len(linhas) >= 3:
            return "maioria_linhas_com_1_celula"

    return None


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
      1. Método preferencial declarado no esqueleto.
      2. Se extração parece ruim E PDF escaneado → OCR guiado.
      3. Se extração AINDA parece ruim → IA barata (com ou sem exemplos).

    "Parece ruim" = 0 linhas, OU colunas hora/data todas vazias, OU maioria
    das linhas é ruído (ver _diagnostica_extracao). Antes, só 0 linhas
    disparava fallback; isso permitia passar por extrações parciais quebradas.
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

    # --- Cascata 1: plumber ruim E PDF escaneado → OCR ---
    diagnostico = _diagnostica_extracao(dados, estrutura)
    if (
        permitir_fallback_ocr
        and diagnostico
        and metodo_efetivo == MetodoExtracao.ESQUELETO_PLUMBER.value
        and parece_pdf_escaneado(pdf_bytes)
    ):
        logger.info(
            "fallback_ocr_guiado esqueleto_id=%s motivo=%s",
            esqueleto.id, diagnostico,
        )
        avisos.append(f"plumber_{diagnostico}_tentando_ocr")
        cabecalho_anterior = dados.get("cabecalho", {})
        dados_novo = _ocr_guiado(pdf_bytes, estrutura, avisos)
        # Preserva cabeçalho se o novo vier pior
        cab_novo = dados_novo.get("cabecalho") or {}
        if not any(cab_novo.values()):
            dados_novo["cabecalho"] = cabecalho_anterior
        dados = dados_novo
        metodo_efetivo = MetodoExtracao.ESQUELETO_OCR.value
        diagnostico = _diagnostica_extracao(dados, estrutura)

    # --- Cascata 2: extração ainda parece ruim → IA barata ---
    # (Não exige exemplos_validados — IA barata funciona também só com a estrutura.)
    if (
        permitir_fallback_llm
        and diagnostico
        and metodo_efetivo != MetodoExtracao.ESQUELETO_IA_BARATA.value
    ):
        logger.info(
            "fallback_ia_barata esqueleto_id=%s motivo=%s vindo_de=%s",
            esqueleto.id, diagnostico, metodo_efetivo,
        )
        avisos.append(f"{metodo_efetivo}_{diagnostico}_tentando_ia_barata")
        # Se já OCRizamos, reusa o texto pra evitar OCR duplicado.
        texto_override: str | None = None
        if metodo_efetivo == MetodoExtracao.ESQUELETO_OCR.value:
            try:
                texto_override = "\n".join(ocr_todo(pdf_bytes))
            except Exception:
                texto_override = None

        cabecalho_anterior = dados.get("cabecalho", {})
        try:
            dados_ia = _ia_barata_com_exemplos(
                pdf_bytes, estrutura, exemplos, avisos, texto_override=texto_override
            )
            # Preserva cabeçalho anterior se a IA não trouxer nada melhor.
            cab_ia = dados_ia.get("cabecalho") or {}
            if not any(cab_ia.values()) and any(cabecalho_anterior.values()):
                dados_ia["cabecalho"] = cabecalho_anterior
            # Se a IA retornou mais linhas que o método anterior, usa a IA;
            # se vier pior que o anterior, mantém o anterior (evita regredir).
            linhas_ia = dados_ia.get("linhas") or []
            linhas_ant = dados.get("linhas") or []
            if len(linhas_ia) > len(linhas_ant):
                dados = dados_ia
                metodo_efetivo = MetodoExtracao.ESQUELETO_IA_BARATA.value
            else:
                avisos.append(
                    f"ia_barata_trouxe_{len(linhas_ia)}_linhas_menos_que_anterior_{len(linhas_ant)}"
                )
        except PontoExtractError as exc:
            avisos.append(f"ia_barata_falhou: {exc}")

    tempo_ms = int((time.monotonic() - inicio) * 1000)
    return ResultadoExtracao(
        cabecalho=dados.get("cabecalho", {}),
        linhas=dados.get("linhas", []),
        metodo_efetivo=metodo_efetivo,
        tempo_ms=tempo_ms,
        avisos=avisos,
    )
