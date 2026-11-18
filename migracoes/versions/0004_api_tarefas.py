"""Chaves da API, tarefas em segundo plano e rótulos do simulador

Revisão: 0004
Anterior: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chaves_api",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(80), nullable=False),
        sa.Column("prefixo", sa.String(16), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
        sa.Column("limite_por_minuto", sa.Integer(), nullable=False),
        sa.Column("ativa", sa.Boolean(), nullable=False),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
        sa.Column("criada_por", sa.String(50), nullable=False),
        sa.Column("ultimo_uso", sa.DateTime(), nullable=True),
        sa.Column("pedidos", sa.Integer(), nullable=False),
        sa.Column("transacoes", sa.Integer(), nullable=False),
    )
    op.create_index("ix_chaves_api_prefixo", "chaves_api", ["prefixo"])

    op.create_table(
        "tarefas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chave", sa.String(32), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("nome_ficheiro", sa.String(255), nullable=False),
        sa.Column("linhas", sa.Integer(), nullable=False),
        sa.Column("utilizador_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(), nullable=True),
        sa.Column("concluida_em", sa.DateTime(), nullable=True),
        sa.Column("analise_chave", sa.String(32), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
    )
    op.create_index("ix_tarefas_chave", "tarefas", ["chave"], unique=True)
    op.create_index("ix_tarefas_estado", "tarefas", ["estado"])
    op.create_index("ix_tarefas_utilizador_id", "tarefas", ["utilizador_id"])

    with op.batch_alter_table("analises") as tabela:
        tabela.add_column(sa.Column("chave_api_id", sa.Integer(), nullable=True))
        tabela.create_foreign_key("fk_analises_chave_api_id", "chaves_api", ["chave_api_id"], ["id"],
                                  ondelete="SET NULL")
        tabela.create_index("ix_analises_chave_api_id", ["chave_api_id"])

    with op.batch_alter_table("transacoes_avaliadas") as tabela:
        tabela.add_column(sa.Column("fraude_simulada", sa.Boolean(), nullable=True))
        tabela.add_column(sa.Column("tipo_fraude_simulada", sa.String(30), nullable=True))
        tabela.create_index("ix_transacoes_avaliadas_id_transacao", ["id_transacao"])


def downgrade() -> None:
    with op.batch_alter_table("transacoes_avaliadas") as tabela:
        tabela.drop_index("ix_transacoes_avaliadas_id_transacao")
        tabela.drop_column("tipo_fraude_simulada")
        tabela.drop_column("fraude_simulada")
    with op.batch_alter_table("analises") as tabela:
        tabela.drop_index("ix_analises_chave_api_id")
        tabela.drop_constraint("fk_analises_chave_api_id", type_="foreignkey")
        tabela.drop_column("chave_api_id")
    op.drop_table("tarefas")
    op.drop_table("chaves_api")
