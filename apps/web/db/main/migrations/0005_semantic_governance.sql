-- Owner web/main. Existing content stays unpublished until reviewed in its own scope.
-- Up Migration
ALTER TABLE web.semantic_scopes ADD COLUMN workspace_id uuid,
    ADD CONSTRAINT semantic_scope_workspace FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES web.workspace(tenant_id,id),
    ADD UNIQUE (tenant_id, scope_id);
ALTER TABLE web.semantic_scopes DROP CONSTRAINT semantic_scopes_check;
ALTER TABLE web.semantic_scopes ADD CONSTRAINT semantic_scope_shape CHECK (
    (kind='platform' AND tenant_id IS NULL AND department_key IS NULL AND owner_user_id IS NULL)
    OR (kind='tenant' AND tenant_id IS NOT NULL AND department_key IS NULL AND owner_user_id IS NULL AND workspace_id IS NULL)
    OR (kind IN ('department','job') AND tenant_id IS NOT NULL AND department_key IS NOT NULL AND owner_user_id IS NULL)
    OR (kind='workspace' AND tenant_id IS NOT NULL AND workspace_id IS NOT NULL AND department_key=workspace_id::text AND department_key IS NOT NULL AND owner_user_id IS NULL)
    OR (kind='personal' AND tenant_id IS NOT NULL AND department_key IS NULL AND owner_user_id IS NOT NULL AND workspace_id IS NULL)
);
CREATE TABLE web.semantic_scope_memberships (
    tenant_id uuid NOT NULL, scope_id uuid NOT NULL, tenant_member_id uuid NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
    PRIMARY KEY (scope_id,tenant_member_id),
    FOREIGN KEY (tenant_id,scope_id) REFERENCES web.semantic_scopes(tenant_id,scope_id),
    FOREIGN KEY (tenant_id,tenant_member_id) REFERENCES web.tenant_member(tenant_id,id)
);
CREATE TABLE web.semantic_scope_grants (
    tenant_id uuid NOT NULL, scope_id uuid NOT NULL, grantee_member_id uuid NOT NULL,
    capability text NOT NULL CHECK (capability IN ('manage','share','business','data','release')),
    granted_by uuid NOT NULL REFERENCES web.app_user, revoked_at timestamptz,
    PRIMARY KEY (scope_id,grantee_member_id,capability),
    FOREIGN KEY (tenant_id,scope_id) REFERENCES web.semantic_scopes(tenant_id,scope_id),
    FOREIGN KEY (tenant_id,grantee_member_id) REFERENCES web.tenant_member(tenant_id,id)
);
ALTER TABLE web.semantic_source ADD COLUMN owner_scope_id uuid REFERENCES web.semantic_scopes,
    ADD COLUMN source_kind text NOT NULL DEFAULT 'package',
    ADD COLUMN material jsonb NOT NULL DEFAULT '{}';
ALTER TABLE web.semantic_assets ADD COLUMN open_draft_id uuid;
ALTER TABLE web.semantic_versions ALTER COLUMN source_id DROP NOT NULL;
CREATE INDEX semantic_alias_lookup ON web.semantic_aliases(locale,alias_norm);
CREATE TABLE web.semantic_reviews (
    review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scope_id uuid NOT NULL REFERENCES web.semantic_scopes,
    version_id uuid NOT NULL REFERENCES web.semantic_versions, digest text NOT NULL,
    step text NOT NULL CHECK (step IN ('submit','business','data','return')),
    actor_id uuid NOT NULL REFERENCES web.app_user, note text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX semantic_reviews_target ON web.semantic_reviews(scope_id,version_id,created_at);
CREATE TABLE web.semantic_release (
    release_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), partition_id uuid NOT NULL REFERENCES web.semantic_scopes,
    audience_id uuid REFERENCES web.semantic_scopes, generation integer NOT NULL CHECK (generation > 0), manifest jsonb NOT NULL,
    digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(partition_id,generation), UNIQUE(partition_id,release_id)
);
CREATE TABLE web.semantic_release_head (
    partition_id uuid PRIMARY KEY REFERENCES web.semantic_scopes,
    generation integer NOT NULL DEFAULT 0, stable_id uuid, gray_id uuid, audience_id uuid REFERENCES web.semantic_scopes,
    FOREIGN KEY(partition_id,stable_id) REFERENCES web.semantic_release(partition_id,release_id),
    FOREIGN KEY(partition_id,gray_id) REFERENCES web.semantic_release(partition_id,release_id),
    CHECK ((gray_id IS NULL)=(audience_id IS NULL))
);
CREATE TABLE web.semantic_event (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actor_id uuid NOT NULL REFERENCES web.app_user,
    workspace_id uuid NOT NULL REFERENCES web.workspace, request_key uuid NOT NULL, action text NOT NULL,
    request_digest text NOT NULL, receipt jsonb NOT NULL, note text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(actor_id,workspace_id,request_key)
);
CREATE TABLE web.semantic_revocation (
    release_id uuid PRIMARY KEY REFERENCES web.semantic_release, actor_id uuid NOT NULL REFERENCES web.app_user,
    reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE web.semantic_reference (
    workspace_id uuid NOT NULL REFERENCES web.workspace, version_id uuid NOT NULL REFERENCES web.semantic_versions,
    kind text NOT NULL, reference_id uuid NOT NULL, PRIMARY KEY(workspace_id,version_id,kind,reference_id)
);
CREATE TABLE web.semantic_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES web.workspace,
    version_id uuid NOT NULL REFERENCES web.semantic_versions, actor_id uuid NOT NULL REFERENCES web.app_user,
    category text NOT NULL, note text NOT NULL, revision_id uuid REFERENCES web.semantic_versions,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- A validated binding may be superseded only by creating a new immutable binding.
ALTER TABLE web.execution_bindings DROP CONSTRAINT execution_bindings_version_id_workspace_id_source_ref_key;
CREATE INDEX semantic_binding_lookup ON web.execution_bindings(version_id,workspace_id);
ALTER TABLE web.execution_bindings DROP CONSTRAINT execution_bindings_mode_check;
ALTER TABLE web.execution_bindings ADD CHECK (mode IN ('trusted_sql','aggregate','view'));
ALTER TABLE web.execution_bindings ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
GRANT SELECT, INSERT ON web.semantic_assets, web.semantic_versions, web.semantic_source, web.semantic_aliases,
    web.semantic_relations, web.semantic_policy, web.execution_bindings, web.semantic_scopes,
    web.semantic_reviews, web.semantic_release, web.semantic_event, web.semantic_reference,
    web.semantic_feedback, web.semantic_revocation TO compass_web_runtime;
GRANT UPDATE(open_draft_id,lifecycle) ON web.semantic_assets TO compass_web_runtime;
GRANT UPDATE(revision_id) ON web.semantic_feedback TO compass_web_runtime;
GRANT SELECT,INSERT,UPDATE ON web.semantic_release_head,web.semantic_scope_grants,web.semantic_scope_memberships TO compass_web_runtime;
ALTER TABLE web.tenant_member DROP CONSTRAINT tenant_member_permission_keys_shape,
    DROP CONSTRAINT tenant_member_permission_keys_allowed;
ALTER TABLE web.workspace_member DROP CONSTRAINT workspace_member_permission_keys_shape,
    DROP CONSTRAINT workspace_member_permission_keys_allowed;
ALTER TABLE web.tenant_member ADD CONSTRAINT tenant_member_permission_keys_shape
  CHECK (cardinality(permission_keys) <= 27 AND (cardinality(permission_keys)=0 OR
    (array_ndims(permission_keys)=1 AND array_position(permission_keys,NULL) IS NULL)));
ALTER TABLE web.tenant_member ADD CONSTRAINT tenant_member_permission_keys_allowed
  CHECK (permission_keys <@ ARRAY[
    'semantics.read','semantics.query','semantics.draft','semantics.govern','module.analysis','models.invoke','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','module.admin.organization',
    'workspaces.read','workspaces.write','members.read','settings.read','settings.write','roles.read',
    'overview.read','audit.read','audit.export'
  ]::text[]);
ALTER TABLE web.workspace_member ADD CONSTRAINT workspace_member_permission_keys_shape
  CHECK (cardinality(permission_keys) <= 21 AND (cardinality(permission_keys)=0 OR
    (array_ndims(permission_keys)=1 AND array_position(permission_keys,NULL) IS NULL)));
ALTER TABLE web.workspace_member ADD CONSTRAINT workspace_member_permission_keys_allowed
  CHECK (permission_keys <@ ARRAY[
    'semantics.read','semantics.query','semantics.draft','semantics.govern','module.analysis','models.invoke','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','workspaces.read',
    'overview.read','audit.read','audit.export'
  ]::text[]);
-- Down Migration
DO $$ BEGIN RAISE EXCEPTION 'Restore a verified backup to recover'; END $$;
