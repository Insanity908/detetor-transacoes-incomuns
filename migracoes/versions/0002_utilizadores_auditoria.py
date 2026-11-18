"""Utilizadores, registo de auditoria e o autor de cada análise

Revisão: 0002
Anterior: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "utilizadores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome_utilizador", sa.String(50), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("perfil", sa.String(20), nullable=False),
        sa.Column("senha_hash", sa.String(255), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("trocar_senha", sa.Boolean(), nullable=False),
        sa.Column("tentativas_falhadas", sa.Integer(), nullable=False),
        sa.Column("bloqueado_ate", sa.DateTime(), nullable=True),
        sa.Column("versao_sessao", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("ultimo_acesso", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_utilizadores_nome_utilizador", "utilizadores", ["nome_utilizador"], unique=True)

    op.create_table(
        "auditoria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("momento", sa.DateTime(), nullable=False),
        sa.Column("utilizador_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("nome_utilizador", sa.String(50), nullable=False),
        sa.Column("acao", sa.String(40), nullable=False),
        sa.Column("alvo", sa.String(255), nullable=True),
        sa.Column("detalhe", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
    )
    op.create_index("ix_auditoria_momento", "auditoria", ["momento"])
    op.create_index("ix_auditoria_utilizador_id", "auditoria", ["utilizador_id"])
    op.create_index("ix_auditoria_acao", "auditoria", ["acao"])
    op.create_index("ix_auditoria_alvo", "auditoria", ["alvo"])

    with op.batch_alter_table("analises") as tabela:
        tabela.add_column(sa.Column("utilizador_id", sa.Integer(), nullable=True))
        tabela.create_foreign_key("fk_analises_utilizador_id", "utilizadores", ["utilizador_id"], ["id"],
                                  ondelete="SET NULL")
        tabela.create_index("ix_analises_utilizador_id", ["utilizador_id"])


def downgrade() -> None:
    with op.batch_alter_table("analises") as tabela:
        tabela.drop_index("ix_analises_utilizador_id")
        tabela.drop_constraint("fk_analises_utilizador_id", type_="foreignkey")
        tabela.drop_column("utilizador_id")
    op.drop_table("auditoria")
    op.drop_table("utilizadores")
