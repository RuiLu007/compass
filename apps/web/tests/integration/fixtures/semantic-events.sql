-- Session-local test data for the cross-month example and report/snapshot boundaries.
CREATE TEMP TABLE tenant(tenant_id smallint,release_id text);
INSERT INTO tenant VALUES (101,'diagnostic-v3-20261019');
CREATE TEMP TABLE source_watermark(tenant_id smallint,source_system text,observed_at timestamptz,complete_through timestamptz,status text);
INSERT INTO source_watermark SELECT 101,s,'2025-12-01 00:00+08','2025-12-01 00:00+08','COMPLETE' FROM unnest(ARRAY['OMS','ERP']) s;
CREATE TEMP TABLE order_header(tenant_id smallint,order_id int,region_id int,channel_id int,selling_site_id int,ordered_at timestamptz,available_at timestamptz);
INSERT INTO order_header VALUES(101,7,1,3,NULL,'2025-10-31 12:00+08','2025-10-31 12:01+08');
CREATE TEMP TABLE sku(tenant_id smallint,sku_id int,spu_id int);
CREATE TEMP TABLE spu(tenant_id smallint,spu_id int,category_id int);
INSERT INTO sku VALUES(101,11,1),(101,12,1);
INSERT INTO spu VALUES(101,1,8);
CREATE TEMP TABLE order_line(tenant_id smallint,order_id int,region_id int,channel_id int,sku_id int,available_at timestamptz);
INSERT INTO order_line SELECT 101,7,1,3,s,'2025-10-31 12:01+08' FROM unnest(ARRAY[11,12,12]) s;
CREATE TEMP TABLE order_unit(tenant_id smallint,order_unit_id int,order_id int,region_id int,sku_id int,available_at timestamptz);
INSERT INTO order_unit VALUES(101,71,7,1,11,'2025-10-31 12:01+08'),(101,72,7,1,12,'2025-10-31 12:01+08'),(101,73,7,1,12,'2025-10-31 12:01+08');
CREATE TEMP TABLE payment(tenant_id smallint,payment_id int,order_id int,region_id int,status text,amount_cent bigint,attempted_at timestamptz,available_at timestamptz);
INSERT INTO payment VALUES(101,1,7,1,'FAILED',10000,'2025-10-31 12:02+08','2025-10-31 12:05+08'),
                         (101,2,7,1,'SUCCEEDED',10000,'2025-10-31 12:03+08','2025-10-31 12:05+08');
CREATE TEMP TABLE refund (LIKE payment);
INSERT INTO refund VALUES(101,2,7,1,'SUCCEEDED',800,'2025-11-04 09:00+08','2025-11-04 09:05+08'),
                        (101,2,7,1,'SUCCEEDED',1200,'2025-11-04 10:00+08','2025-11-04 10:05+08');
CREATE TEMP TABLE revenue_entry(tenant_id smallint,order_unit_id int,region_id int,entry_type text,amount_cent bigint,recognized_at timestamptz,available_at timestamptz);
INSERT INTO revenue_entry VALUES(101,71,1,'DELIVERY',8000,'2025-11-01 15:00+08','2025-11-01 16:00+08'),
 (101,72,1,'DELIVERY',1000,'2025-11-01 15:00+08','2025-11-01 16:00+08'),
 (101,73,1,'DELIVERY',1000,'2025-11-01 15:00+08','2025-11-01 16:00+08'),
 (101,71,1,'RETURN_ACCEPTED',-2000,'2025-11-03 15:00+08','2025-11-03 16:00+08');
CREATE TEMP TABLE channel(tenant_id smallint,channel_id int,channel_type text);
CREATE TEMP TABLE campaign(tenant_id smallint,campaign_id int,target_channel_id int,starts_at timestamptz,ends_at timestamptz,available_at timestamptz);
INSERT INTO channel VALUES(101,3,'PLATFORM');
INSERT INTO campaign VALUES(101,4,3,'2025-06-01 00:00+08','2025-06-03 00:00+08','2025-05-01 00:00+08');
CREATE TEMP TABLE campaign_daily(tenant_id smallint,campaign_id int,channel_id int,report_date date,region_id int,spend_cent int,report_version int,available_at timestamptz);
INSERT INTO campaign_daily SELECT 101,4,3,'2025-06-01',region,0,1,'2025-06-02 00:00+08' FROM generate_series(1,7) region;
INSERT INTO campaign_daily VALUES(101,4,3,'2025-06-01',1,3000,2,'2025-06-10 00:00+08');
INSERT INTO campaign_daily SELECT 101,4,3,'2025-06-02',region,100,1,'2025-06-03 00:00+08' FROM generate_series(1,6) region;
CREATE TEMP TABLE campaign_attribution_daily(tenant_id smallint,campaign_id int,channel_id int,cohort_date date,attribution_model text,model_version text,report_version int,coverage_status text,attributed_paid_cent int,attributed_refund_cent int,available_at timestamptz);
INSERT INTO campaign_attribution_daily VALUES
 (101,4,3,'2025-06-01','PLATFORM_LAST_CLICK_7D','1',1,'KNOWN',10000,1000,'2025-06-09 00:00+08'),
 (101,4,3,'2025-06-01','PLATFORM_LAST_CLICK_7D','1',2,'UNKNOWN',NULL,NULL,'2025-07-01 00:00+08'),
 (101,4,3,'2025-06-02','PLATFORM_LAST_CLICK_7D','1',1,'KNOWN',4000,300,'2025-06-10 00:00+08');
CREATE TEMP TABLE assortment(tenant_id smallint,site_id int,sku_id int,region_id int,valid_from date,valid_to date,available_at timestamptz);
INSERT INTO assortment SELECT 101,1,s,1,'2025-10-01','2025-11-01','2025-09-01 00:00+08' FROM unnest(ARRAY[11,12]) s;
CREATE TEMP TABLE inventory_snapshot(tenant_id smallint,snapshot_date date,site_id int,sku_id int,region_id int,as_of timestamptz,available_at timestamptz,available_qty int,sellable_qty int,reserved_qty int);
INSERT INTO inventory_snapshot VALUES
 (101,'2025-10-01',1,11,1,'2025-10-01 23:59:59+08','2025-10-02 00:00+08',8,10,2),
 (101,'2025-10-02',1,11,1,'2025-10-02 23:59:59+08','2025-10-03 00:00+08',5,7,2),
 (101,'2025-10-02',1,12,1,'2025-10-02 12:00:00+08','2025-10-02 13:00+08',9,9,0);
