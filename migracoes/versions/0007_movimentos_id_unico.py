"""Um id_transacao só pode existir uma vez por conta (impede o duplo débito em pedidos sobrepostos)

Revisão: 0007
Anterior: 0006
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ux_movimentos_conta_id_transacao", "movimentos", ["conta_id", "id_transacao"], unique=True,
                    sqlite_where=sa.text("id_transacao IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ux_movimentos_conta_id_transacao", table_name="movimentos")
