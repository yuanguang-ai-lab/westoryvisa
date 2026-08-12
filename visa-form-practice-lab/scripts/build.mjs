import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stripTypeScriptTypes } from "node:module";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = join(root, "dist");

async function walk(directory) {
  const { readdir } = await import("node:fs/promises");
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(path));
    else files.push(path);
  }
  return files;
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(join(root, "index.html"), join(output, "index.html"));
await cp(join(root, "styles.css"), join(output, "styles.css"));
await cp(join(root, "screen-agent-import.html"), join(output, "screen-agent-import.html"));
await cp(join(root, "screen-agent-import.css"), join(output, "screen-agent-import.css"));

for (const source of await walk(join(root, "src"))) {
  const sourceRelative = relative(join(root, "src"), source);
  const targetRelative = extname(source) === ".ts"
    ? sourceRelative.replace(/\.ts$/, ".js")
    : sourceRelative;
  const target = join(output, "src", targetRelative);
  await mkdir(dirname(target), { recursive: true });
  if (extname(source) !== ".ts") {
    await cp(source, target);
    continue;
  }
  const code = await readFile(source, "utf8");
  const transformed = stripTypeScriptTypes(code, { mode: "transform" })
    .replaceAll(/(from\s+["'][^"']+)\.ts(["'])/g, "$1.js$2")
    .replaceAll(/(import\s*\(["'][^"']+)\.ts(["']\))/g, "$1.js$2");
  await writeFile(target, transformed, "utf8");
}

const htmlPath = join(output, "index.html");
const html = (await readFile(htmlPath, "utf8")).replace("src/app.ts", "src/app.js");
await writeFile(htmlPath, html, "utf8");

const agentHtmlPath = join(output, "screen-agent-import.html");
const agentHtml = (await readFile(agentHtmlPath, "utf8")).replace(
  "src/screen-agent-import.ts", "src/screen-agent-import.js"
);
await writeFile(agentHtmlPath, agentHtml, "utf8");
console.log(`Built static site: ${output}`);
