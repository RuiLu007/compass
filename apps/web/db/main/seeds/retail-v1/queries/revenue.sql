, facts AS (
    SELECT r.recognized_at AS event_at, o.channel_id, o.region_id, o.selling_site_id AS site_id,
           u.sku_id, s.spu_id, product.category_id, r.amount_cent AS amount, NULL::integer AS report_version
    FROM revenue_entry r JOIN order_unit u USING (tenant_id,order_unit_id,region_id)
    JOIN order_header o USING (tenant_id,order_id,region_id)
    JOIN sku s USING (tenant_id,sku_id) JOIN spu product USING (tenant_id,spu_id)
    JOIN p ON p.tenant_id=r.tenant_id
    WHERE r.entry_type IN ('DELIVERY','RETURN_ACCEPTED') AND r.recognized_at >= p.start_at
      AND r.recognized_at < p.end_at AND greatest(r.available_at,u.available_at,o.available_at) <= p.k
)
