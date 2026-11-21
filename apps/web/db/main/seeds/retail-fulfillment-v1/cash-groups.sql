, grouped AS (
    SELECT CASE p.dim WHEN 'day' THEN (f.event_at AT TIME ZONE 'Asia/Shanghai')::date::text
               WHEN 'month' THEN to_char(f.event_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM')
               WHEN 'channel' THEN f.channel_id::text WHEN 'region' THEN f.region_id::text
               WHEN 'site' THEN f.site_id::text ELSE 'total' END AS group_key,
           sum(f.amount)::numeric AS value
    FROM facts f CROSS JOIN p
    WHERE (p.all_regions OR p.region_ids @> jsonb_build_array(f.region_id))
      AND (NOT p.filters ? 'channel' OR f.channel_id=(p.filters->>'channel')::bigint)
      AND (NOT p.filters ? 'region' OR f.region_id=(p.filters->>'region')::bigint)
      AND (NOT p.filters ? 'site' OR f.site_id=(p.filters->>'site')::bigint)
    GROUP BY 1
), keys AS (
    SELECT to_char(m, 'YYYY-MM') AS group_key
    FROM p CROSS JOIN LATERAL generate_series(date_trunc('month', p.d0::timestamp),
         date_trunc('month', (p.d1-1)::timestamp), interval '1 month') m WHERE p.dim='month'
    UNION ALL
    SELECT 'total' FROM p WHERE p.dim='total'
    UNION ALL
    SELECT g.group_key FROM grouped g CROSS JOIN p WHERE p.dim NOT IN ('month','total')
), results AS (
    SELECT k.group_key,CASE WHEN c.gap IS NULL THEN coalesce(g.value,0) END AS value,
           coalesce(c.gap,'OK') AS status,c.detail
    FROM keys k LEFT JOIN grouped g USING(group_key) CROSS JOIN coverage c
    UNION ALL
    SELECT NULL::text,NULL::numeric,coalesce(c.gap,'NO_FACTS'),c.detail
    FROM p CROSS JOIN coverage c WHERE NOT EXISTS (SELECT FROM keys)
)
