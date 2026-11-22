-- Owner web/main. The provisioning administrator installs vector in public.
-- Up Migration
CREATE TABLE web.document_vectors (
  tenant_id uuid NOT NULL REFERENCES web.tenant(id),
  collection_digest text NOT NULL CHECK (collection_digest ~ '^[0-9a-f]{64}$'),
  index_profile text NOT NULL,
  doc_id text NOT NULL,
  version text NOT NULL,
  section_id text NOT NULL,
  section_hash text NOT NULL CHECK (section_hash ~ '^[0-9a-f]{64}$'),
  embedding public.vector(1024) NOT NULL,
  PRIMARY KEY (tenant_id, collection_digest, index_profile, doc_id, version, section_id)
);
GRANT SELECT ON web.document_vectors TO compass_web_runtime;
-- Down Migration
DO $$ BEGIN RAISE EXCEPTION 'Restore the verified web/main backup to recover the previous version'; END $$;
