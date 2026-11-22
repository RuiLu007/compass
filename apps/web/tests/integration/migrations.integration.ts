import { afterAll, beforeAll, it, expect } from "vitest";
import pg from "pg";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { seedTeaching } from "../../scripts/db-seed.mjs";
import { loadSemanticPack, seedRetailSemantics } from "../../scripts/retail-semantic-seed.mjs";
import { spawnSync } from "node:child_process";
import { provision } from "../../scripts/db-provision.mjs";
import { migrate } from "../../scripts/db-migrate.mjs";
import { loadRevision, revisions, connectManaged } from "../../scripts/db-common.mjs";
import { writeSql } from "../../scripts/db-sql.mjs";
import { manage } from "../../scripts/db-manage.mjs";
let admin: pg.Client;
let adminUrl: URL;
const databases: string[] = [];
const suffix = randomUUID().slice(0, 8);
let runtimeUrl: URL, managementUrl: URL;
beforeAll(async () => {
  const adminValue = process.env.WEB_TEST_ADMIN_URL,
    runtimeValue = process.env.WEB_TEST_DATABASE_URL,
    migrationValue = process.env.WEB_TEST_MIGRATION_DATABASE_URL;
  if (!adminValue || !runtimeValue || !migrationValue)
    throw new Error(
      "Explicit WEB_TEST_ADMIN_URL and dedicated Web test credentials required for migration acceptance.",
    );
  adminUrl = new URL(adminValue);
  runtimeUrl = new URL(runtimeValue);
  managementUrl = new URL(migrationValue);
  if (
    !/^\/compass_web_test_[a-z0-9_]+$/.test(runtimeUrl.pathname) ||
    runtimeUrl.host !== adminUrl.host ||
    managementUrl.host !== adminUrl.host
  )
    throw new Error("Test database targets must share an explicit cluster");
  admin = new pg.Client({ connectionString: adminUrl.href });
  await admin.connect();
});
afterAll(async () => {
  if (admin) {
    for (const db of databases) await admin.query(`DROP DATABASE "${db}" WITH (FORCE)`);
    await admin.end();
  }
}, 120_000);
async function target(name: string) {
  const db = `compass_web_test_${suffix}_${name}`;
  databases.push(db);
  const migration = new URL(managementUrl),
    runtime = new URL(runtimeUrl);
  migration.pathname = runtime.pathname = `/${db}`;
  process.env.WEB_PROVISION_DATABASE_URL = adminUrl.href;
  process.env.WEB_MIGRATION_DATABASE_URL = migration.href;
  process.env.WEB_DATABASE_URL = runtime.href;
  await provision();
  return migration.href;
}
async function schema(url: string) {
  const client = await connectManaged(url);
  try {
    return {
      columns: (
        await client.query(
          "SELECT table_schema,table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema IN ('web','web_meta') ORDER BY table_schema,table_name,ordinal_position",
        )
      ).rows,
      constraints: (
        await client.query(
          "SELECT n.nspname,c.relname,t.conname,pg_get_constraintdef(t.oid) AS definition FROM pg_constraint t JOIN pg_class c ON c.oid=t.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('web','web_meta') ORDER BY n.nspname,c.relname,t.conname",
        )
      ).rows,
      indexes: (
        await client.query(
          "SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname IN ('web','web_meta') ORDER BY schemaname,tablename,indexname",
        )
      ).rows,
      grants: (
        await client.query(
          "SELECT table_schema,table_name,grantee,privilege_type FROM information_schema.table_privileges WHERE table_schema IN ('web','web_meta') ORDER BY table_schema,table_name,grantee,privilege_type",
        )
      ).rows,
      columnGrants: (
        await client.query(
          "SELECT table_schema,table_name,column_name,grantee,privilege_type FROM information_schema.column_privileges WHERE table_schema IN ('web','web_meta') ORDER BY table_schema,table_name,column_name,grantee,privilege_type",
        )
      ).rows,
      ledger: (await client.query("SELECT name FROM web_meta.pgmigrations ORDER BY id")).rows,
    };
  } finally {
    await client.end();
  }
}
it("installs native and complete SQL identically, preserves data on replay, and rejects drift", async () => {
  await writeSql(true);
  const native = await target("native"),
    sql = await target("sql"),
    unknown = await target("unknown");
  expect((await Promise.all([migrate({ url: native }), migrate({ url: native })])).sort()).toEqual([
    "already_applied",
    "applied",
  ]);
  expect(await migrate({ mode: "sql", from: "empty", url: sql })).toBe("applied");
  expect(await schema(native)).toEqual(await schema(sql));
  const client = await connectManaged(native);
  try {
    const id = randomUUID();
    await client.query(
      "INSERT INTO web.app_user(id,login_name,password_hash,display_name) VALUES($1,'persist.test','fixture-only','Preserved')",
      [id],
    );
    expect(await migrate({ url: native })).toBe("already_applied");
    expect(await migrate({ mode: "sql", url: sql })).toBe("already_applied");
    expect(
      (await client.query("SELECT display_name FROM web.app_user WHERE id=$1", [id])).rows[0]
        .display_name,
    ).toBe("Preserved");
    await client.query("UPDATE web_meta.migration_source SET sha256=repeat('0',64)");
    await expect(migrate({ url: native })).rejects.toThrow(/checksum/);
    for (const id of revisions)
      await client.query("UPDATE web_meta.migration_source SET sha256=$1 WHERE revision=$2", [
        (await loadRevision(id)).sha256,
        id,
      ]);
  } finally {
    await client.end();
  }
  await expect(migrate({ url: unknown, from: "0001_web_foundation" })).rejects.toThrow(/starting/);
  const other = await connectManaged(unknown);
  try {
    await other.query("CREATE VIEW public.unexpected AS SELECT 1 AS n");
    await expect(migrate({ url: unknown })).rejects.toThrow(/Unknown/);
  } finally {
    await other.end();
  }
  await expect(migrate({ url: adminUrl.href })).rejects.toThrow(/Unregistered/);
}, 30000);

function postgresTool(tool: string, url: string, input?: Buffer): Buffer {
  const connection = new URL(url),
    container = process.env.WEB_TEST_PG_CONTAINER;
  const env = {
    PGHOST: container ? "127.0.0.1" : connection.hostname,
    PGPORT: container ? "5432" : connection.port || "5432",
    PGUSER: decodeURIComponent(connection.username),
    PGPASSWORD: decodeURIComponent(connection.password),
    PGDATABASE: connection.pathname.slice(1),
  };
  const args =
    tool === "pg_dump"
      ? ["--format=custom", "--schema=web", "--schema=web_meta"]
      : [
          "--exit-on-error",
          "--no-owner",
          "--role=compass_web_owner",
          `--dbname=${connection.pathname.slice(1)}`,
        ];
  const result = container
    ? spawnSync(
        "docker",
        [
          "exec",
          "-i",
          ...Object.entries(env).flatMap(([k, v]) => ["-e", `${k}=${v}`]),
          container,
          tool,
          ...args,
        ],
        { input, maxBuffer: 20 * 1024 * 1024 },
      )
    : spawnSync(tool, args, {
        env: { ...process.env, ...env },
        input,
        maxBuffer: 20 * 1024 * 1024,
      });
  if (result.status !== 0)
    throw new Error(
      `${tool} failed; install PostgreSQL 17 tools or set WEB_TEST_PG_CONTAINER to the dedicated test cluster container.`,
    );
  return result.stdout;
}
it("restores representative data and permissions into a new database, then invalidates restored sessions", async () => {
  const source = await target("backup"),
    destination = await target("restore");
  await migrate({ url: source });
  const created = await manage({
    action: "create-user",
    loginName: "restore.admin",
    displayName: "Restored Admin",
    platformAdmin: true,
    password: "restore-test-password",
    url: source,
  });
  const tenantId = randomUUID(),
    workspaceId = randomUUID(),
    fileId = randomUUID();
  const embedding = JSON.stringify(Array(1024).fill(0.25));
  const client = await connectManaged(source);
  try {
    await client.query(
      "INSERT INTO web.web_session(id,user_id,token_hash,device_label,expires_at) VALUES($1,$2,$3,'Restore fixture',now()+interval '1 hour')",
      [randomUUID(), created.id, Buffer.alloc(32, 1)],
    );
    await client.query(
      "INSERT INTO web.tenant(id,name,slug,timezone) VALUES($1,'Restore tenant','restore-tenant','UTC')",
      [tenantId],
    );
    await client.query(
      "INSERT INTO web.workspace(id,tenant_id,name,slug) VALUES($1,$2,'Restore space','restore-space')",
      [workspaceId, tenantId],
    );
    const base = await client.query(
      "INSERT INTO web.knowledge_base(tenant_id,workspace_id,name) VALUES($1,$2,'Restore library') RETURNING id",
      [tenantId, workspaceId],
    );
    await client.query(
      "INSERT INTO web.knowledge_file(id,base_id,name,format,original,status,source_id) VALUES($1,$2,'notes.txt','txt',$3,'ready','original')",
      [fileId, base.rows[0].id, Buffer.from("initial source")],
    );
    await client.query(
      "INSERT INTO web.knowledge_chunk(file_id,ordinal,section_id,heading,body,section_hash,enabled,embedding) VALUES($1,1,'s1','运输','edited text',$2,false,$3::public.vector)",
      [fileId, "a".repeat(64), embedding],
    );
  } finally {
    await client.end();
  }
  const dump = postgresTool("pg_dump", source);
  expect(dump.byteLength).toBeGreaterThan(1000);
  postgresTool("pg_restore", destination, dump);
  expect(await schema(destination)).toEqual(await schema(source));
  expect(await migrate({ url: destination })).toBe("already_applied");
  const restored = await connectManaged(destination);
  try {
    const saved = await restored.query(
      `SELECT convert_from(f.original,'UTF8') AS original,c.body,c.enabled,public.vector_dims(c.embedding) AS dimensions,
      c.embedding OPERATOR(public.<->) $2::public.vector AS distance FROM web.knowledge_file f JOIN web.knowledge_chunk c ON c.file_id=f.id WHERE f.id=$1`,
      [fileId, embedding],
    );
    expect(saved.rows).toEqual([
      {
        original: "initial source",
        body: "edited text",
        enabled: false,
        dimensions: 1024,
        distance: 0,
      },
    ]);

    expect(
      (await restored.query("SELECT display_name FROM web.app_user WHERE id=$1", [created.id]))
        .rows[0].display_name,
    ).toBe("Restored Admin");
    expect(
      (
        await restored.query(
          "SELECT count(*)::int AS n FROM web.web_session WHERE revoked_at IS NULL",
        )
      ).rows[0].n,
    ).toBe(1);
    await restored.query(
      "BEGIN; UPDATE web.web_session SET revoked_at=now() WHERE revoked_at IS NULL; COMMIT",
    );
    expect(
      (
        await restored.query(
          "SELECT count(*)::int AS n FROM web.web_session WHERE revoked_at IS NULL",
        )
      ).rows[0].n,
    ).toBe(0);
  } finally {
    await restored.end();
  }
}, 30000);

it("upgrades v1 through native and incremental SQL while retaining IDs and enforcing scoped slugs", async () => {
  const urls = [await target("upgrade_native"), await target("upgrade_sql")];
  for (const [index, url] of urls.entries()) {
    await migrate({ url, to: "0001_web_foundation" });
    const client = await connectManaged(url);
    try {
      await client.query(
        "INSERT INTO web.tenant(id,name,timezone) VALUES('99500000-0000-4000-8000-000000000010','远山零售','UTC'),('99500000-0000-4000-8000-000000000011','星河商贸','UTC')",
      );
      await client.query(
        "INSERT INTO web.workspace(id,tenant_id,name) VALUES('99500000-0000-4000-8000-000000000021','99500000-0000-4000-8000-000000000010','华东区域')",
      );
      await migrate({ url, from: "0001_web_foundation", mode: index ? "sql" : "native" });
      expect(
        (await client.query("SELECT slug FROM web.tenant ORDER BY id")).rows.map((row) => row.slug),
      ).toEqual(["yuanshan", "xinghe"]);
      expect((await client.query("SELECT slug FROM web.workspace")).rows[0].slug).toBe("huadong");
      await expect(
        client.query(
          "INSERT INTO web.tenant(id,name,timezone,slug) VALUES(gen_random_uuid(),'Duplicate','UTC','yuanshan')",
        ),
      ).rejects.toMatchObject({ code: "23505" });
      await expect(
        client.query(
          "INSERT INTO web.workspace(id,tenant_id,name,slug) VALUES(gen_random_uuid(),'99500000-0000-4000-8000-000000000010','Duplicate','huadong')",
        ),
      ).rejects.toMatchObject({ code: "23505" });
      await client.query(
        "INSERT INTO web.workspace(id,tenant_id,name,slug) VALUES(gen_random_uuid(),'99500000-0000-4000-8000-000000000011','Other tenant','huadong')",
      );
      await expect(
        client.query("UPDATE web.tenant SET slug='../invalid' WHERE slug='xinghe'"),
      ).rejects.toMatchObject({ code: "23514" });
    } finally {
      await client.end();
    }
  }
  expect(await schema(urls[0])).toEqual(await schema(urls[1]));
}, 30000);

it("upgrades v2 permission constraints without changing grants and keeps all whitelist checks", async () => {
  const urls = [await target("models_native"), await target("models_sql")];
  for (const [index, url] of urls.entries()) {
    await migrate({ url, to: "0002_readable_routes" });
    const client = await connectManaged(url);
    const user = randomUUID(),
      tenant = randomUUID(),
      workspace = randomUUID(),
      member = randomUUID(),
      binding = randomUUID();
    try {
      await client.query(
        "INSERT INTO web.app_user(id,login_name,password_hash,display_name) VALUES($1,'model.grantee','fixture','Grantee')",
        [user],
      );
      await client.query(
        "INSERT INTO web.tenant(id,name,timezone) VALUES($1,'Model tenant','UTC')",
        [tenant],
      );
      await client.query(
        "INSERT INTO web.workspace(id,tenant_id,name) VALUES($1,$2,'Model space')",
        [workspace, tenant],
      );
      await client.query(
        "INSERT INTO web.tenant_member(id,tenant_id,user_id,role_key,permission_keys) VALUES($1,$2,$3,'member',ARRAY['module.analysis'])",
        [member, tenant, user],
      );
      await client.query(
        "INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,role_key,permission_keys) VALUES($1,$2,$3,$4,'analyst',ARRAY['module.analysis'])",
        [binding, tenant, workspace, member],
      );
      await migrate({ url, from: "0002_readable_routes", mode: index ? "sql" : "native" });
      for (const [table, key, max] of [
        ["tenant_member", member, 27],
        ["workspace_member", binding, 21],
      ] as const) {
        expect(
          (await client.query(`SELECT permission_keys FROM web.${table} WHERE id=$1`, [key]))
            .rows[0].permission_keys,
        ).toEqual(["module.analysis"]);
        await client.query(
          `UPDATE web.${table} SET permission_keys=ARRAY['module.analysis','models.invoke'] WHERE id=$1`,
          [key],
        );
        for (const invalid of [
          "ARRAY['future.execute']",
          "ARRAY[NULL]::text[]",
          "ARRAY[['models.invoke'],['module.analysis']]",
          `array_fill('models.invoke'::text, ARRAY[${max + 1}])`,
        ])
          await expect(
            client.query(`UPDATE web.${table} SET permission_keys=${invalid} WHERE id=$1`, [key]),
          ).rejects.toMatchObject({ code: "23514" });
      }
      await expect(
        client.query("UPDATE web.tenant_member SET role_key='admin' WHERE id=$1", [member]),
      ).rejects.toMatchObject({ code: "23514" });
      expect(await migrate({ url })).toBe("already_applied");
    } finally {
      await client.end();
    }
  }
  expect(await schema(urls[0])).toEqual(await schema(urls[1]));
}, 30000);

it("installs a single retail edition after v3, preserves scope and refuses content drift", async () => {
  const url = await target("semantic");
  await migrate({ url, to: "0003_model_invocation_permission" });
  await seedTeaching("isolated-semantic-fixture", url);
  await migrate({ url, mode: "sql" });
  const incomplete = await connectManaged(url);
  try {
    await incomplete.query("UPDATE web.workspace SET id=gen_random_uuid() WHERE slug='dianshang'");
    await expect(seedRetailSemantics(url)).rejects.toMatchObject({ code: "23503" });
    expect(
      (await incomplete.query("SELECT count(*)::int AS n FROM web.semantic_assets")).rows[0].n,
    ).toBe(0);
    expect(
      (await incomplete.query("SELECT count(*)::int AS n FROM web.semantic_source")).rows[0].n,
    ).toBe(0);
    await incomplete.query(
      "UPDATE web.workspace SET id='99500000-0000-4000-8000-000000000022' WHERE slug='dianshang'",
    );
  } finally {
    await incomplete.end();
  }
  const installs = await Promise.all([seedRetailSemantics(url), seedRetailSemantics(url)]);
  expect(installs.map((item) => item.status).sort()).toEqual(["already_installed", "installed"]);
  const client = await connectManaged(url);
  try {
    const query = (sql: string) => client.query(sql).then((r) => r.rows);
    expect(
      await query(
        "SELECT to_regclass('web.semantic_policy') AS policy, to_regclass('web.semantic_feedback') AS feedback",
      ),
    ).toEqual([{ policy: null, feedback: null }]);
    // The independently distributed SQL must agree with the repository edition already installed.
    await client.query(
      await readFile(new URL("../../db/main/seeds/retail-v1.sql", import.meta.url), "utf8"),
    );

    expect(await query("SELECT DISTINCT lifecycle FROM web.semantic_assets")).toEqual([
      { lifecycle: "draft" },
    ]);
    expect(
      await query(
        "SELECT count(*)::int AS n FROM web.execution_bindings WHERE validation_receipt IS NOT NULL",
      ),
    ).toEqual([{ n: 0 }]);
    expect(
      await query(`SELECT count(DISTINCT a.asset_id)::int AS definitions,count(*)::int AS bindings
        FROM web.semantic_assets a JOIN web.semantic_versions v USING(asset_id)
        JOIN web.execution_bindings b USING(version_id) WHERE a.code='sales.order_count'`),
    ).toEqual([{ definitions: 1, bindings: 2 }]);
    expect(
      await query(
        "SELECT has_table_privilege('compass_web_runtime','web.execution_bindings','UPDATE') AS writable",
      ),
    ).toEqual([{ writable: false }]);
    await client.query("UPDATE web.semantic_source SET digest=repeat('0',64)");
    await expect(seedRetailSemantics(url)).rejects.toThrow(/differs/);
    expect(await query("SELECT count(*)::int AS n FROM web.semantic_source")).toEqual([{ n: 1 }]);
  } finally {
    await client.end();
  }
});

it("adds both full-text indexes to v5 with native and incremental SQL, preserving grants", async () => {
  const urls = [await target("search_native"), await target("search_sql")];
  for (const [index, url] of urls.entries()) {
    await migrate({ url, to: "0005_semantic_governance" });
    const before = await schema(url);
    await migrate({
      url,
      from: "0005_semantic_governance",
      to: "0006_semantic_search",
      mode: index ? "sql" : "native",
    });
    const after = await schema(url);
    expect(after.grants).toEqual(before.grants);
    expect(after.columnGrants).toEqual(before.columnGrants);
    expect(
      after.indexes
        .filter((entry) => !before.indexes.some((old) => old.indexname === entry.indexname))
        .map((entry) => entry.indexname)
        .sort(),
    ).toEqual(["semantic_alias_search", "semantic_version_search"]);
    expect(await migrate({ url, to: "0006_semantic_search" })).toBe("already_applied");
  }
  expect(await schema(urls[0])).toEqual(await schema(urls[1]));
}, 30000);

it("keeps cash, posting, attribution revisions and period-end stock at their source grains", async () => {
  const url = await target("metric_edges");
  const client = await connectManaged(url);
  const { payload } = await loadSemanticPack();
  try {
    await client.query("BEGIN");
    await client.query("SET LOCAL search_path=pg_temp,pg_catalog");
    await client.query(
      await readFile(new URL("./fixtures/semantic-events.sql", import.meta.url), "utf8"),
    );
    async function value(
      code: string,
      start: string,
      end: string,
      at: string,
      options: Record<string, unknown> = {},
    ) {
      const binding = payload.bindings.find(
        (b) => b.source === "retail:101" && b.ref.endsWith("/" + code),
      );
      if (!binding) throw new Error("Missing fixture binding " + code);
      const output = await client.query(binding.sql, [
        { start, end, as_of: at, ...options },
        {
          tenant_id: 101,
          data_release: "diagnostic-v3-20261019",
          all_regions: true,
          region_ids: [],
        },
      ]);
      const row = output.rows[0];
      return [row.value === null ? null : Number(row.value), row.status];
    }
    const close = "2025-12-01T00:00:00+08:00";
    expect(await value("sales.paid_gmv", "2025-10-01", "2025-11-01", close)).toEqual([10000, "OK"]);
    expect(
      await value("sales.order_count", "2025-10-01", "2025-11-01", close, {
        filters: { category: 8 },
      }),
    ).toEqual([1, "OK"]);
    expect(await value("sales.net_gmv", "2025-11-01", "2025-12-01", close)).toEqual([-2000, "OK"]);
    expect(await value("finance.recognized_revenue", "2025-11-01", "2025-12-01", close)).toEqual([
      8000,
      "OK",
    ]);
    expect(await value("sales.paid_gmv", "2025-11-01", "2025-12-01", close)).toEqual([0, "OK"]);
    expect(
      await value("sales.net_gmv", "2025-11-01", "2025-12-01", "2025-11-04T10:00:00+08:00"),
    ).toEqual([null, "COVERAGE_UNKNOWN"]);
    const roas = (at: string, end = "2025-06-02") =>
      value("marketing.net_roas", "2025-06-01", end, at);
    expect(await roas("2025-06-09T00:00:00+08:00")).toEqual([null, "ZERO_SPEND"]);
    expect(await roas("2025-06-10T00:00:00+08:00")).toEqual([3, "OK"]);
    expect(await roas("2025-06-10T00:00:00+08:00", "2025-06-03")).toEqual([
      null,
      "SPEND_INCOMPLETE",
    ]);
    expect(await roas("2025-07-01T00:00:00+08:00")).toEqual([null, "ATTRIBUTION_UNKNOWN"]);
    expect(
      await value("inventory.closing_available_qty", "2025-10-01", "2025-10-03", close, {
        filters: { sku: 11 },
      }),
    ).toEqual([5, "OK"]);
    expect(
      await value("inventory.closing_available_qty", "2025-10-01", "2025-10-03", close),
    ).toEqual([null, "SNAPSHOT_INCOMPLETE"]);
  } finally {
    await client.query("ROLLBACK");
    await client.end();
  }
});

it("backfills missing demo admin memberships while preserving an explicit suspension", async () => {
  const url = await target("demo_admin");
  await migrate({ url });
  await seedTeaching("isolated-teaching-fixture", url);
  const client = await connectManaged(url);
  try {
    const { rows } = await client.query(
      "SELECT m.id,m.role_key FROM web.tenant_member m JOIN web.app_user u ON m.user_id=u.id WHERE u.login_name='teaching.platform' ORDER BY m.tenant_id",
    );
    expect(rows.map((row) => row.role_key)).toEqual(["admin", "admin"]);
    await client.query("DELETE FROM web.tenant_member WHERE id=$1", [rows[0].id]);
    await client.query("UPDATE web.tenant_member SET status='disabled' WHERE id=$1", [rows[1].id]);
    expect(await seedTeaching(undefined, url)).toMatchObject({ adminMembershipsAdded: 1 });
    expect(await seedTeaching(undefined, url)).toMatchObject({ adminMembershipsAdded: 0 });
    expect(
      (await client.query("SELECT status FROM web.tenant_member WHERE id=$1", [rows[1].id])).rows[0]
        .status,
    ).toBe("disabled");
  } finally {
    await client.end();
  }
});

it.each(["native", "sql"])(
  "removes retired tables after resolving restricted releases (%s)",
  async (mode) => {
    const url = await target("cleanup_" + mode);
    await migrate({ url, to: "0006_semantic_search" });
    await seedTeaching("isolated-cleanup-fixture", url);
    await seedRetailSemantics(url);
    const client = await connectManaged(url);
    try {
      const {
        rows: [v],
      } = await client.query(
        "SELECT owner_scope_id, source_id, version_id, editor_user_id FROM web.semantic_assets JOIN web.semantic_versions USING(asset_id) LIMIT 1",
      );
      await client.query(
        "INSERT INTO web.semantic_policy(scope_id,revision,source_id,policy) VALUES ($1,1,$2,$3)",
        [v.owner_scope_id, v.source_id, { disabled_capabilities: ["query"] }],
      );
      const {
        rows: [r],
      } = await client.query(
        "INSERT INTO web.semantic_release(partition_id,generation,manifest,digest) VALUES ($1,1,$2,repeat('a',64)) RETURNING release_id",
        [v.owner_scope_id, { assets: {}, policies: { [v.owner_scope_id]: 1 } }],
      );
      await client.query(
        "INSERT INTO web.semantic_feedback(workspace_id,version_id,actor_id,category,note) SELECT id,$1,$2,'definition','sample' FROM web.workspace LIMIT 1",
        [v.version_id, v.editor_user_id],
      );
      await expect(migrate({ url, mode, from: "0006_semantic_search" })).rejects.toThrow();
      expect(
        (await client.query("SELECT count(*)::int AS n FROM web.semantic_feedback")).rows[0].n,
      ).toBe(1);
      await client.query(
        "INSERT INTO web.semantic_revocation(release_id,actor_id,reason) VALUES($1,$2,'retired')",
        [r.release_id, v.editor_user_id],
      );
      expect(await migrate({ url, mode, from: "0006_semantic_search" })).toBe("applied");
      expect(
        (
          await client.query(
            "SELECT to_regclass('web.semantic_policy') AS policy,to_regclass('web.semantic_feedback') AS feedback",
          )
        ).rows[0],
      ).toEqual({ policy: null, feedback: null });
      expect(
        (await client.query("SELECT count(*)::int AS n FROM web.semantic_assets")).rows[0].n,
      ).toBe(121);
    } finally {
      await client.end();
    }
  },
  30000,
);
