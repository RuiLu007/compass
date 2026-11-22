-- Owner web/main, Lesson 22. Restore a verified backup for pre-upgrade data.
-- Up Migration
LOCK TABLE web.semantic_policy, web.semantic_release, web.semantic_revocation IN SHARE MODE;
DO $guard$
BEGIN
    IF EXISTS (
        SELECT FROM web.semantic_release r
        CROSS JOIN LATERAL jsonb_each_text(r.manifest->'policies') pin
        JOIN web.semantic_policy p ON p.scope_id::text=pin.key AND p.revision::text=pin.value
        WHERE NOT EXISTS (SELECT FROM web.semantic_revocation x WHERE x.release_id=r.release_id)
          AND jsonb_array_length(coalesce(p.policy->'disabled_capabilities','[]'::jsonb)) > 0
    ) THEN
        RAISE EXCEPTION 'Retire restricted releases and verify replacement access grants before upgrading';
    END IF;
END
$guard$;
DROP TABLE web.semantic_feedback, web.semantic_policy;
-- Down Migration
DO $$ BEGIN RAISE EXCEPTION 'Restore the verified web/main backup to recover removed data'; END $$;
