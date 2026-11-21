, days AS (SELECT d0+n AS day FROM p,generate_series(0,d1-d0-1) n),
readiness AS (
    SELECT day,s.status FROM days CROSS JOIN p LEFT JOIN LATERAL (
        SELECT a.status FROM asset_period_status a
        WHERE a.tenant_id=p.tenant_id AND a.asset_id='core.channel_product_daily'
          AND a.period_start=day AND a.period_end=day+1 AND a.available_at <= p.k
        ORDER BY a.observed_at DESC LIMIT 1
    ) s ON true
), lines AS (
    SELECT r.report_date,r.channel_id,r.paid_amount_cent
    FROM p JOIN channel_product_daily r ON r.tenant_id=p.tenant_id AND r.report_version=1
    WHERE r.report_date >= p.d0 AND r.report_date < p.d1 AND r.available_at <= p.k
      AND (p.all_regions OR p.region_ids @> jsonb_build_array(r.region_id))
      AND (NOT p.filters ? 'channel' OR r.channel_id=(p.filters->>'channel')::bigint)
      AND (NOT p.filters ? 'region' OR r.region_id=(p.filters->>'region')::bigint)
), daily AS (
    SELECT d.day,d.status,count(l.paid_amount_cent) AS row_count,sum(l.paid_amount_cent) AS cents
    FROM readiness d LEFT JOIN lines l ON l.report_date=d.day GROUP BY d.day,d.status
), totals AS (
    SELECT CASE WHEN p.dim='day' THEN d.day::text ELSE 'total' END AS group_key,
           sum(d.row_count)::bigint AS row_count,sum(d.cents)::numeric AS cents,
           array_agg(d.day ORDER BY d.day) FILTER (WHERE d.row_count=0) AS empty_days,
           array_agg(d.day ORDER BY d.day) FILTER (WHERE d.status IS NULL OR d.status='PENDING') AS not_ready,
           bool_or(d.status IS NULL) AS unknown
    FROM daily d CROSS JOIN p GROUP BY 1
), results AS (
    SELECT group_key,CASE WHEN not_ready IS NULL THEN cents END AS value,
           CASE WHEN unknown THEN 'COVERAGE_UNKNOWN' WHEN not_ready IS NOT NULL THEN 'REPORT_INCOMPLETE'
                WHEN row_count=0 THEN 'NO_REPORT_ROWS' ELSE 'OK' END AS status,
           jsonb_build_object('row_count',row_count,'report_versions',CASE WHEN row_count>0 THEN '[1]'::jsonb ELSE '[]'::jsonb END,
               'no_visible_row_days',coalesce(to_jsonb(empty_days),'[]'),'unready_days',coalesce(to_jsonb(not_ready),'[]')) AS detail
    FROM totals
)
