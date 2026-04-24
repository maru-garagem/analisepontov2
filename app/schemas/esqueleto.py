"""
Schemas Pydantic da **estrutura** interna do esqueleto (o JSON em
Esqueleto.estrutura). Usados para validar o que a IA potente propõe no
cadastro assistido e o que o usuário confirma, antes de persistir.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


# --- Cabeçalho ---------------------------------------------------------

class RegraCabecalhoAncoraRegex(BaseModel):
    tipo: Literal["ancora_regex"] = "ancora_regex"
    # Regex com pelo menos um grupo de captura — o valor do campo é group(1).
    regex: str


class RegraCabecalhoCnpj(BaseModel):
    tipo: Literal["regex_cnpj"] = "regex_cnpj"


class RegraCabecalhoLiteral(BaseModel):
    """Valor fixo (p.ex. nome da empresa já conhecido — economiza regex)."""
    tipo: Literal["literal"] = "literal"
    valor: str


RegraCabecalho = Annotated[
    RegraCabecalhoAncoraRegex | RegraCabecalhoCnpj | RegraCabecalhoLiteral,
    Field(discriminator="tipo"),
]


# --- Tabela ------------------------------------------------------------

class ColunaTabela(BaseModel):
    nome: str
    tipo: Literal["texto", "hora", "data", "numero"] = "texto"
    formato: str | None = None


class TabelaSpec(BaseModel):
    num_colunas_esperado: int | None = None
    colunas: list[ColunaTabela] = Field(default_factory=list)
    linhas_descartar_regex: list[str] = Field(default_factory=list)
    header_row_regex: str | None = None


# --- Parsing -----------------------------------------------------------

class ParsingSpec(BaseModel):
    celula_vazia_valor: Any = None
    formato_hora: str = "HH:MM"
    formato_data: str = "DD/MM/YYYY"
    ano_default: int | None = None


# --- Estrutura completa ------------------------------------------------

MetodoPreferencial = Literal["plumber_direto", "ocr_guiado", "ia_barata_com_exemplos"]


class EstruturaEsqueleto(BaseModel):
    metodo_preferencial: MetodoPreferencial = "plumber_direto"
    # Modelo barato usado quando cai no fallback IA (plumber/OCR falham).
    # Se None, usa o default das settings (OPENROUTER_MODEL_BARATO).
    modelo_fallback: str | None = None
    cabecalho: dict[str, RegraCabecalho] = Field(default_factory=dict)
    tabela: TabelaSpec = Field(default_factory=TabelaSpec)
    parsing: ParsingSpec = Field(default_factory=ParsingSpec)


class ExemploValidado(BaseModel):
    """
    Referência few-shot. Usado pelo fallback ia_barata_com_exemplos para
    ensinar o LLM barato a formatar saídas iguais às validadas por humano.
    """
    trecho_pdf: str
    saida_esperada: dict[str, Any]
