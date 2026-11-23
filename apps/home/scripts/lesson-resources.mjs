import { copyFile, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export async function prepareLessonResources(app, lessonNumber) {
  const lessons = JSON.parse(
    await readFile(path.join(app, "src/lib/lesson-resources.json"), "utf8"),
  );
  const reference = path.join(app, "reference");
  const useReference = existsSync(reference);
  const selected =
    lessonNumber === undefined ? lessons : lessons.filter((item) => item.number === lessonNumber);
  if (!selected.length) throw new Error("Unknown lesson " + lessonNumber);
  const files = selected.flatMap((lesson) => {
    const links = new Map(
      lesson.artifacts.map((item) => [
        path.posix.basename(item.source ?? item.href),
        path.posix.basename(item.href),
      ]),
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
  if (lessonNumber === undefined) {
    await rm(path.join(app, "public/resources/lessons"), { recursive: true, force: true });
  }
  for (const { source, destination, links } of files) {
    await mkdir(path.dirname(destination), { recursive: true });
    if (!links) {
      await copyFile(source, destination);
      continue;
    }
    const published = new Set([...links.keys(), ...links.values()]);
    const html = (await readFile(source, "utf8"))
      .replace(
        /<a\b[^>]*\bhref=(["'])(?:\.\/)?([^"'/?#]+\.html)(?:[?#][^"']*)?\1[^>]*>([\s\S]*?)<\/a>/gi,
        (match, _quote, target, label) => (published.has(target) ? match : label),
      )
      .replace(
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
  if (lessonNumber !== undefined) {
    const directory = path.join(app, "public/resources/lessons", String(lessonNumber));
    const destinations = new Set(files.map(({ destination }) => path.resolve(destination)));
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const filename = path.join(directory, entry.name);
      if (
        entry.isFile() &&
        /\.(html|md)$/i.test(entry.name) &&
        !destinations.has(path.resolve(filename))
      )
        await rm(filename);
    }
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const [flag, value, ...extra] = process.argv.slice(2);
  if (flag !== "--lesson" || !/^[1-9]\d*$/.test(value ?? "") || extra.length) {
    throw new Error("Usage: node apps/home/scripts/lesson-resources.mjs --lesson <number>");
  }
  await prepareLessonResources(fileURLToPath(new URL("../", import.meta.url)), Number(value));
}
