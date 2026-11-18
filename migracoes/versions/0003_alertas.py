"""Fila de alertas, histórico de cada alerta e notificações

Revisão: 0003
Anterior: 0002

Os alertas das análises de ficheiros feitas antes desta versão entram na fila como novos.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alertas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transacao_id", sa.Integer(), sa.ForeignKey("transacoes_avaliadas.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("atribuido_a", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL"), nullable=True),
        sa.Column("atribuido_em", sa.DateTime(), nullable=True),
        sa.Column("decidido_por", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decidido_em", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_alertas_criado_em", "alertas", ["criado_em"])
    op.create_index("ix_alertas_estado", "alertas", ["estado"])
    op.create_index("ix_alertas_atribuido_a", "alertas", ["atribuido_a"])
    op.create_index("ix_alertas_decidido_em", "alertas", ["decidido_em"])

    op.create_table(
        "eventos_alerta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alerta_id", sa.Integer(), sa.ForeignKey("alertas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("momento", sa.DateTime(), nullable=False),
        sa.Column("utilizador_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("nome_utilizador", sa.String(50), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("de", sa.String(50), nullable=True),
        sa.Column("para", sa.String(50), nullable=True),
        sa.Column("texto", sa.Text(), nullable=True),
    )
    op.create_index("ix_eventos_alerta_alerta_id", "eventos_alerta", ["alerta_id"])

    with op.batch_alter_table("utilizadores") as tabela:
        tabela.add_column(sa.Column("ultimo_alerta_visto", sa.Integer(), nullable=False, server_default="0"))

    op.execute("""
        INSERT INTO alertas (transacao_id, criado_em, estado)
        SELECT t.id, a.criada_em, 'novo'
        FROM transacoes_avaliadas t JOIN analises a ON a.id = t.analise_id
        WHERE a.origem = 'ficheiro' AND t.nivel <> 'Normal'
        ORDER BY a.id, t.ordem
    """)


def downgrade() -> None:
    with op.batch_alter_table("utilizadores") as tabela:
        tabela.drop_column("ultimo_alerta_visto")
    op.drop_table("eventos_alerta")
    op.drop_table("alertas")
