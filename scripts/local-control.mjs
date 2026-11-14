import net from "node:net";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { mkdirSync, rmSync } from "node:fs";

export const controlPath = (root) =>
  path.join(
    tmpdir(),
    `compass-${process.getuid()}-${createHash("sha256").update(root).digest("hex").slice(0, 12)}`,
    "control.sock",
  );

export function requestStop(root) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(controlPath(root));
    let connected = false;
    let response = "";
    socket.on("data", (chunk) => {
      response += chunk;
    });
    socket.setTimeout(30000, () => socket.destroy(new Error("Timed out stopping local apps")));
    socket.once("connect", () => {
      connected = true;
      socket.end("stop\n");
    });
    socket.once("error", (error) => {
      if (!connected && ["ENOENT", "ECONNREFUSED"].includes(error.code)) resolve(false);
      else reject(error);
    });
    socket.once("end", () => {
      if (response === "stopped\n") resolve(true);
      else
        reject(
          new Error("Application shutdown was not confirmed; dependency containers remain running"),
        );
    });
  });
}

export async function listenControl(root, stop) {
  const file = controlPath(root);
  mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  // Probe stale sockets without sending a stop request to an existing supervisor.
  await new Promise((resolve, reject) => {
    const probe = net.createConnection(file);
    probe.once("connect", () => {
      probe.destroy();
      reject(new Error("Compass is already managed by pnpm start"));
    });
    probe.once("error", (error) => {
      if (["ENOENT", "ECONNREFUSED"].includes(error.code)) resolve();
      else reject(error);
    });
  });
  rmSync(file, { force: true });
  const clients = new Set();
  const server = net.createServer({ allowHalfOpen: true }, (socket) => {
    clients.add(socket);
    socket.once("close", () => clients.delete(socket));
    socket.on("error", () => {});
    let input = "";
    socket.on("data", (chunk) => {
      input += chunk;
      if (input === "stop\n") stop();
      else if (input.length > 16 || input.includes("\n")) socket.destroy();
    });
    socket.on("end", () => {
      if (input !== "stop\n") socket.end();
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(file, resolve);
  });
  return (error) => {
    for (const socket of clients) socket.end(error ? "error\n" : "stopped\n");
    server.close();
  };
}
