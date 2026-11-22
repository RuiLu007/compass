-- 讲师对账。各语句均可单独通过只读连接执行。业务金额的展示列在聚合后由分换元。

-- Q1　区域总盘与六个互斥经营单元。贡献的分母为上期全区 GMV。
WITH totals AS (
  SELECT cell,
    SUM(CASE WHEN day < '2025-10-20' THEN paid_fen ELSE 0 END) AS base_fen,
    SUM(CASE WHEN day >= '2025-10-20' THEN paid_fen ELSE 0 END) AS curr_fen
  FROM sales_day
  WHERE region = 'EAST' AND day >= '2025-10-13' AND day < '2025-10-27'
  GROUP BY cell
), report AS (
  SELECT cell, base_fen, curr_fen FROM totals
  UNION ALL
  SELECT 'EAST_TOTAL', SUM(base_fen), SUM(curr_fen) FROM totals
)
SELECT cell, base_fen / 100.0 AS base_yuan, curr_fen / 100.0 AS current_yuan,
       (curr_fen - base_fen) / 100.0 AS delta_yuan,
       ROUND(100.0 * (curr_fen - base_fen) / NULLIF(base_fen, 0), 2) AS change_pct,
       ROUND(100.0 * (curr_fen - base_fen) / (SELECT SUM(base_fen) FROM totals), 2) AS contribution_pp
FROM report ORDER BY cell;

-- Q2　同口径比较访问、支付率、客单和件单，保留总盘观察构成变化。
WITH cells AS (
  SELECT CASE WHEN day < '2025-10-20' THEN 'W1' ELSE 'W2' END AS wk, cell,
         SUM(visits) AS visits, SUM(paid_orders) AS orders, SUM(paid_fen) AS fen, SUM(units) AS units
  FROM sales_day
  WHERE region = 'EAST' AND day >= '2025-10-13' AND day < '2025-10-27'
  GROUP BY 1, 2
), rollup AS (
  SELECT * FROM cells
  UNION ALL
  SELECT wk, 'EAST_TOTAL', SUM(visits), SUM(orders), SUM(fen), SUM(units) FROM cells GROUP BY wk
)
SELECT wk, cell, visits, orders, ROUND(100.0 * orders / NULLIF(visits, 0), 2) AS cvr_pct,
       ROUND(0.01 * fen / NULLIF(orders, 0), 2) AS aov_yuan,
       ROUND(1.0 * units / NULLIF(orders, 0), 2) AS units_per_order
FROM rollup ORDER BY cell, wk;

-- Q3　两班的实际媒体交付，留足原始分子和分母。
SELECT CASE WHEN day < '2025-10-20' THEN 'W1' ELSE 'W2' END AS wk, slot,
       SUM(spend_fen) / 100.0 AS spend_yuan, SUM(impressions) AS impressions, SUM(clicks) AS clicks,
       SUM(landings) AS landings, ROUND(0.01 * SUM(spend_fen) / NULLIF(SUM(landings), 0), 2) AS yuan_per_landing
FROM ad_slot WHERE region = 'EAST' AND plan = 'PV27' AND day >= '2025-10-13' AND day < '2025-10-27'
GROUP BY 1, 2 ORDER BY 2, 1;

-- Q4　把营业、事件与承诺拒绝放在同一时钟。复盘先读真实业务单据。
SELECT ts, pool, doc_no, event, sku_size, qty
FROM stock_event WHERE region = 'EAST' AND pool = 'EAST-FLAG' ORDER BY ts, sku_size;

-- 以发布日为过渡日，同期对照使用上周相同星期。先按各自粒度聚合。
WITH stock AS (
  SELECT day,
         SUM(CASE WHEN sku_size IN (41,42) THEN attempts ELSE 0 END) AS main_att,
         SUM(CASE WHEN sku_size IN (41,42) THEN short_rejects ELSE 0 END) AS main_short,
         SUM(CASE WHEN sku_size = 40 THEN attempts ELSE 0 END) AS ref_att,
         SUM(CASE WHEN sku_size = 40 THEN short_rejects ELSE 0 END) AS ref_short
  FROM stock_day WHERE region = 'EAST' AND pool = 'EAST-FLAG' GROUP BY day
), paired AS (
  SELECT CASE WHEN s.day < '2025-10-20' THEN 'W1' ELSE 'W2' END AS wk,
         CASE WHEN s.day < '2025-10-17' OR (s.day >= '2025-10-20' AND s.day < '2025-10-24') THEN '1-before'
              WHEN s.day IN ('2025-10-17', '2025-10-24') THEN '2-transition' ELSE '3-after' END AS phase,
         s.visits, s.paid_orders, t.main_att, t.main_short, t.ref_att, t.ref_short
  FROM sales_day AS s JOIN stock AS t ON t.day = s.day
  WHERE s.region = 'EAST' AND s.cell = 'FLAG_RUN' AND s.day >= '2025-10-13' AND s.day < '2025-10-27'
)
SELECT wk, phase, SUM(main_short) AS main_short, SUM(main_att) AS main_attempts,
       ROUND(100.0 * SUM(main_short) / NULLIF(SUM(main_att), 0), 2) AS main_short_pct,
       ROUND(100.0 * SUM(ref_short) / NULLIF(SUM(ref_att), 0), 2) AS ref_short_pct,
       ROUND(100.0 * SUM(paid_orders) / NULLIF(SUM(visits), 0), 2) AS flag_cvr_pct
FROM paired GROUP BY wk, phase ORDER BY wk, phase;

-- Q5　同名券的两个版本。本期城西与未切换规则的滨江分别对照各自基期。
SELECT CASE WHEN day < '2025-10-20' THEN 'W1' ELSE 'W2' END AS wk, site, offer,
       SUM(checks) AS checks, SUM(eligible) AS eligible, SUM(issued) AS issued, SUM(redeemed) AS redeemed,
       ROUND(100.0 * SUM(eligible) / NULLIF(SUM(checks), 0), 2) AS eligible_pct,
       ROUND(100.0 * SUM(redeemed) / NULLIF(SUM(issued), 0), 2) AS redeemed_among_issued_pct,
       SUM(discount_fen) / 100.0 AS discount_yuan
FROM coupon_day WHERE region = 'EAST' AND day >= '2025-10-13' AND day < '2025-10-27'
GROUP BY 1, 2, 3 ORDER BY 2, 1;

-- Q6　最终直营访问的逐日对账，原始打开峰值可与经营有效访问分开查。
SELECT a.day, a.raw_open_events, a.retry_events, a.unlinked_opens, a.visits AS clean_visits,
       s.visits AS sales_visits, a.raw_open_events - a.retry_events - a.unlinked_opens - s.visits AS discrepancy
FROM entry_audit AS a JOIN sales_day AS s ON s.region = a.region AND s.day = a.day AND s.cell = a.cell
WHERE a.region = 'EAST' AND a.cell = 'MP_RUN_NAT' AND a.day >= '2025-10-13' AND a.day < '2025-10-27'
ORDER BY a.day;

-- Q7　现金净额与同龄售后。每日先把营业账聚到区域水平，避免退款跟随经营单元重复。
WITH daily AS (
  SELECT day, SUM(paid_fen) AS paid_fen FROM sales_day
  WHERE region = 'EAST' AND day >= '2025-10-13' AND day < '2025-10-27' GROUP BY day
)
SELECT CASE WHEN d.day < '2025-10-20' THEN 'W1' ELSE 'W2' END AS wk,
       SUM(d.paid_fen) / 100.0 AS paid_yuan, SUM(r.cash_refund_fen) / 100.0 AS cash_refund_yuan,
       SUM(d.paid_fen - r.cash_refund_fen) / 100.0 AS net_cash_yuan,
       SUM(r.cohort_d7_fen) / 100.0 AS d7_refund_yuan,
       ROUND(100.0 * SUM(r.cohort_d7_fen) / NULLIF(SUM(d.paid_fen), 0), 2) AS d7_amount_pct
FROM daily AS d JOIN refund_day AS r ON d.day = r.day AND r.region = 'EAST'
GROUP BY 1 ORDER BY 1;

-- Q8　每个预期资产有十四个经营日，各资产每天一行，由主键保证。
SELECT asset, COUNT(*) AS days_present,
       SUM(CASE WHEN final_parts = expected_parts AND state = 'FINAL'
                 AND final_at <= '2025-11-04 12:00:00' THEN 1 ELSE 0 END) AS sealed_by_cutoff
FROM source_day WHERE region = 'EAST' AND day >= '2025-10-13' AND day < '2025-10-27'
GROUP BY asset ORDER BY asset;

SELECT day, asset, expected_parts, first_parts, final_parts, state, final_at
FROM source_day WHERE region = 'EAST' AND first_parts <> expected_parts ORDER BY day, asset;
