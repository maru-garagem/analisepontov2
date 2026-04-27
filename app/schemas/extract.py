"""
DTOs do fluxo de extração e cadastro assistido.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExtractStartResponse(BaseModel):
    processing_id: str
    status: str


class ExtractStatusResponse(BaseModel):
    processing_id: str
    status: str
    empresa_id: str | None = None
    empresa_nome: str | None = None
    esqueleto_id: str | None = None
    cnpj_detectado: str | None = None
    match_type: str | None = None
    metodo_usado: str | None = None
    score_conformidade: float | None = None
    resultado_json: dict[str, Any] | None = None
    avisos: list[str] = Field(default_factory=list)
    detalhe_erro: str | None = None
    tempo_processamento_ms: int | None = None


class EsqueletoAtivoInfo(BaseModel):
    """
    Resumo do esqueleto ativo de uma empresa candidata, devolvido na proposta
    para a UI decidir entre "anexar layout à versão ativa" vs "criar nova
    versão". Ver DECISIONS.md — múltiplos fingerprints por esqueleto.
    """
    id: str
    versao: int
    fingerprint_principal: str
    fingerprints_extras: list[str] = Field(default_factory=list)
    total_extracoes: int = 0
    taxa_sucesso: float = 0.0


class CadastroPropostaResponse(BaseModel):
    processing_id: str
    empresa_candidata_id: str | None = None       # se matched por CNPJ
    empresa_candidata_nome: str | None = None
    # Quando empresa_candidata existe E tem esqueleto ativo, este campo
    # vem preenchido. Frontend usa para oferecer "Anexar este fingerprint
    # à versão ativa" (não cria nova versão — só registra que este layout
    # também pertence à mesma versão).
    esqueleto_ativo_da_empresa: EsqueletoAtivoInfo | None = None
    nome_empresa_sugerido: str | None = None
    cnpjs_sugeridos: list[str] = Field(default_factory=list)
    cnpj_detectado_no_pdf: str | None = None
    fingerprint_hash: str
    nome_funcionario: str | None = None
    matricula: str | None = None
    periodo: str | None = None
    estrutura: dict[str, Any]
    amostra_linhas: list[dict[str, Any]] = Field(default_factory=list)
    confianca: float | None = None


class CadastroConfirmarRequest(BaseModel):
    nome_empresa: str = Field(min_length=1, max_length=255)
    cnpjs: list[str] = Field(default_factory=list)
    estrutura: dict[str, Any]
    exemplos_validados: list[dict[str, Any]] = Field(default_factory=list)
    # Se o usuário aceitou uma empresa já existente (match_type=cnpj_somente),
    # o frontend envia o ID dela aqui e (por default) criamos nova versão de
    # esqueleto.
    empresa_id: str | None = None
    # Quando True E `empresa_id` aponta pra empresa que já tem esqueleto
    # ativo, NÃO cria nova versão — anexa o fingerprint atual à lista do
    # esqueleto ativo (e atualiza a estrutura, se diferente). Use quando o
    # operador olhou e confirmou que é o MESMO layout, só com flutuação no
    # fingerprint. Ver DECISIONS.md — múltiplos fingerprints por esqueleto.
    anexar_a_versao_atual: bool = False


class ApiExtractExternalResponse(BaseModel):
    """Resposta do endpoint de integração (/api/extract-api)."""
    processing_id: str
    status: str
    resultado_json: dict[str, Any] | None = None
    detalhe_erro: str | None = None
