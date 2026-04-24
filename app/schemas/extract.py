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


class CadastroPropostaResponse(BaseModel):
    processing_id: str
    empresa_candidata_id: str | None = None       # se matched por CNPJ
    empresa_candidata_nome: str | None = None
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
    # o frontend envia o ID dela aqui e criamos nova versão de esqueleto.
    empresa_id: str | None = None


class ApiExtractExternalResponse(BaseModel):
    """Resposta do endpoint de integração (/api/extract-api)."""
    processing_id: str
    status: str
    resultado_json: dict[str, Any] | None = None
    detalhe_erro: str | None = None
