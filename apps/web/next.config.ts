import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const config: NextConfig = {
  // Keep browser acceptance runs separate from the developer's active server.
  ...(process.env.WEB_TEST_ISOLATED_BUILD === "true"
    ? { distDir: `.next-e2e-${process.env.WEB_TEST_PORT ?? "8110"}`, devIndicators: false }
    : {}),
  output: "standalone",
  outputFileTracingRoot: root,
  turbopack: { root },
  // Start each dev session with a fresh compilation to avoid repeated full reloads.
  experimental: { turbopackFileSystemCacheForDev: false },
  poweredByHeader: false,
};
export default config;
