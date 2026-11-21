-- Owner web/main. Index immutable definitions and the aliases attached to each version.
-- Up Migration
CREATE INDEX semantic_version_search ON web.semantic_versions USING gin
    (to_tsvector('simple'::regconfig, name || ' ' || summary));
CREATE INDEX semantic_alias_search ON web.semantic_aliases USING gin
    (to_tsvector('simple'::regconfig, display_alias));
-- Down Migration
DROP INDEX web.semantic_alias_search;
DROP INDEX web.semantic_version_search;
