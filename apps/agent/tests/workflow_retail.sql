CREATE SCHEMA core;

CREATE TABLE core.channel (tenant_id int, channel_id int, channel_code text, channel_name text);
CREATE TABLE core.spu (tenant_id int, spu_id int, spu_code text, spu_name text);
CREATE TABLE core.sku (tenant_id int, sku_id int, spu_id int);
CREATE TABLE core.channel_product_daily (
    tenant_id int, report_date date, channel_id int, sku_id int,
    region_id int, report_version int, paid_amount_cent bigint
);
INSERT INTO core.channel VALUES (101, 1, 'POS', '门店'), (101, 2, 'WEB', '线上');
INSERT INTO core.spu VALUES (101, 1, 'SPU1', '样品一'), (101, 2, 'SPU2', '样品二'), (101, 3, 'SPU3', '样品三');
INSERT INTO core.sku VALUES (101, 1, 1), (101, 2, 2), (101, 3, 3);
INSERT INTO core.channel_product_daily VALUES
    (101, '2025-11-11', 1, 1, 1, 1, 45000),
    (101, '2025-11-17', 1, 1, 1, 1, 30000),
    (101, '2025-11-17', 1, 2, 1, 1, 20000),
    (101, '2025-11-17', 1, 3, 1, 1, 10000),
    (101, '2025-11-24', 1, 1, 1, 1, 9000),
    (101, '2025-11-24', 1, 2, 1, 1, 13000),
    (101, '2025-11-24', 1, 3, 1, 1, 6000),
    (101, '2025-11-17', 2, 1, 1, 1, 1100),
    (101, '2025-11-24', 2, 1, 1, 1, 2000);

ALTER TABLE core.channel ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.spu ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.sku ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.channel_product_daily ENABLE ROW LEVEL SECURITY;
CREATE POLICY channel_scope ON core.channel FOR SELECT USING (tenant_id = 101);
CREATE POLICY spu_scope ON core.spu FOR SELECT USING (tenant_id = 101);
CREATE POLICY sku_scope ON core.sku FOR SELECT USING (tenant_id = 101);
CREATE POLICY sales_scope ON core.channel_product_daily FOR SELECT USING (tenant_id = 101);
