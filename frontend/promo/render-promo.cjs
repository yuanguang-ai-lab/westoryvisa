const { chromium } = require("/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const path = require("path");
const fs = require("fs");
const { pathToFileURL } = require("url");

const promoDir = __dirname;
const videoDir = path.join(promoDir, "rendered");
const finalPath = path.join(videoDir, "docflow-promo-30s.webm");
fs.mkdirSync(videoDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/Users/mac/Library/Caches/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-mac-arm64/chrome-headless-shell"
  });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    recordVideo: { dir: videoDir, size: { width: 1920, height: 1080 } }
  });
  const page = await context.newPage();
  const video = page.video();
  await page.goto(pathToFileURL(path.join(promoDir, "promo-video.html")).href, { waitUntil: "load" });
  await page.waitForFunction(() => window.__promoReady === true);
  await page.waitForFunction(() => window.__promoDone === true, null, { timeout: 40000 });
  await page.waitForTimeout(350);
  await page.close();
  await context.close();
  const recordedPath = await video.path();
  fs.copyFileSync(recordedPath, finalPath);
  if (recordedPath !== finalPath) fs.unlinkSync(recordedPath);
  await browser.close();
  console.log(finalPath);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
