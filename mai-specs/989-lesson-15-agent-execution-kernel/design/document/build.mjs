import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderReview } from "../../../../.agents/skills/my-design-to-html/scripts/render.mjs";

const directory = path.dirname(fileURLToPath(import.meta.url));
const edition = JSON.parse(await fs.readFile(path.join(directory, "edition.json"), "utf8"));
const output = path.resolve(process.argv[2] ?? path.join(directory, edition.output));
const temporary = path.join(path.dirname(output), `.lesson-15-${process.pid}.html`);
const finished = temporary + ".tmp";
const sequenceTitle = "Agent、BFF 与 Chat 流式时序";
const wireTitle = "浏览器收到的完整 SSE";
const raw = await fs.readFile(path.join(directory, "run-example.sse"), "utf8");
const packets = raw.trimEnd().split("\n\n").map((frame) => {
  assert.ok(frame.startsWith("data: "));
  return JSON.parse(frame.slice(6));
});
const escape = (text) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const packetViews = packets.map(({ seq, event, ...envelope }, index) => `
  <details class="protocol-packet"${index === 0 || index === packets.length - 1 ? " open" : ""}>
    <summary><code>seq=${seq}</code> ${escape(event.type)}${Array.isArray(event.payload) ? ` · ${event.payload.length} 个事件` : ""}</summary>
    <pre tabindex="0" aria-label="第 ${seq} 包的 JSON"><code>${escape(JSON.stringify({ ...envelope, seq, event }, null, 2))}</code></pre>
  </details>`).join("");

const css = `
.course-cover > svg { display: block; width: 100%; height: auto; }
.document p:has(+ [data-title="Agent Runtime 总体 UML"]) > strong { padding: 2px 5px; border-radius: 3px; }
.document p:has(+ [data-title="Agent Runtime 总体 UML"]) > strong:first-child { background: #fff1bf; color: #513308; }
.document p:has(+ [data-title="Agent Runtime 总体 UML"]) > strong:nth-child(2) { background: #e9f2df; color: #20372a; }
#file-changes ~ .code-block pre { max-height: none; padding-inline: 16px; }
#file-changes ~ .code-block .line-num { display: none; }
.protocol-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start; margin: 24px 0 32px; }
.protocol-pair > *, .protocol-pair figure { min-width: 0; margin: 0; }
.protocol-pair .diagram-stage { padding: 14px; }
.protocol-pair .diagram-stage > svg { max-width: 100% !important; min-width: 390px; }
.protocol-panel { border: 1px solid var(--line); background: var(--surface); border-radius: 9px; overflow: hidden; }
.protocol-panel > .figure-bar { justify-content: flex-start; }
.protocol-panel p { margin: 10px 14px; color: var(--muted); font-size: 13px; }
.protocol-packet { border-top: 1px solid var(--line); }
.protocol-packet summary, .protocol-raw > summary { min-height: 44px; padding: 10px 14px; font-size: 13px; }
.protocol-packet pre { margin: 0; padding: 12px 14px; max-height: 370px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--soft); font: 12px/1.6 var(--mono); }
.protocol-packet pre > code { font: inherit; }
.protocol-raw { border-top: 1px solid var(--line); }
.protocol-raw > .code-block { margin: 0; border-radius: 0; }
@media (max-width: 900px) { .protocol-pair { grid-template-columns: minmax(0, 1fr); } }
@media print {
  .protocol-pair { display: block; }
  .protocol-pair figure { break-inside: avoid; }
  .protocol-panel { margin-top: 16px; }
  .protocol-raw { display: none; }
  .protocol-packet pre { max-height: none; white-space: pre-wrap; overflow-wrap: anywhere; }
}
`;

try {
  await renderReview(path.join(directory, "edition.json"), { output: temporary });
  let html = await fs.readFile(temporary, "utf8");
  const start = html.indexOf(`data-title="${sequenceTitle}"`);
  const sequenceStart = html.lastIndexOf("<figure", start);
  const sequenceEnd = html.indexOf("</figure>", start) + "</figure>".length;
  const rawTitle = html.indexOf(`>${wireTitle} · text`, sequenceEnd);
  const rawStart = html.lastIndexOf('<div class="code-block"', rawTitle);
  const rawClose = "</code></pre></div>";
  const rawEnd = html.indexOf(rawClose, rawTitle) + rawClose.length;
  assert.ok(sequenceStart >= 0 && sequenceStart < sequenceEnd && sequenceEnd < rawTitle);
  assert.ok(rawStart >= sequenceEnd && rawStart < rawTitle && rawTitle < rawEnd);
  const figure = html.slice(sequenceStart, sequenceEnd);
  const code = html.slice(rawStart, rawEnd);
  assert.ok(code.includes("data-copy="));
  html = html.slice(0, sequenceStart) + `
    <div class="protocol-pair">
      ${figure}
      <section class="protocol-panel" aria-label="${wireTitle}">
        <div class="figure-bar"><span class="figure-label">${wireTitle}</span></div>
        <p>共 ${packets.length} 个数据包。展开可查看完整 JSON，最后一个包之后是 EOF。</p>
        ${packetViews}
        <details class="protocol-raw">
          <summary>查看和复制原始 SSE</summary>
          ${code}
        </details>
      </section>
    </div>
  ` + html.slice(rawEnd);
  const payload = html.match(/<script type="application\/json" id="document-data">([^<]*)<\/script>/);
  assert.ok(payload, "Missing rendered document data");
  assert.ok(JSON.parse(payload[1]).codes.includes(raw), "Markdown appendix and SSE capture differ");
  const fileHeading = /<h2 id="[^"]+">6\. 文件变更清单<\/h2>/;
  assert.ok(fileHeading.test(html), "Missing file change section");
  html = html.replace(fileHeading, '<div id="file-changes" aria-hidden="true"></div>$&');
  const protocolHeading = html.match(/<h2 id="([^"]+)">5\. Agent → BFF → Chat 流式协议<\/h2>/);
  assert.ok(protocolHeading, "Missing protocol heading");
  html = html.replace(
    '<button class="search-trigger"',
    `<a class="desktop-link" href="#${protocolHeading[1]}">5. 流式协议</a><a class="desktop-link" href="#file-changes">6. 文件清单</a><button class="search-trigger"`,
  );
  const cover = /<img src="(data:image\/png;base64,[^"]+)" width="(\d+)" height="(\d+)" alt="([^"]+)" fetchpriority="high">/;
  assert.ok(cover.test(html), "Missing embedded course cover");
  html = html.replace(
    cover,
    '<svg role="img" aria-label="$4" viewBox="0 0 $2 $3" width="$2" height="$3"><image href="$1" width="$2" height="$3" /></svg>',
  );
  html = html.replace("</style>\n  </head>", `${css}</style>\n  </head>`);
  html = html.replace("从目标开始阅读", "从整体链路开始阅读");
  assert.ok(html.includes(css), "Protocol layout stylesheet was not applied");
  await fs.writeFile(finished, html);
  await fs.rename(finished, output);
} finally {
  await fs.rm(temporary, { force: true });
  await fs.rm(finished, { force: true });
}
