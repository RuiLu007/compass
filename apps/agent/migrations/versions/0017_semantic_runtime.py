"""Per-run semantic manifest and owner-bound result references (974, agent/main)."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE semantic_run_binding (
          run_id uuid PRIMARY KEY REFERENCES agent_run(id), tenant_id uuid NOT NULL,
          workspace_id uuid NOT NULL, catalog_kind varchar(20) NOT NULL,
          manifest_hash varchar(64) NOT NULL, partition_releases jsonb NOT NULL,
          bound_at timestamptz NOT NULL)
    """)
    op.execute("""
        CREATE TABLE semantic_metric_offer (
          offer_id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES agent_session(id),
          run_id uuid NOT NULL REFERENCES agent_run(id), owner_id uuid NOT NULL,
          kind varchar(24) NOT NULL, status varchar(24) NOT NULL, private_payload bytea NOT NULL,
          expires_at timestamptz NOT NULL)
    """)
    op.execute(
        "CREATE INDEX ix_semantic_metric_offer_session_id ON semantic_metric_offer(session_id)"
    )
    # Consumers are drained during this upgrade. The next replica registers the new
    # execution policy so that old and new Registry configurations cannot share a queue.
    op.execute("UPDATE agent_runtime_control SET policy_sha256 = NULL WHERE key = 'execution'")


def downgrade() -> None:
    raise RuntimeError("Restore the verified agent/main backup to recover runtime state.")
