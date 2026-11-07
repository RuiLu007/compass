import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

export async function prepareLessonResources(app) {
  const lessons = JSON.parse(
    await readFile(path.join(app, "src/lib/lesson-resources.json"), "utf8"),
  );
  const distribution = path.join(app, "reference/lessons");
  const files = lessons.flatMap((lesson) =>
    lesson.artifacts.map((artifact) => ({
      source: existsSync(path.join(app, "reference"))
        ? path.join(distribution, String(lesson.number), path.basename(artifact.href))
        : path.resolve(app, "../..", artifact.source),
      destination: path.join(app, "public", artifact.href),
    })),
  );
  // Check every source before refreshing the generated directory.
  for (const { source } of files) {
    if (!existsSync(source)) throw new Error(`Missing lesson resource: ${source}`);
  }
  await rm(path.join(app, "public/resources/lessons"), { recursive: true, force: true });
  for (const { source, destination } of files) {
    await mkdir(path.dirname(destination), { recursive: true });
    const html = await readFile(source, "utf8");
    await writeFile(
      destination,
      html.replace(
        "</head>",
        `<style id="home-reader-overrides">
          .topbar { display: none !important; }
          html { scroll-padding-top: 16px !important; }
          [id] { scroll-margin-top: 16px !important; }
        </style></head>`,
      ),
    );
  }
}
