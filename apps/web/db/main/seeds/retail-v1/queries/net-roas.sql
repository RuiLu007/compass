, expected AS (
    SELECT c.campaign_id, c.target_channel_id AS channel_id, p.d0+n AS day
    FROM p JOIN campaign c ON c.tenant_id=p.tenant_id
    JOIN channel ch ON (ch.tenant_id,ch.channel_id)=(c.tenant_id,c.target_channel_id)
    CROSS JOIN generate_series(0,p.d1-p.d0-1) n
    WHERE ch.channel_type='PLATFORM' AND c.available_at <= p.k
      AND c.starts_at < ((p.d0+n+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
      AND c.ends_at > ((p.d0+n)::timestamp AT TIME ZONE 'Asia/Shanghai')
      AND (NOT p.filters ? 'channel' OR c.target_channel_id=(p.filters->>'channel')::bigint)
      AND (NOT p.filters ? 'campaign' OR c.campaign_id=(p.filters->>'campaign')::bigint)
), activity AS (
    SELECT e.*, a.report_version, a.coverage_status, a.attributed_paid_cent-a.attributed_refund_cent AS net,
           spend.amount, spend.regions, spend.versions
    FROM expected e CROSS JOIN p
    LEFT JOIN LATERAL (
        SELECT r.report_version,r.coverage_status,r.attributed_paid_cent,r.attributed_refund_cent
        FROM campaign_attribution_daily r
        WHERE (r.tenant_id,r.campaign_id,r.channel_id,r.cohort_date)=(p.tenant_id,e.campaign_id,e.channel_id,e.day)
          AND r.attribution_model='PLATFORM_LAST_CLICK_7D' AND r.model_version='1' AND r.available_at <= p.k
        ORDER BY r.report_version DESC LIMIT 1
    ) a ON true
    CROSS JOIN LATERAL (
        SELECT sum(latest.spend_cent) AS amount, count(latest.report_version) AS regions,
               jsonb_object_agg(region,latest.report_version ORDER BY region) AS versions
        FROM generate_series(1,7) region LEFT JOIN LATERAL (
            SELECT r.spend_cent,r.report_version FROM campaign_daily r
            WHERE (r.tenant_id,r.campaign_id,r.channel_id,r.report_date,r.region_id)=
                  (p.tenant_id,e.campaign_id,e.channel_id,e.day,region) AND r.available_at <= p.k
            ORDER BY r.report_version DESC LIMIT 1
        ) latest ON true
    ) spend
), cells AS (
    SELECT a.*, CASE WHEN report_version IS NULL THEN 'ATTRIBUTION_MISSING'
                     WHEN coverage_status IS DISTINCT FROM 'KNOWN' OR net IS NULL THEN 'ATTRIBUTION_UNKNOWN'
                     WHEN regions < 7 THEN 'SPEND_INCOMPLETE' END AS gap,
           CASE p.dim WHEN 'day' THEN day::text WHEN 'campaign' THEN campaign_id::text
                      WHEN 'channel' THEN channel_id::text ELSE 'total' END AS group_key
    FROM activity a CROSS JOIN p
), totals AS (
    SELECT group_key, sum(net)::numeric AS numerator, sum(amount)::numeric AS denominator,
           array_agg(DISTINCT gap ORDER BY gap) FILTER (WHERE gap IS NOT NULL) AS gaps,
           jsonb_agg(jsonb_build_object('day',day,'campaign',campaign_id,'channel',channel_id,
               'attribution_version',report_version,'spend_versions',versions,'gap',gap)
               ORDER BY day,campaign_id,channel_id) AS source_versions
    FROM cells GROUP BY group_key
), results AS (
    SELECT group_key, CASE WHEN gaps IS NULL THEN numerator/nullif(denominator,0) END AS value,
           CASE WHEN cardinality(gaps)>1 THEN 'MULTIPLE_GAPS' WHEN gaps IS NOT NULL THEN gaps[1]
                WHEN denominator=0 THEN 'ZERO_SPEND' ELSE 'OK' END AS status,
           jsonb_build_object('activity_days',source_versions,'gaps',gaps,
               'net_cent',CASE WHEN gaps IS NULL THEN numerator END,
               'spend_cent',CASE WHEN gaps IS NULL THEN denominator END) AS detail
    FROM totals
    UNION ALL
    SELECT CASE WHEN dim='total' THEN 'total' END,NULL::numeric,CASE WHEN k < end_at THEN 'PERIOD_NOT_CLOSED' ELSE 'NO_ACTIVITY' END,'{}'::jsonb
    FROM p WHERE NOT EXISTS (SELECT FROM cells)
)
