-- Owner: web/main; Feature: 993; PostgreSQL 17
-- 0002_readable_routes -> 0003_model_invocation_permission
-- Existing permission arrays are preserved; no automatic grants.
-- Up Migration
DO $$
DECLARE item record; removed integer;
BEGIN
    removed := 0;
    FOR item IN SELECT conname FROM pg_constraint
      WHERE conrelid='web.tenant_member'::regclass AND contype='c'
      AND pg_get_constraintdef(oid) LIKE '%permission_keys%'
      AND pg_get_constraintdef(oid) NOT LIKE '%role_key%'
    LOOP
      EXECUTE format('ALTER TABLE web.tenant_member DROP CONSTRAINT %I',item.conname);
      removed := removed + 1;
    END LOOP;
    IF removed <> 2 THEN RAISE EXCEPTION 'Unexpected tenant permission constraints'; END IF;
    removed := 0;
    FOR item IN SELECT conname FROM pg_constraint
      WHERE conrelid='web.workspace_member'::regclass AND contype='c'
      AND pg_get_constraintdef(oid) LIKE '%permission_keys%'
    LOOP
      EXECUTE format('ALTER TABLE web.workspace_member DROP CONSTRAINT %I',item.conname);
      removed := removed + 1;
    END LOOP;
    IF removed <> 2 THEN RAISE EXCEPTION 'Unexpected workspace permission constraints'; END IF;
END $$;
ALTER TABLE web.tenant_member ADD CONSTRAINT tenant_member_permission_keys_shape
  CHECK (cardinality(permission_keys) <= 23 AND (cardinality(permission_keys)=0 OR
    (array_ndims(permission_keys)=1 AND array_position(permission_keys,NULL) IS NULL)));
ALTER TABLE web.tenant_member ADD CONSTRAINT tenant_member_permission_keys_allowed
  CHECK (permission_keys <@ ARRAY[
    'module.analysis','models.invoke','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','module.admin.organization',
    'workspaces.read','workspaces.write','members.read','settings.read','settings.write','roles.read',
    'overview.read','audit.read','audit.export'
  ]::text[]);
ALTER TABLE web.workspace_member ADD CONSTRAINT workspace_member_permission_keys_shape
  CHECK (cardinality(permission_keys) <= 17 AND (cardinality(permission_keys)=0 OR
    (array_ndims(permission_keys)=1 AND array_position(permission_keys,NULL) IS NULL)));
ALTER TABLE web.workspace_member ADD CONSTRAINT workspace_member_permission_keys_allowed
  CHECK (permission_keys <@ ARRAY[
    'module.analysis','models.invoke','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','workspaces.read',
    'overview.read','audit.read','audit.export'
  ]::text[]);
-- Down Migration
-- Keep the expanded permission contract on application recovery, or restore a verified backup.
