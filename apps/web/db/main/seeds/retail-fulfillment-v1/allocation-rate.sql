, windows AS (
    SELECT 'total'::text AS group_key, p.start_at, p.end_at FROM p WHERE dim='total'
    UNION ALL
    SELECT (d0+n)::text, (d0+n)::timestamp AT TIME ZONE 'Asia/Shanghai',
           (d0+n+1)::timestamp AT TIME ZONE 'Asia/Shanghai'
    FROM p CROSS JOIN LATERAL generate_series(0, d1-d0-1) n WHERE dim='day'
), latest AS (
    SELECT source, w.observed_at, w.complete_through, w.status
    FROM p CROSS JOIN contract CROSS JOIN jsonb_array_elements_text(spec->'watermarks') source
    LEFT JOIN LATERAL (
        SELECT observed_at,complete_through,status FROM source_watermark
        WHERE tenant_id=p.tenant_id AND source_system=source AND observed_at<=p.k
        ORDER BY observed_at DESC LIMIT 1
    ) w ON true
), coverage AS (
    SELECT w.group_key,
           CASE WHEN bool_or(l.observed_at IS NULL) THEN 'COVERAGE_UNKNOWN'
                WHEN bool_or(l.status<>'COMPLETE' OR l.complete_through<w.end_at) THEN 'SOURCE_DELAYED'
           END AS gap,
           jsonb_build_object('watermarks',jsonb_object_agg(l.source,to_jsonb(l)-'source')) AS detail
    FROM windows w CROSS JOIN latest l GROUP BY w.group_key
), attempts AS (
    SELECT a.attempted_at,a.outcome
    FROM p JOIN stock_allocation_attempt a ON a.tenant_id=p.tenant_id
    JOIN order_header o ON (o.tenant_id,o.order_id,o.region_id)=(a.tenant_id,a.order_id,a.region_id)
    WHERE a.attempted_at>=p.start_at AND a.attempted_at<p.end_at
      AND greatest(a.available_at,o.available_at)<=p.k
      AND (p.all_regions OR p.region_ids @> jsonb_build_array(a.region_id))
      AND a.outcome IN ('INSUFFICIENT','COMMITTED','ROLLED_BACK')
      AND (NOT p.filters ? 'region' OR a.region_id=(p.filters->>'region')::bigint)
      AND (NOT p.filters ? 'channel' OR o.channel_id=(p.filters->>'channel')::bigint)
      AND (NOT p.filters ? 'fulfillment_site' OR a.site_id=(p.filters->>'fulfillment_site')::bigint)
      AND (NOT p.filters ? 'sku' OR a.sku_id=(p.filters->>'sku')::bigint)
), counts AS (
    SELECT w.group_key,count(a.outcome) AS denominator,
           count(*) FILTER (WHERE a.outcome='INSUFFICIENT') AS numerator
    FROM windows w LEFT JOIN attempts a ON a.attempted_at>=w.start_at AND a.attempted_at<w.end_at
    GROUP BY w.group_key
), results AS (
    SELECT c.group_key,
           CASE WHEN v.gap IS NULL THEN numerator::numeric/nullif(denominator,0) END AS value,
           coalesce(v.gap,CASE WHEN denominator=0 THEN 'NO_FACTS' ELSE 'OK' END) AS status,
           v.detail || CASE WHEN v.gap IS NULL THEN jsonb_build_object('numerator',numerator,'denominator',denominator)
                       ELSE '{}'::jsonb END AS detail
    FROM counts c JOIN coverage v USING(group_key)
)
