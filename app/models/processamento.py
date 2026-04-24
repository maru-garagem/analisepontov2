from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Processamento(Base):
    __tablename__ = "processamentos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)

    # Nullable: pode não ter conseguido identificar, ou pode ter falhado antes.
    empresa_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("empresas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    esqueleto_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("esqueletos.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # IDs externos, fornecidos pelo cliente na chamada (opcionais).
    id_processo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    id_documento: Mapped[str | None] = mapped_column(String(255), nullable=True)

    nome_arquivo_original: Mapped[str] = mapped_column(String(512), nullable=False)

    metodo_usado: Mapped[str] = mapped_column(String(40), nullable=False)
    score_conformidade: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    resultado_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    tempo_processamento_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custo_estimado_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    webhook_enviado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    webhook_resposta: Mapped[str | None] = mapped_column(Text, nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
    criado_por: Mapped[str | None] = mapped_column(String(16), nullable=True)
