import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { runPracticeFlow } from "../e2e/practice-flow.spec.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const port = Number(process.env.E2E_PORT || 4191);
const baseURL = `http://127.0.0.1:${port}`;
const chromeCandidates = [
  process.env.PLAYWRIGHT_CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium"
].filter(Boolean);

async function importPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const fallback = process.env.CODEX_PLAYWRIGHT_PATH
      || "/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/.pnpm/playwright@1.61.1/node_modules/playwright/index.mjs";
    try {
      await access(fallback);
      return await import(pathToFileURL(fallback).href);
    } catch {
      throw new Error("Playwright is unavailable. Install playwright locally or set CODEX_PLAYWRIGHT_PATH.");
    }
  }
}

async function findChrome() {
  for (const candidate of chromeCandidates) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Try the next local browser.
    }
  }
  return undefined;
}

const server = spawn(process.execPath, ["scripts/dev-server.mjs", "dist", String(port)], {
  cwd: root,
  stdio: ["ignore", "pipe", "pipe"]
});
let serverFailure = "";
server.stderr.on("data", (chunk) => { serverFailure += chunk.toString(); });

async function waitForServer() {
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(baseURL);
      if (response.ok) return;
    } catch {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 120));
    }
  }
  const detail = serverFailure.trim().split("\n").at(-1);
  throw new Error(`The local E2E server did not start in time.${detail ? ` ${detail}` : ""}`);
}

let browser;
try {
  await waitForServer();
  const { chromium } = await importPlaywright();
  browser = await chromium.launch({ headless: true, executablePath: await findChrome() });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await context.newPage();
  await runPracticeFlow(page, baseURL);
  await context.close();
  console.log("Playwright practice flow passed.");
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}
