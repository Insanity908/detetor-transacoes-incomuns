"""Banco de demonstração: clientes, contas, movimentos e acesso dos clientes

Revisão: 0006
Anterior: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clientes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codigo", sa.String(10), nullable=False, unique=True),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("data_nascimento", sa.Date(), nullable=False),
        sa.Column("tipo_documento", sa.String(4), nullable=False),
        sa.Column("numero_documento", sa.String(20), nullable=False),
        sa.Column("telefone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(120), nullable=True),
        sa.Column("cidade", sa.String(100), nullable=False),
        sa.Column("morada", sa.String(255), nullable=False),
        sa.Column("sintetico", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("criado_por", sa.String(50), nullable=False),
        sa.UniqueConstraint("tipo_documento", "numero_documento", name="uq_clientes_documento"),
    )
    op.create_table(
        "contas_bancarias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("numero", sa.String(20), nullable=False, unique=True),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("saldo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saldo_retido", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estado", sa.String(10), nullable=False, server_default="ativa"),
        sa.Column("aberta_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint("saldo_retido >= 0", name="ck_contas_saldo_retido"),
    )
    op.create_table(
        "movimentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conta_id", sa.Integer(), sa.ForeignKey("contas_bancarias.id"), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("valor", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False),
        sa.Column("motivo", sa.String(255), nullable=True),
        sa.Column("contraparte_id", sa.Integer(), sa.ForeignKey("contas_bancarias.id"), nullable=True),
        sa.Column("canal", sa.String(12), nullable=False),
        sa.Column("momento", sa.DateTime(), nullable=False),
        sa.Column("registado_em", sa.DateTime(), nullable=False),
        sa.Column("transacao_id", sa.Integer(), sa.ForeignKey("transacoes_avaliadas.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("id_transacao", sa.String(100), nullable=True),
        sa.Column("descricao", sa.String(140), nullable=True),
        sa.Column("saldo_apos", sa.Integer(), nullable=True),
        sa.Column("criado_por", sa.String(50), nullable=False),
        sa.CheckConstraint("valor > 0", name="ck_movimentos_valor"),
    )
    op.create_index("ix_movimentos_conta_id", "movimentos", ["conta_id"])
    op.create_index("ix_movimentos_transacao_id", "movimentos", ["transacao_id"])
    op.create_index("ix_movimentos_id_transacao", "movimentos", ["id_transacao"])
    op.create_table(
        "acessos_cliente",
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("senha_hash", sa.String(255), nullable=False),
        sa.Column("trocar_senha", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tentativas_falhadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bloqueado_ate", sa.DateTime(), nullable=True),
        sa.Column("versao_sessao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ultimo_acesso", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("acessos_cliente")
    op.drop_index("ix_movimentos_id_transacao", "movimentos")
    op.drop_index("ix_movimentos_transacao_id", "movimentos")
    op.drop_index("ix_movimentos_conta_id", "movimentos")
    op.drop_table("movimentos")
    op.drop_table("contas_bancarias")
    op.drop_table("clientes")
