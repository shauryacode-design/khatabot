"""Add customer ledgers, confirmations, and audit tables."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_ledger_and_workflows"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("phone_encrypted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_id", "normalized_name", name="uq_customer_shop_name"),
    )
    op.create_index("ix_customers_shop_id", "customers", ["shop_id"], unique=False)

    for table_name, date_column, amount_column in (
        ("entries", "entry_date", "price_paise"),
        ("payments", "payment_date", "amount_paise"),
    ):
        columns = [
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("shop_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column(date_column, sa.Date(), nullable=False),
        ]
        if table_name == "entries":
            columns.extend(
                [
                    sa.Column("item_name", sa.String(length=160), nullable=False),
                    sa.Column("quantity_text", sa.String(length=80), nullable=True),
                ]
            )
        columns.extend(
            [
                sa.Column(amount_column, sa.Integer(), nullable=False),
                sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column("created_by", sa.Integer(), nullable=False),
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
                sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
                sa.PrimaryKeyConstraint("id"),
            ]
        )
        op.create_table(table_name, *columns)
        op.create_index(
            f"ix_{table_name}_shop_customer_date",
            table_name,
            ["shop_id", "customer_id", date_column],
        )
        op.create_index(f"ix_{table_name}_shop_date", table_name, ["shop_id", date_column])

    op.create_table(
        "pending_confirmations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pending_shop_user_status",
        "pending_confirmations",
        ["shop_id", "user_id", "status"],
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_shop_created", "audit_log", ["shop_id", "created_at"])
    op.create_table(
        "processed_updates",
        sa.Column("update_id", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_invites_shop_id", "invites", ["shop_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invites_shop_id", table_name="invites")
    op.drop_table("invites")
    op.drop_table("processed_updates")
    op.drop_index("ix_audit_shop_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_pending_shop_user_status", table_name="pending_confirmations")
    op.drop_table("pending_confirmations")
    for table_name in ("payments", "entries"):
        op.drop_index(f"ix_{table_name}_shop_date", table_name=table_name)
        op.drop_index(f"ix_{table_name}_shop_customer_date", table_name=table_name)
        op.drop_table(table_name)
    op.drop_index("ix_customers_shop_id", table_name="customers")
    op.drop_table("customers")
