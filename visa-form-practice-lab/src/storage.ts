import { EXAMPLE_DATA } from "./example.ts";
import { generatePracticeNumber } from "./validation.ts";
import type { PracticeData, PracticeDraft } from "./types.ts";

export const STORAGE_KEY = "vfpl_drafts_v1";
export const CONSENT_KEY = "vfpl_practice_consent_v1";
export const APP_NAME = "Visa Form Practice Lab";
export const SCHEMA_VERSION = 1;
export const MAX_IMPORT_BYTES = 1_000_000;

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function storageOrThrow(storage: StorageLike = localStorage): StorageLike {
  const testKey = "vfpl_storage_test";
  storage.setItem(testKey, "1");
  storage.removeItem(testKey);
  return storage;
}

export function storageAvailable(storage: StorageLike = localStorage): boolean {
  try {
    storageOrThrow(storage);
    return true;
  } catch {
    return false;
  }
}

export function loadDrafts(storage: StorageLike = localStorage): PracticeDraft[] {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isPracticeDraft).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  } catch {
    return [];
  }
}

export function persistDrafts(drafts: PracticeDraft[], storage: StorageLike = localStorage): void {
  storageOrThrow(storage).setItem(STORAGE_KEY, JSON.stringify(drafts));
}

export function getDraft(id: string, storage: StorageLike = localStorage): PracticeDraft | null {
  return loadDrafts(storage).find((draft) => draft.id === id) || null;
}

export function createDraft(mode: "blank" | "example", storage: StorageLike = localStorage, now = new Date()): PracticeDraft {
  const iso = now.toISOString();
  const dateLabel = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  const draft: PracticeDraft = {
    id: globalThis.crypto?.randomUUID?.() || `practice-${now.getTime()}-${Math.random().toString(36).slice(2, 8)}`,
    schemaVersion: 1,
    practiceNumber: generatePracticeNumber(now),
    name: mode === "example" ? `Alex Example · ${dateLabel}` : `未命名练习 · ${dateLabel}`,
    mode,
    createdAt: iso,
    updatedAt: iso,
    currentStep: 0,
    acknowledged: true,
    data: mode === "example" ? structuredClone(EXAMPLE_DATA) : {}
  };
  saveDraft(draft, storage);
  return draft;
}

export function saveDraft(draft: PracticeDraft, storage: StorageLike = localStorage): PracticeDraft {
  const drafts = loadDrafts(storage);
  const saved = structuredClone(draft);
  saved.updatedAt = new Date().toISOString();
  const index = drafts.findIndex((item) => item.id === saved.id);
  if (index >= 0) drafts[index] = saved;
  else drafts.unshift(saved);
  persistDrafts(drafts, storage);
  return saved;
}

export function deleteDraft(id: string, storage: StorageLike = localStorage): void {
  persistDrafts(loadDrafts(storage).filter((draft) => draft.id !== id), storage);
}

export function duplicateDraft(id: string, storage: StorageLike = localStorage): PracticeDraft {
  const source = getDraft(id, storage);
  if (!source) throw new Error("Draft not found");
  const now = new Date().toISOString();
  const copy: PracticeDraft = {
    ...structuredClone(source),
    id: globalThis.crypto?.randomUUID?.() || `practice-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    practiceNumber: generatePracticeNumber(),
    name: `${source.name} · Copy`,
    createdAt: now,
    updatedAt: now
  };
  saveDraft(copy, storage);
  return copy;
}

export function renameDraft(id: string, name: string, storage: StorageLike = localStorage): PracticeDraft {
  const draft = getDraft(id, storage);
  if (!draft) throw new Error("Draft not found");
  draft.name = name.trim().slice(0, 120) || draft.name;
  return saveDraft(draft, storage);
}

export function clearAllPracticeData(storage: StorageLike = localStorage): void {
  storage.removeItem(STORAGE_KEY);
  storage.removeItem(CONSENT_KEY);
}

export function setConsent(value: boolean, storage: StorageLike = localStorage): void {
  if (value) storage.setItem(CONSENT_KEY, "accepted");
  else storage.removeItem(CONSENT_KEY);
}

export function hasConsent(storage: StorageLike = localStorage): boolean {
  return storage.getItem(CONSENT_KEY) === "accepted";
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function depth(value: unknown, current = 0): number {
  if (current > 8) return current;
  if (Array.isArray(value)) return value.reduce((max, item) => Math.max(max, depth(item, current + 1)), current);
  if (isPlainObject(value)) return Object.values(value).reduce((max, item) => Math.max(max, depth(item, current + 1)), current);
  return current;
}

export function isPracticeDraft(value: unknown): value is PracticeDraft {
  if (!isPlainObject(value)) return false;
  return value.schemaVersion === 1
    && typeof value.id === "string"
    && typeof value.practiceNumber === "string"
    && value.practiceNumber.startsWith("PRACTICE-")
    && typeof value.name === "string"
    && (value.mode === "blank" || value.mode === "example")
    && typeof value.createdAt === "string"
    && typeof value.updatedAt === "string"
    && Number.isInteger(value.currentStep)
    && value.acknowledged === true
    && isPlainObject(value.data);
}

export function exportPayload(draft: PracticeDraft, now = new Date()): Record<string, unknown> {
  return {
    schemaVersion: SCHEMA_VERSION,
    appName: APP_NAME,
    exportedAt: now.toISOString(),
    disclaimer: "PRACTICE DATA ONLY. NOT A VISA APPLICATION. DO NOT USE REAL PERSONAL INFORMATION.",
    practiceData: structuredClone(draft)
  };
}

export function parseImport(raw: string): PracticeDraft {
  if (new TextEncoder().encode(raw).byteLength > MAX_IMPORT_BYTES) throw new Error("Import file is too large");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("Import file is not valid JSON");
  }
  if (!isPlainObject(parsed) || depth(parsed) > 8) throw new Error("Import structure is invalid or too deeply nested");
  const allowedKeys = new Set(["schemaVersion", "appName", "exportedAt", "disclaimer", "practiceData"]);
  if (Object.keys(parsed).some((key) => !allowedKeys.has(key))) throw new Error("Import contains unknown top-level content");
  if (parsed.schemaVersion !== SCHEMA_VERSION || parsed.appName !== APP_NAME) throw new Error("Import schema or application name is not supported");
  if (!isPracticeDraft(parsed.practiceData)) throw new Error("Import does not contain a valid practice draft");
  const imported = structuredClone(parsed.practiceData);
  imported.id = globalThis.crypto?.randomUUID?.() || `practice-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  imported.practiceNumber = generatePracticeNumber();
  imported.name = `${imported.name} · Imported`;
  imported.createdAt = new Date().toISOString();
  imported.updatedAt = imported.createdAt;
  return imported;
}

export function importDraft(raw: string, storage: StorageLike = localStorage): PracticeDraft {
  const draft = parseImport(raw);
  saveDraft(draft, storage);
  return draft;
}

export function resetExampleDraft(draft: PracticeDraft): PracticeDraft {
  return {
    ...draft,
    mode: "example",
    currentStep: 0,
    data: structuredClone(EXAMPLE_DATA),
    updatedAt: new Date().toISOString()
  };
}
