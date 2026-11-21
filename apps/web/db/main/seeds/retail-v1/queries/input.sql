-- $1: validated business request. $2: server-attested source scope, never a model argument.
, input AS (
    SELECT ($1::jsonb->>'start')::date AS d0, ($1::jsonb->>'end')::date AS d1,
           ($1::jsonb->>'as_of')::timestamptz AS k, coalesce($1::jsonb->>'group_by','total') AS dim,
           coalesce($1::jsonb->'filters','{}') AS filters,
           ($2::jsonb->>'tenant_id')::smallint AS tenant_id, $2::jsonb->>'data_release' AS release_id,
           ($2::jsonb->>'all_regions')::boolean AS all_regions, $2::jsonb->'region_ids' AS region_ids
), checked AS (
    SELECT i.*, i.d0::timestamp AT TIME ZONE 'Asia/Shanghai' AS start_at,
           i.d1::timestamp AT TIME ZONE 'Asia/Shanghai' AS end_at,
           CASE WHEN i.d0 IS NULL OR i.d1 IS NULL OR i.k IS NULL OR i.d1 <= i.d0 THEN 'INVALID_WINDOW'
                WHEN i.dim <> 'total' AND NOT (spec->'dimensions' ? i.dim) THEN 'UNSUPPORTED_DIMENSION'
                WHEN EXISTS (SELECT FROM jsonb_object_keys(i.filters) f WHERE NOT (spec->'filters' ? f))
                     THEN 'UNSUPPORTED_FILTER'
                WHEN i.all_regions IS NULL OR (NOT i.all_regions AND
                     (coalesce(jsonb_array_length(i.region_ids),0) = 0 OR spec->>'access' = 'all_regions'
                      OR (i.filters ? 'region' AND NOT (i.region_ids @> jsonb_build_array(i.filters->'region')))))
                     THEN 'SCOPE_DENIED'
                WHEN t.release_id IS NULL OR i.release_id IS DISTINCT FROM t.release_id
                     OR t.release_id <> spec->>'release' THEN 'RELEASE_UNAVAILABLE'
                WHEN (CASE WHEN spec ? 'first_snapshot' THEN i.d1-1 ELSE i.d0 END) < (spec->>'start')::date OR i.d1 > (spec->>'end')::date THEN 'OUTSIDE_COVERAGE'
           END AS invalid
    FROM input i CROSS JOIN contract LEFT JOIN tenant t ON t.tenant_id = i.tenant_id
), p AS (SELECT * FROM checked WHERE invalid IS NULL)
