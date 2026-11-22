-- Owner web/main. Preserve the previous release catalog and vectors.
-- Up Migration
CREATE TABLE web.knowledge_base (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, workspace_id uuid NOT NULL,
 name text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100), description text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(), deleted boolean NOT NULL DEFAULT false, revision integer NOT NULL DEFAULT 1,
 FOREIGN KEY (tenant_id, workspace_id) REFERENCES web.workspace(tenant_id,id)
);
CREATE UNIQUE INDEX knowledge_base_name ON web.knowledge_base(tenant_id,workspace_id,lower(name)) WHERE NOT deleted;
CREATE TABLE web.knowledge_file (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), base_id uuid NOT NULL REFERENCES web.knowledge_base,
 name text NOT NULL, format text NOT NULL CHECK (format IN ('md','pdf','txt')), original bytea NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), char_count integer NOT NULL DEFAULT 0,
 status text NOT NULL CHECK (status IN ('processing','ready','failed')), error text NOT NULL DEFAULT '',
 revision integer NOT NULL DEFAULT 1, attempt uuid, started_at timestamptz DEFAULT now(),
 source_id text NOT NULL, source_version text NOT NULL DEFAULT '1', origin_digest text,
 deleted boolean NOT NULL DEFAULT false, UNIQUE (base_id,source_id,source_version)
);
CREATE TABLE web.knowledge_chunk (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), file_id uuid NOT NULL REFERENCES web.knowledge_file,
 ordinal integer NOT NULL CHECK (ordinal>0), section_id text NOT NULL, heading text NOT NULL,
 body text NOT NULL CHECK (length(body) BETWEEN 1 AND 8192), section_hash text NOT NULL,
 enabled boolean NOT NULL DEFAULT true, revision integer NOT NULL DEFAULT 1, embedding public.vector(1024) NOT NULL,
 UNIQUE(file_id,section_id), UNIQUE(file_id,ordinal)
);
GRANT SELECT,INSERT,UPDATE ON web.knowledge_base,web.knowledge_file,web.knowledge_chunk TO compass_web_runtime;
-- Down Migration
DO $$ BEGIN RAISE EXCEPTION 'Restore the verified web/main backup to recover the previous version'; END $$;
