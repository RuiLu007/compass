"""Scheduled tasks and replica coordination (984, agent/main)."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $switch$
        BEGIN
            IF NOT pg_try_advisory_xact_lock(985001) THEN
                RAISE EXCEPTION 'Stop the Agent execution owners before migrating to 0015';
            END IF;
        END;
        $switch$
    """)
    op.execute("""
        CREATE TABLE agent_runtime_control (
            key VARCHAR(24) PRIMARY KEY,
            mode VARCHAR(16),
            policy_sha256 VARCHAR(64),
            holder_id UUID,
            generation BIGINT DEFAULT 0 NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE
        )
    """)
    op.execute("ALTER TABLE agent_runtime_control ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY runtime_control_read ON agent_runtime_control FOR SELECT USING (true)"
    )
    op.execute("""
        CREATE POLICY runtime_control_write ON agent_runtime_control FOR UPDATE
        USING (key = 'scheduler' OR current_user <> 'compass_agent_scheduler')
        WITH CHECK (key = 'scheduler' OR current_user <> 'compass_agent_scheduler')
    """)
    op.execute("""
        INSERT INTO agent_runtime_control (key, mode)
        VALUES ('execution', 'internal'), ('scheduler', NULL)
    """)
    op.execute("""
        ALTER TABLE agent_command ALTER COLUMN session_id DROP NOT NULL,
        ADD CONSTRAINT ck_agent_command_schedule_scope CHECK (
            (kind = 'schedule' AND session_id IS NULL AND run_id IS NULL)
            OR (kind <> 'schedule' AND session_id IS NOT NULL)
        )
    """)
    op.execute("""
        ALTER TABLE agent_run_job
        ADD COLUMN recovery_token UUID,
        ADD COLUMN recovery_expires_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN active_since TIMESTAMP WITH TIME ZONE
    """)
    op.execute("""
        ALTER TABLE agent_run
        ADD COLUMN publish_token UUID,
        ADD COLUMN publish_expires_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN publish_initialized BOOLEAN DEFAULT false NOT NULL,
        ADD COLUMN publish_generation BIGINT DEFAULT 0 NOT NULL
    """)
    op.execute("""
        CREATE TABLE agent_schedule (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            workspace_id UUID NOT NULL,
            owner_user_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            cron VARCHAR(120) NOT NULL,
            timezone VARCHAR(128) NOT NULL,
            input_ciphertext BYTEA NOT NULL,
            status VARCHAR(12) NOT NULL,
            revision BIGINT NOT NULL,
            next_fire_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT ck_agent_schedule_state CHECK (
                status IN ('enabled','disabled','deleted') AND revision > 0
                AND ((status = 'enabled') = (next_fire_at IS NOT NULL))
            )
        )
    """)
    op.execute(
        "CREATE INDEX ix_agent_schedule_owner ON agent_schedule "
        "(tenant_id, workspace_id, owner_user_id, id)"
    )
    op.execute(
        "CREATE INDEX ix_agent_schedule_due ON agent_schedule (next_fire_at, id) "
        "WHERE status = 'enabled'"
    )
    op.execute("""
        CREATE TABLE agent_schedule_trigger (
            id UUID PRIMARY KEY,
            schedule_id UUID NOT NULL REFERENCES agent_schedule(id),
            schedule_revision BIGINT NOT NULL,
            scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
            detected_at TIMESTAMP WITH TIME ZONE NOT NULL,
            coalesced_from_at TIMESTAMP WITH TIME ZONE,
            input_ciphertext BYTEA,
            delivery_status VARCHAR(20) NOT NULL,
            reason_code VARCHAR(80),
            delivery_attempts INTEGER DEFAULT 0 NOT NULL,
            delivery_deadline_at TIMESTAMP WITH TIME ZONE,
            next_attempt_at TIMESTAMP WITH TIME ZONE,
            delivery_token UUID,
            delivery_generation BIGINT,
            delivery_expires_at TIMESTAMP WITH TIME ZONE,
            run_id UUID REFERENCES agent_run(id),
            accepted_at TIMESTAMP WITH TIME ZONE,
            observed_run_status VARCHAR(16),
            observed_run_version BIGINT,
            observed_reason_code VARCHAR(80),
            observed_at TIMESTAMP WITH TIME ZONE,
            finished_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT uq_agent_trigger_slot UNIQUE (schedule_id, scheduled_at),
            CONSTRAINT uq_agent_trigger_run UNIQUE (run_id),
            CONSTRAINT ck_agent_trigger_state CHECK (
                delivery_status IN ('pending','dispatching','retry_wait','reconcile_pending',
                                    'submitted','delivery_failed','skipped')
                AND delivery_attempts BETWEEN 0 AND 8
                AND ((delivery_status = 'submitted') = (run_id IS NOT NULL))
                AND ((run_id IS NOT NULL) = (accepted_at IS NOT NULL))
            )
        )
    """)
    op.execute("""
        CREATE INDEX ix_agent_trigger_history ON agent_schedule_trigger
        (schedule_id, scheduled_at, id)
    """)
    op.execute("""
        CREATE INDEX ix_agent_trigger_delivery ON agent_schedule_trigger
        (next_attempt_at, scheduled_at, id)
        WHERE delivery_status::text = ANY (ARRAY['pending','retry_wait','dispatching']::text[])
    """)
    op.execute("""
        CREATE INDEX ix_agent_trigger_reconcile ON agent_schedule_trigger
        (next_attempt_at, scheduled_at, id) WHERE delivery_status = 'reconcile_pending'
    """)
    op.execute("""
        CREATE INDEX ix_agent_trigger_sync ON agent_schedule_trigger
        (observed_at, id) WHERE delivery_status = 'submitted' AND finished_at IS NULL
    """)
    op.execute("""
        CREATE VIEW agent_scheduled_run_summary WITH (security_barrier = true) AS
        SELECT run.id, run.version, run.status, run.reason_code, run.finished_at
        FROM agent_run AS run
        JOIN agent_schedule_trigger AS trigger ON trigger.run_id = run.id
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Preserve the 0015 database and restore the verified 0014 backup separately."
    )
