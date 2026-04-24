from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Empresa(Base):
    __tablename__ = "empresas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    atualizada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
    criada_por: Mapped[str | None] = mapped_column(String(16), nullable=True)

    cnpjs: Mapped[List["EmpresaCNPJ"]] = relationship(
        back_populates="empresa",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    esqueletos: Mapped[List["Esqueleto"]] = relationship(  # noqa: F821
        back_populates="empresa",
        cascade="all, delete-orphan",
    )


class EmpresaCNPJ(Base):
    __tablename__ = "empresa_cnpjs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cnpj: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)

    empresa: Mapped["Empresa"] = relationship(back_populates="cnpjs")
