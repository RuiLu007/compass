"""Keep deleted workflow assets for historical references (977)."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workflow_asset ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE workflow_asset DROP CONSTRAINT uq_workflow_asset_scope_script")
    op.execute("""
        CREATE UNIQUE INDEX uq_workflow_asset_scope_script ON workflow_asset
        (tenant_id, workspace_id, owner_user_id, runner_api_version, entry_contract, script_sha256)
        WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    raise RuntimeError("Restore the verified 0013 backup to a separate database.")
