from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HistoryItem(BaseModel):
    id: str
    criado_em: datetime
    nome_arquivo_original: str
    empresa_id: str | None = None
    empresa_nome: str | None = None
    esqueleto_id: str | None = None
    metodo_usado: str
    status: str
    score_conformidade: float | None = None
    tempo_processamento_ms: int | None = None
    id_processo: str | None = None
    id_documento: str | None = None


class HistoryListResponse(BaseModel):
    itens: list[HistoryItem]
    total: int
    limit: int
    offset: int


class HistoryDetailResponse(HistoryItem):
    resultado_json: dict[str, Any] | None = None
    custo_estimado_usd: float | None = None
    webhook_enviado: bool = False
