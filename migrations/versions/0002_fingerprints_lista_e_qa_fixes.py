"""fingerprints como lista + ajustes do QA 25/04

Revision ID: 0002_qa_fixes_25_04
Revises: 0001_initial
Create Date: 2026-04-27

Mudanças:
1. esqueletos.fingerprints (JSON, lista) — nova coluna. Backfill com
   [fingerprint] de cada linha. Permite anexar fingerprints adicionais à
   mesma versão de esqueleto quando a heurística de fingerprint flutua
   entre PDFs do mesmo layout (ver DECISIONS.md — Fingerprint v2).

Nada destrutivo: coluna `fingerprint` (singular) é preservada para compat
e continua sendo o fingerprint "principal" da versão. Queries existentes
seguem funcionando.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002_qa_fixes_25_04"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adiciona coluna como nullable inicialmente, faz backfill, depois marca
    # NOT NULL. Postgres aceita default JSON literal; SQLite faz o mesmo via
    # SQLAlchemy 2.0.
    op.add_column(
        "esqueletos",
        sa.Column("fingerprints", sa.JSON(), nullable=True),
    )

    # Backfill: cada linha existente recebe lista com seu fingerprint atual.
    bind = op.get_bind()
    # Usa SQL literal pra portabilidade: json_array funciona em Postgres,
    # SQLite tem json_array nativo desde 3.38. Se for ambiente velho, o
    # caminho de Python abaixo cobre.
    try:
        bind.execute(
            sa.text(
                """
                UPDATE esqueletos
                SET fingerprints = json_array(fingerprint)
                WHERE fingerprints IS NULL
                """
            )
        )
    except Exception:
        # Fallback: dialeto sem json_array — atualiza linha a linha em Python.
        rows = bind.execute(
            sa.text("SELECT id, fingerprint FROM esqueletos WHERE fingerprints IS NULL")
        ).fetchall()
        for row in rows:
            bind.execute(
                sa.text(
                    "UPDATE esqueletos SET fingerprints = :fps WHERE id = :id"
                ),
                {"fps": f'["{row.fingerprint}"]', "id": str(row.id)},
            )

    # Agora a coluna pode virar NOT NULL com default vazio para futuras inserções.
    with op.batch_alter_table("esqueletos") as batch:
        batch.alter_column(
            "fingerprints",
            existing_type=sa.JSON(),
            nullable=False,
        )


def downgrade() -> None:
    op.drop_column("esqueletos", "fingerprints")
