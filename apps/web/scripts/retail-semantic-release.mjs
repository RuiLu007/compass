import { readFile } from "node:fs/promises";
import { connectManaged, databaseRoot, hash } from "./db-common.mjs";
import { editionLineage, loadSemanticPack } from "./retail-semantic-seed.mjs";

async function file(name) {
  return JSON.parse(await readFile(new URL("seeds/retail-v2/" + name, databaseRoot), "utf8"));
}

export async function loadDefinitions() {
  const [{ payload, digest: baseDigest }, fields, revisions, joins] = await Promise.all([
    loadSemanticPack(),
    file("fields.json"),
    file("revisions.json"),
    file("joins.json"),
  ]);
  const assets = payload.assets.map((original) => {
    const a = structuredClone(original);
    if (a.kind === "entity") {
      a.definition.joins.push(...(joins[a.definition.table] ?? []));
      a.definition.fields = fields[a.definition.table];
      for (const key of a.definition.primary_key)
        if (!a.definition.fields?.[key])
          throw new Error("Unknown primary key: " + a.code + "/" + key);
      for (const j of a.definition.joins)
        if (
          j.local.length !== j.remote.length ||
          !j.local.every((k) => a.definition.fields[k]) ||
          !j.remote.every((k) => fields[j.target]?.[k]) ||
          !j.cardinality ||
          !j.use ||
          !j.validity
        )
          throw new Error("Incomplete relationship: " + a.code + "/" + j.target);
    } else if (a.kind === "term") {
      a.summary = revisions.terms[a.code] ?? a.summary;
      if (
        !a.definition.question ||
        !a.definition.counterexample ||
        a.summary === a.definition.question ||
        !payload.relations.some((r) => r.from === a.ref && r.kind === "denotes")
      )
        throw new Error("Incomplete term: " + a.code);
    } else {
      const { summary, ...changes } = revisions.metrics[a.code] ?? {};
      a.summary = summary ?? a.summary;
      if (revisions.lineage[a.code]) a.lineage = revisions.lineage[a.code];
      Object.assign(a.definition, changes);
      if (a.definition.readiness === "definition_only")
        Object.assign(a.definition, {
          timezone: "Asia/Shanghai",
          window: "经营日期区间 [start, end)，期末口径取 end 前一个自然日",
          known_at: "仅纳入截至查询截止时点已经可见的记录",
        });
      for (const value of [
        a.summary,
        a.responsible,
        a.grain,
        a.unit,
        a.definition.formula,
        a.definition.event_time,
        a.definition.timezone,
        a.definition.aggregation,
      ])
        if (!value?.trim()) throw new Error("Incomplete metric: " + a.code);
    }
    return a;
  });
  return {
    assets,
    baseDigest,
    edition: payload.edition,
    digest: hash(JSON.stringify({ baseDigest: editionLineage(baseDigest), assets })),
  };
}

// Only the untouched v1 teaching package is upgraded. Hand-authored versions and other
// stable entries belong to their maintainers. Executable candidates retain independent governance.
export async function publishRetailDefinitions(url) {
  const { assets, baseDigest, digest, edition } = await loadDefinitions();
  const db = await connectManaged(url);
  try {
    await db.query("BEGIN");
    await db.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [edition.locator]);
    const { rows: sources } = await db.query(
      "SELECT source_id,source_version,digest FROM web.semantic_source WHERE locator=$1 AND source_version IN ('1','2')",
      [edition.locator],
    );
    const previous = sources.find((s) => s.source_version === "1");
    if (!previous || ![baseDigest, editionLineage(baseDigest)].includes(previous.digest))
      throw new Error("Install the original retail-semantics fixture first");
    const installed = sources.find((s) => s.source_version === "2");
    if (installed) {
      if (installed.digest !== digest)
        throw new Error("Installed edition 2 differs from this package");
      await db.query("COMMIT");
      return { status: "already_installed", edition: 2, digest };
    }
    const {
      rows: [source],
    } = await db.query(
      "INSERT INTO web.semantic_source(locator,source_version,digest) VALUES($1,'2',$2) RETURNING source_id",
      [edition.locator, digest],
    );
    const { rows: rows } = await db.query(
      `SELECT a.asset_id,a.owner_scope_id,a.kind,a.code,a.lifecycle,v.version_id,s.kind owner_kind,s.tenant_id,s.department_key,
        (SELECT max(version_no) FROM web.semantic_versions WHERE asset_id=a.asset_id) latest,
        (SELECT scope_id FROM web.semantic_scopes WHERE kind=CASE WHEN s.kind='platform' THEN 'platform' ELSE 'tenant' END AND tenant_id IS NOT DISTINCT FROM s.tenant_id) partition_id
      FROM web.semantic_assets a JOIN web.semantic_versions v USING(asset_id) JOIN web.semantic_scopes s ON s.scope_id=a.owner_scope_id
      WHERE v.source_id=$1 AND v.version_no=1 ORDER BY a.asset_id FOR UPDATE OF a`,
      [previous.source_id],
    );
    if (rows.length !== assets.length) throw new Error("Incomplete original retail package");
    const byRef = new Map(assets.map((a) => [a.ref, a]));
    const partitions = new Map();
    const entityIds = new Map(
      rows.filter((r) => r.kind === "entity").map((r) => [r.tenant_id + "/" + r.code, r.asset_id]),
    );
    for (const row of rows) {
      const tenant = edition.tenants.find((t) => t.tenant === row.tenant_id);
      const scope =
        row.owner_kind === "platform"
          ? "platform"
          : row.owner_kind === "personal"
            ? edition.personal.scope
            : row.owner_kind === "department"
              ? tenant.key + "." + row.department_key
              : tenant.key;
      const asset = byRef.get(scope + "/" + row.code);
      if (!asset || row.latest !== 1 || row.lifecycle !== "draft")
        throw new Error("Definition already changed: " + row.code);
      const {
        rows: [v],
      } = await db.query(
        `INSERT INTO web.semantic_versions(asset_id,version_no,parent_version_id,usage_scope_id,editor_user_id,name,summary,grain,unit,schema_version,definition,source_id,source_position,digest)
      SELECT asset_id,2,version_id,usage_scope_id,editor_user_id,name,$2,grain,unit,schema_version,$3,$4,source_position,$5 FROM web.semantic_versions WHERE version_id=$1 RETURNING version_id`,
        [
          row.version_id,
          asset.summary,
          asset.definition,
          source.source_id,
          hash(JSON.stringify(asset)),
        ],
      );
      await copyNamesAndRelations(db, row.version_id, v.version_id);
      const newJoins = asset.kind === "entity" ? asset.definition.joins : [];
      if (newJoins.length) {
        const { rows: existing } = await db.query(
          "SELECT rule FROM web.semantic_relations WHERE from_version_id=$1 AND kind='entity_join'",
          [v.version_id],
        );
        for (const join of newJoins) {
          if (
            existing.some(
              (r) =>
                r.rule.target === join.target &&
                JSON.stringify(r.rule.local) === JSON.stringify(join.local) &&
                JSON.stringify(r.rule.remote) === JSON.stringify(join.remote),
            )
          )
            continue;
          const target = entityIds.get(row.tenant_id + "/entity." + join.target);
          await db.query(
            "INSERT INTO web.semantic_relations VALUES($1,'entity_join',$2,$3,$4,$5)",
            [
              v.version_id,
              target,
              row.owner_scope_id,
              join.target + "/" + join.local.join(","),
              join,
            ],
          );
        }
      }
      for (const code of asset.lineage ?? [])
        await db.query(
          "INSERT INTO web.semantic_relations VALUES($1,'derived_from',$2,$3,$4,'{}')",
          [
            v.version_id,
            entityIds.get(row.tenant_id + "/entity." + code),
            row.owner_scope_id,
            code,
          ],
        );
      await db.query(
        `INSERT INTO web.execution_bindings(version_id,tenant_id,workspace_id,source_ref,mode,target_ref,sql_text,target_hash,parameter_schema,result_schema,access_requirements)
         SELECT $2,tenant_id,workspace_id,source_ref,mode,target_ref,sql_text,target_hash,parameter_schema,result_schema,access_requirements FROM web.execution_bindings
         WHERE version_id=$1 AND mode='trusted_sql' AND target_ref=$3 AND validation_receipt IS NULL`,
        [row.version_id, v.version_id, edition.locator + "/1/" + row.code],
      );
      await db.query(
        "UPDATE web.semantic_assets SET lifecycle='published',open_draft_id=NULL WHERE asset_id=$1",
        [row.asset_id],
      );
      if (!partitions.has(row.partition_id)) partitions.set(row.partition_id, {});
      partitions.get(row.partition_id)[row.asset_id] = { version: v.version_id };
    }
    const releases = await publishEntries(db, partitions, new Map());
    await db.query("COMMIT");
    return { status: "published", edition: 2, assets: assets.length, releases, digest };
  } catch (e) {
    await db.query("ROLLBACK");
    throw e;
  } finally {
    await db.end();
  }
}

async function publishEntries(db, partitions, expectedVersions) {
  const releases = [];
  for (const [partition, entries] of [...partitions].sort(([a], [b]) => a.localeCompare(b))) {
    await db.query(
      "INSERT INTO web.semantic_release_head(partition_id) VALUES($1) ON CONFLICT DO NOTHING",
      [partition],
    );
    const {
      rows: [h],
    } = await db.query("SELECT * FROM web.semantic_release_head WHERE partition_id=$1 FOR UPDATE", [
      partition,
    ]);
    if (h.gray_id) throw new Error("An active rollout exists in this partition");
    const {
      rows: [base],
    } = await db.query("SELECT manifest FROM web.semantic_release WHERE release_id=$1", [
      h.stable_id,
    ]);
    const manifest = { assets: base?.manifest.assets ?? {} };
    if (
      Object.keys(entries).some((id) => manifest.assets[id]?.version !== expectedVersions.get(id))
    )
      throw new Error("Active release differs from the expected definition");
    for (const [id, entry] of Object.entries(entries))
      manifest.assets[id] = { ...manifest.assets[id], ...entry };
    const {
      rows: [r],
    } = await db.query(
      "INSERT INTO web.semantic_release(partition_id,generation,manifest,digest) VALUES($1,$2,$3,$4) RETURNING release_id",
      [partition, h.generation + 1, manifest, hash(JSON.stringify(manifest))],
    );
    await db.query(
      "UPDATE web.semantic_release_head SET generation=$2,stable_id=$3 WHERE partition_id=$1",
      [partition, h.generation + 1, r.release_id],
    );
    releases.push(r.release_id);
  }
  return releases;
}

export async function loadRetailTermRevision() {
  return JSON.parse(await readFile(new URL("seeds/retail-v3/term.json", databaseRoot), "utf8"));
}

// Only replace pristine, published teaching terms. Locally maintained definitions retain priority.
export async function publishRetailTerms(url) {
  const [base, revision] = await Promise.all([loadDefinitions(), loadRetailTermRevision()]);
  const terms = base.assets.filter((a) => a.kind === "term" && a.code === revision.code);
  if (!terms.length || !revision.summary?.trim()) throw new Error("Missing v3 term or summary");
  const digest = hash(JSON.stringify([base.digest, terms.map((a) => a.ref), revision.summary]));
  const db = await connectManaged(url);
  try {
    await db.query("BEGIN");
    await db.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [base.edition.locator]);
    const { rows } = await db.query(
      "SELECT source_id,source_version,digest FROM web.semantic_source WHERE locator=$1 AND source_version IN ('2','3')",
      [base.edition.locator],
    );
    const previous = rows.find((s) => s.source_version === "2");
    if (previous?.digest !== base.digest)
      throw new Error("Install the retail-semantics-v2 fixture first");
    const installed = rows.find((s) => s.source_version === "3");
    if (installed && installed.digest !== digest)
      throw new Error("Installed edition 3 differs from this package");
    if (installed) {
      await db.query("COMMIT");
      return { status: "already_installed", edition: 3, digest };
    }
    const {
      rows: [source],
    } = await db.query(
      "INSERT INTO web.semantic_source(locator,source_version,digest) VALUES($1,'3',$2) RETURNING source_id",
      [base.edition.locator, digest],
    );
    const { rows: originals } = await db.query(
      `SELECT a.asset_id,a.owner_scope_id,a.lifecycle,v.version_id,v.digest
       FROM web.semantic_versions v JOIN web.semantic_assets a USING(asset_id)

       WHERE v.source_id=$1 AND v.version_no=2 AND a.kind='term' AND a.code=$2
       ORDER BY a.asset_id FOR UPDATE OF a`,
      [previous.source_id, revision.code],
    );
    if (originals.length !== terms.length) throw new Error("Incomplete retail-semantics-v2 terms");
    const partitions = new Map(),
      expected = new Map();
    for (const term of originals) {
      const {
        rows: [state],
      } = await db.query(
        `SELECT (SELECT max(version_no) FROM web.semantic_versions WHERE asset_id=$1) latest,
          (SELECT r.manifest->'assets'->($1::uuid)::text->>'version' FROM web.semantic_release_head h
           JOIN web.semantic_release r ON h.stable_id=r.release_id WHERE h.partition_id=$2) active`,
        [term.asset_id, term.owner_scope_id],
      );
      if (state.latest !== 2 || term.lifecycle !== "published" || state.active !== term.version_id)
        continue;
      const {
        rows: [updated],
      } = await db.query(
        `INSERT INTO web.semantic_versions(asset_id,version_no,parent_version_id,usage_scope_id,editor_user_id,name,summary,grain,unit,schema_version,definition,source_id,source_position,digest)
        SELECT asset_id,3,version_id,usage_scope_id,editor_user_id,name,$2,grain,unit,schema_version,definition,$3,source_position,$4
        FROM web.semantic_versions WHERE version_id=$1 RETURNING version_id`,
        [
          term.version_id,
          revision.summary,
          source.source_id,
          hash(JSON.stringify([term.digest, revision.summary])),
        ],
      );
      await copyNamesAndRelations(db, term.version_id, updated.version_id);
      expected.set(term.asset_id, term.version_id);
      partitions.set(term.owner_scope_id, { [term.asset_id]: { version: updated.version_id } });
    }
    const releases = await publishEntries(db, partitions, expected);
    await db.query("COMMIT");
    return {
      status: "published",
      edition: 3,
      assets: expected.size,
      skipped: terms.length - expected.size,
      releases,
      digest,
    };
  } catch (e) {
    await db.query("ROLLBACK");
    throw e;
  } finally {
    await db.end();
  }
}

async function copyNamesAndRelations(db, from, to) {
  await db.query(
    "INSERT INTO web.semantic_aliases SELECT $2,target_asset_id,scope_id,locale,alias_norm,display_alias FROM web.semantic_aliases WHERE origin_version_id=$1",
    [from, to],
  );
  await db.query(
    "INSERT INTO web.semantic_relations SELECT $2,kind,to_asset_id,scope_id,role,rule FROM web.semantic_relations WHERE from_version_id=$1",
    [from, to],
  );
}
