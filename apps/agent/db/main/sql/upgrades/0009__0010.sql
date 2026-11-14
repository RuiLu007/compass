-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=984; PostgreSQL 17; from=0009; to=0010
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0009 -> 0010

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
    WHERE status::text = ANY (ARRAY['leased','reclaiming']::text[]);;

UPDATE alembic_version SET version_num='0010' WHERE alembic_version.version_num = '0009';
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON demo_daily_sales TO compass_agent_runtime;
GRANT SELECT ON alembic_version TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON model_provider, model_call TO compass_agent_runtime;
GRANT SELECT, INSERT ON model_change TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON data_connection TO compass_agent_runtime;
GRANT SELECT, INSERT ON workflow_asset TO compass_agent_runtime;
GRANT UPDATE (name, status, metadata_revision, updated_at) ON workflow_asset TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON mcp_connection TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON sandbox_allocation TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON agent_action, agent_artifact, agent_attempt, agent_checkpoint, agent_command, agent_demo_effect, agent_message, agent_outbox, agent_run, agent_session, agent_step, agent_tool_binding, agent_wait, agent_workflow_draft, model_attempt TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON agent_run_job, agent_tenant_schedule TO compass_agent_runtime;
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08", "0004": "0de305dc970161bc065373d077f441277e2d2094e21e819e3f76692a74e646cb", "0005": "468a7d8025c6f10ba13c8000ced21061df5c7801f42a1d56a839471efb335f1b", "0006": "766151447cb24d33194e792156d8844eb6a20916201547089fe19c6f14f10fad", "0007": "630ab76dbf04b8b55d21b35c7befea8d4a5b7b1d191aaa721851804d356364cd", "0008": "05accae62e4747b875a9ac58757c74d068b6440ec3235c0b56059bbadf8d8b9a", "0009": "8d13979b8334ec228e9866444ef5be407f918e639b449e05765b1e1824a73528", "0010": "1ff54134ba93bdcdd35ef723c11a4bb788dc8938f1a7820681217597bf757e76"}';
COMMIT;
