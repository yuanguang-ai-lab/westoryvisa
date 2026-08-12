import { ALL_FIELDS, STEPS, fieldById } from "./config.ts";
import { icon } from "./icons.ts";
import { detectLocale, setLocale, t } from "./i18n.ts";
import {
  clearAllPracticeData,
  createDraft,
  deleteDraft,
  duplicateDraft,
  exportPayload,
  getDraft,
  importDraft,
  loadDrafts,
  renameDraft,
  resetExampleDraft,
  saveDraft,
  setConsent,
  storageAvailable
} from "./storage.ts";
import { cleanHiddenValues, overallCompletion, validateStep } from "./validation.ts";
import { escapeHtml } from "./ui.ts";
import { renderApplication, renderDrafts, renderHelp, renderHome, renderPrivacy, renderStart } from "./views.ts";
import type { Locale, PracticeDraft, ValidationErrors } from "./types.ts";

type Route =
  | { name: "home" }
  | { name: "start"; mode: string }
  | { name: "drafts" }
  | { name: "help" }
  | { name: "privacy" }
  | { name: "application"; id: string; step: number };

const app = document.querySelector<HTMLDivElement>("#app");
const announcer = document.querySelector<HTMLDivElement>("#announcer");

if (!app) throw new Error("Application root was not found");

let locale: Locale = detectLocale();
let currentRoute: Route = { name: "home" };
let activeDraft: PracticeDraft | null = null;
let currentStep = 0;
let currentErrors: ValidationErrors = {};
let revealSensitive = false;
let dirty = false;
let saveTimer: number | undefined;
let pendingPrint = false;
let firstRender = true;

const conditionSources = new Set(
  ALL_FIELDS.flatMap((field) => field.condition ? [field.condition.field] : [])
);

function parseRoute(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, queryString = ""] = raw.split("?");
  const segments = path.split("/").filter(Boolean).map((part) => decodeURIComponent(part));
  const query = new URLSearchParams(queryString);
  if (!segments.length) return { name: "home" };
  if (segments[0] === "start") return { name: "start", mode: query.get("mode") || "blank" };
  if (segments[0] === "drafts") return { name: "drafts" };
  if (segments[0] === "help") return { name: "help" };
  if (segments[0] === "privacy") return { name: "privacy" };
  if (segments[0] === "application" && segments[1]) {
    const parsedStep = Number.parseInt(segments[2] || "0", 10);
    return {
      name: "application",
      id: segments[1],
      step: Number.isFinite(parsedStep) ? Math.min(Math.max(parsedStep, 0), STEPS.length - 1) : 0
    };
  }
  return { name: "home" };
}

function routeUrl(draft: PracticeDraft, step: number): string {
  return `#/application/${encodeURIComponent(draft.id)}/${step}`;
}

function announce(message: string): void {
  if (!announcer) return;
  announcer.textContent = "";
  window.setTimeout(() => { announcer.textContent = message; }, 20);
}

function setDocumentMetadata(route: Route): void {
  document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  const labels: Record<Route["name"], string> = {
    home: locale === "zh" ? "首页" : "Home",
    start: locale === "zh" ? "开始练习" : "Start practice",
    drafts: locale === "zh" ? "练习草稿" : "Practice drafts",
    help: locale === "zh" ? "帮助" : "Help",
    privacy: locale === "zh" ? "隐私" : "Privacy",
    application: locale === "zh" ? "填写练习" : "Form practice"
  };
  document.title = `${labels[route.name]} · Visa Form Practice Lab`;
}

function setSaveStatus(state: "saving" | "saved" | "unavailable"): void {
  const status = document.querySelector<HTMLElement>("#autosaveStatus");
  if (!status) return;
  status.classList.toggle("is-saving", state === "saving");
  const copy = status.querySelector("span");
  if (copy) copy.textContent = t(locale, `status.${state}`);
  const savedAt = status.querySelector<HTMLTimeElement>("time");
  if (savedAt && state === "saved" && activeDraft) {
    const date = new Date(activeDraft.updatedAt);
    savedAt.dateTime = activeDraft.updatedAt;
    savedAt.textContent = Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
      hour: "2-digit",
      minute: "2-digit"
    }).format(date);
  }
}

function flushSave(showToast = false): void {
  if (saveTimer) window.clearTimeout(saveTimer);
  saveTimer = undefined;
  if (!activeDraft || !dirty) {
    if (showToast) toast(t(locale, "toast.saved"));
    return;
  }
  if (!storageAvailable()) {
    setSaveStatus("unavailable");
    announce(t(locale, "status.unavailable"));
    return;
  }
  try {
    activeDraft.data = cleanHiddenValues(activeDraft.data);
    activeDraft = saveDraft(activeDraft);
    dirty = false;
    setSaveStatus("saved");
    if (showToast) toast(t(locale, "toast.saved"));
  } catch {
    setSaveStatus("unavailable");
    announce(t(locale, "status.unavailable"));
  }
}

function scheduleSave(): void {
  dirty = true;
  setSaveStatus("saving");
  if (saveTimer) window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => flushSave(), 800);
}

function renderRoute(moveFocus = true): void {
  if (dirty) flushSave();
  currentRoute = parseRoute();
  setDocumentMetadata(currentRoute);
  currentErrors = {};
  revealSensitive = false;

  if (currentRoute.name === "home") {
    activeDraft = null;
    app.innerHTML = renderHome(locale);
  } else if (currentRoute.name === "start") {
    activeDraft = null;
    app.innerHTML = renderStart(locale, currentRoute.mode);
  } else if (currentRoute.name === "drafts") {
    activeDraft = null;
    app.innerHTML = renderDrafts(loadDrafts(), locale);
  } else if (currentRoute.name === "help") {
    activeDraft = null;
    app.innerHTML = renderHelp(locale);
  } else if (currentRoute.name === "privacy") {
    activeDraft = null;
    app.innerHTML = renderPrivacy(locale);
  } else {
    activeDraft = getDraft(currentRoute.id);
    if (!activeDraft) {
      window.location.hash = "#/drafts";
      return;
    }
    currentStep = currentRoute.step;
    activeDraft.currentStep = currentStep;
    app.innerHTML = renderApplication(activeDraft, currentStep, locale, currentErrors, revealSensitive);
    dirty = true;
    scheduleSave();
  }

  if (!storageAvailable()) showStorageNotice();
  if (moveFocus && !firstRender) {
    const focusTarget = document.querySelector<HTMLElement>("#stepTitle, #main-content h1, #main-content");
    window.setTimeout(() => focusTarget?.focus({ preventScroll: true }), 10);
    window.scrollTo({ top: 0, behavior: "auto" });
  }
  firstRender = false;
  maybeShowFirstVisitGuide();
  if (pendingPrint && currentRoute.name === "application" && STEPS[currentStep]?.kind === "print") {
    pendingPrint = false;
    window.setTimeout(() => window.print(), 150);
  }
}

function renderActiveApplication(options: { focusField?: string; focusErrors?: boolean } = {}): void {
  if (!activeDraft || currentRoute.name !== "application") return;
  app.innerHTML = renderApplication(activeDraft, currentStep, locale, currentErrors, revealSensitive);
  if (!storageAvailable()) showStorageNotice();
  window.setTimeout(() => {
    if (options.focusErrors) {
      document.querySelector<HTMLElement>(".error-summary")?.focus();
      return;
    }
    if (options.focusField) focusFieldControl(options.focusField);
  }, 10);
}

function focusFieldControl(fieldId: string): void {
  const candidates = [...document.querySelectorAll<HTMLElement>("[data-field], [data-repeat-field]")];
  const target = candidates.find((element) => element.dataset.field === fieldId || element.dataset.repeatField === fieldId)
    || document.querySelector<HTMLElement>(`#field-${safeCssIdentifier(fieldId)}`);
  target?.focus();
  target?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function safeCssIdentifier(value: string): string {
  return globalThis.CSS?.escape ? CSS.escape(value) : value.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function showStorageNotice(): void {
  if (document.querySelector(".storage-notice")) return;
  const notice = document.createElement("div");
  notice.className = "storage-notice";
  notice.setAttribute("role", "alert");
  notice.innerHTML = `${icon("alert")}<span>${escapeHtml(t(locale, "status.unavailable"))}</span>`;
  document.querySelector(".site-header")?.insertAdjacentElement("afterend", notice);
}

function toast(message: string, tone: "default" | "error" = "default"): void {
  document.querySelector(".app-toast")?.remove();
  const element = document.createElement("div");
  element.className = `app-toast ${tone === "error" ? "error" : ""}`;
  element.setAttribute("role", tone === "error" ? "alert" : "status");
  element.innerHTML = `${icon(tone === "error" ? "alert" : "check")}<span>${escapeHtml(message)}</span>`;
  document.body.append(element);
  requestAnimationFrame(() => element.classList.add("visible"));
  window.setTimeout(() => {
    element.classList.remove("visible");
    window.setTimeout(() => element.remove(), 260);
  }, 3200);
  announce(message);
}

type DialogOptions = {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  input?: { label: string; value: string; maxLength?: number };
  cancelable?: boolean;
};

function openDialog(options: DialogOptions): Promise<string | true | null> {
  return new Promise((resolve) => {
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = document.createElement("dialog");
    dialog.className = "product-dialog";
    const inputMarkup = options.input ? `<label class="dialog-input"><span>${escapeHtml(options.input.label)}</span><input id="dialogValue" type="text" value="${escapeHtml(options.input.value)}" maxlength="${options.input.maxLength || 120}" autocomplete="off"></label>` : "";
    dialog.innerHTML = `<div class="dialog-panel"><div class="dialog-handle" aria-hidden="true"></div><header><div><span class="eyebrow">Visa Form Practice Lab</span><h2>${escapeHtml(options.title)}</h2></div>${options.cancelable === false ? "" : `<button class="dialog-close icon-button" type="button" data-dialog="cancel" aria-label="${escapeHtml(t(locale, "action.cancel"))}">${icon("close")}</button>`}</header><p>${escapeHtml(options.message)}</p>${inputMarkup}<footer>${options.cancelable === false ? "" : `<button class="secondary-button" type="button" data-dialog="cancel">${escapeHtml(options.cancelLabel || t(locale, "action.cancel"))}</button>`}<button class="${options.danger ? "danger-button" : "primary-button"}" type="button" data-dialog="confirm">${escapeHtml(options.confirmLabel || t(locale, "action.confirm"))}</button></footer></div>`;
    document.body.append(dialog);

    let settled = false;
    const finish = (value: string | true | null) => {
      if (settled) return;
      settled = true;
      dialog.classList.add("is-closing");
      window.setTimeout(() => {
        dialog.close();
        dialog.remove();
        if (returnFocus?.isConnected) returnFocus.focus();
        resolve(value);
      }, 220);
    };
    dialog.addEventListener("click", (event) => {
      const button = (event.target as HTMLElement).closest<HTMLElement>("[data-dialog]");
      if (button?.dataset.dialog === "cancel") finish(null);
      if (button?.dataset.dialog === "confirm") {
        const input = dialog.querySelector<HTMLInputElement>("#dialogValue");
        finish(input ? input.value.trim() : true);
      }
      if (event.target === dialog && options.cancelable !== false) finish(null);
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      if (options.cancelable !== false) finish(null);
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && options.input) {
        event.preventDefault();
        const input = dialog.querySelector<HTMLInputElement>("#dialogValue");
        finish(input ? input.value.trim() : true);
      }
    });
    dialog.showModal();
    requestAnimationFrame(() => dialog.classList.add("is-open"));
    window.setTimeout(() => (dialog.querySelector<HTMLInputElement>("input") || dialog.querySelector<HTMLButtonElement>("[data-dialog=confirm]"))?.focus(), 40);
  });
}

function maybeShowFirstVisitGuide(): void {
  if (currentRoute.name !== "home") return;
  let hasSeen = true;
  try { hasSeen = localStorage.getItem("vfpl_safety_guide_v1") === "seen"; } catch { return; }
  if (hasSeen || document.querySelector(".product-dialog")) return;
  window.setTimeout(async () => {
    await openDialog({
      title: locale === "zh" ? "开始前，请记住三件事" : "Three things before you begin",
      message: locale === "zh"
        ? "这里只练习长表单流程；请始终使用虚构资料；完成后不会提交、付款或连接任何政府网站。"
        : "This is only long-form practice. Always use fictional data. Finishing never submits, pays or connects to a government website.",
      confirmLabel: locale === "zh" ? "我知道了" : "Understood",
      cancelable: false
    });
    try { localStorage.setItem("vfpl_safety_guide_v1", "seen"); } catch { /* Storage notice is already shown. */ }
  }, 220);
}

function updateProgressOnly(): void {
  if (!activeDraft) return;
  const progress = document.querySelector<HTMLProgressElement>(".overall-progress progress");
  const label = document.querySelector<HTMLElement>(".overall-progress strong");
  if (!progress || !label) return;
  const nextValue = overallCompletion(activeDraft.data, locale);
  progress.value = nextValue;
  label.textContent = `${nextValue}%`;
  progress.setAttribute("aria-valuenow", String(nextValue));
}

function updateBasicField(target: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement): void {
  if (!activeDraft || !target.dataset.field) return;
  if (target instanceof HTMLInputElement && target.type === "radio" && !target.checked) return;
  const fieldId = target.dataset.field;
  const value = target instanceof HTMLInputElement && target.type === "checkbox" ? target.checked : target.value;
  activeDraft.data[fieldId] = value;
  currentErrors = { ...currentErrors };
  delete currentErrors[fieldId];
  scheduleSave();
  if (conditionSources.has(fieldId)) {
    activeDraft.data = cleanHiddenValues(activeDraft.data);
    renderActiveApplication({ focusField: fieldId });
  } else {
    updateProgressOnly();
  }
}

function updateRepeatedField(target: HTMLInputElement | HTMLSelectElement): void {
  if (!activeDraft || !target.dataset.repeatField) return;
  const fieldId = target.dataset.repeatField;
  const index = Number.parseInt(target.dataset.repeatIndex || "-1", 10);
  const key = target.dataset.repeatKey || "value";
  const values = Array.isArray(activeDraft.data[fieldId]) ? structuredClone(activeDraft.data[fieldId]) as unknown[] : [];
  if (index < 0 || index >= values.length) return;
  const field = fieldById(fieldId);
  if (field?.kind === "stringList") values[index] = target.value;
  else {
    const record = values[index] && typeof values[index] === "object" ? values[index] as Record<string, unknown> : {};
    record[key] = target.value;
    values[index] = record;
  }
  activeDraft.data[fieldId] = values;
  delete currentErrors[fieldId];
  scheduleSave();
  updateProgressOnly();
}

function addRepeatedItem(fieldId: string): void {
  if (!activeDraft) return;
  const field = fieldById(fieldId);
  if (!field) return;
  const values = Array.isArray(activeDraft.data[fieldId]) ? structuredClone(activeDraft.data[fieldId]) as unknown[] : [];
  if (field.kind === "stringList") values.push("");
  else values.push(Object.fromEntries((field.columns || []).map((column) => [column.key, ""])));
  activeDraft.data[fieldId] = values;
  scheduleSave();
  renderActiveApplication();
  window.setTimeout(() => {
    const inputs = [...document.querySelectorAll<HTMLElement>(`[data-repeat-field="${safeCssIdentifier(fieldId)}"]`)];
    inputs.at(-1)?.focus();
  }, 20);
}

function removeRepeatedItem(fieldId: string, index: number): void {
  if (!activeDraft) return;
  const values = Array.isArray(activeDraft.data[fieldId]) ? structuredClone(activeDraft.data[fieldId]) as unknown[] : [];
  values.splice(index, 1);
  activeDraft.data[fieldId] = values;
  scheduleSave();
  renderActiveApplication({ focusField: fieldId });
}

function moveRepeatedItem(fieldId: string, index: number, direction: -1 | 1): void {
  if (!activeDraft) return;
  const values = Array.isArray(activeDraft.data[fieldId]) ? structuredClone(activeDraft.data[fieldId]) as unknown[] : [];
  const destination = index + direction;
  if (index < 0 || destination < 0 || index >= values.length || destination >= values.length) return;
  [values[index], values[destination]] = [values[destination], values[index]];
  activeDraft.data[fieldId] = values;
  scheduleSave();
  renderActiveApplication({ focusField: fieldId });
}

async function continueStep(): Promise<void> {
  if (!activeDraft) return;
  const step = STEPS[currentStep];
  if (step.kind === "form") {
    activeDraft.data = cleanHiddenValues(activeDraft.data);
    currentErrors = validateStep(currentStep, activeDraft.data, locale);
    if (Object.keys(currentErrors).length) {
      const firstError = Object.keys(currentErrors)[0];
      renderActiveApplication(firstError ? { focusField: firstError } : {});
      announce(t(locale, "form.errorSummary"));
      return;
    }
  }
  currentStep = Math.min(currentStep + 1, STEPS.length - 1);
  activeDraft.currentStep = currentStep;
  dirty = true;
  flushSave();
  window.location.hash = routeUrl(activeDraft, currentStep);
}

function previousStep(): void {
  if (!activeDraft) return;
  currentStep = Math.max(currentStep - 1, 0);
  activeDraft.currentStep = currentStep;
  dirty = true;
  flushSave();
  window.location.hash = routeUrl(activeDraft, currentStep);
}

function downloadDraft(draft: PracticeDraft): void {
  const raw = JSON.stringify(exportPayload(draft), null, 2);
  const blob = new Blob([raw], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const filename = draft.practiceNumber.replace(/[^a-zA-Z0-9_-]/g, "_");
  anchor.href = url;
  anchor.download = `${filename}.json`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  toast(t(locale, "toast.exported"));
}

async function confirmExport(draft: PracticeDraft): Promise<void> {
  const confirmed = await openDialog({
    title: locale === "zh" ? "导出本地练习数据？" : "Export local practice data?",
    message: locale === "zh"
      ? "导出文件会保存到您的设备。请先确认草稿只包含虚构资料，不包含真实个人信息。"
      : "The export will be saved to your device. Confirm the draft contains only fictional data and no real personal information.",
    confirmLabel: locale === "zh" ? "确认并导出" : "Confirm and export"
  });
  if (confirmed) downloadDraft(draft);
}

async function clearCurrentSection(): Promise<void> {
  if (!activeDraft) return;
  const step = STEPS[currentStep];
  if (step.kind !== "form") return;
  const confirmed = await openDialog({
    title: locale === "zh" ? "清空本节内容？" : "Clear this section?",
    message: locale === "zh" ? "本节已经填写的练习数据将被删除，此操作无法撤销。" : "Practice data entered in this section will be deleted. This cannot be undone.",
    confirmLabel: locale === "zh" ? "清空本节" : "Clear section",
    danger: true
  });
  if (!confirmed) return;
  for (const field of step.fields) delete activeDraft.data[field.id];
  currentErrors = {};
  dirty = true;
  flushSave();
  renderActiveApplication();
  toast(t(locale, "toast.cleared"));
}

async function handleAction(action: string, element: HTMLElement): Promise<void> {
  if (action === "toggle-locale") {
    locale = locale === "zh" ? "en" : "zh";
    try { setLocale(locale); } catch { /* Locale still applies for this session. */ }
    if (activeDraft && currentRoute.name === "application") renderActiveApplication();
    else renderRoute(false);
    setDocumentMetadata(currentRoute);
    return;
  }
  if (action === "create-draft") {
    const consent = document.querySelector<HTMLInputElement>("#practiceConsent");
    if (!consent?.checked) return;
    try {
      setConsent(true);
      const draft = createDraft(element.dataset.mode === "example" ? "example" : "blank");
      window.location.hash = routeUrl(draft, 0);
    } catch {
      toast(t(locale, "status.unavailable"), "error");
    }
    return;
  }
  if (action === "continue-step") return continueStep();
  if (action === "previous-step") return previousStep();
  if (action === "save-draft") {
    dirty = true;
    flushSave(true);
    return;
  }
  if (action === "clear-section") return clearCurrentSection();
  if (action === "add-item") return addRepeatedItem(element.dataset.fieldId || "");
  if (action === "remove-item") return removeRepeatedItem(element.dataset.fieldId || "", Number.parseInt(element.dataset.index || "-1", 10));
  if (action === "move-item-up") return moveRepeatedItem(element.dataset.fieldId || "", Number.parseInt(element.dataset.index || "-1", 10), -1);
  if (action === "move-item-down") return moveRepeatedItem(element.dataset.fieldId || "", Number.parseInt(element.dataset.index || "-1", 10), 1);
  if (action === "toggle-sensitive") {
    revealSensitive = !revealSensitive;
    renderActiveApplication();
    return;
  }
  if (action === "reset-example" && activeDraft) {
    const confirmed = await openDialog({
      title: locale === "zh" ? "重置虚构示例？" : "Reset fictional example?",
      message: locale === "zh" ? "当前修改将被 Alex Example 的初始虚构资料覆盖。" : "Your changes will be replaced by the original fictional Alex Example data.",
      confirmLabel: locale === "zh" ? "重置示例" : "Reset example"
    });
    if (confirmed) {
      activeDraft = resetExampleDraft(activeDraft);
      currentStep = 0;
      dirty = true;
      flushSave();
      window.location.hash = routeUrl(activeDraft, 0);
    }
    return;
  }
  if (action === "print-page" && activeDraft) {
    const printStep = STEPS.findIndex((step) => step.kind === "print");
    if (currentStep === printStep) window.print();
    else {
      pendingPrint = true;
      window.location.hash = routeUrl(activeDraft, printStep);
    }
    return;
  }
  if (action === "delete-current-draft" && activeDraft) {
    const confirmed = await openDialog({
      title: locale === "zh" ? "删除当前练习？" : "Delete this practice?",
      message: locale === "zh" ? "当前草稿会从这个浏览器中永久删除。" : "This draft will be permanently removed from this browser.",
      confirmLabel: locale === "zh" ? "永久删除" : "Delete permanently",
      danger: true
    });
    if (confirmed) {
      deleteDraft(activeDraft.id);
      activeDraft = null;
      window.location.hash = "#/drafts";
    }
    return;
  }

  const id = element.dataset.id || "";
  if (action === "rename-draft") {
    const draft = getDraft(id);
    if (!draft) return;
    const value = await openDialog({
      title: locale === "zh" ? "重命名练习" : "Rename practice",
      message: locale === "zh" ? "名称只用于您在本地识别这份虚构草稿。" : "The name is only used to identify this fictional local draft.",
      confirmLabel: locale === "zh" ? "保存名称" : "Save name",
      input: { label: locale === "zh" ? "练习名称" : "Practice name", value: draft.name }
    });
    if (typeof value === "string" && value) {
      renameDraft(id, value);
      renderRoute(false);
    }
    return;
  }
  if (action === "duplicate-draft") {
    try {
      duplicateDraft(id);
      renderRoute(false);
      toast(locale === "zh" ? "已复制练习草稿。" : "Practice draft duplicated.");
    } catch { toast(locale === "zh" ? "无法复制该草稿。" : "The draft could not be duplicated.", "error"); }
    return;
  }
  if (action === "export-draft") {
    const draft = getDraft(id);
    if (draft) await confirmExport(draft);
    return;
  }
  if (action === "delete-draft") {
    const confirmed = await openDialog({
      title: locale === "zh" ? "删除这份练习？" : "Delete this practice?",
      message: locale === "zh" ? "这份本地草稿将被永久删除，无法恢复。" : "This local draft will be permanently deleted and cannot be recovered.",
      confirmLabel: locale === "zh" ? "永久删除" : "Delete permanently",
      danger: true
    });
    if (confirmed) {
      deleteDraft(id);
      renderRoute(false);
      toast(t(locale, "toast.cleared"));
    }
    return;
  }
  if (action === "clear-all-drafts") {
    const confirmed = await openDialog({
      title: locale === "zh" ? "清除全部练习数据？" : "Clear all practice data?",
      message: locale === "zh" ? "当前浏览器里的所有练习草稿和练习承诺记录都将被永久删除。" : "Every practice draft and consent record in this browser will be permanently deleted.",
      confirmLabel: locale === "zh" ? "全部清除" : "Clear everything",
      danger: true
    });
    if (confirmed) {
      clearAllPracticeData();
      renderRoute(false);
      toast(t(locale, "toast.cleared"));
    }
  }
}

document.addEventListener("click", (event) => {
  const target = event.target as HTMLElement;
  const focusLink = target.closest<HTMLElement>("[data-focus-field]");
  if (focusLink?.dataset.focusField) {
    event.preventDefault();
    focusFieldControl(focusLink.dataset.focusField);
    return;
  }
  const actionTarget = target.closest<HTMLElement>("[data-action]");
  if (!actionTarget?.dataset.action) return;
  event.preventDefault();
  void handleAction(actionTarget.dataset.action, actionTarget);
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement) {
    if (target.dataset.field) updateBasicField(target);
    if (target.dataset.repeatField && !(target instanceof HTMLTextAreaElement)) updateRepeatedField(target);
  }
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.id === "practiceConsent") {
    document.querySelectorAll<HTMLButtonElement>("[data-action=create-draft]").forEach((button) => { button.disabled = !target.checked; });
  }
  if (target instanceof HTMLInputElement && target.id === "importDraftFile" && target.files?.[0]) {
    const file = target.files[0];
    void file.text().then((raw) => {
      try {
        importDraft(raw);
        renderRoute(false);
        toast(t(locale, "toast.imported"));
      } catch (error) {
        const message = error instanceof Error ? error.message : (locale === "zh" ? "导入失败。" : "Import failed.");
        toast(message, "error");
      } finally {
        target.value = "";
      }
    });
  }
});

window.addEventListener("hashchange", () => renderRoute(true));
window.addEventListener("beforeunload", (event) => {
  if (!dirty) return;
  flushSave();
  if (dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushSave(); });

renderRoute(false);
