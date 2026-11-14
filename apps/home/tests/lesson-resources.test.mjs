import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { prepareLessonResources } from "../scripts/lesson-resources.mjs";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const lessons = JSON.parse(await readFile(path.join(app, "src/lib/lesson-resources.json"), "utf8"));

test("lesson 11–20 reading editions exist and are included in cache inputs", async () => {
  assert.deepEqual(
    lessons.map((lesson) => lesson.number),
    [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
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
      if (artifact.markdown) {
        const { source, href } = artifact.markdown;
        assert.match(await readFile(path.resolve(app, "../..", source), "utf8"), /^# /);
        assert.equal(href, artifact.href.replace(/\.html$/, ".md"));
        for (const task of ["build", "test"])
          assert.ok(turbo.tasks[task].inputs.includes(`$TURBO_ROOT$/${source}`));
      }
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
      source: `design/${id.toUpperCase()}.html`,
      href: `/resources/lessons/11/${id}.html`,
    }));
    artifacts[0].markdown = { source: "design/DESIGN.md", href: "/resources/lessons/11/design.md" };
    const markdown = "# Design\n\n[Appendix](APPENDIX.html#details)\n";
    const markdownSource = path.join(fixture, "reference/lessons/11/design.md");
    const markdownDestination = path.join(fixture, "public/resources/lessons/11/design.md");
    await writeFile(
      path.join(fixture, "src/lib/lesson-resources.json"),
      JSON.stringify([{ number: 11, artifacts }]),
    );
    for (const artifact of artifacts)
      await writeFile(
        path.join(fixture, "reference/lessons/11", `${artifact.id}.html`),
        `<html><head></head>${artifact.id}<a href="APPENDIX.html#details">Appendix</a></html>`,
      );
    await writeFile(markdownSource, markdown);
    await prepareLessonResources(fixture);
    for (const artifact of artifacts)
      assert.match(
        await readFile(path.join(fixture, "public", artifact.href), "utf8"),
        /href="\/resources\/lessons\/11\/appendix\.html#details"/,
      );
    assert.equal(await readFile(markdownDestination, "utf8"), markdown);
    await rm(markdownSource);
    await assert.rejects(prepareLessonResources(fixture), /Missing lesson resource: .*design\.md/);
    assert.match(
      await readFile(path.join(fixture, "public/resources/lessons/11/design.html"), "utf8"),
      /home-reader-overrides/,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});
