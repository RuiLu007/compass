"""Personal MCP connections and fenced sandbox allocation/volume leases."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE mcp_connection (
	id UUID NOT NULL,
	tenant_id UUID NOT NULL,
	workspace_id UUID NOT NULL,
	actor_user_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	name VARCHAR(80) NOT NULL,
	transport VARCHAR(24) NOT NULL,
	auth_type VARCHAR(12) NOT NULL,
	endpoint VARCHAR(2048) NOT NULL,
	token_ciphertext TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	required BOOLEAN NOT NULL,
	max_inflight INTEGER NOT NULL,
	tools JSON NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_mcp_revision CHECK (revision > 0),
	CONSTRAINT ck_mcp_transport CHECK (transport = 'streamable_http'),
	CONSTRAINT ck_mcp_auth_type CHECK (auth_type IN ('none', 'bearer')),
	CONSTRAINT ck_mcp_inflight CHECK (max_inflight BETWEEN 1 AND 2),
	CONSTRAINT uq_mcp_owner_name UNIQUE (tenant_id, workspace_id, actor_user_id, name)
)
    """)
    op.execute("""
CREATE INDEX ix_mcp_owner ON mcp_connection (tenant_id, workspace_id, actor_user_id)
    """)
    op.execute("""
CREATE TABLE sandbox_allocation (
	id UUID NOT NULL,
	tenant_id UUID NOT NULL,
	workspace_id UUID NOT NULL,
	actor_user_id UUID NOT NULL,
	run_id UUID NOT NULL,
	step_id UUID NOT NULL,
	tool_call_id VARCHAR(128) NOT NULL,
	attempt_id UUID NOT NULL,
	host VARCHAR(128) NOT NULL,
	slot INTEGER NOT NULL,
	request_digest VARCHAR(64) NOT NULL,
	sandbox_id VARCHAR(128),
	state VARCHAR(16) NOT NULL,
	resource_phase VARCHAR(16) NOT NULL,
	resource_seq INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	resource_deadline TIMESTAMP WITH TIME ZONE NOT NULL,
	retain_until TIMESTAMP WITH TIME ZONE,
	reconcile_until TIMESTAMP WITH TIME ZONE,
	reconcile_token UUID,
	PRIMARY KEY (id),
	CONSTRAINT uq_sandbox_attempt UNIQUE (tenant_id, workspace_id, actor_user_id, attempt_id),
	CONSTRAINT ck_sandbox_slot_seq CHECK (slot >= 0 AND resource_seq > 0),
	CONSTRAINT ck_sandbox_state CHECK (state IN (
        'creating','running','stopping','retained','unknown','released')),
	CONSTRAINT ck_sandbox_phase CHECK (resource_phase IN ('compute','collect','inspect','clean')),
	CONSTRAINT ck_sandbox_deadline CHECK (resource_deadline > created_at),
	CONSTRAINT ck_sandbox_retention CHECK (retain_until IS NULL OR retain_until >= created_at)
)
    """)
    op.execute("""
CREATE INDEX ix_sandbox_owner ON sandbox_allocation (tenant_id, workspace_id, actor_user_id, run_id)
    """)
    op.execute("""
CREATE INDEX ix_sandbox_reconcile ON sandbox_allocation (state, resource_deadline)
    """)
    op.execute("""
CREATE UNIQUE INDEX uq_sandbox_live_slot ON sandbox_allocation (host, slot)
    WHERE state <> 'released'
    """)


def downgrade() -> None:
    raise RuntimeError("Restore the verified pre-upgrade Agent backup to a separate database")
