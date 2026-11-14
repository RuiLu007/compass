-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=982; PostgreSQL 17; from=0002; to=0006
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; transactional Retail copy; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0002 -> 0003

ALTER TABLE model_provider DROP CONSTRAINT ck_model_provider_adapter;

UPDATE model_provider SET adapter='systemone_decision' WHERE adapter='aliyun_decision';

ALTER TABLE model_provider ADD CONSTRAINT ck_model_provider_adapter CHECK (adapter IN ('litellm_chat','systemone_decision'));

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';

-- Running upgrade 0003 -> 0004

ALTER TABLE model_call ADD COLUMN execution JSON;

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';

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
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON demo_daily_sales TO compass_agent_runtime;
GRANT SELECT ON alembic_version TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON model_provider, model_call TO compass_agent_runtime;
GRANT SELECT, INSERT ON model_change TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON data_connection TO compass_agent_runtime;
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08", "0004": "0de305dc970161bc065373d077f441277e2d2094e21e819e3f76692a74e646cb", "0005": "468a7d8025c6f10ba13c8000ced21061df5c7801f42a1d56a839471efb335f1b", "0006": "766151447cb24d33194e792156d8844eb6a20916201547089fe19c6f14f10fad"}';
COMMIT;
