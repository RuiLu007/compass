import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { renderReview } from "../../../../../.agents/skills/my-design-to-html/scripts/render.mjs";

const config = new URL("./edition.json", import.meta.url);
await renderReview(fileURLToPath(config));
const output = new URL("../../INTENT_TAXONOMY.html", import.meta.url);
let html = await readFile(output, "utf8");
const styles = await readFile(new URL("./filters.css", import.meta.url), "utf8");
const script = await readFile(new URL("./filters.js", import.meta.url), "utf8");
html = html.replace("</head>", `<style>${styles}</style></head>`);
html = html.replace("</body>", `<script>${script}</script></body>`);
await writeFile(output, html);
console.log(`Generated filterable intent document: ${fileURLToPath(output)}`);
