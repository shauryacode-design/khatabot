"""telegram ids to bigint

Revision ID: 6d62fb347d59
Revises: 0003_conversation_memory
Create Date: 2026-10-24 10:54:53.663826
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6d62fb347d59'
down_revision: Union[str, None] = '0003_conversation_memory'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "telegram_user_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
        )



def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "telegram_user_id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
        )
