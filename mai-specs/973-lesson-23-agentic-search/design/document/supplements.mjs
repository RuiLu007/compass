import { mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { renderCourseEdition } from "../../../../scripts/course-design/reader.mjs";

const topics = {
  VECTOR_SEARCH: [
    "OpenAI 向量检索与华东 GMV 诊断",
    "从 12 份经营资料检索线索，再回到账本解释两周差额",
  ],
  CODE_DESIGN: ["检索循环与代码实现", "从固定 Run 来源，到核验比较表中的每个数值"],
  EVIDENCE: ["经营结论与引用校验", "工具回执怎样进入答案，读者如何重新打开原文"],
  RETAIL_CASE: ["零售数据与课堂验收", "投放复议和华东 GMV 诊断分别使用哪些资料与经营时点"],
};

export async function buildSupplements(directory) {
  const tmp = await mkdtemp(path.join(os.tmpdir(), "compass-reading-"));
  try {
    for (const [name, [title, subtitle]] of Object.entries(topics)) {
      const source = path.join(directory, name + ".md");
      const markdown = await readFile(source, "utf8");
      const links = [];
      const main = await readFile(path.join(directory, "TECH_DESIGN.md"), "utf8");
      const mainSections = [...main.matchAll(/^## (.+)$/gmu)].map(([, heading]) =>
        heading
          .toLowerCase()
          .replace(/[^\p{Letter}\p{Number}\s-]/gu, "")
          .replace(/\s+/gu, "-"),
      );
      // Package sibling publications as real links. Repository source paths are readable labels in offline editions.
      const guide = markdown
        .replace(/^# .+\n/u, "")
        .replace(/\[([^\]\n]+)\]\(([^\s)]+)\)/gu, (_, label, href) => {
          const filename = href.split("#")[0].replace(/\.md$/u, "");
          if (filename !== "TECH_DESIGN" && !Object.hasOwn(topics, filename)) return label;
          const fragment = href.split("#")[1];
          let section = "";
          if (fragment && filename === "TECH_DESIGN") {
            const index = mainSections.indexOf(fragment);
            if (index < 0) throw new Error("Unknown main chapter: " + href);
            section = "#section-" + index;
          }
          links.push(filename + ".html" + section);
          return `[${label}](#publication-${links.length - 1})`;
        });
      await writeFile(path.join(tmp, "guide.md"), guide);
      const output = path.join(tmp, "review.html");
      const config = {
        lesson: 23,
        title,
        subtitle,
        source,
        guide: "guide.md",
        output,
        brand: "Compass",
        label: "第 23 课 · 技术方案",
      };
      const file = path.join(tmp, "edition.json");
      await writeFile(file, JSON.stringify(config));
      await renderCourseEdition(file);
      let html = await readFile(output, "utf8");
      html = html.replace(/href="#publication-(\d+)"/gu, (_, i) => `href="${links[Number(i)]}"`);
      const dest = path.join(directory, name + ".html");
      const staged = dest + "." + process.pid + ".tmp";
      try {
        await writeFile(staged, html);
        await rename(staged, dest);
      } finally {
        await rm(staged, { force: true });
      }
      console.log("Rebuilt", name + ".html");
    }
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
}
