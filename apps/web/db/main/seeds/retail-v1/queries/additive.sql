, grouped AS (
    SELECT CASE p.dim WHEN 'day' THEN (event_at AT TIME ZONE 'Asia/Shanghai')::date::text
               WHEN 'channel' THEN channel_id::text WHEN 'region' THEN region_id::text
               WHEN 'site' THEN site_id::text WHEN 'sku' THEN sku_id::text WHEN 'spu' THEN spu_id::text
               WHEN 'category' THEN category_id::text ELSE 'total' END AS group_key,
           sum(amount)::numeric AS value,
           array_agg(DISTINCT report_version ORDER BY report_version) FILTER (WHERE report_version IS NOT NULL) AS versions
    FROM facts CROSS JOIN p
    WHERE (p.all_regions OR p.region_ids @> jsonb_build_array(region_id))
      AND (NOT p.filters ? 'channel' OR channel_id=(p.filters->>'channel')::bigint)
      AND (NOT p.filters ? 'region' OR region_id=(p.filters->>'region')::bigint)
      AND (NOT p.filters ? 'site' OR site_id=(p.filters->>'site')::bigint)
      -- Orders applied the three product filters in one correlated EXISTS before counting.
      AND (sku_id IS NULL OR NOT p.filters ? 'sku' OR sku_id=(p.filters->>'sku')::bigint)
      AND (spu_id IS NULL OR NOT p.filters ? 'spu' OR spu_id=(p.filters->>'spu')::bigint)
      AND (category_id IS NULL OR NOT p.filters ? 'category' OR category_id=(p.filters->>'category')::bigint)
    GROUP BY 1
), results AS (
    SELECT g.group_key, CASE WHEN c.gap IS NULL THEN g.value END AS value,
           coalesce(c.gap,'OK') AS status, c.detail ||
           CASE WHEN g.versions IS NULL THEN '{}'::jsonb ELSE jsonb_build_object('report_versions',g.versions) END AS detail
    FROM grouped g CROSS JOIN coverage c
    UNION ALL
    SELECT CASE WHEN p.dim='total' THEN 'total' END,
           CASE WHEN c.gap IS NULL AND p.dim='total' THEN 0::numeric END,
           coalesce(c.gap, CASE WHEN p.dim='total' THEN 'OK' ELSE 'NO_FACTS' END), c.detail
    FROM p CROSS JOIN coverage c WHERE NOT EXISTS (SELECT FROM grouped)
)
