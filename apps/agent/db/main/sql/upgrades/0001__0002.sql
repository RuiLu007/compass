-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=993; PostgreSQL 17; from=0001; to=0002
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0001 -> 0002

CREATE TABLE model_provider (
    id UUID NOT NULL, 
    catalog_id VARCHAR(200) NOT NULL, 
    vendor VARCHAR(200) NOT NULL, 
    api_model VARCHAR(200) NOT NULL, 
    sdk_model VARCHAR(200), 
    alias VARCHAR(80) NOT NULL, 
    kind VARCHAR(20) NOT NULL, 
    adapter VARCHAR(30) NOT NULL, 
    base_url VARCHAR(2048) NOT NULL, 
    api_key_ciphertext TEXT NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    chat_visible BOOLEAN NOT NULL, 
    version INTEGER NOT NULL, 
    binding_version INTEGER NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_by UUID NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_model_provider_chat_kind CHECK (kind = 'llm' OR chat_visible = false), 
    CONSTRAINT ck_model_provider_adapter CHECK (adapter IN ('litellm_chat','aliyun_decision')), 
    CONSTRAINT ck_model_provider_version CHECK (version > 0 AND binding_version > 0 AND binding_version <= version), 
    CONSTRAINT ck_model_provider_kind CHECK (kind IN ('llm','decision'))
);

CREATE UNIQUE INDEX uq_model_provider_alias ON model_provider (lower(trim(alias)));

CREATE TABLE model_change (
    operation_id UUID NOT NULL, 
    provider_id UUID NOT NULL, 
    actor_user_id UUID NOT NULL, 
    request_mac VARCHAR(64) NOT NULL, 
    before_version INTEGER, 
    after_version INTEGER NOT NULL, 
    changed_fields JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (operation_id), 
    FOREIGN KEY(provider_id) REFERENCES model_provider (id)
);

CREATE INDEX ix_model_change_provider_time ON model_change (provider_id, created_at);

CREATE TABLE model_call (
    call_id UUID NOT NULL, 
    provider_id UUID NOT NULL, 
    config_version INTEGER NOT NULL, 
    binding_version INTEGER NOT NULL, 
    requested_model VARCHAR(200) NOT NULL, 
    response_model VARCHAR(200), 
    purpose VARCHAR(20) NOT NULL, 
    actor_user_id UUID NOT NULL, 
    tenant_id UUID, 
    workspace_id UUID, 
    capability_summary JSON NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    deadline_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    duration_ms INTEGER, 
    usage JSON, 
    error_code VARCHAR(80), 
    upstream_request_id VARCHAR(200), 
    upstream_outcome_unknown BOOLEAN NOT NULL, 
    PRIMARY KEY (call_id), 
    CONSTRAINT ck_model_call_scope CHECK ((purpose = 'test' AND tenant_id IS NULL AND workspace_id IS NULL) OR (purpose = 'chat' AND tenant_id IS NOT NULL AND workspace_id IS NOT NULL)), 
    FOREIGN KEY(provider_id) REFERENCES model_provider (id), 
    CONSTRAINT ck_model_call_purpose CHECK (purpose IN ('test','chat')), 
    CONSTRAINT ck_model_call_status CHECK (status IN ('running','completed','failed','canceled','incomplete'))
);

CREATE INDEX ix_model_call_actor_time ON model_call (tenant_id, actor_user_id, started_at);

CREATE INDEX ix_model_call_provider_time ON model_call (provider_id, purpose, started_at);

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON demo_daily_sales TO compass_agent_runtime;
GRANT SELECT ON alembic_version TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON model_provider, model_call TO compass_agent_runtime;
GRANT SELECT, INSERT ON model_change TO compass_agent_runtime;
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202"}';
COMMIT;
