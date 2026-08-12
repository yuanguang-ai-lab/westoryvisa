import { icon } from "./icons.ts";
import { t, text } from "./i18n.ts";
import { STEPS } from "./config.ts";
import { overallCompletion, stepStatus } from "./validation.ts";
import type { FieldConfig, Locale, PracticeData, PracticeDraft, ValidationErrors } from "./types.ts";

export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function otherLocale(locale: Locale): Locale {
  return locale === "zh" ? "en" : "zh";
}

export function bilingual(value: { en: string; zh: string }, locale: Locale): string {
  const secondary = otherLocale(locale);
  return `<span>${escapeHtml(text(value, locale))}</span><small lang="${secondary === "zh" ? "zh-CN" : "en"}">${escapeHtml(text(value, secondary))}</small>`;
}

export function renderBanner(locale: Locale): string {
  return `<div class="unofficial-banner" role="note">${icon("shield")}<strong>${escapeHtml(t(locale, "banner"))}</strong></div>`;
}

export function renderHeader(locale: Locale, route: string): string {
  const nav = [
    ["home", "#/", "nav.home", "home"],
    ["drafts", "#/drafts", "nav.drafts", "file"],
    ["help", "#/help", "nav.help", "help"],
    ["privacy", "#/privacy", "nav.privacy", "lock"]
  ];
  return `${renderBanner(locale)}
    <header class="site-header">
      <a class="brand" href="#/" aria-label="${escapeHtml(t(locale, "app.name"))}">
        <span class="brand-mark">VF</span>
        <span><strong>${escapeHtml(t(locale, "app.name"))}</strong><small>${escapeHtml(t(locale, "app.nameEn"))}</small></span>
      </a>
      <nav aria-label="Primary navigation">
        ${nav.map(([id, href, key, iconName]) => `<a href="${href}" class="${route === id ? "active" : ""}">${icon(iconName)}<span>${escapeHtml(t(locale, key))}</span></a>`).join("")}
      </nav>
      <button class="language-button" type="button" data-action="toggle-locale">${icon("globe")}<span>${escapeHtml(t(locale, "nav.language"))}</span></button>
    </header>`;
}

export function renderFooter(locale: Locale): string {
  return `<footer class="site-footer"><strong>Visa Form Practice Lab</strong><span>${escapeHtml(t(locale, "footer"))}</span><span>© ${new Date().getFullYear()} Practice Lab</span></footer>`;
}

export function renderShell(content: string, locale: Locale, route: string, className = ""): string {
  return `${renderHeader(locale, route)}<main id="main-content" class="page ${className}" tabindex="-1">${content}</main>${renderFooter(locale)}`;
}

export function renderProgress(draft: PracticeDraft, locale: Locale): string {
  const value = overallCompletion(draft.data, locale);
  return `<div class="overall-progress">
    <div><span>${locale === "zh" ? "本地练习完成度" : "Local practice completion"}</span><strong>${value}%</strong></div>
    <progress value="${value}" max="100" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${value}" aria-label="${locale === "zh" ? "本地练习完成度" : "Local practice completion"}">${value}%</progress>
  </div>`;
}

export function renderStepper(draft: PracticeDraft, locale: Locale, current: number): string {
  return `<nav class="application-stepper" aria-label="${locale === "zh" ? "练习步骤" : "Practice steps"}">
    <div class="stepper-heading"><span>${locale === "zh" ? "练习流程" : "Practice flow"}</span><strong>${current + 1} / ${STEPS.length}</strong></div>
    <ol>${STEPS.map((step, index) => {
      const status = step.kind === "form" ? stepStatus(index, draft.data, locale) : index < current ? "complete" : index === current ? "started" : "notStarted";
      const statusLabel = t(locale, `status.${status}`);
      return `<li><a href="#/application/${encodeURIComponent(draft.id)}/${index}" class="${index === current ? "current" : ""}" ${index === current ? "aria-current=\"step\"" : ""}><span class="step-index">${status === "complete" ? icon("check") : index + 1}</span><span class="step-name">${escapeHtml(text(step.shortTitle, locale))}<small>${escapeHtml(statusLabel)}</small></span><i class="status-dot ${status}" aria-hidden="true"></i></a></li>`;
    }).join("")}</ol>
  </nav>`;
}

export function renderApplicationFrame(draft: PracticeDraft, locale: Locale, current: number, main: string): string {
  const step = STEPS[current];
  const savedDate = new Date(draft.updatedAt);
  const savedTime = Number.isNaN(savedDate.getTime()) ? "" : new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit" }).format(savedDate);
  return `<div class="application-topline">
      <div><a href="#/drafts">${icon("arrowLeft")} ${locale === "zh" ? "草稿管理" : "Drafts"}</a><span>${escapeHtml(draft.practiceNumber)}</span></div>
      <div class="autosave-status" id="autosaveStatus" role="status">${icon("save")}<span>${escapeHtml(t(locale, "status.saved"))}</span><time datetime="${escapeHtml(draft.updatedAt)}">${escapeHtml(savedTime)}</time></div>
    </div>
    ${draft.mode === "example" ? `<div class="example-mode-banner">${icon("eye")}<strong>${escapeHtml(t(locale, "mode.example"))}</strong><button type="button" data-action="reset-example">${locale === "zh" ? "重置示例" : "Reset example"}</button></div>` : ""}
    ${renderProgress(draft, locale)}
    <details class="mobile-stepper"><summary>${escapeHtml(text(step.shortTitle, locale))}<span>${current + 1} / ${STEPS.length}</span></summary>${renderStepper(draft, locale, current)}</details>
    <div class="application-layout">
      ${renderStepper(draft, locale, current)}
      <section class="application-main">${main}</section>
      <aside class="context-help"><span class="eyebrow">${locale === "zh" ? "本节说明" : "Section guide"}</span><h2>${escapeHtml(text(step.shortTitle, locale))}</h2><p>${escapeHtml(text(step.help, locale))}</p><div class="mini-safety">${icon("shield")}<span>${escapeHtml(t(locale, "privacy.local"))}</span></div></aside>
    </div>`;
}

export function renderErrorSummary(errors: ValidationErrors, fields: FieldConfig[], locale: Locale): string {
  const entries = Object.entries(errors);
  if (!entries.length) return "";
  return `<section class="error-summary" role="alert" aria-labelledby="error-summary-title" tabindex="-1"><div>${icon("alert")}<h2 id="error-summary-title">${escapeHtml(t(locale, "form.errorSummary"))}</h2></div><ul>${entries.map(([id, message]) => {
    const field = fields.find((item) => item.id === id);
    return `<li><a href="#field-${escapeHtml(id)}" data-focus-field="${escapeHtml(id)}"><strong>${escapeHtml(field ? text(field.label, locale) : id)}</strong><span>${escapeHtml(message)}</span></a></li>`;
  }).join("")}</ul></section>`;
}
