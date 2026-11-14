"""Persist Retail connection configuration by workspace."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retail_connection",
        sa.Column("workspace_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(63), nullable=False),
        sa.Column("role", sa.String(63), nullable=False),
        sa.Column("sslmode", sa.String(11), nullable=False),
        sa.Column("retail_tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("password_ciphertext", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_retail_connection_port"),
        sa.CheckConstraint("retail_tenant_id > 0", name="ck_retail_connection_tenant"),
        sa.CheckConstraint("revision > 0", name="ck_retail_connection_revision"),
    )


def downgrade() -> None:
    raise RuntimeError("Restore the verified pre-upgrade Agent backup to a separate database")
