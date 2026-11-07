-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=993; PostgreSQL 17; from=0002; to=0003
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0002 -> 0003

ALTER TABLE model_provider DROP CONSTRAINT ck_model_provider_adapter;

UPDATE model_provider SET adapter='systemone_decision' WHERE adapter='aliyun_decision';

ALTER TABLE model_provider ADD CONSTRAINT ck_model_provider_adapter CHECK (adapter IN ('litellm_chat','systemone_decision'));

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON demo_daily_sales TO compass_agent_runtime;
GRANT SELECT ON alembic_version TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON model_provider, model_call TO compass_agent_runtime;
GRANT SELECT, INSERT ON model_change TO compass_agent_runtime;
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08"}';
COMMIT;
