SELECT group_key, value, status, detail, c.d0 AS period_start, c.d1 AS period_end,
       c.k AS as_of, c.release_id AS data_release, 'Asia/Shanghai'::text AS business_timezone,
       spec->>'unit' AS unit
FROM checked c CROSS JOIN contract CROSS JOIN LATERAL (
    SELECT r.* FROM results r WHERE c.invalid IS NULL
    UNION ALL
    SELECT NULL::text, NULL::numeric, c.invalid, '{}'::jsonb WHERE c.invalid IS NOT NULL
) receipt ORDER BY group_key NULLS FIRST
