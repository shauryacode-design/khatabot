"""allow duplicate customer names with labels

Revision ID: 4e7b8a1c2d90
Revises: 2ca71e8b43d0
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4e7b8a1c2d90"
down_revision: Union[str, None] = "2ca71e8b43d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("label", sa.String(length=80), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    has_labels = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM customers WHERE label IS NOT NULL)"
        )
    ).scalar_one()
    if has_labels:
        raise RuntimeError(
            "Cannot downgrade while labeled customers exist; remove labels first."
        )
    op.drop_column("customers", "label")
