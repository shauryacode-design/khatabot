"""widen stored Telegram user ids

Revision ID: 9f3c2d1a7b64
Revises: 6d62fb347d59
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f3c2d1a7b64"
down_revision: Union[str, None] = "6d62fb347d59"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TELEGRAM_ID_COLUMNS = (
    ("entries", "created_by"),
    ("payments", "created_by"),
    ("audit_log", "user_id"),
    ("invites", "created_by"),
)


def upgrade() -> None:
    for table_name, column_name in _TELEGRAM_ID_COLUMNS:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                column_name,
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=False,
            )


def downgrade() -> None:
    connection = op.get_bind()
    minimum = -(2**31)
    maximum = 2**31 - 1
    for table_name, column_name in _TELEGRAM_ID_COLUMNS:
        out_of_range = connection.execute(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {table_name} "
                f"WHERE {column_name} < :minimum OR {column_name} > :maximum)"
            ),
            {"minimum": minimum, "maximum": maximum},
        ).scalar_one()
        if out_of_range:
            raise RuntimeError(
                f"Cannot downgrade {table_name}.{column_name}: values exceed "
                "the 32-bit INTEGER range."
            )

    for table_name, column_name in reversed(_TELEGRAM_ID_COLUMNS):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                column_name,
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=False,
            )
