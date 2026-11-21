-- For a fresh local compass_retail_test_975 database on the isolated E2E cluster.
-- Pass -v reader_password=... to psql; the Agent connection uses this same test secret.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
    IF current_database() <> 'compass_retail_test_975' THEN
        RAISE EXCEPTION 'expected isolated Compass lesson 21 test database';
    END IF;
END $$;
CREATE ROLE compass_l21_reader LOGIN PASSWORD :'reader_password';
ALTER ROLE compass_l21_reader SET default_transaction_read_only = on;
CREATE SCHEMA core;
CREATE SCHEMA sec;
CREATE TABLE sec.grants (
    principal name,
    tenant_id smallint,
    region_id bigint,
    PRIMARY KEY (principal, tenant_id, region_id)
);
INSERT INTO sec.grants VALUES ('compass_l21_reader',101,1),('compass_l21_reader',101,2);
CREATE FUNCTION sec.allowed(t smallint, r bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (
        SELECT FROM sec.grants g
        WHERE principal=session_user AND tenant_id=t AND (r IS NULL OR region_id=r)
    )
$$;
REVOKE ALL ON FUNCTION sec.allowed(smallint,bigint) FROM PUBLIC;
GRANT USAGE ON SCHEMA core,sec TO compass_l21_reader;
GRANT EXECUTE ON FUNCTION sec.allowed(smallint,bigint) TO compass_l21_reader;
DO $$
DECLARE rel text;
BEGIN
    FOREACH rel IN ARRAY ARRAY['channel_product_daily','channel','sku','spu'] LOOP
        EXECUTE format('CREATE TABLE core.%I(tenant_id smallint,region_id bigint,amount integer,cost integer)', rel);
        EXECUTE format('INSERT INTO core.%I VALUES (101,1,120,50),(101,2,80,30),(102,9,9999,9999)', rel);
        EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', rel);
        EXECUTE format('CREATE POLICY retail_scope ON core.%I FOR SELECT USING(sec.allowed(tenant_id,region_id))', rel);
        EXECUTE format('GRANT SELECT(tenant_id,region_id,amount,cost) ON core.%I TO compass_l21_reader', rel);
    END LOOP;
END $$;
COMMIT;
