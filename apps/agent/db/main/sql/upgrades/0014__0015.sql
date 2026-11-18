-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=984; PostgreSQL 17; from=0014; to=0015
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0014 -> 0015

DO $switch$
        BEGIN
            IF NOT pg_try_advisory_xact_lock(985001) THEN
                RAISE EXCEPTION 'Stop the Agent execution owners before migrating to 0015';
            END IF;
        END;
        $switch$;

CREATE TABLE agent_runtime_control (
            key VARCHAR(24) PRIMARY KEY,
            mode VARCHAR(16),
            policy_sha256 VARCHAR(64),
            holder_id UUID,
            generation BIGINT DEFAULT 0 NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE
        );

ALTER TABLE agent_runtime_control ENABLE ROW LEVEL SECURITY;

CREATE POLICY runtime_control_read ON agent_runtime_control FOR SELECT USING (true);

CREATE POLICY runtime_control_write ON agent_runtime_control FOR UPDATE
        USING (key = 'scheduler' OR current_user <> 'compass_agent_scheduler')
        WITH CHECK (key = 'scheduler' OR current_user <> 'compass_agent_scheduler');

INSERT INTO agent_runtime_control (key, mode)
        VALUES ('execution', 'internal'), ('scheduler', NULL);

ALTER TABLE agent_command ALTER COLUMN session_id DROP NOT NULL,
        ADD CONSTRAINT ck_agent_command_schedule_scope CHECK (
            (kind = 'schedule' AND session_id IS NULL AND run_id IS NULL)
            OR (kind <> 'schedule' AND session_id IS NOT NULL)
        );

ALTER TABLE agent_run_job
        ADD COLUMN recovery_token UUID,
        ADD COLUMN recovery_expires_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN active_since TIMESTAMP WITH TIME ZONE;

ALTER TABLE agent_run
        ADD COLUMN publish_token UUID,
        ADD COLUMN publish_expires_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN publish_initialized BOOLEAN DEFAULT false NOT NULL,
        ADD COLUMN publish_generation BIGINT DEFAULT 0 NOT NULL;

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
        );

CREATE INDEX ix_agent_schedule_owner ON agent_schedule (tenant_id, workspace_id, owner_user_id, id);

CREATE INDEX ix_agent_schedule_due ON agent_schedule (next_fire_at, id) WHERE status = 'enabled';

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
        );

CREATE INDEX ix_agent_trigger_history ON agent_schedule_trigger
        (schedule_id, scheduled_at, id);

CREATE INDEX ix_agent_trigger_delivery ON agent_schedule_trigger
        (next_attempt_at, scheduled_at, id)
        WHERE delivery_status::text = ANY (ARRAY['pending','retry_wait','dispatching']::text[]);

CREATE INDEX ix_agent_trigger_reconcile ON agent_schedule_trigger
        (next_attempt_at, scheduled_at, id) WHERE delivery_status = 'reconcile_pending';

CREATE INDEX ix_agent_trigger_sync ON agent_schedule_trigger
        (observed_at, id) WHERE delivery_status = 'submitted' AND finished_at IS NULL;

CREATE VIEW agent_scheduled_run_summary WITH (security_barrier = true) AS
        SELECT run.id, run.version, run.status, run.reason_code, run.finished_at
        FROM agent_run AS run
        JOIN agent_schedule_trigger AS trigger ON trigger.run_id = run.id;

UPDATE alembic_version SET version_num='0015' WHERE alembic_version.version_num = '0014';
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON demo_daily_sales TO compass_agent_runtime;
GRANT SELECT ON alembic_version TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON model_provider, model_call TO compass_agent_runtime;
GRANT SELECT, INSERT ON model_change TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON data_connection TO compass_agent_runtime;
GRANT SELECT, INSERT ON workflow_asset TO compass_agent_runtime;
GRANT UPDATE (name, status, metadata_revision, updated_at, deleted_at) ON workflow_asset TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON mcp_connection TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON sandbox_allocation TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON agent_action, agent_artifact, agent_attempt, agent_checkpoint, agent_command, agent_demo_effect, agent_message, agent_outbox, agent_run, agent_session, agent_step, agent_tool_binding, agent_wait, agent_workflow_draft, model_attempt TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON agent_run_job, agent_tenant_schedule TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON agent_schedule, agent_schedule_trigger TO compass_agent_runtime;
GRANT SELECT, UPDATE (policy_sha256, holder_id) ON agent_runtime_control TO compass_agent_runtime;
GRANT SELECT ON alembic_version, agent_runtime_control, agent_schedule,
    agent_schedule_trigger, agent_scheduled_run_summary TO compass_agent_scheduler;
GRANT UPDATE (holder_id, generation, expires_at) ON agent_runtime_control TO compass_agent_scheduler;
GRANT UPDATE (next_fire_at) ON agent_schedule TO compass_agent_scheduler;
GRANT INSERT (id, schedule_id, schedule_revision, scheduled_at, detected_at, coalesced_from_at,
    input_ciphertext, delivery_status, reason_code, delivery_deadline_at, next_attempt_at),
    UPDATE (delivery_status, reason_code, delivery_attempts, next_attempt_at, input_ciphertext,
    delivery_token, delivery_generation, delivery_expires_at, observed_run_status,
    observed_run_version, observed_reason_code, observed_at, finished_at)
    ON agent_schedule_trigger TO compass_agent_scheduler;
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08", "0004": "0de305dc970161bc065373d077f441277e2d2094e21e819e3f76692a74e646cb", "0005": "468a7d8025c6f10ba13c8000ced21061df5c7801f42a1d56a839471efb335f1b", "0006": "766151447cb24d33194e792156d8844eb6a20916201547089fe19c6f14f10fad", "0007": "630ab76dbf04b8b55d21b35c7befea8d4a5b7b1d191aaa721851804d356364cd", "0008": "05accae62e4747b875a9ac58757c74d068b6440ec3235c0b56059bbadf8d8b9a", "0009": "8d13979b8334ec228e9866444ef5be407f918e639b449e05765b1e1824a73528", "0010": "1ff54134ba93bdcdd35ef723c11a4bb788dc8938f1a7820681217597bf757e76", "0011": "86d1068cfaa7d78c7fde00b91f9d1308e904591b464f335507af8bfc1b5e7950", "0012": "5260c371a96a73006a9ef3cce698b7912aa9bf0f753e67d87298511ee1b8c302", "0013": "fb388e9deb8f6f9311596742b7d0b45e838fb3642f1433429b9aeffd4dceb7ac", "0014": "26a9d717852a4670de56ec5f8fb87a894b0952831f7b5dc5cfaa8d37046e4059", "0015": "74ebf595a7953b7cb74b6d10017e0cbeea7b757761f79d73fdf1952e76fe0d0b"}';
COMMIT;
