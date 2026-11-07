import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { prepareLessonResources } from "../scripts/lesson-resources.mjs";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const lessons = JSON.parse(await readFile(path.join(app, "src/lib/lesson-resources.json"), "utf8"));

test("lesson 11–14 reading editions exist and are included in cache inputs", async () => {
  assert.deepEqual(
    lessons.map((lesson) => lesson.number),
    [11, 12, 13, 14],
  );
  const turbo = JSON.parse(await readFile(path.join(app, "turbo.json"), "utf8"));
  for (const lesson of lessons) {
    for (const artifact of lesson.artifacts) {
      const source = await readFile(path.resolve(app, "../..", artifact.source), "utf8");
      assert.match(source, /<!doctype html>/i);
      assert.match(
        artifact.href,
        new RegExp(`^/resources/lessons/${lesson.number}/[\\w-]+\\.html$`),
      );
      for (const task of ["build", "test"])
        assert.ok(turbo.tasks[task].inputs.includes(`$TURBO_ROOT$/${artifact.source}`));
    }
  }
});

test("distribution preserves multiple artifacts per lesson and rejects missing sources", async () => {
  const fixture = await mkdtemp(path.join(tmpdir(), "compass-lessons-"));
  try {
    await mkdir(path.join(fixture, "src/lib"), { recursive: true });
    await mkdir(path.join(fixture, "reference/lessons/11"), { recursive: true });
    const artifacts = ["design", "appendix"].map((id) => ({
      id,
      href: `/resources/lessons/11/${id}.html`,
    }));
    await writeFile(
      path.join(fixture, "src/lib/lesson-resources.json"),
      JSON.stringify([{ number: 11, artifacts }]),
    );
    for (const artifact of artifacts)
      await writeFile(
        path.join(fixture, "reference/lessons/11", `${artifact.id}.html`),
        `<html>${artifact.id}</html>`,
      );
    await prepareLessonResources(fixture);
    for (const artifact of artifacts)
      assert.equal(
        await readFile(path.join(fixture, "public", artifact.href), "utf8"),
        `<html>${artifact.id}</html>`,
      );
    await rm(path.join(fixture, "reference/lessons/11/design.html"));
    await assert.rejects(prepareLessonResources(fixture), /Missing lesson resource/);
    assert.equal(
      await readFile(path.join(fixture, "public/resources/lessons/11/design.html"), "utf8"),
      "<html>design</html>",
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});
