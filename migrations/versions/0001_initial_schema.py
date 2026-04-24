"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-24

Cria as tabelas iniciais: empresas, empresa_cnpjs, esqueletos, processamentos.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "empresas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("criada_por", sa.String(length=16), nullable=True),
    )
    op.create_index("ix_empresas_nome", "empresas", ["nome"])

    op.create_table(
        "empresa_cnpjs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("empresa_id", sa.Uuid(), nullable=False),
        sa.Column("cnpj", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_empresa_cnpjs_cnpj", "empresa_cnpjs", ["cnpj"], unique=True)
    op.create_index("ix_empresa_cnpjs_empresa_id", "empresa_cnpjs", ["empresa_id"])

    op.create_table(
        "esqueletos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("empresa_id", sa.Uuid(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ativo"),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("estrutura", sa.JSON(), nullable=False),
        sa.Column("exemplos_validados", sa.JSON(), nullable=False),
        sa.Column("taxa_sucesso", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_extracoes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("criado_por", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_esqueletos_empresa_id", "esqueletos", ["empresa_id"])
    op.create_index("ix_esqueletos_fingerprint", "esqueletos", ["fingerprint"])
    op.create_index("ix_esqueletos_status", "esqueletos", ["status"])

    op.create_table(
        "processamentos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("empresa_id", sa.Uuid(), nullable=True),
        sa.Column("esqueleto_id", sa.Uuid(), nullable=True),
        sa.Column("id_processo", sa.String(length=255), nullable=True),
        sa.Column("id_documento", sa.String(length=255), nullable=True),
        sa.Column("nome_arquivo_original", sa.String(length=512), nullable=False),
        sa.Column("metodo_usado", sa.String(length=40), nullable=False),
        sa.Column("score_conformidade", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("resultado_json", sa.JSON(), nullable=True),
        sa.Column("tempo_processamento_ms", sa.Integer(), nullable=True),
        sa.Column("custo_estimado_usd", sa.Float(), nullable=True),
        sa.Column("webhook_enviado", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("webhook_resposta", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("criado_por", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["esqueleto_id"], ["esqueletos.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_processamentos_empresa_id", "processamentos", ["empresa_id"])
    op.create_index("ix_processamentos_esqueleto_id", "processamentos", ["esqueleto_id"])
    op.create_index("ix_processamentos_criado_em", "processamentos", ["criado_em"])
    op.create_index("ix_processamentos_status", "processamentos", ["status"])


def downgrade() -> None:
    op.drop_index("ix_processamentos_status", table_name="processamentos")
    op.drop_index("ix_processamentos_criado_em", table_name="processamentos")
    op.drop_index("ix_processamentos_esqueleto_id", table_name="processamentos")
    op.drop_index("ix_processamentos_empresa_id", table_name="processamentos")
    op.drop_table("processamentos")

    op.drop_index("ix_esqueletos_status", table_name="esqueletos")
    op.drop_index("ix_esqueletos_fingerprint", table_name="esqueletos")
    op.drop_index("ix_esqueletos_empresa_id", table_name="esqueletos")
    op.drop_table("esqueletos")

    op.drop_index("ix_empresa_cnpjs_empresa_id", table_name="empresa_cnpjs")
    op.drop_index("ix_empresa_cnpjs_cnpj", table_name="empresa_cnpjs")
    op.drop_table("empresa_cnpjs")

    op.drop_index("ix_empresas_nome", table_name="empresas")
    op.drop_table("empresas")
