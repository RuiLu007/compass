"""Agent-owned queue and tenant fair scheduling (984), agent/main 0009 -> 0010.

Preserve the original database and Redis volume; restore a coordinated pre-upgrade
backup to an isolated database for a 0009 rollback.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE agent_tenant_schedule (
    tenant_id UUID PRIMARY KEY,
    last_claimed_at TIMESTAMPTZ
);
CREATE TABLE agent_run_job (
    run_id UUID PRIMARY KEY REFERENCES agent_run(id),
    tenant_id UUID NOT NULL REFERENCES agent_tenant_schedule(tenant_id),
    status VARCHAR(16) NOT NULL,
    ready_at TIMESTAMPTZ,
    first_claimed_at TIMESTAMPTZ,
    worker_id VARCHAR(64),
    worker_boot_id UUID,
    lease_expires_at TIMESTAMPTZ,
    claim_count INTEGER NOT NULL,
    CONSTRAINT ck_agent_job_status
      CHECK (status IN ('ready','leased','reclaiming','parked','done')),
    CONSTRAINT ck_agent_job_claim_count CHECK (claim_count >= 0),
    CONSTRAINT ck_agent_job_ready CHECK (status != 'ready' OR ready_at IS NOT NULL),
    CONSTRAINT ck_agent_job_lease CHECK (
        (status IN ('leased','reclaiming') AND worker_id IS NOT NULL
         AND worker_boot_id IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status NOT IN ('leased','reclaiming') AND worker_id IS NULL
            AND worker_boot_id IS NULL AND lease_expires_at IS NULL)
    )
);
CREATE INDEX ix_agent_job_ready ON agent_run_job(tenant_id, ready_at, run_id)
    WHERE status = 'ready';
CREATE INDEX ix_agent_job_expiry ON agent_run_job(lease_expires_at) WHERE status = 'leased';
CREATE UNIQUE INDEX uq_agent_job_worker ON agent_run_job(worker_id)
    WHERE status::text = ANY (ARRAY['leased','reclaiming']::text[]);
""")


def downgrade() -> None:
    raise RuntimeError("Restore the verified 0009 backup to a separate database and Redis volume.")
