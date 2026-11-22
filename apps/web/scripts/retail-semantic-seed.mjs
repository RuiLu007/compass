import { readFile, writeFile } from "node:fs/promises";
import { connectManaged, databaseRoot, hash, literal } from "./db-common.mjs";

const root = new URL("seeds/retail-v1/", databaseRoot);
const read = (file) => readFile(new URL(file, root), "utf8");
const json = async (file) => JSON.parse(await read(file));
const tenantOf = (scope) => scope.split(".")[0];

const resultRow = {
  type: "object",
  additionalProperties: false,
  required: [
    "group_key",
    "value",
    "status",
    "detail",
    "period_start",
    "period_end",
    "as_of",
    "data_release",
    "business_timezone",
    "unit",
  ],
  properties: {
    group_key: { type: ["string", "null"] },
    value: { type: ["string", "null"], pattern: "^-?[0-9]+(\\.[0-9]+)?$" },
    status: { type: "string" },
    detail: { type: "object" },
    period_start: { type: "string", format: "date" },
    period_end: { type: "string", format: "date" },
    as_of: { type: "string", format: "date-time" },
    data_release: { type: "string" },
    business_timezone: { const: "Asia/Shanghai" },
    unit: { enum: ["CNY_fen", "order", "ratio", "piece"] },
  },
};

const commonStatuses = [
  "OK",
  "INVALID_WINDOW",
  "UNSUPPORTED_DIMENSION",
  "UNSUPPORTED_FILTER",
  "SCOPE_DENIED",
  "RELEASE_UNAVAILABLE",
  "OUTSIDE_COVERAGE",
];

function bindingResultSchema(metric) {
  const statuses = metric.binding.statuses;
  return {
    ...resultRow,
    properties: {
      ...resultRow.properties,
      status: { enum: [...commonStatuses, ...statuses] },
      unit: { const: metric.unit },
    },
    if: { properties: { status: { const: "OK" } } },
    then: { properties: { value: { type: "string" } } },
    else: { properties: { value: { type: "null" } } },
  };
}

function requestSchema({ dimensions, filters }) {
  return {
    type: "object",
    additionalProperties: false,
    required: ["start", "end", "as_of"],
    properties: {
      start: { type: "string", format: "date" },
      end: { type: "string", format: "date" },
      as_of: { type: "string", format: "date-time" },
      group_by: { enum: ["total", ...dimensions] },
      filters: {
        type: "object",
        additionalProperties: false,
        properties: Object.fromEntries(
          filters.map((key) => [
            key,
            { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER },
          ]),
        ),
      },
    },
  };
}

async function makeBinding(
  metric,
  release,
  readFragment = (name) => read(`queries/${name}.sql`),
  edition = "1",
) {
  const { dimensions, filters } = metric.definition;
  const { access, watermarks, start, end, first_snapshot, fragments } = metric.binding;
  const spec = {
    release,
    dimensions,
    filters,
    unit: metric.unit,
    access,
    watermarks,
    start,
    end,
    first_snapshot,
  };
  const parts = await Promise.all(["input", ...fragments, "result"].map(readFragment));
  const sql = [
    `WITH contract(spec) AS (VALUES (${literal(JSON.stringify(spec))}::jsonb))`,
    ...parts,
  ].join("\n");
  return {
    sql,
    target_hash: hash(sql),
    parameter_schema: requestSchema(metric.definition),
    result_schema: bindingResultSchema(metric),
    target_ref: `compass-retail/semantic-content/${edition}/${metric.code}`,
    access: {
      scope: access,
      source_release: release,
      protocol: "postgres_readonly",
      identity: "verified_session_user",
      namespace_profiles: {
        learner: ["pg_catalog", "retail_course", "pg_temp"],
        managed: ["pg_catalog", "core", "ops", "pg_temp"],
      },
      required_relations: ["tenant", ...metric.binding.sources],
      trusted_context_schema: {
        type: "object",
        additionalProperties: false,
        required: ["tenant_id", "data_release", "all_regions", "region_ids"],
        properties: {
          tenant_id: { type: "integer" },
          data_release: { const: release },
          all_regions: { type: "boolean" },
          region_ids: { type: "array", uniqueItems: true, items: { type: "integer", minimum: 1 } },
        },
      },
    },
  };
}

/** An unapproved binding candidate. Publication goes through the workspace's review and trial. */
export async function loadFulfillmentMetric() {
  const source = new URL("seeds/retail-fulfillment-v1/", databaseRoot);
  const metric = JSON.parse(await readFile(new URL("metric.json", source), "utf8"));
  const { release } = await json("edition.json");
  const readFragment = (name) =>
    name === "allocation-rate"
      ? readFile(new URL("allocation-rate.sql", source), "utf8")
      : read(`queries/${name}.sql`);
  return { metric, binding: await makeBinding(metric, release, readFragment, "fulfillment-1") };
}

/** Later approved bindings keep the same cash events and add a business-month grouping. */
export async function loadMonthlyCashBindings() {
  const { release } = await json("edition.json");
  const metrics = (await json("metrics.json")).filter((m) =>
    ["sales.paid_gmv", "sales.net_gmv"].includes(m.code),
  );
  const source = new URL("seeds/retail-fulfillment-v1/cash-groups.sql", databaseRoot);
  const fragment = (name) =>
    name === "additive" ? readFile(source, "utf8") : read(`queries/${name}.sql`);
  return Object.fromEntries(
    await Promise.all(
      metrics.map(async (metric) => {
        metric.definition.dimensions.push("month");
        return [metric.code, await makeBinding(metric, release, fragment, "cash-month-1")];
      }),
    ),
  );
}

export async function loadSemanticPack() {
  const [edition, entities, terms, metrics, installer] = await Promise.all([
    json("edition.json"),
    json("entities.json"),
    json("terms.json"),
    json("metrics.json"),
    read("install.sql"),
  ]);
  const scopes = [{ key: "platform", kind: "platform" }];
  for (const tenant of edition.tenants) {
    scopes.push({ key: tenant.key, kind: "tenant", tenant: tenant.tenant });
    for (const department of tenant.departments)
      scopes.push({
        key: `${tenant.key}.${department}`,
        kind: "department",
        tenant: tenant.tenant,
        department,
      });
  }
  const personalTenant = edition.tenants.find((t) => t.key === edition.personal.tenant);
  scopes.push({
    key: edition.personal.scope,
    kind: "personal",
    tenant: personalTenant.tenant,
    user: edition.personal.user,
  });

  const assets = [],
    relations = [],
    aliases = [],
    bindings = [];
  const kindLists = [
    ["entity", entities, "entities.json"],
    ["metric", metrics, "metrics.json"],
    ["term", terms, "terms.json"],
  ];
  for (const [kind, items, file] of kindLists) {
    for (const item of items) {
      for (const scope of item.owners ?? edition.tenants.map((t) => t.key)) {
        const code = kind === "entity" ? `entity.${item.code}` : item.code;
        const ref = `${scope}/${code}`;
        const asset = {
          ref,
          scope,
          kind,
          code,
          domain: item.domain,
          name: item.name,
          summary: item.summary,
          responsible:
            item.responsible ??
            (kind === "entity" ? `${item.domain} 数据负责人` : `${item.domain} 业务负责人`),
          grain: item.grain ?? "一项组织用语",
          unit: item.unit ?? "无",
          definition: item.definition,
          position: `${file}#${item.code}`,
        };
        assets.push(asset);
        for (const name of new Set([item.name, ...(item.aliases ?? [])]))
          aliases.push({
            ref,
            scope,
            display_alias: name,
            alias_norm: name.normalize("NFKC").trim().toLowerCase(),
          });
      }
    }
  }
  const byRef = new Map(assets.map((a) => [a.ref, a]));
  function link(from, to, scope, kind, role, rule = {}) {
    if (!byRef.has(from) || !byRef.has(to))
      throw new Error(`Unknown semantic relation: ${from} -> ${to}`);
    relations.push({ from, to, scope, kind, role, rule });
  }
  for (const entity of entities)
    for (const tenant of edition.tenants)
      for (const join of entity.definition.joins)
        link(
          `${tenant.key}/entity.${entity.code}`,
          `${tenant.key}/entity.${join.target}`,
          tenant.key,
          "entity_join",
          join.target,
          join,
        );
  for (const term of terms) {
    for (const scope of term.owners) {
      for (const code of term.targets_by_scope?.[scope] ?? term.targets) {
        const targets = assets.filter(
          (a) =>
            a.code === code && (a.scope === "platform" || tenantOf(a.scope) === tenantOf(scope)),
        );
        if (targets.length !== 1)
          throw new Error(`Expected one target for ${scope}/${term.code} -> ${code}`);
        const target = targets[0];
        const visibleScope = target.scope.includes(".") ? target.scope : scope;
        link(`${scope}/${term.code}`, target.ref, visibleScope, "denotes", code);
      }
    }
  }
  for (const metric of metrics.filter((m) => m.binding)) {
    const artifact = await makeBinding(metric, edition.release);
    for (const owner of metric.owners) {
      for (const tenant of edition.tenants.filter(
        (t) => owner === "platform" || t.key === tenantOf(owner),
      )) {
        const ref = `${owner}/${metric.code}`;
        bindings.push({
          ...artifact,
          access: { ...artifact.access, retail_tenant_id: Number(tenant.key) },
          ref,
          tenant: tenant.tenant,
          workspace: tenant.workspace,
          source: tenant.source,
        });
        for (const source of metric.binding.sources)
          link(
            ref,
            `${tenant.key}/entity.${source}`,
            owner === "platform" ? tenant.key : owner,
            "derived_from",
            source,
          );
      }
    }
  }
  const payload = {
    edition,
    scopes,
    assets,
    relations,
    aliases,
    bindings,
    installer_hash: hash(installer),
  };
  return { payload, installer, digest: hash(JSON.stringify(payload)) };
}

// The two installers publish identical asset definitions. Preserve historical source provenance.
const cleanEditionDigest = "7fd5eebbab8597b24301fd82f0f536df7a166cecc9c3b88d85cfa908699dc0e3";
const originalEditionDigest = "8d244913d333e6f9166c7a85a81d7109c6e931e59bff3979ef380f45ba87a788";
export function editionLineage(digest) {
  return digest === cleanEditionDigest ? originalEditionDigest : digest;
}

const stageInput = `CREATE TEMP TABLE seed_input(
  raw_body text NOT NULL,
  body jsonb GENERATED ALWAYS AS (raw_body::jsonb) STORED,
  digest text NOT NULL CHECK (digest=encode(sha256(convert_to(raw_body,'UTF8')),'hex')),
  compatible_digest text GENERATED ALWAYS AS (CASE WHEN digest=${literal(cleanEditionDigest)} THEN ${literal(originalEditionDigest)}::text END) STORED,
  outcome text
) ON COMMIT DROP`;

// The portable file and the managed seed run the exact same transaction body.
export async function exportRetailSemanticSql(path) {
  const { payload, installer, digest } = await loadSemanticPack();
  const sql = [
    "-- Compass Retail semantic content v1. PostgreSQL 17, web/main, explicit teaching fixture.",
    "BEGIN;",
    "SET LOCAL ROLE compass_web_owner;",
    "SET LOCAL standard_conforming_strings=on;",
    "SET LOCAL search_path=pg_catalog,pg_temp;",
    stageInput + ";",
    `INSERT INTO pg_temp.seed_input(raw_body,digest) VALUES (${literal(JSON.stringify(payload))},${literal(digest)});`,
    installer,
    "SELECT outcome,digest FROM pg_temp.seed_input;",
    "COMMIT;",
    "",
  ].join("\n");
  await writeFile(path, sql, { flag: "wx" });
  return { status: "exported", path, digest };
}

export async function seedRetailSemantics(url) {
  const { payload, installer, digest } = await loadSemanticPack();
  const client = await connectManaged(url);
  try {
    await client.query("BEGIN");
    await client.query(stageInput);
    await client.query("INSERT INTO pg_temp.seed_input(raw_body,digest) VALUES ($1,$2)", [
      JSON.stringify(payload),
      digest,
    ]);
    await client.query(installer);
    const {
      rows: [{ outcome }],
    } = await client.query("SELECT outcome FROM pg_temp.seed_input");
    await client.query("COMMIT");
    return {
      status: outcome,
      version: payload.edition.version,
      digest,
      assets: payload.assets.length,
      bindings: payload.bindings.length,
    };
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    await client.end();
  }
}
