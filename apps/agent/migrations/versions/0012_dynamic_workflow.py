"""Store immutable model tool contracts for dynamic workflows (977)."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE workflow_asset
    ADD COLUMN description VARCHAR(500),
    ADD COLUMN parameters JSONB,
    ADD CONSTRAINT ck_workflow_asset_tool_definition CHECK (
        (entry_contract = 'agent_workflow@1'
            AND description IS NOT NULL AND length(btrim(description)) > 0
            AND parameters IS NOT NULL AND jsonb_typeof(parameters) = 'object')
        OR (entry_contract <> 'agent_workflow@1' AND description IS NULL AND parameters IS NULL)
    );
""")


def downgrade() -> None:
    raise RuntimeError("Restore the verified 0011 backup to a separate database.")
