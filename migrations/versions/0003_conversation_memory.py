"""Persist short shop-scoped conversation context."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_conversation_memory"
down_revision: Union[str, None] = "0002_ledger_and_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_memory",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("last_customer_name", sa.String(length=120), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_conversation_memory_shop_id",
        "conversation_memory",
        ["shop_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_memory_shop_id", table_name="conversation_memory")
    op.drop_table("conversation_memory")
