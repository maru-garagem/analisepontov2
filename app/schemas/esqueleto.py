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
    # Settings opcionais passados direto pro `pdfplumber.Page.extract_tables`.
    # Útil quando o layout não tem grade visível e precisa de
    # `vertical_strategy="text"` etc. None = settings default do pdfplumber.
    table_settings: dict[str, Any] | None = None


# --- Parsing -----------------------------------------------------------

class CompletarDataDoPeriodoSpec(BaseModel):
    """
    Quando o cartão lista apenas o DIA (ex: `21, 22, 23, ...`) e o período
    completo está no cabeçalho (ex: `21/12/2015 - 20/01/2016`), esta regra
    instrui o parser a montar a data completa por linha.

    Algoritmo:
    1. Lê `campo_periodo` do cabeçalho extraído.
    2. Aplica `regex_periodo` para extrair (dia_inicio, mes_inicio, ano_inicio,
       dia_fim, mes_fim, ano_fim).
    3. Para cada linha, lê `coluna_dia` (string) e converte para int.
    4. Se dia >= dia_inicio → mês/ano = (mes_inicio, ano_inicio).
       Senão → mês/ano = (mes_fim, ano_fim).
    5. Grava a data completa em `coluna_destino` (formato DD/MM/YYYY).

    `coluna_destino` pode ser igual a `coluna_dia` (sobrescreve) ou nome
    novo (preserva o original).
    """
    campo_periodo: str = Field(
        description="Nome do campo de cabeçalho que contém o período (ex: 'periodo')."
    )
    coluna_dia: str = Field(
        description="Nome da coluna da tabela com o DIA isolado (ex: 'dia')."
    )
    coluna_destino: str = Field(
        description="Nome da coluna onde gravar a data completa (ex: 'data')."
    )
    regex_periodo: str = Field(
        default=r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*[-aàté]+\s*(\d{1,2})/(\d{1,2})/(\d{2,4})",
        description=(
            "Regex com 6 grupos: (dia_ini, mes_ini, ano_ini, dia_fim, mes_fim, "
            "ano_fim). Default cobre formatos comuns ('DD/MM/AAAA - DD/MM/AAAA',"
            " 'DD/MM/AAAA a DD/MM/AAAA', 'DD/MM/AAAA até DD/MM/AAAA')."
        ),
    )


class ParsingSpec(BaseModel):
    celula_vazia_valor: Any = None
    formato_hora: str = "HH:MM"
    formato_data: str = "DD/MM/YYYY"
    ano_default: int | None = None
    # Quando declarado, o pipeline pós-extração combina o DIA da linha com
    # o PERÍODO do cabeçalho para gerar a data completa. Útil em cartões
    # que só listam o dia. Ver CompletarDataDoPeriodoSpec acima.
    completar_data_do_periodo: CompletarDataDoPeriodoSpec | None = None


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
