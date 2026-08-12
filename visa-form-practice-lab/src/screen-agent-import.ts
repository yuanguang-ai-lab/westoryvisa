import { createDraft, getDraft, saveDraft } from "./storage.ts";
import type { PracticeDraft } from "./types.ts";

type ImportMapping = { targets: string[]; normalize?: (value: string) => string };

const FIELD_MAPPING: Record<string, ImportMapping> = {
  "personal.surname": { targets: ["surname"] },
  "personal.givenNames": { targets: ["givenName"] },
  "personal.dateOfBirth": { targets: ["dateOfBirth"] },
  "personal.placeOfBirth": { targets: ["birthCity"] },
  "passport.number": { targets: ["passportNumber"] },
  "passport.issueDate": { targets: ["passportIssueDate"] },
  "passport.expiration": { targets: ["passportExpiration"] },
  "travel.visaType": { targets: ["practiceVisaCategory"], normalize: normalizeVisaCategory },
  "travel.arrivalDate": { targets: ["arrivalDate"] },
  "contact.usAddress": { targets: ["tripAddress", "usContactAddress"] },
  "contact.organizationName": { targets: ["usOrganization"] },
  "contact.phone": { targets: ["usContactPhone"] },
  "work.employerName": { targets: ["employerName"] },
  "education.schoolName": { targets: ["practiceSchoolName"] },
  "education.sevisId": { targets: ["practiceSevisId"] },
  "education.programNumber": { targets: ["practiceProgramNumber"] }
};

const parameters = new URLSearchParams(window.location.search);
const jobId = parameters.get("job") || "";
const requestedFields = new Set(
  (parameters.get("fields") || "").split(",").filter((fieldId) => Object.hasOwn(FIELD_MAPPING, fieldId))
);
const rows = [...document.querySelectorAll<HTMLElement>("[data-docflow-field]")];
const counter = document.querySelector<HTMLElement>("#fieldCounter");
const status = document.querySelector<HTMLElement>("#saveStatus");
const reviewLink = document.querySelector<HTMLAnchorElement>("#reviewDraft");
const jobLabel = document.querySelector<HTMLElement>("#jobLabel");
const agentProgress = document.querySelector<HTMLElement>("#agentProgress");
const lastField = document.querySelector<HTMLElement>("#lastField");
const executorLabel = document.querySelector<HTMLElement>("#executorLabel");

let draft: PracticeDraft;
let saveTimer: number | undefined;

if (!/^(?:screen-agent|open-cowork)-[0-9a-f]{24}$/.test(jobId) || requestedFields.size === 0) {
  document.body.innerHTML = `<main class="fatal-state"><h1>无效的本地 Screen Agent 任务</h1><p>请从 DocFlow 客户档案重新启动本机练习任务。</p></main>`;
  throw new Error("Invalid local Screen Agent job manifest");
}

jobLabel!.textContent = jobId.slice(-12).toUpperCase();
if (executorLabel && jobId.startsWith("open-cowork-")) executorLabel.textContent = "OpenCowork computer use ready";
for (const row of rows) {
  const fieldId = row.dataset.docflowField || "";
  row.hidden = !requestedFields.has(fieldId);
}

try {
  draft = loadOrCreateDraft();
  hydrateInputs();
  updateProgress();
} catch {
  document.body.innerHTML = `<main class="fatal-state"><h1>浏览器本地存储不可用</h1><p>Practice Lab 无法保存 Screen Agent 的练习草稿。</p></main>`;
  throw new Error("Practice Lab localStorage is unavailable");
}

for (const row of rows.filter((item) => !item.hidden)) {
  const input = row.querySelector<HTMLInputElement>("input");
  input?.addEventListener("input", () => {
    const fieldId = row.dataset.docflowField || "";
    applyField(fieldId, input.value);
    row.classList.toggle("filled", Boolean(input.value.trim()));
    if (lastField) {
      const label = row.querySelector<HTMLElement>("strong")?.textContent?.trim() || fieldId;
      lastField.textContent = `LAST FILLED ${label}`;
    }
    updateProgress();
    scheduleSave();
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  (document.activeElement as HTMLElement | null)?.blur();
  document.body.classList.add("agent-stopped");
  const ready = document.querySelector<HTMLElement>(".agent-ready");
  if (ready) ready.innerHTML = "<span></span> Agent stopped by operator";
});

function normalizeVisaCategory(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized.startsWith("F")) return "F1";
  if (normalized.startsWith("J")) return "J1";
  if (normalized.startsWith("B")) return "B1_B2";
  return "OTHER";
}

function loadOrCreateDraft(): PracticeDraft {
  const key = `vfpl_agent_${jobId}`;
  const existingId = localStorage.getItem(key);
  const existing = existingId ? getDraft(existingId) : null;
  if (existing) return existing;

  let created = createDraft("example");
  created.name = `Screen Agent Demo · ${new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date())}`;
  created.currentStep = 13;
  for (const fieldId of requestedFields) {
    for (const target of FIELD_MAPPING[fieldId].targets) delete created.data[target];
  }
  created.data.screenAgentImport = {
    jobId,
    executor: jobId.startsWith("open-cowork-") ? "open-cowork" : "native-screen-agent",
    localPracticeOnly: true,
    importedAt: new Date().toISOString()
  };
  created = saveDraft(created);
  localStorage.setItem(key, created.id);
  return created;
}

function hydrateInputs(): void {
  for (const row of rows.filter((item) => !item.hidden)) {
    const fieldId = row.dataset.docflowField || "";
    const mapping = FIELD_MAPPING[fieldId];
    const firstValue = String(draft.data[mapping.targets[0]] || "");
    const input = row.querySelector<HTMLInputElement>("input");
    if (input) input.value = fieldId === "travel.visaType" ? displayVisaCategory(firstValue) : firstValue;
    row.classList.toggle("filled", Boolean(input?.value.trim()));
  }
  if (reviewLink) reviewLink.href = `index.html#/application/${encodeURIComponent(draft.id)}/13`;
}

function displayVisaCategory(value: string): string {
  if (value === "B1_B2") return "B1/B2";
  return value;
}

function applyField(fieldId: string, rawValue: string): void {
  const mapping = FIELD_MAPPING[fieldId];
  if (!mapping) return;
  const value = mapping.normalize ? mapping.normalize(rawValue) : rawValue.trim();
  for (const target of mapping.targets) draft.data[target] = value;
}

function scheduleSave(): void {
  if (saveTimer) window.clearTimeout(saveTimer);
  if (status) status.textContent = "正在保存本机练习字段…";
  saveTimer = window.setTimeout(() => {
    draft.data.screenAgentImport = {
      jobId,
      executor: jobId.startsWith("open-cowork-") ? "open-cowork" : "native-screen-agent",
      localPracticeOnly: true,
      importedAt: new Date().toISOString()
    };
    draft = saveDraft(draft);
    if (status) status.textContent = `已保存到当前浏览器 · ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`;
  }, 120);
}

function updateProgress(): void {
  const visibleRows = rows.filter((item) => !item.hidden);
  const completed = visibleRows.filter((row) => Boolean(row.querySelector<HTMLInputElement>("input")?.value.trim())).length;
  if (counter) counter.textContent = `${completed} / ${visibleRows.length}`;
  if (agentProgress) agentProgress.textContent = `FIELDS FILLED ${completed} OF ${visibleRows.length}`;
}
