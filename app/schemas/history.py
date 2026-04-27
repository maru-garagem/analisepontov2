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
    # Só True para processamentos em `aguardando_cadastro` cujo PDF ainda
    # está no storage — frontend usa para decidir entre "Retomar" e "Reenviar".
    pode_retomar: bool = False


class HistoryListResponse(BaseModel):
    itens: list[HistoryItem]
    total: int
    limit: int
    offset: int


class HistoryDetailResponse(HistoryItem):
    resultado_json: dict[str, Any] | None = None
    custo_estimado_usd: float | None = None
    webhook_enviado: bool = False
    # Truncado em 500 chars no banco. Útil pra debug — quando o webhook
    # falha, o operador vê o motivo (status HTTP do receptor, erro de rede).
    webhook_resposta: str | None = None
