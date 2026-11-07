"""Add bounded execution metadata to model calls (Feature 991)."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("model_call", sa.Column("execution", sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError(
        "Restore a verified backup to a new owned database; call history is retained."
    )
