"""store Telegram update identifiers as bigint

Revision ID: 2ca71e8b43d0
Revises: 9f3c2d1a7b64
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2ca71e8b43d0"
down_revision: Union[str, None] = "9f3c2d1a7b64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("processed_updates") as batch_op:
        batch_op.alter_column(
            "update_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    out_of_range = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM processed_updates "
            "WHERE update_id < :minimum OR update_id > :maximum)"
        ),
        {"minimum": -(2**31), "maximum": 2**31 - 1},
    ).scalar_one()
    if out_of_range:
        raise RuntimeError(
            "Cannot downgrade processed_updates.update_id: values exceed "
            "the 32-bit INTEGER range."
        )
    with op.batch_alter_table("processed_updates") as batch_op:
        batch_op.alter_column(
            "update_id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
