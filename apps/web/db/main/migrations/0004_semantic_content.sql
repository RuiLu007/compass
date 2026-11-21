-- Owner web/main. Seeded content is an explicit, separate transaction.
-- Up Migration
CREATE TABLE web.semantic_source (
    source_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    locator text NOT NULL,
    source_version text NOT NULL,
    digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (locator, source_version)
);

CREATE TABLE web.semantic_scopes (
    scope_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL,
    tenant_id uuid REFERENCES web.tenant(id),
    department_key text,
    owner_user_id uuid,
    FOREIGN KEY (tenant_id,owner_user_id) REFERENCES web.tenant_member(tenant_id,user_id),
    CHECK ((kind = 'platform' AND tenant_id IS NULL AND department_key IS NULL AND owner_user_id IS NULL)
        OR (kind = 'tenant' AND tenant_id IS NOT NULL AND department_key IS NULL AND owner_user_id IS NULL)
        OR (kind = 'department' AND tenant_id IS NOT NULL AND department_key IS NOT NULL AND owner_user_id IS NULL)
        OR (kind = 'personal' AND tenant_id IS NOT NULL AND department_key IS NULL AND owner_user_id IS NOT NULL)),
    UNIQUE NULLS NOT DISTINCT (kind, tenant_id, department_key, owner_user_id)
);

CREATE TABLE web.semantic_assets (
    asset_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_scope_id uuid NOT NULL REFERENCES web.semantic_scopes,
    kind text NOT NULL CHECK (kind IN ('entity','term','metric')),
    code text NOT NULL,
    domain text NOT NULL,
    responsible_ref text NOT NULL,
    creator_user_id uuid NOT NULL REFERENCES web.app_user(id),
    lifecycle text NOT NULL DEFAULT 'draft',
    UNIQUE (owner_scope_id, kind, code)
);

CREATE TABLE web.semantic_versions (
    version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id uuid NOT NULL REFERENCES web.semantic_assets,
    version_no integer NOT NULL CHECK (version_no > 0),
    parent_version_id uuid,
    draft_id uuid NOT NULL DEFAULT gen_random_uuid(),
    usage_scope_id uuid NOT NULL REFERENCES web.semantic_scopes,
    editor_user_id uuid NOT NULL REFERENCES web.app_user(id),
    name text NOT NULL,
    summary text NOT NULL,
    grain text NOT NULL,
    unit text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    definition jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
    source_id uuid NOT NULL REFERENCES web.semantic_source,
    source_position text NOT NULL,
    digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (asset_id, version_no),
    UNIQUE (asset_id, version_id),
    FOREIGN KEY (asset_id, parent_version_id) REFERENCES web.semantic_versions(asset_id, version_id)
);
CREATE INDEX semantic_version_visibility ON web.semantic_versions(usage_scope_id, name);

CREATE TABLE web.semantic_aliases (
    origin_version_id uuid NOT NULL REFERENCES web.semantic_versions,
    target_asset_id uuid NOT NULL REFERENCES web.semantic_assets,
    scope_id uuid NOT NULL REFERENCES web.semantic_scopes,
    locale text NOT NULL,
    alias_norm text NOT NULL,
    display_alias text NOT NULL,
    PRIMARY KEY (scope_id, locale, alias_norm, origin_version_id, target_asset_id)
);

CREATE TABLE web.semantic_relations (
    from_version_id uuid NOT NULL REFERENCES web.semantic_versions,
    kind text NOT NULL,
    to_asset_id uuid NOT NULL REFERENCES web.semantic_assets,
    scope_id uuid NOT NULL REFERENCES web.semantic_scopes,
    role text NOT NULL,
    rule jsonb NOT NULL CHECK (jsonb_typeof(rule) = 'object'),
    PRIMARY KEY (from_version_id, kind, to_asset_id, scope_id, role)
);
CREATE INDEX semantic_relation_target ON web.semantic_relations(to_asset_id);

CREATE TABLE web.execution_bindings (
    binding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id uuid NOT NULL REFERENCES web.semantic_versions,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    source_ref text NOT NULL,
    mode text NOT NULL CHECK (mode = 'trusted_sql'),
    target_ref text NOT NULL,
    sql_text text NOT NULL,
    target_hash text NOT NULL CHECK (target_hash = encode(sha256(convert_to(sql_text,'UTF8')), 'hex')),
    parameter_schema jsonb NOT NULL,
    result_schema jsonb NOT NULL,
    access_requirements jsonb NOT NULL,
    validation_receipt jsonb,
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES web.workspace(tenant_id, id),
    UNIQUE (version_id, workspace_id, source_ref)
);

CREATE TABLE web.semantic_policy (
    scope_id uuid NOT NULL REFERENCES web.semantic_scopes,
    revision integer NOT NULL CHECK (revision > 0),
    source_id uuid NOT NULL REFERENCES web.semantic_source,
    policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object'),
    PRIMARY KEY (scope_id, revision)
);

-- The BFF reads this owner-managed content. Governance writes and publication need their own IAM.
GRANT SELECT ON web.semantic_source, web.semantic_scopes, web.semantic_assets,
    web.semantic_versions, web.semantic_aliases, web.semantic_relations,
    web.execution_bindings, web.semantic_policy TO compass_web_runtime;
-- Down Migration
DO $$ BEGIN RAISE EXCEPTION 'Restore a verified backup to recover'; END $$;
