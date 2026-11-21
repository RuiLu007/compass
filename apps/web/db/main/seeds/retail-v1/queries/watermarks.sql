, watermarks AS (
    SELECT source, last_seen.observed_at, last_seen.complete_through, last_seen.status
    FROM p CROSS JOIN contract CROSS JOIN jsonb_array_elements_text(spec->'watermarks') source
    LEFT JOIN LATERAL (
        SELECT w.observed_at, w.complete_through, w.status FROM source_watermark w
        WHERE w.tenant_id=p.tenant_id AND w.source_system=source AND w.observed_at <= p.k
        ORDER BY w.observed_at DESC LIMIT 1
    ) last_seen ON true
), coverage AS (
    SELECT CASE WHEN count(*) FILTER (WHERE w.observed_at IS NULL)>0 THEN 'COVERAGE_UNKNOWN'
                WHEN bool_or(w.status='FAILED' OR w.complete_through < p.end_at) THEN 'SOURCE_DELAYED'
           END AS gap,
           jsonb_build_object('watermarks',jsonb_object_agg(source,to_jsonb(w)-'source')) AS detail
    FROM watermarks w CROSS JOIN p
)
