"""
Aplica um esqueleto a um PDF e retorna dados estruturados (cabeçalho + linhas).

Métodos suportados:
  - `plumber_direto` (principal): pdfplumber extrai texto + tabelas;
    cabeçalho é preenchido via regex-âncora; linhas vêm das tabelas detectadas.
    Aceita `tabela.table_settings` opcional repassado a `extract_tables()`.
  - `ocr_guiado`: OCR + reconstrução de tabela por bounding boxes
    (`ocr_tabela_por_bbox`). Cabeçalho via regex contra o texto OCRizado.
    Acionado em fallback quando o método principal entrega lixo, mesmo
    para PDFs digitais (não só escaneados) — vide _diagnostica_extracao.
  - `ia_barata_com_exemplos`: envia texto do PDF + exemplos few-shot ao
    LLM barato. Acionado se OCR também falhar.

Cascata de fallback:
  1. método preferencial declarado
  2. se diagnóstico ruim → OCR guiado (mesmo em PDFs digitais quando o
     plumber detectou tabela quebrada)
  3. se ainda ruim → IA barata
  4. pós-processamento: `parsing.completar_data_do_periodo` aplica regra
     que combina DIA da linha + PERÍODO do cabeçalho (cobre layouts onde
     o ano/mês só aparece no header).

Entrada: bytes do PDF + esqueleto ORM + (opcional) modelo_barato_override.
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
_RE_DIA_ISOLADO = re.compile(r"^\s*(\d{1,2})\b")


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


# --- Pós-processamento: completar data por período ---------------------

def _aplicar_completar_data_do_periodo(
    cabecalho: dict[str, Any],
    linhas: list[dict[str, Any]],
    parsing: dict[str, Any],
    avisos: list[str],
) -> list[dict[str, Any]]:
    """
    Combina o DIA da linha com o PERÍODO do cabeçalho para gerar uma
    data completa. No-op se a regra não estiver configurada ou faltarem
    dados (período não casou com regex, dia inválido, etc).
    """
    cfg = parsing.get("completar_data_do_periodo")
    if not cfg or not isinstance(cfg, dict):
        return linhas

    campo_periodo = cfg.get("campo_periodo")
    coluna_dia = cfg.get("coluna_dia")
    coluna_destino = cfg.get("coluna_destino") or coluna_dia
    regex_periodo = cfg.get("regex_periodo") or (
        r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*[-aàté]+\s*(\d{1,2})/(\d{1,2})/(\d{2,4})"
    )

    if not (campo_periodo and coluna_dia):
        avisos.append("completar_data_periodo_config_invalida")
        return linhas

    periodo_str = cabecalho.get(campo_periodo)
    if not periodo_str or not isinstance(periodo_str, str):
        avisos.append(f"completar_data_periodo_sem_valor_em_cabecalho_{campo_periodo}")
        return linhas

    try:
        m = re.search(regex_periodo, periodo_str)
    except re.error:
        avisos.append("completar_data_regex_invalida")
        return linhas
    if not m or len(m.groups()) < 6:
        avisos.append("completar_data_periodo_nao_casou_regex")
        return linhas

    try:
        dia_ini = int(m.group(1))
        mes_ini = int(m.group(2))
        ano_ini = int(m.group(3))
        dia_fim = int(m.group(4))
        mes_fim = int(m.group(5))
        ano_fim = int(m.group(6))
    except ValueError:
        avisos.append("completar_data_periodo_grupos_nao_numericos")
        return linhas

    if ano_ini < 100:
        ano_ini += 2000
    if ano_fim < 100:
        ano_fim += 2000

    novas: list[dict[str, Any]] = []
    aplicacoes_ok = 0
    for linha in linhas:
        nova = dict(linha)
        valor_dia_raw = nova.get(coluna_dia)
        if valor_dia_raw is None:
            novas.append(nova)
            continue
        m_dia = _RE_DIA_ISOLADO.match(str(valor_dia_raw))
        if not m_dia:
            # Já é data completa (DD/MM/AAAA) ou outra coisa — preserva.
            novas.append(nova)
            continue
        try:
            dia_int = int(m_dia.group(1))
        except ValueError:
            novas.append(nova)
            continue
        if not 1 <= dia_int <= 31:
            novas.append(nova)
            continue

        # Decisão mês/ano: dia >= dia_ini → bloco inicial; senão, bloco fim.
        # Cobre o caso clássico "21/12/2015 a 20/01/2016": dia 25 → 12/2015,
        # dia 5 → 01/2016.
        if dia_int >= dia_ini:
            mes_efetivo, ano_efetivo = mes_ini, ano_ini
        else:
            mes_efetivo, ano_efetivo = mes_fim, ano_fim

        nova[coluna_destino] = f"{dia_int:02d}/{mes_efetivo:02d}/{ano_efetivo:04d}"
        aplicacoes_ok += 1
        novas.append(nova)

    if aplicacoes_ok == 0:
        avisos.append("completar_data_periodo_zero_linhas_aplicadas")
    return novas


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
    table_settings = tabela_spec.get("table_settings") or None

    linhas: list[dict[str, Any]] = []

    with abrir_pdf(pdf_bytes) as pdf:
        for pagina in pdf.pages:
            try:
                if table_settings:
                    tabelas = pagina.extract_tables(table_settings) or []
                else:
                    tabelas = pagina.extract_tables() or []
            except Exception as exc:
                logger.warning("plumber_extract_tables_falhou pagina_err=%s", exc)
                tabelas = []
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
    OU para PDFs digitais cujo `plumber_direto` entregou tabela quebrada
    (ex: tabela sem grade visível, com colunas posicionais — caso Itaú).

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
    modelo_barato_override: str | None = None,
) -> dict[str, Any]:
    """
    Fallback usando LLM barato. Funciona **com ou sem** exemplos validados —
    se não houver exemplos, usa só a estrutura declarada como guia.

    Modelo usado, em ordem:
      1. `modelo_barato_override` se passado (ex: usuário escolheu para esta
         chamada específica).
      2. `estrutura.modelo_fallback` salvo no esqueleto.
      3. `OPENROUTER_MODEL_BARATO` das settings.
    Apenas modelos da whitelist `modelos_baratos_permitidos` são aceitos —
    qualquer outro cai silenciosamente no default.
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
    modelo_efetivo: str | None = None
    if modelo_barato_override and modelo_barato_override in settings.modelos_baratos_permitidos:
        modelo_efetivo = modelo_barato_override
    elif modelo_barato_override:
        avisos.append(
            f"modelo_barato_override_fora_whitelist_ignorado={modelo_barato_override}"
        )
    if modelo_efetivo is None:
        modelo_custom = estrutura.get("modelo_fallback")
        if modelo_custom and modelo_custom in settings.modelos_baratos_permitidos:
            modelo_efetivo = modelo_custom
        else:
            modelo_efetivo = settings.OPENROUTER_MODEL_BARATO
    logger.info(
        "ia_barata chamando modelo=%s exemplos_uteis=%d texto_len=%d",
        modelo_efetivo,
        sum(1 for e in (exemplos or []) if e.get("trecho_pdf") and e.get("saida_esperada")),
        len(texto_pdf),
    )

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
    se há sinais de que precisa de fallback (OCR / IA) para completar/refazer.

    Critérios (ordenados do mais forte ao mais fraco):
      1. `zero_linhas`: nenhuma linha extraída.
      2. `colunas_tipadas_todas_vazias`: tabela tem colunas hora/data
         declaradas mas nenhuma célula preenchida em nenhuma linha → plumber
         provavelmente quebrou a tabela.
      3. `linhas_em_celula_unica`: muitas linhas têm apenas 1 célula
         significativa, mas o esqueleto declara várias colunas → tabela
         posicional (sem grade) que o plumber colapsou em 1 coluna gigante
         (caso clássico Itaú).
      4. `maioria_linhas_com_1_celula`: critério antigo, mantido por compat.
      5. `poucas_linhas_para_tabela_declarada`: o esqueleto declara N
         colunas e foi preenchido apenas o cabeçalho, sem linhas reais.
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

    if colunas:
        def _celulas_preenchidas(linha):
            return sum(
                1 for v in linha.values()
                if v not in (None, "", valor_vazio)
            )
        # Critério forte: tabela declara >=3 colunas mas TODAS as linhas
        # têm 0 ou 1 célula → plumber colapsou tudo em uma coluna só.
        if len(colunas) >= 3 and len(linhas) >= 3:
            todas_em_1 = all(_celulas_preenchidas(l) <= 1 for l in linhas)
            if todas_em_1:
                return "linhas_em_celula_unica"

        ruidosas = sum(1 for l in linhas if _celulas_preenchidas(l) <= 1)
        if ruidosas / len(linhas) > 0.7 and len(linhas) >= 3:
            return "maioria_linhas_com_1_celula"

    return None


def _diagnostica_pos_completar_data(
    linhas: list[dict[str, Any]], estrutura: dict[str, Any]
) -> str | None:
    """
    Diagnóstico extra **após** a regra de completar_data_do_periodo:
    se a regra estava configurada e nenhuma linha tem data preenchida no
    `coluna_destino`, sinaliza para o operador no resultado.
    """
    parsing = estrutura.get("parsing") or {}
    cfg = parsing.get("completar_data_do_periodo")
    if not cfg or not isinstance(cfg, dict):
        return None
    coluna_destino = cfg.get("coluna_destino") or cfg.get("coluna_dia")
    if not coluna_destino:
        return None
    if not linhas:
        return None
    com_data = sum(
        1 for l in linhas
        if isinstance(l.get(coluna_destino), str)
        and re.match(r"^\d{2}/\d{2}/\d{4}$", l[coluna_destino])
    )
    if com_data == 0:
        return "completar_data_periodo_sem_efeito"
    return None


# --- Entry point -------------------------------------------------------

def aplicar_esqueleto(
    pdf_bytes: bytes,
    esqueleto: Esqueleto,
    *,
    permitir_fallback_llm: bool = True,
    permitir_fallback_ocr: bool = True,
    modelo_barato_override: str | None = None,
) -> ResultadoExtracao:
    """
    Aplica o esqueleto em cascata:
      1. Método preferencial declarado no esqueleto.
      2. Se extração parece ruim → OCR guiado (em PDF escaneado OU em PDF
         digital com sinais de tabela quebrada — `colunas_tipadas_todas_vazias`
         ou `linhas_em_celula_unica`).
      3. Se extração AINDA parece ruim → IA barata (com ou sem exemplos).
      4. Pós-processamento: aplica `parsing.completar_data_do_periodo`
         se configurado.

    `modelo_barato_override` (opcional): se vier preenchido e estiver na
    whitelist, é usado quando a cascata cair na IA barata. Útil para
    permitir ao chamador da `/api/extract` testar um modelo específico
    sem alterar o esqueleto. Não persistente.

    "Parece ruim" = ver `_diagnostica_extracao`.
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
        dados = _ia_barata_com_exemplos(
            pdf_bytes, estrutura, exemplos, avisos,
            modelo_barato_override=modelo_barato_override,
        )
        metodo_efetivo = MetodoExtracao.ESQUELETO_IA_BARATA.value
    else:
        raise PontoExtractError(f"Método '{metodo_preferencial}' não suportado.")

    # --- Cascata 1: plumber ruim → OCR ---
    # Antes só rodava OCR se fosse PDF escaneado. Agora também roda quando
    # plumber detectou tabela quebrada (max 1 célula por linha mas o esqueleto
    # declara várias colunas, ou colunas hora/data todas vazias) — isso cobre
    # PDFs digitais com tabela posicional sem grade (caso Itaú).
    diagnostico = _diagnostica_extracao(dados, estrutura)
    deve_tentar_ocr = (
        permitir_fallback_ocr
        and diagnostico
        and metodo_efetivo == MetodoExtracao.ESQUELETO_PLUMBER.value
        and (
            parece_pdf_escaneado(pdf_bytes)
            or diagnostico in (
                "linhas_em_celula_unica",
                "colunas_tipadas_todas_vazias",
            )
        )
    )
    if deve_tentar_ocr:
        logger.info(
            "fallback_ocr_guiado esqueleto_id=%s motivo=%s",
            esqueleto.id, diagnostico,
        )
        avisos.append(f"plumber_{diagnostico}_tentando_ocr")
        cabecalho_anterior = dados.get("cabecalho", {})
        try:
            dados_novo = _ocr_guiado(pdf_bytes, estrutura, avisos)
        except Exception as exc:
            # OCR pode falhar (Tesseract ausente, PDF malformado). Loga e
            # segue para a IA barata.
            logger.warning("ocr_guiado_falhou esqueleto_id=%s err=%s", esqueleto.id, exc)
            avisos.append(f"ocr_falhou: {exc}")
            dados_novo = None
        if dados_novo is not None:
            cab_novo = dados_novo.get("cabecalho") or {}
            if not any(cab_novo.values()):
                dados_novo["cabecalho"] = cabecalho_anterior
            # Só aceita o resultado do OCR se trouxer mais linhas que o anterior.
            if len(dados_novo.get("linhas") or []) > len(dados.get("linhas") or []):
                dados = dados_novo
                metodo_efetivo = MetodoExtracao.ESQUELETO_OCR.value
            else:
                avisos.append(
                    f"ocr_trouxe_{len(dados_novo.get('linhas') or [])}_linhas"
                    f"_<=_anterior_{len(dados.get('linhas') or [])}_mantendo_plumber"
                )
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
                pdf_bytes, estrutura, exemplos, avisos,
                texto_override=texto_override,
                modelo_barato_override=modelo_barato_override,
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

    # --- Pós-processamento: completar_data_do_periodo ---
    parsing_spec = estrutura.get("parsing") or {}
    if parsing_spec.get("completar_data_do_periodo"):
        dados["linhas"] = _aplicar_completar_data_do_periodo(
            dados.get("cabecalho") or {},
            dados.get("linhas") or [],
            parsing_spec,
            avisos,
        )
        diag_pos = _diagnostica_pos_completar_data(dados.get("linhas") or [], estrutura)
        if diag_pos:
            avisos.append(diag_pos)

    tempo_ms = int((time.monotonic() - inicio) * 1000)
    return ResultadoExtracao(
        cabecalho=dados.get("cabecalho", {}),
        linhas=dados.get("linhas", []),
        metodo_efetivo=metodo_efetivo,
        tempo_ms=tempo_ms,
        avisos=avisos,
    )
