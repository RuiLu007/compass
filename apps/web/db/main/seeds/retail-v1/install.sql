-- Both entry points supply the same checked pg_temp.seed_input and one transaction.
DO $semantic_import$
DECLARE
    edition jsonb;
    expected_digest text;
    installed_digest text;
BEGIN
    SELECT body->'edition',digest INTO STRICT edition,expected_digest FROM pg_temp.seed_input;
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname=current_database()
                   AND shobj_description(oid,'pg_database')='compass:web/main') THEN
        RAISE EXCEPTION 'Unregistered Web database target';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(edition->>'locator',0));
    SELECT digest INTO installed_digest FROM web.semantic_source
        WHERE locator=edition->>'locator' AND source_version=edition->>'version';
    IF FOUND THEN
        IF installed_digest <> expected_digest THEN
            RAISE EXCEPTION 'Semantic content differs for this edition; use a new reviewed version';
        END IF;
        UPDATE pg_temp.seed_input SET outcome='already_installed';
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT FROM web.app_user WHERE id=(edition->>'editor')::uuid
                   AND login_name='teaching.platform' AND is_platform_admin AND status='active') THEN
        RAISE EXCEPTION 'Install the Web teaching fixture before the retail semantics edition';
    END IF;
    INSERT INTO web.semantic_source(locator,source_version,digest)
    SELECT body->'edition'->>'locator',body->'edition'->>'version',digest FROM pg_temp.seed_input;

    CREATE TEMP TABLE seed_scopes ON COMMIT DROP AS
    SELECT s.key,s.kind,s.tenant,s.department,s."user" AS owner_user
    FROM pg_temp.seed_input CROSS JOIN LATERAL jsonb_to_recordset(body->'scopes')
         AS s(key text,kind text,tenant uuid,department text,"user" uuid);
    ALTER TABLE pg_temp.seed_scopes ADD PRIMARY KEY(key);
    INSERT INTO web.semantic_scopes(kind,tenant_id,department_key,owner_user_id)
    SELECT kind,tenant,department,owner_user FROM pg_temp.seed_scopes
    ON CONFLICT (kind,tenant_id,department_key,owner_user_id) DO NOTHING;
    ALTER TABLE pg_temp.seed_scopes ADD COLUMN id uuid;
    UPDATE pg_temp.seed_scopes s SET id=w.scope_id FROM web.semantic_scopes w
    WHERE (s.kind,s.tenant,s.department,s.owner_user)
          IS NOT DISTINCT FROM (w.kind,w.tenant_id,w.department_key,w.owner_user_id);
    ALTER TABLE pg_temp.seed_scopes ALTER COLUMN id SET NOT NULL;

    CREATE TEMP TABLE seed_assets ON COMMIT DROP AS
    SELECT a->>'ref' AS ref,a,gen_random_uuid() AS asset,gen_random_uuid() AS version,
           (SELECT id FROM pg_temp.seed_scopes WHERE key=a->>'scope') AS scope,
           (body->'edition'->>'editor')::uuid AS editor,
           (SELECT source_id FROM web.semantic_source
            WHERE locator=body->'edition'->>'locator' AND source_version=body->'edition'->>'version') AS source
    FROM pg_temp.seed_input CROSS JOIN LATERAL jsonb_array_elements(body->'assets') a;
    ALTER TABLE pg_temp.seed_assets ADD PRIMARY KEY(ref);

    -- A pre-existing identity outside this import raises a unique violation and rolls back the whole edition.
    INSERT INTO web.semantic_assets(asset_id,owner_scope_id,kind,code,domain,responsible_ref,creator_user_id)
    SELECT asset,scope,a->>'kind',a->>'code',a->>'domain',a->>'responsible',editor FROM pg_temp.seed_assets;

    INSERT INTO web.semantic_versions(version_id,asset_id,version_no,usage_scope_id,editor_user_id,
        name,summary,grain,unit,schema_version,definition,source_id,source_position,digest)
    SELECT version,asset,1,scope,editor,a->>'name',a->>'summary',a->>'grain',a->>'unit',1,a->'definition',
           source,a->>'position',encode(sha256(convert_to(a::text,'UTF8')),'hex')
    FROM pg_temp.seed_assets;

    INSERT INTO web.semantic_aliases(origin_version_id,target_asset_id,scope_id,locale,alias_norm,display_alias)
    SELECT (SELECT version FROM pg_temp.seed_assets WHERE ref=v->>'ref'),
           (SELECT asset FROM pg_temp.seed_assets WHERE ref=v->>'ref'),
           (SELECT id FROM pg_temp.seed_scopes WHERE key=v->>'scope'),'zh-CN',v->>'alias_norm',v->>'display_alias'
    FROM pg_temp.seed_input CROSS JOIN LATERAL jsonb_array_elements(body->'aliases') v;

    INSERT INTO web.semantic_relations(from_version_id,kind,to_asset_id,scope_id,role,rule)
    SELECT (SELECT version FROM pg_temp.seed_assets WHERE ref=r->>'from'),r->>'kind',
           (SELECT asset FROM pg_temp.seed_assets WHERE ref=r->>'to'),
           (SELECT id FROM pg_temp.seed_scopes WHERE key=r->>'scope'),r->>'role',r->'rule'
    FROM pg_temp.seed_input CROSS JOIN LATERAL jsonb_array_elements(body->'relations') r;

    INSERT INTO web.execution_bindings(version_id,tenant_id,workspace_id,source_ref,mode,target_ref,sql_text,
        target_hash,parameter_schema,result_schema,access_requirements)
    SELECT (SELECT version FROM pg_temp.seed_assets WHERE ref=b->>'ref'),(b->>'tenant')::uuid,
           (b->>'workspace')::uuid,b->>'source','trusted_sql',b->>'target_ref',b->>'sql',b->>'target_hash',
           b->'parameter_schema',b->'result_schema',b->'access'
    FROM pg_temp.seed_input CROSS JOIN LATERAL jsonb_array_elements(body->'bindings') b;

    -- Pins affect discovery. Only explicit term -> metric mappings supply a departmental default.
    INSERT INTO web.semantic_policy(scope_id,revision,source_id,policy)
    SELECT s.id,1,(SELECT source_id FROM web.semantic_source WHERE locator=body->'edition'->>'locator'
                   AND source_version=body->'edition'->>'version'),
           jsonb_build_object('pinned_asset_ids',
               (SELECT jsonb_agg(a.asset ORDER BY pin.ord) FROM jsonb_array_elements_text(v->'pins')
                  WITH ORDINALITY AS pin(ref,ord) JOIN pg_temp.seed_assets a USING (ref)),
               'defaults',CASE WHEN v ? 'term' THEN jsonb_build_array(jsonb_build_object(
                   'term_ref',(SELECT asset FROM pg_temp.seed_assets WHERE ref=v->>'term'),
                   'target_metric_ref',(SELECT asset FROM pg_temp.seed_assets WHERE ref=v->>'default'),
                   'applicable_scope_ref',s.id)) ELSE '[]'::jsonb END)
    FROM pg_temp.seed_input CROSS JOIN LATERAL jsonb_array_elements(body->'edition'->'policies') v
    JOIN pg_temp.seed_scopes s ON s.key=v->>'scope';
    UPDATE pg_temp.seed_input SET outcome='installed';
END
$semantic_import$;
