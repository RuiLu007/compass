-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=977; PostgreSQL 17; from=0008; to=0014
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0008 -> 0009

CREATE TABLE agent_session (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    owner_user_id UUID NOT NULL,
    title TEXT NOT NULL,
    session_type VARCHAR(16) NOT NULL,
    workflow_key TEXT,
    version BIGINT NOT NULL,
    context_start_ordinal INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_agent_session_version CHECK (version > 0 AND context_start_ordinal > 0),
    CONSTRAINT ck_agent_session_type CHECK (session_type IN ('chat','workflow'))
);

CREATE INDEX ix_agent_session_owner_activity ON agent_session (tenant_id, workspace_id, owner_user_id, updated_at, id);

CREATE TABLE agent_run (
    id UUID NOT NULL,
    session_id UUID NOT NULL,
    request_id UUID NOT NULL,
    ordinal INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL,
    version BIGINT NOT NULL,
    projected_seq VARCHAR(48) NOT NULL,
    checkpoint_no BIGINT,
    execution_epoch UUID,
    owner_boot_id UUID,
    control_intent VARCHAR(16),
    recovery_status VARCHAR(16) NOT NULL,
    cleanup_status VARCHAR(16) NOT NULL,
    reason_code VARCHAR(80),
    config_snapshot JSONB NOT NULL,
    public_view JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    active_ms BIGINT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_run_session_id UNIQUE (session_id, id),
    CONSTRAINT uq_agent_run_ordinal UNIQUE (session_id, ordinal),
    CONSTRAINT uq_agent_run_request UNIQUE (session_id, request_id),
    CONSTRAINT ck_agent_run_status CHECK (status IN ('created','running','waiting','paused','cancelling','completed','failed','canceled')),
    CONSTRAINT ck_agent_run_finished CHECK ((status IN ('completed','failed','canceled')) = (finished_at IS NOT NULL)),
    CONSTRAINT ck_agent_run_counts CHECK (version > 0 AND ordinal > 0 AND active_ms >= 0),
    CONSTRAINT ck_agent_run_cleanup CHECK (cleanup_status IN ('none','pending','confirmed','unconfirmed')),
    CONSTRAINT ck_agent_run_recovery CHECK (recovery_status IN ('ready','required','blocked')),
    CONSTRAINT ck_agent_run_intent CHECK (control_intent IS NULL OR control_intent = 'pause'),
    FOREIGN KEY(session_id) REFERENCES agent_session (id)
);

CREATE UNIQUE INDEX uq_agent_run_active ON agent_run (session_id) WHERE status::text = ANY (ARRAY['created','running','waiting','paused','cancelling']::text[]);

CREATE TABLE agent_command (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    actor_user_id UUID NOT NULL,
    request_id UUID NOT NULL,
    session_id UUID NOT NULL,
    run_id UUID,
    kind VARCHAR(16) NOT NULL,
    request_sha256 VARCHAR(64) NOT NULL,
    response JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_command_request UNIQUE (tenant_id, workspace_id, actor_user_id, request_id),
    CONSTRAINT fk_agent_command_run FOREIGN KEY(session_id, run_id) REFERENCES agent_run (session_id, id),
    FOREIGN KEY(session_id) REFERENCES agent_session (id)
);

CREATE TABLE agent_outbox (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    run_version BIGINT NOT NULL,
    request_id UUID NOT NULL,
    operation_id UUID NOT NULL,
    operation_sha256 VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    last_stream_id VARCHAR(48),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    delivered_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_outbox_run_id UNIQUE (run_id, id),
    CONSTRAINT uq_agent_outbox_operation UNIQUE (run_id, operation_id),
    CONSTRAINT uq_agent_outbox_version UNIQUE (run_id, run_version),
    CONSTRAINT ck_agent_outbox_delivered CHECK ((last_stream_id IS NULL) = (delivered_at IS NULL)),
    FOREIGN KEY(run_id) REFERENCES agent_run (id)
);

CREATE INDEX ix_agent_outbox_pending ON agent_outbox (run_id, run_version) WHERE delivered_at IS NULL;

CREATE TABLE agent_tool_binding (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    catalog_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    original_name TEXT NOT NULL,
    source VARCHAR(16) NOT NULL,
    config_ref TEXT,
    config_revision INTEGER,
    declaration JSONB NOT NULL,
    schema_sha256 VARCHAR(64) NOT NULL,
    binding JSONB NOT NULL,
    policy JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_tool_binding_run_id UNIQUE (run_id, id),
    CONSTRAINT uq_agent_tool_binding_alias UNIQUE (run_id, alias),
    FOREIGN KEY(run_id) REFERENCES agent_run (id)
);

CREATE TABLE agent_checkpoint (
    run_id UUID NOT NULL,
    number BIGINT NOT NULL,
    schema_version INTEGER NOT NULL,
    boundary_outbox_id UUID NOT NULL,
    position JSONB NOT NULL,
    completed_refs JSONB NOT NULL,
    message_refs JSONB NOT NULL,
    tool_binding_refs JSONB NOT NULL,
    artifact_refs JSONB NOT NULL,
    plan_state JSONB NOT NULL,
    verifier_state BYTEA NOT NULL,
    budget_state JSONB NOT NULL,
    pending_calls JSONB NOT NULL,
    continuation_state BYTEA,
    state_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (run_id, number),
    CONSTRAINT fk_agent_checkpoint_boundary FOREIGN KEY(run_id, boundary_outbox_id) REFERENCES agent_outbox (run_id, id),
    CONSTRAINT ck_agent_checkpoint_version CHECK (number > 0 AND schema_version > 0),
    FOREIGN KEY(run_id) REFERENCES agent_run (id)
);

CREATE TABLE agent_step (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    parent_step_id UUID,
    node_key TEXT NOT NULL,
    kind VARCHAR(16) NOT NULL,
    ordinal INTEGER NOT NULL,
    call_ordinal INTEGER,
    tool_binding_id UUID,
    group_id TEXT,
    status VARCHAR(16) NOT NULL,
    revision INTEGER NOT NULL,
    definition_snapshot JSONB NOT NULL,
    input_sha256 VARCHAR(64) NOT NULL,
    accepted_attempt_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_step_run_id UNIQUE (run_id, id),
    CONSTRAINT uq_agent_step_key UNIQUE (run_id, node_key),
    CONSTRAINT uq_agent_step_ordinal UNIQUE (run_id, ordinal),
    CONSTRAINT uq_agent_step_host_call UNIQUE (parent_step_id, call_ordinal),
    CONSTRAINT fk_agent_step_parent FOREIGN KEY(run_id, parent_step_id) REFERENCES agent_step (run_id, id),
    CONSTRAINT fk_agent_step_tool FOREIGN KEY(run_id, tool_binding_id) REFERENCES agent_tool_binding (run_id, id),
    CONSTRAINT ck_agent_step_counts CHECK (ordinal > 0 AND revision > 0 AND (call_ordinal IS NULL OR call_ordinal > 0)),
    CONSTRAINT ck_agent_step_status CHECK (status IN ('pending','running','waiting','completed','failed','canceled')),
    CONSTRAINT ck_agent_step_result CHECK (status <> 'completed' OR kind IN ('group','control') OR accepted_attempt_id IS NOT NULL),
    CONSTRAINT ck_agent_step_parent CHECK (parent_step_id IS NULL OR parent_step_id <> id),
    FOREIGN KEY(run_id) REFERENCES agent_run (id)
);

CREATE TABLE agent_attempt (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    step_id UUID NOT NULL,
    number INTEGER NOT NULL,
    execution_epoch UUID NOT NULL,
    status VARCHAR(16) NOT NULL,
    input_content BYTEA NOT NULL,
    output_content BYTEA,
    output_sha256 VARCHAR(64),
    receipt JSONB NOT NULL,
    budget_receipt JSONB NOT NULL,
    delivery_no INTEGER,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    dispatched_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    error_code VARCHAR(80),
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_attempt_run_id UNIQUE (run_id, id),
    CONSTRAINT uq_agent_attempt_step_id UNIQUE (step_id, id),
    CONSTRAINT uq_agent_attempt_number UNIQUE (step_id, number),
    CONSTRAINT fk_agent_attempt_step FOREIGN KEY(run_id, step_id) REFERENCES agent_step (run_id, id),
    CONSTRAINT ck_agent_attempt_number CHECK (number > 0),
    CONSTRAINT ck_agent_attempt_status CHECK (status IN ('prepared','dispatched','completed','failed','incomplete','canceled','unknown')),
    FOREIGN KEY(run_id) REFERENCES agent_run (id)
);

CREATE UNIQUE INDEX uq_agent_attempt_active ON agent_attempt (step_id) WHERE status::text = ANY (ARRAY['prepared','dispatched']::text[]);

CREATE TABLE agent_wait (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    step_id UUID NOT NULL,
    checkpoint_no BIGINT NOT NULL,
    kind VARCHAR(24) NOT NULL,
    proposal_id UUID NOT NULL,
    proposal_revision INTEGER NOT NULL,
    proposal_sha256 VARCHAR(64) NOT NULL,
    public_proposal JSONB NOT NULL,
    private_proposal BYTEA NOT NULL,
    expectation JSONB NOT NULL,
    status VARCHAR(16) NOT NULL,
    decision_id UUID,
    decider_user_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    decided_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_wait_run_id UNIQUE (run_id, id),
    CONSTRAINT fk_agent_wait_step FOREIGN KEY(run_id, step_id) REFERENCES agent_step (run_id, id),
    CONSTRAINT fk_agent_wait_checkpoint FOREIGN KEY(run_id, checkpoint_no) REFERENCES agent_checkpoint (run_id, number) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_agent_wait_kind CHECK (kind IN ('approval','external_event','reconciliation')),
    CONSTRAINT ck_agent_wait_status CHECK (status IN ('pending','approved','rejected','expired','canceled')),
    CONSTRAINT ck_agent_wait_revision CHECK (proposal_revision > 0),
    CONSTRAINT ck_agent_wait_expiry CHECK (kind = 'reconciliation' OR expires_at > created_at),
    CONSTRAINT ck_agent_wait_decision CHECK (status NOT IN ('approved','rejected') OR (decision_id IS NOT NULL AND decider_user_id IS NOT NULL AND decided_at IS NOT NULL)),
    FOREIGN KEY(run_id) REFERENCES agent_run (id),
    UNIQUE (decision_id)
);

CREATE INDEX ix_agent_wait_expiry ON agent_wait (status, expires_at);

CREATE UNIQUE INDEX uq_agent_wait_pending ON agent_wait (run_id) WHERE status = 'pending';

CREATE TABLE agent_workflow_draft (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    step_id UUID NOT NULL,
    suggested_name TEXT NOT NULL,
    script_source TEXT NOT NULL,
    script_sha256 VARCHAR(64) NOT NULL,
    definition_version INTEGER NOT NULL,
    runner_api_version INTEGER NOT NULL,
    validation_summary JSONB NOT NULL,
    status VARCHAR(16) NOT NULL,
    asset_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    saved_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_workflow_draft_step UNIQUE (run_id, step_id),
    CONSTRAINT fk_agent_workflow_draft_step FOREIGN KEY(run_id, step_id) REFERENCES agent_step (run_id, id),
    CONSTRAINT ck_agent_workflow_draft_status CHECK (status IN ('offered','saved','expired')),
    CONSTRAINT ck_agent_workflow_draft_saved CHECK ((status = 'saved') = (asset_id IS NOT NULL AND saved_at IS NOT NULL)),
    FOREIGN KEY(run_id) REFERENCES agent_run (id),
    FOREIGN KEY(asset_id) REFERENCES workflow_asset (asset_id)
);

CREATE TABLE agent_action (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    wait_id UUID NOT NULL,
    step_id UUID NOT NULL,
    proposal_sha256 VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL,
    receipt JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_agent_action_wait FOREIGN KEY(run_id, wait_id) REFERENCES agent_wait (run_id, id),
    CONSTRAINT fk_agent_action_step FOREIGN KEY(run_id, step_id) REFERENCES agent_step (run_id, id),
    CONSTRAINT ck_agent_action_state CHECK (state IN ('pending','applied','unknown','denied')),
    FOREIGN KEY(run_id) REFERENCES agent_run (id),
    UNIQUE (wait_id)
);

CREATE TABLE agent_artifact (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    tool_call_id TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type VARCHAR(48) NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    scope JSONB NOT NULL,
    content BYTEA NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_artifact_run_id UNIQUE (run_id, id),
    CONSTRAINT fk_agent_artifact_attempt FOREIGN KEY(run_id, attempt_id) REFERENCES agent_attempt (run_id, id),
    CONSTRAINT ck_agent_artifact_bytes CHECK (size_bytes = octet_length(content)),
    CONSTRAINT ck_agent_artifact_size CHECK (size_bytes > 0 AND size_bytes <= 2097152),
    CONSTRAINT ck_agent_artifact_type CHECK (media_type IN ('image/png','text/csv')),
    FOREIGN KEY(run_id) REFERENCES agent_run (id)
);

CREATE INDEX ix_agent_artifact_run ON agent_artifact (run_id, created_at);

CREATE TABLE agent_message (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    step_id UUID,
    attempt_id UUID,
    ordinal INTEGER NOT NULL,
    channel VARCHAR(16) NOT NULL,
    role VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    revision INTEGER NOT NULL,
    public_content JSONB,
    private_content BYTEA,
    tool_call_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_message_run_id UNIQUE (run_id, id),
    CONSTRAINT uq_agent_message_ordinal UNIQUE (run_id, ordinal),
    CONSTRAINT ck_agent_message_counts CHECK (revision > 0 AND ordinal > 0),
    CONSTRAINT ck_agent_message_status CHECK (status IN ('streaming','completed','incomplete')),
    CONSTRAINT ck_agent_message_channel CHECK ((channel = 'conversation' AND role IN ('user','assistant') AND public_content IS NOT NULL AND private_content IS NULL) OR (channel = 'context' AND role IN ('system','user','assistant','tool') AND public_content IS NULL AND private_content IS NOT NULL)),
    CONSTRAINT fk_agent_message_step FOREIGN KEY(run_id, step_id) REFERENCES agent_step (run_id, id),
    CONSTRAINT fk_agent_message_attempt FOREIGN KEY(run_id, attempt_id) REFERENCES agent_attempt (run_id, id),
    FOREIGN KEY(run_id) REFERENCES agent_run (id)
);

CREATE UNIQUE INDEX uq_agent_message_conversation ON agent_message (run_id, role) WHERE channel = 'conversation';

CREATE TABLE agent_demo_effect (
    action_id UUID NOT NULL,
    campaign_key TEXT NOT NULL,
    delta_cent BIGINT NOT NULL,
    proposal_sha256 VARCHAR(64) NOT NULL,
    committed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (action_id),
    FOREIGN KEY(action_id) REFERENCES agent_action (id)
);

CREATE TABLE model_attempt (
    id UUID NOT NULL,
    call_id UUID NOT NULL,
    number INTEGER NOT NULL,
    provider_id UUID NOT NULL,
    config_version INTEGER NOT NULL,
    binding_version INTEGER NOT NULL,
    requested_model TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    origin VARCHAR(16) NOT NULL,
    request_content BYTEA,
    output_content BYTEA,
    output_sha256 VARCHAR(64),
    sent BOOLEAN NOT NULL,
    outcome_unknown BOOLEAN NOT NULL,
    cleanup_confirmed BOOLEAN NOT NULL,
    usage JSONB NOT NULL,
    reserved_tokens BIGINT NOT NULL,
    reserved_cost_microusd BIGINT NOT NULL,
    actual_cost_microusd BIGINT,
    upstream_request_id TEXT,
    error_code VARCHAR(80),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    dispatched_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    duration_ms BIGINT,
    PRIMARY KEY (id),
    CONSTRAINT uq_model_attempt_number UNIQUE (call_id, number),
    CONSTRAINT ck_model_attempt_counts CHECK (number > 0 AND reserved_tokens >= 0 AND reserved_cost_microusd >= 0),
    CONSTRAINT ck_model_attempt_status CHECK (status IN ('prepared','dispatched','completed','failed','canceled','incomplete','unknown')),
    FOREIGN KEY(call_id) REFERENCES model_call (call_id),
    FOREIGN KEY(provider_id) REFERENCES model_provider (id)
);

CREATE INDEX ix_model_attempt_call ON model_attempt (call_id, created_at);

ALTER TABLE agent_run ADD CONSTRAINT fk_agent_run_checkpoint FOREIGN KEY(id, checkpoint_no) REFERENCES agent_checkpoint (run_id, number) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE agent_step ADD CONSTRAINT fk_agent_step_accepted FOREIGN KEY(id, accepted_attempt_id) REFERENCES agent_attempt (step_id, id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE model_call ADD COLUMN run_id UUID;

ALTER TABLE model_call ADD COLUMN agent_attempt_id UUID;

ALTER TABLE model_call ADD COLUMN input_message_refs JSON;

ALTER TABLE model_call ADD COLUMN request_content BYTEA;

ALTER TABLE model_call ADD COLUMN output_content BYTEA;

ALTER TABLE model_call ADD COLUMN content_status VARCHAR(20) DEFAULT 'not_captured' NOT NULL;

ALTER TABLE model_call ADD CONSTRAINT fk_model_call_agent_attempt FOREIGN KEY(run_id, agent_attempt_id) REFERENCES agent_attempt (run_id, id);

ALTER TABLE model_call ADD CONSTRAINT ck_model_call_run CHECK ((run_id IS NULL) = (agent_attempt_id IS NULL));

CREATE INDEX ix_model_call_run ON model_call (run_id, started_at);

INSERT INTO model_attempt (
    id, call_id, number, provider_id, config_version, binding_version, requested_model,
    status, origin, sent, outcome_unknown, cleanup_confirmed, usage,
    reserved_tokens, reserved_cost_microusd, upstream_request_id, error_code,
    created_at, dispatched_at, finished_at, duration_ms
)
SELECT
    CASE WHEN a.value->>'attempt_id' ~* '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$'
      THEN (a.value->>'attempt_id')::uuid
      ELSE md5(c.call_id::text || ':legacy:' || a.ordinality::text)::uuid END,
    c.call_id, a.ordinality::int, c.provider_id, c.config_version, c.binding_version,
    c.requested_model,
    CASE WHEN a.value->>'status' IN ('completed','failed','canceled','incomplete')
      THEN a.value->>'status' ELSE 'unknown' END,
    'legacy_summary', COALESCE((a.value->>'sent')::boolean, false),
    COALESCE((a.value->>'upstream_outcome_unknown')::boolean, true),
    COALESCE((a.value->>'cleanup_confirmed')::boolean, false),
    COALESCE(a.value->'usage', '{}'::jsonb), 0, 0,
    a.value->>'upstream_request_id', a.value->>'error_code', c.started_at,
    CASE WHEN pg_input_is_valid(a.value->>'started_at', 'timestamptz')
      THEN (a.value->>'started_at')::timestamptz ELSE NULL END,
    c.finished_at,
    CASE WHEN a.value->>'duration_ms' ~ '^[0-9]{1,12}$'
      THEN (a.value->>'duration_ms')::bigint ELSE NULL END
FROM model_call c CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(c.execution::jsonb->'attempts') = 'array'
      THEN c.execution::jsonb->'attempts' ELSE '[]'::jsonb END
) WITH ORDINALITY AS a(value, ordinality);

UPDATE alembic_version SET version_num='0009' WHERE alembic_version.version_num = '0008';

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

-- Running upgrade 0010 -> 0011

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
    WHERE title_status = 'pending';;

UPDATE alembic_version SET version_num='0011' WHERE alembic_version.version_num = '0010';

-- Running upgrade 0011 -> 0012

ALTER TABLE workflow_asset
    ADD COLUMN description VARCHAR(500),
    ADD COLUMN parameters JSONB,
    ADD CONSTRAINT ck_workflow_asset_tool_definition CHECK (
        (entry_contract = 'agent_workflow@1'
            AND description IS NOT NULL AND length(btrim(description)) > 0
            AND parameters IS NOT NULL AND jsonb_typeof(parameters) = 'object')
        OR (entry_contract <> 'agent_workflow@1' AND description IS NULL AND parameters IS NULL)
    );;

UPDATE alembic_version SET version_num='0012' WHERE alembic_version.version_num = '0011';

-- Running upgrade 0012 -> 0013

ALTER TABLE model_provider ADD COLUMN generation_profile JSONB;

UPDATE alembic_version SET version_num='0013' WHERE alembic_version.version_num = '0012';

-- Running upgrade 0013 -> 0014

ALTER TABLE workflow_asset ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE workflow_asset DROP CONSTRAINT uq_workflow_asset_scope_script;

CREATE UNIQUE INDEX uq_workflow_asset_scope_script ON workflow_asset
        (tenant_id, workspace_id, owner_user_id, runner_api_version, entry_contract, script_sha256)
        WHERE deleted_at IS NULL;

UPDATE alembic_version SET version_num='0014' WHERE alembic_version.version_num = '0013';
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
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08", "0004": "0de305dc970161bc065373d077f441277e2d2094e21e819e3f76692a74e646cb", "0005": "468a7d8025c6f10ba13c8000ced21061df5c7801f42a1d56a839471efb335f1b", "0006": "766151447cb24d33194e792156d8844eb6a20916201547089fe19c6f14f10fad", "0007": "630ab76dbf04b8b55d21b35c7befea8d4a5b7b1d191aaa721851804d356364cd", "0008": "05accae62e4747b875a9ac58757c74d068b6440ec3235c0b56059bbadf8d8b9a", "0009": "8d13979b8334ec228e9866444ef5be407f918e639b449e05765b1e1824a73528", "0010": "1ff54134ba93bdcdd35ef723c11a4bb788dc8938f1a7820681217597bf757e76", "0011": "86d1068cfaa7d78c7fde00b91f9d1308e904591b464f335507af8bfc1b5e7950", "0012": "5260c371a96a73006a9ef3cce698b7912aa9bf0f753e67d87298511ee1b8c302", "0013": "fb388e9deb8f6f9311596742b7d0b45e838fb3642f1433429b9aeffd4dceb7ac", "0014": "26a9d717852a4670de56ec5f8fb87a894b0952831f7b5dc5cfaa8d37046e4059"}';
COMMIT;
