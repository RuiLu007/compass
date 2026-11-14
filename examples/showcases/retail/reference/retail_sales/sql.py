"""Fixed, parameterized Lesson 15 Retail queries; no caller-supplied SQL enters this module."""

CHANNEL_SALES_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (
        d.tenant_id, d.report_date, d.channel_id, d.sku_id, d.region_id
    )
        d.tenant_id,
        d.report_date,
        d.channel_id,
        d.sku_id,
        d.region_id,
        d.paid_amount_cent
    FROM core.channel_product_daily AS d
    WHERE d.tenant_id = %(tenant)s
      AND (
        (d.report_date >= %(start)s AND d.report_date < %(end)s)
        OR (
          %(compare_start)s::date IS NOT NULL
          AND d.report_date >= %(compare_start)s::date
          AND d.report_date < %(compare_end)s::date
        )
      )
    ORDER BY
        d.tenant_id,
        d.report_date,
        d.channel_id,
        d.sku_id,
        d.region_id,
        d.report_version DESC
), totals AS (
    SELECT
        ch.channel_code,
        ch.channel_name,
        COALESCE(SUM(d.paid_amount_cent) FILTER (
            WHERE d.report_date >= %(start)s AND d.report_date < %(end)s
        ), 0) AS paid,
        COALESCE(SUM(d.paid_amount_cent) FILTER (
            WHERE %(compare_start)s::date IS NOT NULL
              AND d.report_date >= %(compare_start)s::date
              AND d.report_date < %(compare_end)s::date
        ), 0) AS compared,
        SUM(d.paid_amount_cent) AS total_paid,
        BOOL_OR(COUNT(*) FILTER (
            WHERE d.report_date >= %(start)s AND d.report_date < %(end)s
        ) > 0) OVER () AS base_covered,
        BOOL_OR(COUNT(*) FILTER (
            WHERE %(compare_start)s::date IS NOT NULL
              AND d.report_date >= %(compare_start)s::date
              AND d.report_date < %(compare_end)s::date
        ) > 0) OVER () AS comparison_covered
    FROM latest AS d
    JOIN core.channel AS ch
      ON ch.tenant_id = d.tenant_id AND ch.channel_id = d.channel_id
    GROUP BY ch.channel_code, ch.channel_name
)
SELECT
    channel_code,
    channel_name,
    paid,
    CASE WHEN %(compare_start)s::date IS NOT NULL THEN compared END AS compared,
    CASE WHEN %(compare_start)s::date IS NOT NULL THEN compared - paid END AS delta,
    base_covered,
    comparison_covered
FROM totals
WHERE total_paid <> 0
ORDER BY
    CASE
      WHEN %(compare_start)s::date IS NULL THEN paid
      ELSE ABS(compared - paid)
    END DESC,
    channel_code COLLATE "C"
LIMIT 201
"""


PRODUCT_SALES_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (
        d.tenant_id, d.report_date, d.channel_id, d.sku_id, d.region_id
    )
        d.tenant_id,
        d.report_date,
        d.channel_id,
        d.sku_id,
        d.region_id,
        d.paid_amount_cent
    FROM core.channel_product_daily AS d
    JOIN core.channel AS ch
      ON ch.tenant_id = d.tenant_id AND ch.channel_id = d.channel_id
    WHERE d.tenant_id = %(tenant)s
      AND ch.channel_code = %(channel)s
      AND (
        (d.report_date >= %(start)s AND d.report_date < %(end)s)
        OR (
          %(compare_start)s::date IS NOT NULL
          AND d.report_date >= %(compare_start)s::date
          AND d.report_date < %(compare_end)s::date
        )
      )
    ORDER BY
        d.tenant_id,
        d.report_date,
        d.channel_id,
        d.sku_id,
        d.region_id,
        d.report_version DESC
), totals AS (
    SELECT
        p.spu_code,
        p.spu_name,
        COALESCE(SUM(d.paid_amount_cent) FILTER (
            WHERE d.report_date >= %(start)s AND d.report_date < %(end)s
        ), 0) AS paid,
        COALESCE(SUM(d.paid_amount_cent) FILTER (
            WHERE %(compare_start)s::date IS NOT NULL
              AND d.report_date >= %(compare_start)s::date
              AND d.report_date < %(compare_end)s::date
        ), 0) AS compared,
        SUM(d.paid_amount_cent) AS total_paid,
        BOOL_OR(COUNT(*) FILTER (
            WHERE d.report_date >= %(start)s AND d.report_date < %(end)s
        ) > 0) OVER () AS base_covered,
        BOOL_OR(COUNT(*) FILTER (
            WHERE %(compare_start)s::date IS NOT NULL
              AND d.report_date >= %(compare_start)s::date
              AND d.report_date < %(compare_end)s::date
        ) > 0) OVER () AS comparison_covered
    FROM latest AS d
    JOIN core.sku AS k ON k.tenant_id = d.tenant_id AND k.sku_id = d.sku_id
    JOIN core.spu AS p ON p.tenant_id = k.tenant_id AND p.spu_id = k.spu_id
    GROUP BY p.spu_code, p.spu_name
)
SELECT
    spu_code,
    spu_name,
    paid,
    CASE WHEN %(compare_start)s::date IS NOT NULL THEN compared END AS compared,
    CASE WHEN %(compare_start)s::date IS NOT NULL THEN compared - paid END AS delta,
    base_covered,
    comparison_covered
FROM totals
WHERE total_paid <> 0
ORDER BY
    CASE
      WHEN %(compare_start)s::date IS NULL THEN paid
      ELSE ABS(compared - paid)
    END DESC,
    spu_code COLLATE "C"
LIMIT 201
"""


RETAIL_IDENTITY_SQL = """
SELECT
    session_user,
    current_user,
    current_setting('transaction_read_only'),
    (
        SELECT bool_and(
            row_security_active(relation::regclass)
            AND NOT has_table_privilege(relation, 'INSERT, UPDATE, DELETE, TRUNCATE')
            AND NOT has_any_column_privilege(relation, 'INSERT, UPDATE')
        )
        FROM (VALUES ('core.channel_product_daily'), ('core.channel'), ('core.sku'), ('core.spu'))
             AS required(relation)
    ),
    EXISTS (SELECT FROM core.channel WHERE tenant_id = %(tenant)s)
"""
