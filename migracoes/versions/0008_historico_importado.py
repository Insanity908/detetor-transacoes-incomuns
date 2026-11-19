"""Histórico anterior importado para clientes novos (só para o detetor)

Revisão: 0008
Anterior: 0007
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historico_importado",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("data_hora", sa.DateTime(), nullable=False),
        sa.Column("valor", sa.Integer(), nullable=False),
        sa.Column("cidade", sa.String(100), nullable=False),
        sa.Column("pais", sa.String(100), nullable=False),
        sa.Column("categoria", sa.String(100), nullable=False),
        sa.Column("origem", sa.String(10), nullable=False),
        sa.Column("importado_em", sa.DateTime(), nullable=False),
        sa.Column("importado_por", sa.String(50), nullable=False),
        sa.CheckConstraint("valor > 0", name="ck_historico_importado_valor"),
        sa.UniqueConstraint("cliente_id", "data_hora", "valor", "cidade", "pais", "categoria",
                            name="uq_historico_importado_linha"),
    )
    op.create_index("ix_historico_importado_cliente_id", "historico_importado", ["cliente_id"])


def downgrade() -> None:
    op.drop_index("ix_historico_importado_cliente_id", table_name="historico_importado")
    op.drop_table("historico_importado")
