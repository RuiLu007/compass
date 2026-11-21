, days AS (
    SELECT p.d0 + n AS day FROM p, generate_series(0,p.d1-p.d0-1) n
), readiness AS (
    SELECT d.day, s.status FROM days d CROSS JOIN p
    LEFT JOIN LATERAL (
        SELECT a.status FROM asset_period_status a WHERE a.tenant_id=p.tenant_id
          AND a.asset_id='core.channel_product_daily' AND a.period_start=d.day AND a.period_end=d.day+1
          AND a.available_at <= p.k ORDER BY a.observed_at DESC LIMIT 1
    ) s ON true
), report AS (
    SELECT DISTINCT ON (r.report_date,r.channel_id,r.sku_id,r.region_id)
           r.report_date,r.channel_id,r.sku_id,r.region_id,r.report_version,r.paid_amount_cent
    FROM p JOIN channel_product_daily r ON r.tenant_id=p.tenant_id
    WHERE r.report_date >= p.d0 AND r.report_date < p.d1 AND r.available_at <= p.k
      AND (p.all_regions OR p.region_ids @> jsonb_build_array(r.region_id))
    ORDER BY r.report_date,r.channel_id,r.sku_id,r.region_id,r.report_version DESC
), coverage AS (
    SELECT CASE WHEN count(*) FILTER (WHERE status IS NULL)>0 THEN 'COVERAGE_UNKNOWN'
                WHEN count(*) FILTER (WHERE status <> 'COMPLETE')>0 THEN 'REPORT_INCOMPLETE' END AS gap,
           jsonb_build_object('missing_days',coalesce(jsonb_agg(day ORDER BY day)
                 FILTER (WHERE status IS DISTINCT FROM 'COMPLETE'),'[]')) AS detail
    FROM readiness
), facts AS (
    SELECT r.report_date::timestamp AT TIME ZONE 'Asia/Shanghai' AS event_at, r.channel_id,r.region_id,
           NULL::bigint AS site_id,r.sku_id,s.spu_id,u.category_id,r.paid_amount_cent AS amount,r.report_version
    FROM report r CROSS JOIN p JOIN sku s ON s.tenant_id=p.tenant_id AND s.sku_id=r.sku_id
    JOIN spu u ON u.tenant_id=s.tenant_id AND u.spu_id=s.spu_id
)
