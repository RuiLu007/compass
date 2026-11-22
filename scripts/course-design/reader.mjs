import { readFile, mkdtemp, rename, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

// The shared offline renderer supplies search, typography, tables, code and diagrams.
// Use the same compact heading and embedded navigation in standalone and Home editions.
const escape = (s) =>
  s
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");

export async function applyCourseReader(html, { lesson, title, subtitle, cover }) {
  const css = await readFile(new URL("reader.css", import.meta.url), "utf8");
  const chapters = [...html.matchAll(/<h2 id="(section-\d+)">([\s\S]*?)<\/h2>/gu)];
  if (!chapters.length) throw new Error("Missing course chapter headings");
  const links = chapters
    .map(([, id, heading]) => `<a href="#${id}">${heading.replace(/<[^>]*>/gu, "")}</a>`)
    .join("");
  let figure = "";
  if (cover) {
    const image = await readFile(cover.file);
    figure = `<details class="lesson-cover"><summary><img alt="${escape(cover.alt)}" src="data:image/png;base64,${image.toString("base64")}"><span class="cover-open">展开架构图</span><span class="cover-close">收起架构图</span></summary></details>`;
  }
  const heading = `<header class="lesson-heading" aria-labelledby="page-title">
    <div><p class="lesson-number">${lesson == null ? "Compass" : "第 " + lesson + " 课"} · 技术方案</p><h1 id="page-title">${escape(title)}</h1><p class="lesson-question">${escape(subtitle)}</p></div>${figure}
    <div class="lesson-controls"><details class="lesson-toc"><summary>章节目录</summary><nav aria-label="章节目录">${links}</nav></details><button class="plain-button" type="button" data-search>检索文档</button></div></header>`;
  const hero = /<section class="hero"[\s\S]*?<\/section>/u;
  if (!hero.test(html)) throw new Error("Course document requires the offline review shell");
  const behavior = `<script>(() => {
    const menu = document.querySelector(".lesson-toc");
    menu.addEventListener("click", (event) => { if (event.target.closest("a")) menu.open = false; });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
    document.addEventListener("pointerdown", (event) => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
  })()</script>`;
  return html
    .replace("</body>", () => behavior + "</body>")
    .replace(hero, () => heading)
    .replace(
      "</head>",
      () => `<!-- prettier-ignore --><style id="course-reader">${css}</style></head>`,
    )
    .replace('name="color-scheme" content="light"', 'name="color-scheme" content="light dark"');
}

// Course editions add a lesson number to the standard offline configuration.
export async function renderCourseEdition(configFile) {
  const { renderReview } =
    await import("../../.agents/skills/my-design-to-html/scripts/render.mjs");
  const config = JSON.parse(await readFile(configFile, "utf8"));
  if (config.lesson != null && (!Number.isSafeInteger(config.lesson) || config.lesson < 1))
    throw new Error("Set a positive lesson number in edition.json");
  const directory = path.dirname(path.resolve(configFile));
  const dest = path.resolve(directory, config.output);
  if (path.extname(dest) !== ".html") throw new Error("The course reading edition must be HTML");
  const scratch = await mkdtemp(path.join(os.tmpdir(), "compass-course-reader-"));
  try {
    const temporary = path.join(scratch, "reader.html");
    await renderReview(configFile, { output: temporary });
    const html = await applyCourseReader(await readFile(temporary, "utf8"), {
      lesson: config.lesson,
      title: config.title,
      subtitle: config.subtitle ?? "",
      cover: config.cover && { ...config.cover, file: path.resolve(directory, config.cover.file) },
    });
    const staged = dest + "." + process.pid + ".tmp";
    try {
      await writeFile(staged, html, { flag: "wx" });
      await rename(staged, dest);
    } finally {
      await rm(staged, { force: true });
    }
    return dest;
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  if (process.argv.length !== 3)
    throw new Error("Usage: node scripts/course-design/reader.mjs path/to/edition.json");
  console.log(await renderCourseEdition(process.argv[2]));
}
