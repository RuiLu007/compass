, facts AS (
    SELECT f.attempted_at AS event_at, o.channel_id, o.region_id, o.selling_site_id AS site_id,
           NULL::bigint AS sku_id, NULL::bigint AS spu_id, NULL::bigint AS category_id, f.amount_cent AS amount, NULL::integer AS report_version
    FROM cash f JOIN order_header o USING (tenant_id, order_id, region_id)
    CROSS JOIN p WHERE o.available_at <= p.k
)
