from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import StatusEsqueleto

if TYPE_CHECKING:
    from app.models.empresa import Empresa


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Esqueleto(Base):
    __tablename__ = "esqueletos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, index=True
    )

    versao: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=StatusEsqueleto.ATIVO.value, index=True
    )

    # Assinatura visual do layout — ver services/fingerprint.py.
    #
    # `fingerprint` (singular): o fingerprint "principal" — o primeiro visto
    # no cadastro. Mantido por compat com queries existentes e como sinal de
    # qual layout fundou esta versão.
    #
    # `fingerprints` (plural): conjunto de fingerprints aceitos por esta
    # versão. Quando um PDF da mesma empresa cai em cadastro assistido com
    # fingerprint diferente mas o operador confirma "é o mesmo layout",
    # adicionamos aqui em vez de versionar — evita o looping de cadastros
    # sucessivos quando a heurística do fingerprint flutua entre meses.
    # Sempre inclui o `fingerprint` principal.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fingerprints: Mapped[List[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    # Mapa semântico: cabeçalho, tabela, regras de parsing, método preferencial.
    # Estrutura detalhada documentada em DECISIONS.md + services/cadastro_assistido.py.
    estrutura: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Few-shot: 2-3 extrações de referência validadas por humano.
    exemplos_validados: Mapped[List[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    taxa_sucesso: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_extracoes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
    criado_por: Mapped[str | None] = mapped_column(String(16), nullable=True)

    empresa: Mapped["Empresa"] = relationship(back_populates="esqueletos")
