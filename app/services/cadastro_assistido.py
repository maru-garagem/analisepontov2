"""
Cadastro assistido: dado um PDF de empresa desconhecida, usa IA potente
(Vision) para propor uma estrutura de esqueleto + extrair amostra de linhas.

A proposta retorna ao usuário para validação visual. Quando confirmada,
o esqueleto é salvo e passa a ser reutilizado sem IA nos próximos uploads
da mesma empresa.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from app.config import get_settings
from app.services.identificacao import validar_cnpj
from app.services.llm import (
    LLMImageUnsupportedError,
    encode_image_base64,
    get_llm_client,
    message_with_image,
)
from app.utils.errors import LLMUnavailableError, NotACardPontoError, PontoExtractError
from app.utils.ocr import rasterizar
from app.utils.pdf import extrair_texto_todo, parece_pdf_escaneado

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Você é um especialista em cartões de ponto trabalhistas brasileiros.
Dada a PRIMEIRA PÁGINA de um PDF, sua tarefa é:
1. Identificar: nome da empresa, CNPJ, funcionário, período, matrícula, departamento.
2. Identificar a tabela de batidas: quantas colunas tem, qual a função de cada coluna
   e que tipo de dado contém cada uma (hora HH:MM, data DD/MM/YYYY, texto livre).
3. Propor uma ESTRUTURA de extração em JSON, de modo que sistemas futuros reusem
   essa estrutura sem precisar de IA.
4. Extrair 3 a 5 LINHAS de amostra da tabela, já processadas.

Retorne APENAS JSON válido com este shape:
{
  "nome_empresa": "...",
  "cnpjs_sugeridos": ["12345678000100"],
  "nome_funcionario": "...",
  "matricula": "...",
  "periodo": "...",
  "estrutura": {
    "metodo_preferencial": "plumber_direto",
    "cabecalho": {
      "empresa_nome": {"tipo": "ancora_regex", "regex": "..."},
      "cnpj": {"tipo": "regex_cnpj"},
      "funcionario_nome": {"tipo": "ancora_regex", "regex": "..."},
      "matricula": {"tipo": "ancora_regex", "regex": "..."},
      "periodo": {"tipo": "ancora_regex", "regex": "..."}
    },
    "tabela": {
      "num_colunas_esperado": N,
      "colunas": [
        {"nome": "data", "tipo": "data"},
        {"nome": "entrada", "tipo": "hora"}
      ],
      "linhas_descartar_regex": ["(?i)^total"],
      "header_row_regex": "(?i)data.*entrada"
    },
    "parsing": {
      "formato_hora": "HH:MM",
      "formato_data": "DD/MM/YYYY",
      "ano_default": null,
      "celula_vazia_valor": null
    }
  },
  "amostra_linhas": [
    {"data": "01/03/2026", "entrada": "08:00"}
  ],
  "confianca": 0.95
}

REGRAS IMPORTANTES:
- Cada regex do cabeçalho DEVE ter UM grupo de captura `(...)` contendo o VALOR final.
- As regex devem usar `(?i)` quando o label pode aparecer em maiúsculas ou minúsculas.
- Os tipos válidos de coluna são EXATAMENTE: "data", "hora", "texto", "numero".
- CNPJs sugeridos devem ser strings só de dígitos (14 chars), com DV válido.
- Se o documento NÃO for um cartão de ponto, retorne {"erro": "nao_cartao_ponto"}.
- Não inclua comentários nem texto fora do JSON.
"""


@dataclass
class Proposta:
    nome_empresa: str | None
    cnpjs_sugeridos: list[str]
    nome_funcionario: str | None
    matricula: str | None
    periodo: str | None
    estrutura: dict[str, Any]
    amostra_linhas: list[dict[str, Any]]
    confianca: float | None
    modelo_usado: str
    custo_estimado_usd: float | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome_empresa": self.nome_empresa,
            "cnpjs_sugeridos": self.cnpjs_sugeridos,
            "nome_funcionario": self.nome_funcionario,
            "matricula": self.matricula,
            "periodo": self.periodo,
            "estrutura": self.estrutura,
            "amostra_linhas": self.amostra_linhas,
            "confianca": self.confianca,
            "modelo_usado": self.modelo_usado,
            "custo_estimado_usd": self.custo_estimado_usd,
        }


def _filtrar_cnpjs_validos(cnpjs_raw: list[Any]) -> list[str]:
    validos: list[str] = []
    for c in cnpjs_raw:
        digits = "".join(ch for ch in str(c) if ch.isdigit())
        if len(digits) == 14 and validar_cnpj(digits) and digits not in validos:
            validos.append(digits)
    return validos


def _custo_estimado(usage: dict[str, Any] | None, modelo: str) -> float | None:
    """
    Estimativa de custo baseada em tokens de entrada/saída. Valores
    aproximados — exatidão não é crítica, usado só para logging/alerta.
    """
    if not usage:
        return None
    # Preços por 1M tokens (atualizar conforme evolução dos modelos).
    tabela = {
        "x-ai/grok-4": (5.0, 15.0),
        "openai/gpt-4o": (2.5, 10.0),
        "x-ai/grok-4-fast": (0.2, 0.5),
    }
    chave = modelo.lower()
    preco_in, preco_out = tabela.get(chave, (1.0, 3.0))
    inp = usage.get("prompt_tokens", 0) or 0
    out = usage.get("completion_tokens", 0) or 0
    return round((inp * preco_in + out * preco_out) / 1_000_000, 6)


def gerar_proposta(pdf_bytes: bytes, modelo: str | None = None) -> Proposta:
    """
    Analisa a primeira página do PDF via LLM Vision e retorna uma proposta
    de esqueleto + amostra. Se `modelo` não for passado, usa o default das
    settings (OPENROUTER_MODEL_POTENTE). Modelos inválidos/fora da whitelist
    caem silenciosamente no default.

    Levanta NotACardPontoError se o modelo identificar que o documento não
    é cartão de ponto.
    """
    # 1. Escolhe modelo efetivo e detecta se suporta visão
    settings = get_settings()
    modelo_escolhido = modelo if modelo in settings.modelos_potentes_permitidos else None
    modelo_efetivo = modelo_escolhido or settings.OPENROUTER_MODEL_POTENTE
    if modelo and not modelo_escolhido:
        logger.warning(
            "modelo_fora_whitelist solicitado=%s caindo_no_default=%s",
            modelo, modelo_efetivo,
        )
    suporta_visao = settings.modelo_suporta_visao(modelo_efetivo)

    # 2. Texto digital do PDF
    textos = extrair_texto_todo(pdf_bytes)
    texto_primeira = (textos[0] if textos else "")[:8000]
    pdf_eh_escaneado = parece_pdf_escaneado(pdf_bytes)

    # 3. Se o PDF é escaneado mas o modelo é text-only, erro claro — texto
    # extraído provavelmente está vazio ou lixo.
    if pdf_eh_escaneado and not suporta_visao:
        raise PontoExtractError(
            f"Este PDF parece ser escaneado (pouco texto digital) e o modelo "
            f"'{modelo_efetivo}' não suporta imagens. Escolha um modelo com "
            f"visão (Claude, GPT-4/5, Gemini) ou envie um PDF digital."
        )

    client = get_llm_client()

    def _chamar(com_imagem: bool) -> dict:
        if com_imagem:
            imagens = rasterizar(pdf_bytes, dpi=150, first_page=1, last_page=1)
            if not imagens:
                raise PontoExtractError("Falha ao rasterizar primeira página do PDF.")
            buf = BytesIO()
            imagens[0].save(buf, format="PNG")
            image_data_url = encode_image_base64(buf.getvalue(), mime="image/png")
            user_prompt = (
                "Texto extraído pela camada digital do PDF (pode estar vazio se for escaneado):\n"
                f"```\n{texto_primeira}\n```\n\n"
                "Analise a imagem da primeira página e o texto acima, e retorne a proposta JSON exigida."
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                message_with_image(user_prompt, image_data_url),
            ]
        else:
            user_prompt = (
                "Analise o texto abaixo (extraído pela camada digital do PDF) e "
                "retorne a proposta JSON exigida. Você NÃO receberá imagem — "
                "baseie-se apenas no texto.\n\n"
                f"```\n{texto_primeira}\n```"
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

        return client.chat(
            model=modelo_efetivo,
            messages=messages,
            max_tokens=4000,
            temperature=0.1,
            response_format={"type": "json_object"},
        )

    try:
        if suporta_visao:
            resposta = _chamar(com_imagem=True)
        else:
            resposta = _chamar(com_imagem=False)
    except LLMImageUnsupportedError:
        # Surpresa — catálogo dizia que suporta mas o provider rejeitou.
        # Atualizar catálogo e tentar só-texto.
        logger.warning(
            "modelo_rejeitou_imagem modelo=%s fallback_para_texto_only",
            modelo_efetivo,
        )
        if pdf_eh_escaneado:
            raise PontoExtractError(
                f"O modelo '{modelo_efetivo}' rejeitou a imagem e o PDF é "
                f"escaneado. Escolha outro modelo com visão."
            )
        try:
            resposta = _chamar(com_imagem=False)
        except LLMUnavailableError:
            raise
    except LLMUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover
        logger.exception("erro_inesperado_cadastro_assistido")
        raise LLMUnavailableError(f"Falha ao chamar IA potente: {exc}") from exc

    try:
        content = resposta["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMUnavailableError("Resposta da IA sem choices.") from exc

    import json
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("proposta_json_invalido content=%s", content[:500])
        raise LLMUnavailableError("IA retornou JSON inválido.") from exc

    if data.get("erro") == "nao_cartao_ponto":
        raise NotACardPontoError("IA identificou que o documento não é cartão de ponto.")

    custo = _custo_estimado(resposta.get("usage"), modelo_efetivo)

    return Proposta(
        nome_empresa=data.get("nome_empresa"),
        cnpjs_sugeridos=_filtrar_cnpjs_validos(data.get("cnpjs_sugeridos") or []),
        nome_funcionario=data.get("nome_funcionario"),
        matricula=data.get("matricula"),
        periodo=data.get("periodo"),
        estrutura=data.get("estrutura") or {},
        amostra_linhas=data.get("amostra_linhas") or [],
        confianca=data.get("confianca"),
        modelo_usado=modelo_efetivo,
        custo_estimado_usd=custo,
        raw=data,
    )
