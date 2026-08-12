import test from "node:test";
import assert from "node:assert/strict";

import {
  APP_NAME,
  MAX_IMPORT_BYTES,
  clearAllPracticeData,
  createDraft,
  deleteDraft,
  duplicateDraft,
  exportPayload,
  getDraft,
  importDraft,
  loadDrafts,
  parseImport,
  renameDraft,
  saveDraft,
  setConsent
} from "../src/storage.ts";

class MemoryStorage {
  #data = new Map();
  getItem(key) { return this.#data.has(key) ? this.#data.get(key) : null; }
  setItem(key, value) { this.#data.set(key, String(value)); }
  removeItem(key) { this.#data.delete(key); }
}

test("drafts serialize, restore and preserve dynamic arrays", () => {
  const storage = new MemoryStorage();
  const draft = createDraft("blank", storage, new Date("2026-07-14T10:00:00Z"));
  draft.data.travelCompanions = [{ surname: "Sample", givenName: "Taylor", relationship: "friend" }];
  saveDraft(draft, storage);
  const restored = getDraft(draft.id, storage);
  assert.deepEqual(restored.data.travelCompanions, draft.data.travelCompanions);
  assert.equal(loadDrafts(storage).length, 1);
});

test("draft management supports rename, duplicate and delete", () => {
  const storage = new MemoryStorage();
  const draft = createDraft("example", storage, new Date("2026-07-14T10:00:00Z"));
  renameDraft(draft.id, "Fictional classroom demo", storage);
  assert.equal(getDraft(draft.id, storage).name, "Fictional classroom demo");
  const copy = duplicateDraft(draft.id, storage);
  assert.notEqual(copy.id, draft.id);
  assert.match(copy.practiceNumber, /^PRACTICE-/);
  deleteDraft(draft.id, storage);
  assert.equal(getDraft(draft.id, storage), null);
  assert.equal(loadDrafts(storage).length, 1);
});

test("export wrapper contains explicit practice metadata and can be imported", () => {
  const sourceStorage = new MemoryStorage();
  const draft = createDraft("example", sourceStorage, new Date("2026-07-14T10:00:00Z"));
  const raw = JSON.stringify(exportPayload(draft, new Date("2026-07-14T11:00:00Z")));
  const parsed = parseImport(raw);
  assert.notEqual(parsed.id, draft.id);
  assert.match(parsed.name, /Imported/);
  assert.match(parsed.practiceNumber, /^PRACTICE-/);

  const targetStorage = new MemoryStorage();
  importDraft(raw, targetStorage);
  assert.equal(loadDrafts(targetStorage).length, 1);
});

test("imports reject malformed, foreign, executable-looking and oversized content", () => {
  assert.throws(() => parseImport("not json"), /valid JSON/);
  assert.throws(() => parseImport(JSON.stringify({ schemaVersion: 1, appName: "Another App", practiceData: {} })), /not supported/);
  assert.throws(() => parseImport(JSON.stringify({ schemaVersion: 1, appName: APP_NAME, script: "alert(1)", practiceData: {} })), /unknown/);
  assert.throws(() => parseImport("x".repeat(MAX_IMPORT_BYTES + 1)), /too large/);
});

test("clear all removes drafts and consent", () => {
  const storage = new MemoryStorage();
  createDraft("blank", storage);
  setConsent(true, storage);
  clearAllPracticeData(storage);
  assert.equal(loadDrafts(storage).length, 0);
});
