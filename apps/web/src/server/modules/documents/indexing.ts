import "server-only";
import { z } from "zod";
import { hash } from "./contracts";
import { ApplicationError, reject } from "../../platform/errors";
import { boundedJson, fetchAgent } from "../../agent-transport";
import type { TrustedModelContext } from "../models/public";

export async function parseFile(format: string, original: Buffer) {
  let text: string;
  try {
    if (format === "pdf") {
      if (!original.subarray(0, 5).equals(Buffer.from("%PDF-")))
        reject("validation_failed", "PDF 文件头无效。");
      const { getDocumentProxy, extractText } = await import("unpdf");
      const pdf = await getDocumentProxy(Uint8Array.from(original), {
        maxImageSize: 16000000,
      });
      try {
        if (pdf.numPages > 300) reject("validation_failed", "每个 PDF 最多支持 300 页。");
        const result = await extractText(pdf, { mergePages: false });
        text = result.text.join("\n\n");
      } finally {
        await pdf.loadingTask.destroy();
      }
    } else text = new TextDecoder("utf-8", { fatal: true }).decode(original);
  } catch (error) {
    if (error instanceof ApplicationError) throw error;
    reject(
      "validation_failed",
      format === "pdf" ? "无法解析 PDF，请检查加密状态与文本层。" : "请上传 UTF-8 编码的文本。",
    );
  }
  text = text.normalize("NFC").replace(/\r\n?/g, "\n").trim();
  if (!text || text.includes("\u0000"))
    reject("validation_failed", "没有可提取的正文，请上传带文本层的 PDF 或 UTF-8 文档。");
  if (text.length > 150000) reject("validation_failed", "解析正文超过 15 万字符，请拆分文件。");
  let heading = "正文",
    buffer = "";
  const chunks: { heading: string; body: string; hash: string }[] = [];
  function flush() {
    if (buffer) chunks.push({ heading, body: buffer, hash: hash(buffer) });
    buffer = "";
  }
  for (const part of text.split(
    format === "md" ? /\n\s*\n|(?=^#{1,6} +)|(?<=^#{1,6} .{1,240})\n/gm : /\n\s*\n/,
  )) {
    const paragraph = part.trim();
    if (!paragraph) continue;
    if (format === "md" && /^#{1,6} [^\n]+$/.test(paragraph)) {
      flush();
      heading = paragraph.replace(/^#+\s+/, "").slice(0, 200);
      continue;
    }
    if (buffer && buffer.length + paragraph.length > 600) flush();
    const characters = [...paragraph];
    for (let i = 0; i < characters.length; i += 900) {
      if (i) flush();
      buffer = [buffer, characters.slice(i, i + 900).join("")].filter(Boolean).join("\n\n");
    }
  }
  flush();
  if (!chunks.length || chunks.length > 256)
    reject("validation_failed", "文档需包含正文且最多 256 个分段，请检查或拆分文件。");
  return { chars: [...text].length, chunks };
}

const batch = z.object({ vectors: z.array(z.array(z.number().finite()).length(1024)) });
export async function vectors(context: TrustedModelContext, texts: string[]) {
  const result: number[][] = [];
  for (let i = 0; i < texts.length; i += 32) {
    const group = texts.slice(i, i + 32);
    const fail = () =>
      new ApplicationError("unavailable", "向量生成失败，请检查 OpenAI 连接后重试。");
    try {
      const response = await fetchAgent(
        {
          path: "/v1/models/knowledge-embeddings",
          context,
          method: "POST",
          input: { texts: group },
        },
        process.env.COMPASS_AGENT_SERVICE_TOKEN ?? "",
        AbortSignal.timeout(35000),
      );
      if (!response.ok) throw fail();
      const data = batch.parse(await boundedJson(response, fail));
      if (data.vectors.length !== group.length) throw fail();
      result.push(...data.vectors);
    } catch {
      throw fail();
    }
  }
  return result;
}
