"""Move each workspace Retail binding into the multi-engine connection catalog."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_connection",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("engine", sa.String(12), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(64), nullable=False),
        sa.Column("role", sa.String(128), nullable=False),
        sa.Column("sslmode", sa.String(11), nullable=False),
        sa.Column("retail_tenant_id", sa.BigInteger(), nullable=True),
        sa.Column("password_ciphertext", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_data_connection_port"),
        sa.CheckConstraint("revision > 0", name="ck_data_connection_revision"),
        sa.CheckConstraint("engine IN ('postgresql', 'mysql')", name="ck_data_connection_engine"),
        sa.CheckConstraint(
            "(retail_tenant_id IS NULL AND id <> workspace_id) OR "
            "(retail_tenant_id IS NOT NULL AND retail_tenant_id > 0 "
            "AND engine = 'postgresql' AND id = workspace_id)",
            name="ck_data_connection_retail",
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_data_connection_workspace_name"),
    )
    op.create_index("ix_data_connection_scope", "data_connection", ["tenant_id", "workspace_id"])
    op.execute("""
        INSERT INTO data_connection
            (id, workspace_id, tenant_id, name, engine, host, port, database_name, role,
             sslmode, retail_tenant_id, password_ciphertext, revision)
        SELECT workspace_id, workspace_id, tenant_id, '本地 Retail', 'postgresql',
               host, port, database_name, role, sslmode, retail_tenant_id, password_ciphertext,
               revision
        FROM retail_connection
    """)
    op.drop_table("retail_connection")


def downgrade() -> None:
    raise RuntimeError("Restore the verified pre-upgrade Agent backup to a separate database")
