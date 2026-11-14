import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

export async function prepareLessonResources(app) {
  const lessons = JSON.parse(
    await readFile(path.join(app, "src/lib/lesson-resources.json"), "utf8"),
  );
  const reference = path.join(app, "reference");
  const useReference = existsSync(reference);
  const files = lessons.flatMap((lesson) => {
    const links = new Map(
      lesson.artifacts.map((item) => [path.posix.basename(item.source ?? item.href), item.href]),
    );
    return lesson.artifacts.flatMap((artifact) => {
      const editions = artifact.markdown ? [artifact, artifact.markdown] : [artifact];
      return editions.map((edition) => ({
        source: useReference
          ? path.join(reference, "lessons", String(lesson.number), path.basename(edition.href))
          : path.resolve(app, "../..", edition.source),
        destination: path.join(app, "public", edition.href),
        links: edition === artifact ? links : undefined,
      }));
    });
  });
  // Check every source before refreshing the generated directory.
  for (const { source } of files) {
    if (!existsSync(source)) throw new Error(`Missing lesson resource: ${source}`);
  }
  await rm(path.join(app, "public/resources/lessons"), { recursive: true, force: true });
  for (const { source, destination, links } of files) {
    await mkdir(path.dirname(destination), { recursive: true });
    if (!links) {
      await copyFile(source, destination);
      continue;
    }
    const html = (await readFile(source, "utf8")).replace(
      /href=(["'])(?:\.\/)?([^"'#?]+)([?#][^"']*)?\1/g,
      (match, quote, target, suffix = "") =>
        links.has(target) ? `href=${quote}${links.get(target)}${suffix}${quote}` : match,
    );
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
