import { readdir, readFile } from "node:fs/promises";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stripTypeScriptTypes } from "node:module";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(path));
    else if (extname(path) === ".ts") files.push(path);
  }
  return files;
}

for (const file of await walk(join(root, "src"))) {
  stripTypeScriptTypes(await readFile(file, "utf8"), { mode: "transform" });
}
console.log("TypeScript syntax check passed.");
