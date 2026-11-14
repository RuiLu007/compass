-- Generated from Agent Alembic migrations; do not edit.
-- owner=agent/main; feature=977; PostgreSQL 17; from=0013; to=0014
-- Apply through db:apply-sql after provision and ownership/version/checksum checks.
-- Single transaction; additive schema; restore verified backup to a new owned database.
BEGIN;
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
