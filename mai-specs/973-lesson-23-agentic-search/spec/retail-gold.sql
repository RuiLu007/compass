-- 讲师独立复算。用 psql 连接已导入学员 SQL 的数据库后执行本文件。
-- 固定场景参数只用于验收。下列查询不发布到指标库或学员可检索的知识库。
\set ON_ERROR_STOP on
\set known_at '2025-10-09T09:00:00+08:00'
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path TO retail_course, pg_catalog;
SET LOCAL TIME ZONE 'Asia/Shanghai';

-- 核对分发版本、业务对象。观察截止 K 是模拟经营会时间。
SELECT release_id, schema_version FROM dataset_release;

SELECT t.tenant_name, ch.channel_name, u.spu_name, s.sku_code, s.color_code, s.size_value,
       wh.site_name
FROM sku s
JOIN tenant t USING (tenant_id)
JOIN spu u USING (tenant_id, spu_id)
JOIN channel ch ON ch.tenant_id = s.tenant_id AND ch.channel_id = 4
JOIN site wh ON wh.tenant_id = s.tenant_id AND wh.site_id = 7 AND wh.region_id = 1
WHERE s.tenant_id = 101 AND s.sku_id = 435;

SELECT DISTINCT ON (source_system) source_system, observed_at, complete_through, status
FROM source_watermark
WHERE tenant_id = 101 AND observed_at <= :'known_at'::timestamptz
  AND source_system IN ('OMS', 'WEB', 'WMS')
ORDER BY source_system, observed_at DESC;

-- G1 / B。收款与退款按各自成功事件归期。先分别取数，避免明细联接倍增。
WITH cash AS (
    SELECT p.attempted_at AS event_at, p.amount_cent AS paid, 0::bigint AS refunded
    FROM payment p JOIN order_header o USING (tenant_id, order_id, region_id)
    WHERE p.tenant_id = 101 AND p.region_id = 1 AND o.channel_id = 4 AND p.status = 'SUCCEEDED'
      AND p.available_at <= :'known_at' AND o.available_at <= :'known_at'
      AND p.attempted_at >= date '2025-07-01' AND p.attempted_at < date '2025-10-01'
    UNION ALL
    SELECT r.attempted_at, 0::bigint, r.amount_cent
    FROM refund r JOIN payment p USING (tenant_id, payment_id, order_id, region_id)
    JOIN order_header o USING (tenant_id, order_id, region_id)
    WHERE r.tenant_id = 101 AND r.region_id = 1 AND o.channel_id = 4
      AND r.status = 'SUCCEEDED' AND p.status = 'SUCCEEDED'
      AND greatest(r.available_at, p.available_at, o.available_at) <= :'known_at'
      AND r.attempted_at >= date '2025-07-01' AND r.attempted_at < date '2025-10-01'
)
SELECT date_trunc('month', event_at)::date AS period_start, sum(paid) AS paid_cent,
       sum(refunded) AS refunded_cent, sum(paid) - sum(refunded) AS net_cent
FROM cash GROUP BY 1 ORDER BY 1;

-- G2 / G3。原采购单、到货通知、实际收货各自有时点。恢复窗以实际收货日为起点。
SELECT d.document_no, l.quantity, d.planned_at, r.notified_at, r.revised_due_at,
       m.event_at AS received_at, m.sellable_delta AS received_units
FROM inventory_document d JOIN inventory_document_line l USING (tenant_id, inventory_doc_id)
JOIN procurement_revision r USING (tenant_id, inventory_doc_line_id)
JOIN inventory_movement m USING (tenant_id, inventory_doc_line_id)
WHERE d.tenant_id = 101 AND d.doc_type = 'PURCHASE' AND d.region_id = 1 AND d.to_site_id = 7
  AND l.sku_id = 435 AND m.site_id = 7 AND m.movement_type = 'RECEIPT'
  AND d.planned_at >= date '2025-09-01' AND d.planned_at < date '2025-10-01'
  AND greatest(d.available_at, l.available_at, r.available_at, m.available_at) <= :'known_at'
ORDER BY d.document_no, r.revision_no, m.event_at;

SELECT m.event_at::date AS arrival_day, sum(m.sellable_delta) AS transfer_in_units
FROM inventory_movement m
WHERE m.tenant_id = 101 AND m.region_id = 1 AND m.site_id = 7 AND m.sku_id = 435
  AND m.movement_type = 'TRANSFER_IN' AND m.available_at <= :'known_at'
  AND m.event_at >= date '2025-09-07' AND m.event_at < date '2025-09-21'
GROUP BY 1 ORDER BY 1;

-- G2 / G4。先在各自业务粒度归并，再按窗口并列。访客人日、会话、订单和锁库尝试分开。
WITH windows(label, lo, hi) AS (
    VALUES ('故障窗', date '2025-09-07', date '2025-09-12'),
           ('实收后', date '2025-09-16', date '2025-09-21')
), latest_days AS (
    SELECT DISTINCT ON (s.period_start) s.period_start, s.status
    FROM asset_period_status s JOIN windows w ON s.period_start >= w.lo AND s.period_start < w.hi
    WHERE s.tenant_id = 101 AND s.asset_id = 'core.channel_product_daily'
      AND s.period_end = s.period_start + 1 AND s.available_at <= :'known_at'
    ORDER BY s.period_start, s.observed_at DESC
), reports AS (
    SELECT DISTINCT ON (d.report_date) d.report_date, d.product_visitors, d.submitted_orders,
           d.paid_orders, d.paid_amount_cent
    FROM channel_product_daily d JOIN windows w ON d.report_date >= w.lo AND d.report_date < w.hi
    WHERE d.tenant_id = 101 AND d.region_id = 1 AND d.channel_id = 4 AND d.sku_id = 435
      AND d.available_at <= :'known_at'
    ORDER BY d.report_date, d.report_version DESC
), funnel AS (
    SELECT w.label, count(*) FILTER (WHERE s.status = 'COMPLETE') AS complete_days,
           count(d.report_date) AS visible_days, sum(d.product_visitors) AS visitor_days,
           sum(d.submitted_orders) AS submitted, sum(d.paid_orders) AS paid,
           sum(d.paid_amount_cent) AS paid_cent
    FROM windows w LEFT JOIN latest_days s ON s.period_start >= w.lo AND s.period_start < w.hi
    LEFT JOIN reports d ON d.report_date = s.period_start GROUP BY w.label
), allocations AS (
    SELECT w.label, count(*) AS attempts, count(*) FILTER (WHERE a.outcome = 'INSUFFICIENT') AS short,
           count(*) FILTER (WHERE a.outcome = 'COMMITTED') AS committed,
           count(*) FILTER (WHERE a.outcome = 'ROLLED_BACK') AS rolled_back
    FROM windows w JOIN stock_allocation_attempt a ON a.attempted_at >= w.lo AND a.attempted_at < w.hi
    JOIN order_header o USING (tenant_id, order_id, region_id)
    WHERE a.tenant_id = 101 AND a.region_id = 1 AND a.site_id = 7 AND a.sku_id = 435
      AND o.channel_id = 4 AND greatest(a.available_at, o.available_at) <= :'known_at'
    GROUP BY w.label
), visits AS (
    SELECT w.label, count(DISTINCT s.session_id) AS view_sessions,
           count(DISTINCT s.session_id) FILTER (WHERE s.campaign_id IS NOT NULL) AS tagged_sessions
    FROM windows w JOIN behavior_event b ON b.event_at >= w.lo AND b.event_at < w.hi
    JOIN web_session s USING (tenant_id, session_id, region_id)
    WHERE b.tenant_id = 101 AND b.region_id = 1 AND b.sku_id = 435 AND s.channel_id = 4
      AND b.event_type = 'PRODUCT_VIEW' AND greatest(b.available_at, s.available_at) <= :'known_at'
    GROUP BY w.label
)
SELECT w.label, f.complete_days, f.visible_days, f.visitor_days, f.submitted, f.paid, f.paid_cent,
       a.attempts, a.short, a.committed, a.rolled_back,
       round(100.0 * a.short / nullif(a.attempts, 0), 2) AS insufficient_pct,
       v.view_sessions, v.tagged_sessions
FROM windows w LEFT JOIN funnel f USING (label) LEFT JOIN allocations a USING (label)
LEFT JOIN visits v USING (label) ORDER BY w.lo;

-- G5。核对上一自然日的有效配货与同一期末快照，只取单日，不累计。
SELECT s.snapshot_date, s.sellable_qty, s.reserved_qty, s.available_qty, s.available_at
FROM inventory_snapshot s
WHERE s.tenant_id = 101 AND s.region_id = 1 AND s.site_id = 7 AND s.sku_id = 435
  AND s.snapshot_date = date '2025-10-08' AND s.as_of = '2025-10-08 23:59:59+08'
  AND s.available_at <= :'known_at'
  AND EXISTS (SELECT FROM assortment a WHERE (a.tenant_id, a.region_id, a.site_id, a.sku_id) =
              (s.tenant_id, s.region_id, s.site_id, s.sku_id)
              AND a.valid_from <= s.snapshot_date AND a.valid_to > s.snapshot_date
              AND a.available_at <= :'known_at');

-- 迁移题 B。把 9 月成功退款回溯到原支付月。
SELECT date_trunc('month', p.attempted_at)::date AS pay_month, count(*) AS refund_events,
       sum(r.amount_cent) AS refund_cent
FROM refund r JOIN payment p USING (tenant_id, payment_id, order_id, region_id)
JOIN order_header o USING (tenant_id, order_id, region_id)
WHERE r.tenant_id = 101 AND r.region_id = 1 AND o.channel_id = 4
  AND r.status = 'SUCCEEDED' AND p.status = 'SUCCEEDED'
  AND r.attempted_at >= date '2025-09-01' AND r.attempted_at < date '2025-10-01'
  AND greatest(r.available_at, p.available_at, o.available_at) <= :'known_at'
GROUP BY 1 ORDER BY 1;

-- 迁移题 C。两家标准门店独立归集 9 月成功支付。
SELECT o.selling_site_id, sum(p.amount_cent) AS paid_cent
FROM payment p JOIN order_header o USING (tenant_id, order_id, region_id)
WHERE p.tenant_id = 101 AND p.region_id = 1 AND o.channel_id = 6 AND o.selling_site_id IN (1, 5)
  AND p.status = 'SUCCEEDED' AND p.attempted_at >= date '2025-09-01' AND p.attempted_at < date '2025-10-01'
  AND greatest(p.available_at, o.available_at) <= :'known_at'
GROUP BY 1 ORDER BY 1;
COMMIT;
