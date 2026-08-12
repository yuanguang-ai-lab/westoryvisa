import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { createDraft } from "../src/storage.ts";
import { stepIndexById } from "../src/config.ts";
import { renderApplication, renderHome, renderStart } from "../src/views.ts";
import { t, text } from "../src/i18n.ts";

class MemoryStorage {
  #data = new Map();
  getItem(key) { return this.#data.has(key) ? this.#data.get(key) : null; }
  setItem(key, value) { this.#data.set(key, String(value)); }
  removeItem(key) { this.#data.delete(key); }
}

test("home repeats the nonofficial identity and has no government assets", () => {
  const html = renderHome("zh");
  assert.match(html, /非官方网站/);
  assert.match(html, /不能提交真实签证申请/);
  assert.doesNotMatch(html, /\.gov\/|official seal|confirmation barcode/i);
});

test("start actions remain disabled until the controller receives consent", () => {
  const html = renderStart("zh", "blank");
  assert.match(html, /id="practiceConsent"/);
  assert.equal((html.match(/data-action="create-draft"[^>]*disabled/g) || []).length, 2);
});

test("a Yes answer exposes its dependent explanation field", () => {
  const storage = new MemoryStorage();
  const draft = createDraft("blank", storage);
  draft.data.usedOtherNames = "yes";
  const html = renderApplication(draft, stepIndexById("personal-1"), "en", {}, false);
  assert.match(html, /id="field-otherSurname"/);
  assert.match(html, /id="field-otherGivenName"/);
});

test("a No answer keeps dependent fields out of the rendered form", () => {
  const storage = new MemoryStorage();
  const draft = createDraft("blank", storage);
  draft.data.usedOtherNames = "no";
  const html = renderApplication(draft, stepIndexById("personal-1"), "en", {}, false);
  assert.doesNotMatch(html, /id="field-otherSurname"/);
});

test("validation summary links to the exact field", () => {
  const storage = new MemoryStorage();
  const draft = createDraft("blank", storage);
  const html = renderApplication(draft, stepIndexById("personal-1"), "en", { surname: "Please complete this field." }, false);
  assert.match(html, /data-focus-field="surname"/);
  assert.match(html, /href="#field-surname"/);
});

test("dynamic records expose add, delete and reorder controls", () => {
  const storage = new MemoryStorage();
  const draft = createDraft("example", storage);
  const html = renderApplication(draft, stepIndexById("companions"), "en", {}, false);
  assert.match(html, /data-action="add-item"/);
  assert.match(html, /data-action="remove-item"/);
  assert.match(html, /data-action="move-item-down"/);
});

test("print view is unmistakably a practice copy", () => {
  const storage = new MemoryStorage();
  const draft = createDraft("example", storage);
  const html = renderApplication(draft, stepIndexById("print"), "en", {}, false);
  assert.match(html, /PRACTICE COPY/);
  assert.match(html, /NOT A VISA APPLICATION/);
  assert.match(html, /UNOFFICIAL TRAINING MATERIAL/);
  assert.doesNotMatch(html, /<img[^>]+barcode|<canvas|class="confirmation-page"/i);
});

test("i18n selects requested content and falls back safely", () => {
  assert.equal(t("zh", "nav.home"), "首页");
  assert.equal(t("en", "nav.home"), "Home");
  assert.equal(t("zh", "missing.translation.key"), "key");
  assert.equal(text({ en: "English", zh: "中文" }, "zh"), "中文");
});

test("screen agent import exposes visual field acknowledgements", async () => {
  const html = await readFile(new URL("../screen-agent-import.html", import.meta.url), "utf8");
  const controller = await readFile(new URL("../src/screen-agent-import.ts", import.meta.url), "utf8");
  assert.match(html, /VISA FORM PRACTICE LAB/);
  assert.match(html, /id="agentProgress">FIELDS FILLED 0 OF 0/);
  assert.match(html, /id="lastField">WAITING FOR FIELD INPUT/);
  assert.match(controller, /FIELDS FILLED \$\{completed\} OF \$\{visibleRows\.length\}/);
  assert.match(controller, /LAST FILLED \$\{label\}/);
  assert.match(controller, /open-cowork/);
  assert.match(controller, /OpenCowork computer use ready/);
  assert.match(controller, /vfpl_agent_/);
});
