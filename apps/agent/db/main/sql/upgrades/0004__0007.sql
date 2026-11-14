-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=987; PostgreSQL 17; from=0004; to=0007
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0004 -> 0005

CREATE TABLE retail_connection (
    workspace_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    host VARCHAR(253) NOT NULL,
    port INTEGER NOT NULL,
    database_name VARCHAR(63) NOT NULL,
    role VARCHAR(63) NOT NULL,
    sslmode VARCHAR(11) NOT NULL,
    retail_tenant_id BIGINT NOT NULL,
    password_ciphertext TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (workspace_id),
    CONSTRAINT ck_retail_connection_port CHECK (port BETWEEN 1 AND 65535),
    CONSTRAINT ck_retail_connection_tenant CHECK (retail_tenant_id > 0),
    CONSTRAINT ck_retail_connection_revision CHECK (revision > 0)
);

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';

-- Running upgrade 0005 -> 0006

CREATE TABLE data_connection (
    id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    name VARCHAR(80) NOT NULL,
    engine VARCHAR(12) NOT NULL,
    host VARCHAR(253) NOT NULL,
    port INTEGER NOT NULL,
    database_name VARCHAR(64) NOT NULL,
    role VARCHAR(128) NOT NULL,
    sslmode VARCHAR(11) NOT NULL,
    retail_tenant_id BIGINT,
    password_ciphertext TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_data_connection_port CHECK (port BETWEEN 1 AND 65535),
    CONSTRAINT ck_data_connection_revision CHECK (revision > 0),
    CONSTRAINT ck_data_connection_engine CHECK (engine IN ('postgresql', 'mysql')),
    CONSTRAINT ck_data_connection_retail CHECK ((retail_tenant_id IS NULL AND id <> workspace_id) OR (retail_tenant_id IS NOT NULL AND retail_tenant_id > 0 AND engine = 'postgresql' AND id = workspace_id)),
    CONSTRAINT uq_data_connection_workspace_name UNIQUE (workspace_id, name)
);

CREATE INDEX ix_data_connection_scope ON data_connection (tenant_id, workspace_id);

INSERT INTO data_connection
            (id, workspace_id, tenant_id, name, engine, host, port, database_name, role,
             sslmode, retail_tenant_id, password_ciphertext, revision)
        SELECT workspace_id, workspace_id, tenant_id, '本地 Retail', 'postgresql',
               host, port, database_name, role, sslmode, retail_tenant_id, password_ciphertext,
               revision
        FROM retail_connection;

DROP TABLE retail_connection;

UPDATE alembic_version SET version_num='0006' WHERE alembic_version.version_num = '0005';

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
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON demo_daily_sales TO compass_agent_runtime;
GRANT SELECT ON alembic_version TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON model_provider, model_call TO compass_agent_runtime;
GRANT SELECT, INSERT ON model_change TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON data_connection TO compass_agent_runtime;
GRANT SELECT, INSERT ON workflow_asset TO compass_agent_runtime;
GRANT UPDATE (name, status, metadata_revision, updated_at) ON workflow_asset TO compass_agent_runtime;
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08", "0004": "0de305dc970161bc065373d077f441277e2d2094e21e819e3f76692a74e646cb", "0005": "468a7d8025c6f10ba13c8000ced21061df5c7801f42a1d56a839471efb335f1b", "0006": "766151447cb24d33194e792156d8844eb6a20916201547089fe19c6f14f10fad", "0007": "630ab76dbf04b8b55d21b35c7befea8d4a5b7b1d191aaa721851804d356364cd"}';
COMMIT;
