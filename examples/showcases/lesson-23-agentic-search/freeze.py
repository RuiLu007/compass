"""Build the classroom's isolated point-in-time database from the distributed learner SQL."""

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import subprocess
from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[3]
RELEASE = "diagnostic-v3-20261019"
K = datetime.fromisoformat("2025-10-09T09:00:00+08:00")
# Only the following columns and business keys enter the independent cluster.
# Curated metadata never includes source credentials, course_asset or instructor queries.
COLUMNS = {
    "tenant": "tenant_id tenant_name tenant_code timezone currency release_id",
    "geo_area": "tenant_id geo_id geo_name region_id",
    "channel": "tenant_id channel_id channel_name channel_code channel_type",
    "site": "tenant_id site_id site_name site_code site_type region_id opened_on",
    "spu": "tenant_id spu_id spu_name spu_code category_id launch_on",
    "sku": "tenant_id sku_id spu_id sku_code color_code size_system size_value launch_on",
    "assortment": (
        "tenant_id assortment_id region_id site_id sku_id valid_from valid_to available_at"
    ),
    "campaign": (
        "tenant_id campaign_id campaign_name target_channel_id starts_at ends_at available_at"
    ),
    "order_header": (
        "tenant_id order_id order_no channel_id region_id selling_site_id ordered_at "
        "order_date currency payable_cent available_at"
    ),
    "payment": (
        "tenant_id payment_id order_id region_id method status amount_cent attempted_at "
        "available_at"
    ),
    "refund": (
        "tenant_id refund_id order_id region_id payment_id status amount_cent "
        "attempted_at available_at"
    ),
    "web_session": (
        "tenant_id session_id channel_id region_id campaign_id started_at available_at"
    ),
    "behavior_event": (
        "tenant_id behavior_event_id session_id region_id event_type sku_id order_id "
        "event_at available_at"
    ),
    "inventory_document": (
        "tenant_id inventory_doc_id document_no doc_type from_site_id to_site_id "
        "region_id created_at planned_at available_at"
    ),
    "inventory_document_line": (
        "tenant_id inventory_doc_line_id inventory_doc_id region_id sku_id quantity available_at"
    ),
    "procurement_revision": (
        "tenant_id inventory_doc_line_id revision_no region_id notified_at revised_due_at "
        "reason_code notice_text available_at"
    ),
    "inventory_movement": (
        "tenant_id movement_id site_id region_id sku_id inventory_doc_line_id "
        "movement_type sellable_delta reserved_delta event_at available_at"
    ),
    "stock_allocation_attempt": (
        "tenant_id allocation_attempt_id allocation_group_id order_id region_id site_id "
        "sku_id outcome attempted_at available_at"
    ),
    "inventory_snapshot": (
        "tenant_id snapshot_date site_id region_id sku_id sellable_qty reserved_qty "
        "available_qty as_of available_at"
    ),
    "channel_product_daily": (
        "tenant_id report_date channel_id sku_id region_id product_views product_visitors "
        "submitted_orders paid_orders paid_amount_cent report_version available_at"
    ),
    "source_watermark": "tenant_id source_system observed_at complete_through status",
    "asset_period_status": (
        "tenant_id asset_id period_start period_end observed_at status available_at"
    ),
}

# Children depend on retained parents. An unresolved optional foreign key excludes the row.
PARENTS = {
    "sku": {"spu_id": "spu"},
    "assortment": {"site_id": "site", "sku_id": "sku"},
    "order_header": {"selling_site_id": "site", "channel_id": "channel"},
    "payment": {"order_id": "order_header"},
    "refund": {"payment_id": "payment", "order_id": "order_header"},
    "web_session": {"channel_id": "channel", "campaign_id": "campaign"},
    "behavior_event": {"session_id": "web_session", "sku_id": "sku", "order_id": "order_header"},
    "inventory_document": {"to_site_id": "site"},
    "inventory_document_line": {"inventory_doc_id": "inventory_document", "sku_id": "sku"},
    "procurement_revision": {"inventory_doc_line_id": "inventory_document_line"},
    "inventory_movement": {
        "inventory_doc_line_id": "inventory_document_line",
        "sku_id": "sku",
        "site_id": "site",
    },
    "stock_allocation_attempt": {"order_id": "order_header", "site_id": "site", "sku_id": "sku"},
    "inventory_snapshot": {"site_id": "site", "sku_id": "sku"},
    "channel_product_daily": {"sku_id": "sku", "channel_id": "channel"},
}

META = {"source_watermark", "asset_period_status"}
ROLE = "classroom_east"
DB = "classroom"
INSERT = re.compile(r'^INSERT INTO "retail_course"\."(\w+)" \(([^)]+)\) VALUES$')
DIM_DATE = {"sku": "launch_on", "spu": "launch_on", "site": "opened_on"}


def schema(table):
    return "ops" if table in META else "core"


def catalog(stream):
    header = []
    for line in stream:
        if line.startswith("INSERT INTO "):
            break
        header.append(line)
    prefix = "".join(header)
    result = {}
    for table, cols in COLUMNS.items():
        ddl = re.search(r'CREATE TABLE "retail_course"\.' + table + r" \((.*?)\n\);", prefix, re.S)[
            1
        ]
        # Types are read from the checksummed release, retaining its precision and nullability.
        defs = dict(re.findall(r"^    (\w+) ([a-z][a-z0-9() ,]*?(?: NOT NULL)?),?$", ddl, re.M))
        result[table] = [(name, defs[name].rstrip(",")) for name in cols.split()]
    return result, prefix


def stage(conn, path):
    counts = dict.fromkeys(COLUMNS, 0)
    with path.open() as stream:
        types, prefix = catalog(stream)
        for name, fields in types.items():
            columns = ",".join(col + " " + typ for col, typ in fields)
            conn.execute(f"CREATE TEMP TABLE {name} ({columns}) ON COMMIT DROP")
        active = None
        with conn.cursor() as cur:
            for line in stream:
                if line.startswith("INSERT INTO "):
                    header = INSERT.match(line.rstrip())
                    active = header[1] if header and header[1] in COLUMNS else None
                    if active:
                        source = [v.strip('"') for v in header[2].split(",")]
                        indices = [source.index(col) for col, _ in types[active]]
                        names = [col for col, _ in types[active]]
                        tenant = source.index("tenant_id")
                        region = source.index("region_id") if "region_id" in source else None
                        timestamp = next(
                            (
                                source.index(c)
                                for c in ("available_at", "observed_at")
                                if c in source
                            ),
                            None,
                        )
                        business_date = (
                            source.index(DIM_DATE[active]) if active in DIM_DATE else None
                        )
                        batch = []
                    continue
                if not active:
                    continue
                row = line.rstrip()
                if not row.startswith("(") or row[-2:] not in ("),", ");"):
                    raise ValueError("Unexpected distributed SQL row in " + active)
                values = next(csv.reader([row[1:-2]], delimiter=",", quotechar="'", strict=True))
                if (
                    values[tenant] == "101"
                    and (region is None or values[region] == "1")
                    and (timestamp is None or datetime.fromisoformat(values[timestamp]) <= K)
                    and (
                        business_date is None
                        or date.fromisoformat(values[business_date]) <= K.date()
                    )
                ):
                    batch.append(tuple(None if values[i] == "NULL" else values[i] for i in indices))
                if row.endswith(";"):
                    if batch:
                        with cur.copy(
                            f"COPY pg_temp.{active} ({','.join(names)}) FROM STDIN"
                        ) as writer:
                            for record in batch:
                                writer.write_row(record)
                        counts[active] += len(batch)
                    active = None
    if not all(counts.values()):
        raise ValueError("Missing relation data: " + str(counts))
    return types, prefix


def publish(conn, path, password):
    types, prefix = stage(conn, path)
    for name in ("core", "ops", "sec", "retail_course"):
        conn.execute("CREATE SCHEMA " + name)
    conn.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT").format(
            sql.Identifier(ROLE), sql.Literal(password)
        )
    )
    conn.execute("REVOKE ALL ON DATABASE classroom FROM PUBLIC")
    conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    conn.execute("GRANT CONNECT ON DATABASE classroom TO classroom_east")
    conn.execute("GRANT USAGE ON SCHEMA core,ops,sec TO classroom_east")
    conn.execute("""CREATE FUNCTION sec.allowed(t smallint,r bigint) RETURNS boolean
       LANGUAGE sql STABLE PARALLEL SAFE SET search_path=pg_catalog AS
       $$ SELECT session_user='classroom_east' AND t=101 AND (r IS NULL OR r=1) $$""")
    conn.execute("REVOKE ALL ON FUNCTION sec.allowed(smallint,bigint) FROM PUBLIC")
    conn.execute("GRANT EXECUTE ON FUNCTION sec.allowed(smallint,bigint) TO classroom_east")
    counts = {}
    hashes = {}
    for table, fields in types.items():
        target = schema(table) + "." + table
        conditions = []
        for key, parent in PARENTS.get(table, {}).items():
            target_key = "site_id" if key in ("selling_site_id", "to_site_id") else key
            conditions.append(
                f"(a.{key} IS NULL OR EXISTS (SELECT FROM {schema(parent)}.{parent} p "
                f"WHERE p.tenant_id=a.tenant_id AND p.{target_key}=a.{key}))"
            )
        where = " AND ".join(conditions) or "TRUE"
        projection = "a.*"
        if table == "inventory_document":
            # The receiving region owns the inbound document. Expose the optional sending
            # location only if its own directory entry is within the reader's region.
            projection = ",".join(
                "(SELECT site_id FROM core.site s WHERE s.tenant_id=a.tenant_id "
                "AND s.site_id=a.from_site_id) AS from_site_id"
                if col == "from_site_id"
                else f"a.{col}"
                for col, _ in fields
            )
        conn.execute(
            f"CREATE TABLE {target} AS SELECT {projection} FROM pg_temp.{table} a WHERE {where}"
        )
        ddl = re.search(r'CREATE TABLE "retail_course"\.' + table + r" \((.*?)\n\);", prefix, re.S)[
            1
        ]
        primary_key = re.search(r"PRIMARY KEY \(([^()]*)\)", ddl)[1]
        if not set(primary_key.split(", ")) <= {col for col, _ in fields}:
            raise ValueError("Slice is missing a primary key column: " + table)
        conn.execute(f"ALTER TABLE {target} ADD PRIMARY KEY ({primary_key})")
        names = {col for col, _ in fields}
        # Index parent keys before materializing children, plus common subject/time lookups.
        for column in names:
            if (
                column.endswith("_id")
                and column not in ("tenant_id", "region_id")
                or column
                in ("observed_at", "attempted_at", "available_at", "period_start", "report_date")
            ):
                conn.execute(f"CREATE INDEX ON {target} ({column})")
        conn.execute(f"ANALYZE {target}")
        counts[table] = conn.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
        row_digest = hashlib.sha256()
        with conn.cursor().copy(
            f"COPY (SELECT * FROM {target} ORDER BY {primary_key}) TO STDOUT (FORMAT BINARY)"
        ) as rows:
            for chunk in rows:
                row_digest.update(chunk)
        hashes[table] = row_digest.hexdigest()
        rls = "region_id" if "region_id" in names else "NULL::bigint"
        conn.execute(f"ALTER TABLE {target} ENABLE ROW LEVEL SECURITY")
        conn.execute(
            f"CREATE POLICY source_scope ON {target} FOR SELECT USING(sec.allowed(tenant_id,{rls}))"
        )
        conn.execute(f"GRANT SELECT ON {target} TO classroom_east")
        for column, comment in re.findall(
            r'COMMENT ON (?:COLUMN|TABLE) "retail_course"\.'
            + table
            + r"(\.\w+)? IS ('(?:[^']|'')*');",
            prefix,
        ):
            if not column or column[1:] in names:
                conn.execute(
                    f"COMMENT ON {'COLUMN' if column else 'TABLE'} {target}{column} IS {comment}"
                )
        # Instructor SQL uses the distributed learner namespace with an independent login.
        conn.execute(f"CREATE VIEW retail_course.{table} AS SELECT * FROM {target}")
    conn.execute(
        "CREATE VIEW retail_course.dataset_release AS SELECT release_id,"
        "'compass-retail-3'::text AS schema_version FROM core.tenant"
    )
    if conn.execute("SELECT release_id FROM core.tenant").fetchall() != [(RELEASE,)]:
        raise ValueError("Unexpected source release")
    conn.execute("ALTER ROLE classroom_east SET default_transaction_read_only=on")
    conn.execute(
        "ALTER ROLE classroom_east IN DATABASE classroom "
        "SET search_path=pg_catalog,core,ops,pg_temp"
    )
    return counts, hashes


def main():
    args = argparse.ArgumentParser()
    args.add_argument(
        "output", type=Path, help="A new private directory for the isolated PostgreSQL cluster"
    )
    args.add_argument("--port", type=int, default=55473)
    opt = args.parse_args()
    target = opt.output.resolve()
    source = ROOT / "data/retail/compass-retail.sql"
    with source.open("rb") as inp:
        fingerprint = hashlib.file_digest(inp, "sha256").hexdigest()
    expected = source.with_name("compass-retail.sha256").read_text().split()[0]
    if fingerprint != expected:
        raise ValueError("Learner release checksum mismatch")
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    cluster = target / "pgdata"
    subprocess.run(
        [
            "initdb",
            "-D",
            cluster,
            "-A",
            "peer",
            "--auth-host=scram-sha-256",
            "--encoding=UTF8",
            "--locale=C",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with (cluster / "pg_hba.conf").open("w") as out:
        out.write(f"local all all peer\nhost {DB} {ROLE} 127.0.0.1/32 scram-sha-256\n")
    socket = "/tmp"
    subprocess.run(
        [
            "pg_ctl",
            "-D",
            cluster,
            "-l",
            target / "postgres.log",
            "-o",
            f"-h 127.0.0.1 -k {socket} -p {opt.port}",
            "start",
            "-w",
        ],
        check=True,
    )
    admin = dict(host=socket, port=opt.port)
    password = secrets.token_urlsafe(32)
    try:
        with psycopg.connect(dbname="postgres", autocommit=True, **admin) as conn:
            conn.execute("CREATE DATABASE classroom")
        with psycopg.connect(dbname=DB, **admin) as conn:
            counts, hashes = publish(conn, source, password)
    except BaseException:
        subprocess.run(["pg_ctl", "-D", cluster, "stop", "-m", "fast"], check=False)
        raise
    connection = dict(
        host="127.0.0.1",
        port=opt.port,
        database_name=DB,
        role=ROLE,
        password=password,
        sslmode="disable",
        engine="postgresql",
        retail_tenant_id=101,
    )
    with os.fdopen(
        os.open(target / "connection.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as out:
        json.dump(connection, out)
    manifest = dict(
        release=RELEASE,
        knownAt=K.isoformat(),
        distributionSha256=fingerprint,
        sliceVersion=2,
        sliceSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        rows=counts,
        relationSha256=hashes,
        dataSha256=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    )
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
