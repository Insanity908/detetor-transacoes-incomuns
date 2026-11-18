"""Versões dos modelos, ajustes da interface e a versão que avaliou cada análise

Revisão: 0005
Anterior: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "versoes_modelo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("numero", sa.Integer(), nullable=False, unique=True),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("origem", sa.String(20), nullable=False),
        sa.Column("pasta", sa.String(255), nullable=False),
        sa.Column("classificador", sa.String(50), nullable=True),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
        sa.Column("criada_por", sa.String(50), nullable=False),
        sa.Column("publicada_em", sa.DateTime(), nullable=True),
        sa.Column("publicada_por", sa.String(50), nullable=True),
        sa.Column("metricas", sa.Text(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
    )
    op.create_index("ix_versoes_modelo_estado", "versoes_modelo", ["estado"])
    op.create_table(
        "ajustes",
        sa.Column("chave", sa.String(50), primary_key=True),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column("alterado_em", sa.DateTime(), nullable=False),
        sa.Column("alterado_por", sa.String(50), nullable=False),
    )
    with op.batch_alter_table("analises") as tabela:
        tabela.add_column(sa.Column("versao_modelo", sa.Integer(), nullable=True))
        tabela.create_index("ix_analises_versao_modelo", ["versao_modelo"])


def downgrade() -> None:
    with op.batch_alter_table("analises") as tabela:
        tabela.drop_index("ix_analises_versao_modelo")
        tabela.drop_column("versao_modelo")
    op.drop_table("ajustes")
    op.drop_table("versoes_modelo")
