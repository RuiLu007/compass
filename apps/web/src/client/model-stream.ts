export class ModelStreamError extends Error {}

/** Parse POST SSE without reconnecting or accepting an unbounded partial frame. */
export async function* readModelEvents(
  stream: ReadableStream<Uint8Array>,
  signal: AbortSignal,
): AsyncGenerator<unknown> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let bytes = 0;
  const limit = 2 * 1024 * 1024;
  const abort = () => void reader.cancel().catch(() => undefined);
  signal.addEventListener("abort", abort, { once: true });
  try {
    while (true) {
      if (signal.aborted) throw new DOMException("调用已停止。", "AbortError");
      const { done, value } = await reader.read();
      if (signal.aborted) throw new DOMException("调用已停止。", "AbortError");
      if (value) {
        bytes += value.byteLength;
        if (bytes > 4 * limit) throw new ModelStreamError("返回内容超过本次调用的限制。");
      }
      buffer += decoder.decode(value, { stream: !done });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (new TextEncoder().encode(frame).byteLength > limit)
          throw new ModelStreamError("返回片段超过本次调用的限制。");
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (data) {
          try {
            yield JSON.parse(data) as unknown;
          } catch (error) {
            if (error instanceof SyntaxError) throw new ModelStreamError("返回片段格式不完整。");
            throw error;
          }
        }
      }
      if (new TextEncoder().encode(buffer).byteLength > limit)
        throw new ModelStreamError("返回片段超过本次调用的限制。");
      if (done) {
        if (buffer.trim()) throw new ModelStreamError("输出连接已中断，结果不完整。");
        break;
      }
    }
  } finally {
    signal.removeEventListener("abort", abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
