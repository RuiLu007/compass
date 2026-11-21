, closing AS (
    SELECT p.*, d1-1 AS day, (d1::timestamp - interval '1 second') AT TIME ZONE 'Asia/Shanghai' AS closing_at FROM p
), expected AS (
    SELECT DISTINCT a.site_id,a.sku_id,a.region_id,s.spu_id,u.category_id
    FROM closing c JOIN assortment a ON a.tenant_id=c.tenant_id
    JOIN sku s ON (s.tenant_id,s.sku_id)=(a.tenant_id,a.sku_id)
    JOIN spu u ON (u.tenant_id,u.spu_id)=(s.tenant_id,s.spu_id)
    WHERE a.valid_from <= c.day AND c.day < a.valid_to AND a.available_at <= c.k
      AND (c.all_regions OR c.region_ids @> jsonb_build_array(a.region_id))
      AND (NOT c.filters ? 'region' OR a.region_id=(c.filters->>'region')::bigint)
      AND (NOT c.filters ? 'site' OR a.site_id=(c.filters->>'site')::bigint)
      AND (NOT c.filters ? 'sku' OR a.sku_id=(c.filters->>'sku')::bigint)
      AND (NOT c.filters ? 'spu' OR s.spu_id=(c.filters->>'spu')::bigint)
      AND (NOT c.filters ? 'category' OR u.category_id=(c.filters->>'category')::bigint)
), selected AS (
    SELECT e.*, s.available_qty, s.available_qty=s.sellable_qty-s.reserved_qty AS balanced,
           CASE c.dim WHEN 'day' THEN c.day::text WHEN 'site' THEN e.site_id::text
                 WHEN 'region' THEN e.region_id::text WHEN 'sku' THEN e.sku_id::text
                 WHEN 'spu' THEN e.spu_id::text WHEN 'category' THEN e.category_id::text ELSE 'total' END AS group_key
    FROM expected e CROSS JOIN closing c LEFT JOIN inventory_snapshot s
      ON (s.tenant_id,s.snapshot_date,s.site_id,s.sku_id,s.region_id,s.as_of)=
         (c.tenant_id,c.day,e.site_id,e.sku_id,e.region_id,c.closing_at) AND s.available_at <= c.k
), totals AS (
    SELECT group_key,count(*) AS expected_count,count(available_qty) AS visible_count,
           bool_and(balanced) AS balanced,sum(available_qty)::numeric AS qty,
           jsonb_agg(jsonb_build_array(site_id,sku_id) ORDER BY site_id,sku_id)
               FILTER (WHERE available_qty IS NULL) AS missing
    FROM selected GROUP BY group_key
), evaluated AS (
    SELECT coalesce(t.group_key, CASE WHEN c.dim='total' THEN 'total' END) AS group_key, t.qty,
           CASE WHEN c.day < (spec->>'first_snapshot')::date THEN 'OUTSIDE_SNAPSHOT_COVERAGE'
                WHEN c.k < c.closing_at THEN 'BEFORE_CLOSE'
                WHEN t.expected_count IS NULL THEN 'NO_ASSORTMENT'
                WHEN t.visible_count=0 THEN 'SNAPSHOT_NOT_AVAILABLE'
                WHEN t.visible_count<t.expected_count THEN 'SNAPSHOT_INCOMPLETE'
                WHEN NOT t.balanced THEN 'QUANTITY_MISMATCH' ELSE 'OK' END AS status,
           jsonb_build_object('snapshot_date',c.day,'as_of',c.closing_at,'expected',coalesce(t.expected_count,0),
                              'visible',coalesce(t.visible_count,0),'missing_site_sku',coalesce(t.missing,'[]')) AS detail
    FROM closing c CROSS JOIN contract LEFT JOIN totals t ON true
), results AS (
    SELECT group_key,CASE WHEN status='OK' THEN qty END AS value,status,detail FROM evaluated
)
