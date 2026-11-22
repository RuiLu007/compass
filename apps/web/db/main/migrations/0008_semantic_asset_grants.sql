-- Owner web/main. Grants apply to a published asset in one workspace.
-- Up Migration
CREATE TABLE web.semantic_asset_grants (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    asset_id uuid NOT NULL REFERENCES web.semantic_assets,
    department_id uuid,
    member_id uuid,
    active boolean NOT NULL,
    CHECK (num_nonnulls(department_id, member_id) = 1),
    UNIQUE NULLS NOT DISTINCT (workspace_id, asset_id, department_id, member_id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES web.workspace(tenant_id, id),
    FOREIGN KEY (tenant_id, department_id) REFERENCES web.semantic_scopes(tenant_id, scope_id),
    FOREIGN KEY (tenant_id, member_id) REFERENCES web.tenant_member(tenant_id, id)
);
GRANT SELECT, INSERT ON web.semantic_asset_grants TO compass_web_runtime;
GRANT UPDATE(active) ON web.semantic_asset_grants TO compass_web_runtime;
-- Down Migration
DO $$ BEGIN RAISE EXCEPTION 'Restore the verified web/main backup to recover the previous version'; END $$;
