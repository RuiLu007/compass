, paid AS (
    SELECT f.tenant_id, f.order_id, f.region_id, f.attempted_at, f.amount_cent
    FROM p JOIN payment f ON f.tenant_id=p.tenant_id
    WHERE f.status='SUCCEEDED' AND f.attempted_at >= p.start_at AND f.attempted_at < p.end_at AND f.available_at <= p.k
)
