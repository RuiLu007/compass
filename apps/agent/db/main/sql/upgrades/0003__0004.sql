-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=991; PostgreSQL 17; from=0003; to=0004
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
-- Running upgrade 0003 -> 0004

ALTER TABLE model_call ADD COLUMN execution JSON;

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON demo_daily_sales TO compass_agent_runtime;
GRANT SELECT ON alembic_version TO compass_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON model_provider, model_call TO compass_agent_runtime;
GRANT SELECT, INSERT ON model_change TO compass_agent_runtime;
COMMENT ON TABLE alembic_version IS 'compass:agent/main;sources={"0001": "9513f1fcaa4a4f65d43e3942745c4e794dab30b10425d31be26c427dc379d1ac", "0002": "98c56506d80e00c70a9ff542ea252d0e51fbf6aae310e61b9e21ae0dd2b72202", "0003": "fca5d24e5ec8a8d83e3e2195a42c0842e2768111b46e3046897e9e9bde199c08", "0004": "0de305dc970161bc065373d077f441277e2d2094e21e819e3f76692a74e646cb"}';
COMMIT;
