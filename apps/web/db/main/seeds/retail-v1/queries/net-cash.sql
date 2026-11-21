, cash AS (
    SELECT * FROM paid
    UNION ALL
    SELECT r.tenant_id, r.order_id, r.region_id, r.attempted_at, -r.amount_cent
    FROM p JOIN refund r ON r.tenant_id=p.tenant_id
    JOIN payment original ON (original.tenant_id,original.payment_id,original.order_id,original.region_id)
                           =(r.tenant_id,r.payment_id,r.order_id,r.region_id)
    WHERE r.status='SUCCEEDED' AND original.status='SUCCEEDED' AND original.available_at <= p.k
      AND r.attempted_at >= p.start_at AND r.attempted_at < p.end_at AND r.available_at <= p.k
)
