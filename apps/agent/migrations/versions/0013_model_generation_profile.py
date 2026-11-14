"""Store Provider limits and default generation parameters (976)."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_provider ADD COLUMN generation_profile JSONB")


def downgrade() -> None:
    raise RuntimeError("Restore the verified 0012 backup to a separate database.")
