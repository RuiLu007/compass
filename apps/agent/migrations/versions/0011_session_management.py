"""Session titles and soft deletion, agent/main 0010 -> 0011 (981)."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE agent_session
    ADD COLUMN title_status VARCHAR(12) NOT NULL DEFAULT 'fallback',
    ADD COLUMN title_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN title_next_at TIMESTAMPTZ,
    ADD COLUMN deleted_at TIMESTAMPTZ,
    ADD CONSTRAINT ck_agent_session_title_status
        CHECK (title_status IN ('fallback','pending','generated','manual')),
    ADD CONSTRAINT ck_agent_session_title_job
        CHECK (title_attempts BETWEEN 0 AND 2
            AND ((title_status = 'pending') = (title_next_at IS NOT NULL))
            AND (deleted_at IS NULL OR title_status != 'pending'));
CREATE INDEX ix_agent_session_title_ready ON agent_session(title_next_at, id)
    WHERE title_status = 'pending';
""")


def downgrade() -> None:
    raise RuntimeError("Restore the verified 0010 backup to a separate database.")
