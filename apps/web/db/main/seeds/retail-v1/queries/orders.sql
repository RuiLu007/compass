, facts AS (
    SELECT o.ordered_at AS event_at, o.channel_id, o.region_id, o.selling_site_id AS site_id,
           NULL::bigint AS sku_id, NULL::bigint AS spu_id, NULL::bigint AS category_id, 1::numeric AS amount, NULL::integer AS report_version
    FROM p JOIN order_header o ON o.tenant_id=p.tenant_id
    WHERE o.ordered_at >= p.start_at AND o.ordered_at < p.end_at AND o.available_at <= p.k
      AND (NOT (p.filters ?| ARRAY['sku','spu','category']) OR EXISTS (
          SELECT FROM order_line l JOIN sku s USING (tenant_id,sku_id) JOIN spu u USING (tenant_id,spu_id)
          WHERE (l.tenant_id,l.order_id,l.region_id,l.channel_id)=(o.tenant_id,o.order_id,o.region_id,o.channel_id)
            AND l.available_at <= p.k
            AND (NOT p.filters ? 'sku' OR l.sku_id=(p.filters->>'sku')::bigint)
            AND (NOT p.filters ? 'spu' OR s.spu_id=(p.filters->>'spu')::bigint)
            AND (NOT p.filters ? 'category' OR u.category_id=(p.filters->>'category')::bigint)
      ))
)
