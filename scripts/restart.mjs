import { options } from "./start.mjs";

// pnpm's built-in restart runs stop → restart → start and forwards these arguments.
// Keep this phase finite so the following start phase owns the application lifecycle.
try {
  options(process.argv.slice(2));
  console.log("Restarting Compass…");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
