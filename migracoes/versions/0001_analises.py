"""Tabelas das análises e das transações avaliadas

Revisão: 0001
Anterior:
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chave", sa.String(32), nullable=False),
        sa.Column("origem", sa.String(20), nullable=False),
        sa.Column("nome_ficheiro", sa.String(255), nullable=True),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("alertas", sa.Integer(), nullable=False),
        sa.Column("modelo", sa.String(50), nullable=False),
    )
    op.create_index("ix_analises_chave", "analises", ["chave"], unique=True)
    op.create_index("ix_analises_criada_em", "analises", ["criada_em"])

    op.create_table(
        "transacoes_avaliadas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analise_id", sa.Integer(), sa.ForeignKey("analises.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("id_transacao", sa.String(100), nullable=False),
        sa.Column("cliente", sa.String(100), nullable=False),
        sa.Column("data_hora", sa.DateTime(), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.Column("cidade", sa.String(100), nullable=False),
        sa.Column("pais", sa.String(100), nullable=False),
        sa.Column("categoria", sa.String(100), nullable=False),
        sa.Column("risco", sa.Float(), nullable=False),
        sa.Column("anomalia", sa.Float(), nullable=False),
        sa.Column("nivel", sa.String(20), nullable=False),
        sa.Column("motivos", sa.Text(), nullable=False),
        sa.Column("historico_curto", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_transacoes_avaliadas_analise_id", "transacoes_avaliadas", ["analise_id"])
    op.create_index("ix_transacoes_avaliadas_cliente", "transacoes_avaliadas", ["cliente"])
    op.create_index("ix_transacoes_avaliadas_nivel", "transacoes_avaliadas", ["nivel"])


def downgrade() -> None:
    op.drop_table("transacoes_avaliadas")
    op.drop_table("analises")
