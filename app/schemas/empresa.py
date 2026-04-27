from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EsqueletoListItem(BaseModel):
    id: str
    versao: int
    status: str
    fingerprint: str
    # Lista completa de fingerprints aceitos por esta versão (inclui o
    # principal). UI mostra como tags na tela do esqueleto. Frontend pode
    # comparar pelo length para sinalizar versões "consolidadas" (>1 fp).
    fingerprints: list[str] = Field(default_factory=list)
    taxa_sucesso: float
    total_extracoes: int
    criado_em: datetime


class EsqueletoDetail(EsqueletoListItem):
    empresa_id: str
    empresa_nome: str | None = None
    estrutura: dict[str, Any]
    exemplos_validados: list[dict[str, Any]]
    atualizado_em: datetime


class EsqueletoUpdateRequest(BaseModel):
    estrutura: dict[str, Any] | None = None
    exemplos_validados: list[dict[str, Any]] | None = None


class EmpresaListItem(BaseModel):
    id: str
    nome: str
    cnpjs: list[str] = Field(default_factory=list)
    total_esqueletos: int
    taxa_sucesso_media: float | None = None
    criada_em: datetime


class EmpresaListResponse(BaseModel):
    itens: list[EmpresaListItem]
    total: int


class EmpresaDetail(BaseModel):
    id: str
    nome: str
    cnpjs: list[str]
    criada_em: datetime
    atualizada_em: datetime
    esqueletos: list[EsqueletoListItem]


class EmpresaUpdateRequest(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=255)
    cnpjs_adicionar: list[str] = Field(default_factory=list)
    cnpjs_remover: list[str] = Field(default_factory=list)
