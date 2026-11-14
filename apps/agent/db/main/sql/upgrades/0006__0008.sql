-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=986; PostgreSQL 17; from=0006; to=0008
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0006 -> 0007

CREATE TABLE workflow_asset (
    asset_id uuid CONSTRAINT pk_workflow_asset PRIMARY KEY,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    name varchar(80) NOT NULL,
    status text NOT NULL DEFAULT 'active',
    definition_version integer NOT NULL DEFAULT 1,
    runner_api_version integer NOT NULL DEFAULT 1,
    entry_contract varchar(64) NOT NULL,
    script_source text NOT NULL,
    script_sha256 bytea NOT NULL,
    metadata_revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_workflow_asset_name CHECK (name = btrim(name) AND name <> ''),
    CONSTRAINT ck_workflow_asset_status CHECK (status IN ('active', 'disabled')),
    CONSTRAINT ck_workflow_asset_versions
        CHECK (definition_version = 1 AND runner_api_version = 1),
    CONSTRAINT ck_workflow_asset_contract
        CHECK (entry_contract = btrim(entry_contract) AND entry_contract <> ''),
    CONSTRAINT ck_workflow_asset_source
        CHECK (octet_length(script_source) BETWEEN 1 AND 16384),
    CONSTRAINT ck_workflow_asset_sha256 CHECK (octet_length(script_sha256) = 32),
    CONSTRAINT ck_workflow_asset_metadata_revision CHECK (metadata_revision >= 1),
    CONSTRAINT uq_workflow_asset_scope_script UNIQUE (
        tenant_id, workspace_id, owner_user_id, runner_api_version, entry_contract, script_sha256
    )
);

CREATE INDEX ix_workflow_asset_scope_updated
    ON workflow_asset (
        tenant_id, workspace_id, owner_user_id, updated_at DESC, asset_id DESC
    );

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';

-- Running upgrade 0007 -> 0008

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
);

CREATE INDEX ix_mcp_owner ON mcp_connection (tenant_id, workspace_id, actor_user_id);

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
);

CREATE INDEX ix_sandbox_owner ON sandbox_allocation (tenant_id, workspace_id, actor_user_id, run_id);

CREATE INDEX ix_sandbox_reconcile ON sandbox_allocation (state, resource_deadline);

CREATE UNIQUE INDEX uq_sandbox_live_slot ON sandbox_allocation (host, slot)
    WHERE state <> 'released';

UPDATE alembic_version SET version_num='0008' WHERE alembic_version.version_num = '0007';
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
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08", "0004": "0de305dc970161bc065373d077f441277e2d2094e21e819e3f76692a74e646cb", "0005": "468a7d8025c6f10ba13c8000ced21061df5c7801f42a1d56a839471efb335f1b", "0006": "766151447cb24d33194e792156d8844eb6a20916201547089fe19c6f14f10fad", "0007": "630ab76dbf04b8b55d21b35c7befea8d4a5b7b1d191aaa721851804d356364cd", "0008": "05accae62e4747b875a9ac58757c74d068b6440ec3235c0b56059bbadf8d8b9a"}';
COMMIT;
