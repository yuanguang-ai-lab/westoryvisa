import { ALL_FIELDS } from "./config.ts";
import { icon } from "./icons.ts";
import { t, text } from "./i18n.ts";
import { bilingual, escapeHtml } from "./ui.ts";
import { isVisible } from "./validation.ts";
import type { FieldConfig, Locale, PracticeData, ValidationErrors } from "./types.ts";

function valueOf(data: PracticeData, id: string): string {
  const value = data[id];
  return value === undefined || value === null ? "" : String(value);
}

function fieldMeta(field: FieldConfig, locale: Locale): string {
  return `<span class="field-meta">${field.required ? `<span class="required-tag">${escapeHtml(t(locale, "form.required"))}</span>` : `<span>${escapeHtml(t(locale, "form.optional"))}</span>`}${field.sensitive ? `<span class="fictional-tag">${icon("shield")}${escapeHtml(t(locale, "form.fictional"))}</span>` : ""}</span>`;
}

function fieldMessage(field: FieldConfig, locale: Locale): string {
  if (field.fictionalRule === "passport") return locale === "zh" ? "仅输入虚构号码，例如 DEMO123456。" : "Enter a fictional number only, such as DEMO123456.";
  if (field.fictionalRule === "email") return locale === "zh" ? "请使用 alex@example.com 等保留域名。" : "Use a reserved address such as alex@example.com.";
  if (field.fictionalRule === "phone") return locale === "zh" ? "推荐使用 +1 202-555-0100。" : "Recommended: +1 202-555-0100.";
  if (field.fictionalRule === "address") return locale === "zh" ? "推荐使用 100 Example Avenue, Sample City。" : "Recommended: 100 Example Avenue, Sample City.";
  if (field.fictionalRule === "nationalId") return locale === "zh" ? "请使用 DEMO-ID-2026 等虚构编号。" : "Use a fictional identifier such as DEMO-ID-2026.";
  return "";
}

function renderFieldLabel(field: FieldConfig, locale: Locale): string {
  return `<div class="field-heading"><label id="label-${escapeHtml(field.id)}" for="field-${escapeHtml(field.id)}">${bilingual(field.label, locale)}</label>${fieldMeta(field, locale)}</div>`;
}

function descriptionIds(field: FieldConfig, error?: string): string {
  return [field.hint || field.fictionalRule ? `hint-${field.id}` : "", error ? `error-${field.id}` : ""].filter(Boolean).join(" ");
}

function renderMessages(field: FieldConfig, locale: Locale, error?: string): string {
  const safety = fieldMessage(field, locale);
  return `${field.hint || safety ? `<p class="field-hint ${safety ? "safety-hint" : ""}" id="hint-${escapeHtml(field.id)}">${safety ? icon("shield") : ""}<span>${escapeHtml(safety || text(field.hint!, locale))}</span></p>` : ""}${error ? `<p class="field-error" id="error-${escapeHtml(field.id)}">${icon("alert")}<span>${escapeHtml(error)}</span></p>` : ""}`;
}

function renderOptions(field: FieldConfig, locale: Locale, current: string): string {
  return (field.options || []).map((item) => `<option value="${escapeHtml(item.value)}" ${current === item.value ? "selected" : ""}>${escapeHtml(text(item.label, locale))} / ${escapeHtml(text(item.label, locale === "zh" ? "en" : "zh"))}</option>`).join("");
}

function renderYesNo(field: FieldConfig, data: PracticeData, locale: Locale, error?: string): string {
  const current = valueOf(data, field.id);
  const yesTargets = ALL_FIELDS.filter((candidate) => candidate.condition?.field === field.id && candidate.condition.equals === "yes").map((candidate) => `field-${candidate.id}`).join(" ");
  const noTargets = ALL_FIELDS.filter((candidate) => candidate.condition?.field === field.id && candidate.condition.equals === "no").map((candidate) => `field-${candidate.id}`).join(" ");
  const yesExpanded = yesTargets ? `aria-controls="${escapeHtml(yesTargets)}" aria-expanded="${current === "yes"}"` : "";
  const noExpanded = noTargets ? `aria-controls="${escapeHtml(noTargets)}" aria-expanded="${current === "no"}"` : "";
  return `<fieldset class="form-field yes-no-field ${error ? "invalid" : ""}" id="field-${escapeHtml(field.id)}" aria-describedby="${descriptionIds(field, error)}">
    <legend>${bilingual(field.label, locale)}</legend>
    ${fieldMeta(field, locale)}
    <div class="segmented-choice">
      <label><input type="radio" name="${escapeHtml(field.id)}" value="yes" data-field="${escapeHtml(field.id)}" ${current === "yes" ? "checked" : ""} ${field.required ? "required" : ""} ${yesExpanded}><span>${escapeHtml(t(locale, "form.yes"))}</span></label>
      <label><input type="radio" name="${escapeHtml(field.id)}" value="no" data-field="${escapeHtml(field.id)}" ${current === "no" ? "checked" : ""} ${field.required ? "required" : ""} ${noExpanded}><span>${escapeHtml(t(locale, "form.no"))}</span></label>
    </div>
    ${renderMessages(field, locale, error)}
  </fieldset>`;
}

function renderCheckbox(field: FieldConfig, data: PracticeData, locale: Locale, error?: string): string {
  return `<div class="form-field checkbox-field ${error ? "invalid" : ""}" id="field-${escapeHtml(field.id)}"><label><input type="checkbox" data-field="${escapeHtml(field.id)}" ${data[field.id] === true ? "checked" : ""}><span class="checkbox-mark">${icon("check")}</span><span class="checkbox-copy">${bilingual(field.label, locale)}</span></label>${renderMessages(field, locale, error)}</div>`;
}

function renderBasicField(field: FieldConfig, data: PracticeData, locale: Locale, error?: string): string {
  const current = valueOf(data, field.id);
  const describedBy = descriptionIds(field, error);
  const common = `id="field-${escapeHtml(field.id)}" data-field="${escapeHtml(field.id)}" ${field.required ? "required aria-required=\"true\"" : ""} ${describedBy ? `aria-describedby="${escapeHtml(describedBy)}"` : ""} ${error ? "aria-invalid=\"true\"" : ""}`;
  let control = "";
  if (field.kind === "select") {
    control = `<select ${common}><option value="">${escapeHtml(t(locale, "form.select"))}</option>${renderOptions(field, locale, current)}</select>`;
  } else if (field.kind === "textarea") {
    control = `<textarea ${common} rows="4" ${field.maxLength ? `maxlength="${field.maxLength}"` : ""} placeholder="${escapeHtml(field.placeholder ? text(field.placeholder, locale) : "")}">${escapeHtml(current)}</textarea>`;
  } else {
    const type = ["date", "email", "tel", "number"].includes(field.kind) ? field.kind : "text";
    control = `<input ${common} type="${type}" value="${escapeHtml(current)}" ${field.maxLength ? `maxlength="${field.maxLength}"` : ""} placeholder="${escapeHtml(field.placeholder ? text(field.placeholder, locale) : "")}" autocomplete="off" spellcheck="false">`;
  }
  return `<div class="form-field ${error ? "invalid" : ""}">${renderFieldLabel(field, locale)}${control}${renderMessages(field, locale, error)}</div>`;
}

function renderRepeaterInput(field: FieldConfig, locale: Locale, index: number, column: NonNullable<FieldConfig["columns"]>[number], value: string): string {
  const id = `${field.id}-${index}-${column.key}`;
  if (column.type === "select") {
    return `<label for="${escapeHtml(id)}"><span>${escapeHtml(text(column.label, locale))}</span><select id="${escapeHtml(id)}" data-repeat-field="${escapeHtml(field.id)}" data-repeat-index="${index}" data-repeat-key="${escapeHtml(column.key)}"><option value="">${escapeHtml(t(locale, "form.select"))}</option>${(column.options || []).map((item) => `<option value="${escapeHtml(item.value)}" ${item.value === value ? "selected" : ""}>${escapeHtml(text(item.label, locale))}</option>`).join("")}</select></label>`;
  }
  return `<label for="${escapeHtml(id)}"><span>${escapeHtml(text(column.label, locale))}</span><input id="${escapeHtml(id)}" type="${column.type === "date" ? "date" : "text"}" value="${escapeHtml(value)}" data-repeat-field="${escapeHtml(field.id)}" data-repeat-index="${index}" data-repeat-key="${escapeHtml(column.key)}" autocomplete="off"></label>`;
}

function renderRepeater(field: FieldConfig, data: PracticeData, locale: Locale, error?: string): string {
  const records = Array.isArray(data[field.id]) ? data[field.id] as Record<string, unknown>[] : [];
  return `<section class="form-field repeatable-field ${error ? "invalid" : ""}" id="field-${escapeHtml(field.id)}" aria-labelledby="label-${escapeHtml(field.id)}">
    <div class="field-heading"><div id="label-${escapeHtml(field.id)}" class="group-label">${bilingual(field.label, locale)}</div>${fieldMeta(field, locale)}</div>
    <div class="repeatable-list">${records.length ? records.map((record, index) => `<article class="repeatable-item"><header><strong>${locale === "zh" ? "记录" : "Record"} ${index + 1}</strong><div><button class="icon-button" type="button" data-action="move-item-up" data-field-id="${escapeHtml(field.id)}" data-index="${index}" aria-label="${escapeHtml(t(locale, "form.moveUp"))}" ${index === 0 ? "disabled" : ""}>${icon("chevronUp")}</button><button class="icon-button" type="button" data-action="move-item-down" data-field-id="${escapeHtml(field.id)}" data-index="${index}" aria-label="${escapeHtml(t(locale, "form.moveDown"))}" ${index === records.length - 1 ? "disabled" : ""}>${icon("chevronDown")}</button><button class="icon-button danger" type="button" data-action="remove-item" data-field-id="${escapeHtml(field.id)}" data-index="${index}" aria-label="${escapeHtml(t(locale, "form.remove"))}">${icon("trash")}</button></div></header><div class="repeatable-grid">${(field.columns || []).map((column) => renderRepeaterInput(field, locale, index, column, String(record[column.key] || ""))).join("")}</div></article>`).join("") : `<div class="empty-inline"><strong>${locale === "zh" ? "还没有记录" : "No records yet"}</strong><span>${locale === "zh" ? "需要时添加虚构条目。" : "Add a fictional item when needed."}</span></div>`}</div>
    <button class="add-item-button" type="button" data-action="add-item" data-field-id="${escapeHtml(field.id)}">${icon("plus")}<span>${escapeHtml(field.addLabel ? text(field.addLabel, locale) : t(locale, "form.add"))}</span></button>
    ${renderMessages(field, locale, error)}
  </section>`;
}

function renderStringList(field: FieldConfig, data: PracticeData, locale: Locale, error?: string): string {
  const values = Array.isArray(data[field.id]) ? data[field.id] as string[] : [];
  return `<section class="form-field repeatable-field compact ${error ? "invalid" : ""}" id="field-${escapeHtml(field.id)}"><div class="field-heading"><div class="group-label">${bilingual(field.label, locale)}</div>${fieldMeta(field, locale)}</div><div class="string-list">${values.map((value, index) => `<div><input type="text" value="${escapeHtml(value)}" data-repeat-field="${escapeHtml(field.id)}" data-repeat-index="${index}" data-repeat-key="value" placeholder="${escapeHtml(field.placeholder ? text(field.placeholder, locale) : "")}"><button class="icon-button danger" type="button" data-action="remove-item" data-field-id="${escapeHtml(field.id)}" data-index="${index}" aria-label="${escapeHtml(t(locale, "form.remove"))}">${icon("close")}</button></div>`).join("")}</div><button class="add-item-button" type="button" data-action="add-item" data-field-id="${escapeHtml(field.id)}">${icon("plus")}<span>${escapeHtml(field.addLabel ? text(field.addLabel, locale) : t(locale, "form.add"))}</span></button>${renderMessages(field, locale, error)}</section>`;
}

export function renderFields(fields: FieldConfig[], data: PracticeData, locale: Locale, errors: ValidationErrors): string {
  return `<div class="form-grid">${fields.filter((field) => isVisible(field, data)).map((field) => {
    const error = errors[field.id];
    if (field.kind === "yesno") return renderYesNo(field, data, locale, error);
    if (field.kind === "checkbox") return renderCheckbox(field, data, locale, error);
    if (field.kind === "repeater") return renderRepeater(field, data, locale, error);
    if (field.kind === "stringList") return renderStringList(field, data, locale, error);
    return renderBasicField(field, data, locale, error);
  }).join("")}</div>`;
}

export function displayFieldValue(field: FieldConfig, value: unknown, locale: Locale): string {
  if (value === undefined || value === null || value === "") return locale === "zh" ? "未填写" : "Not completed";
  if (field.kind === "yesno") return value === "yes" ? t(locale, "form.yes") : t(locale, "form.no");
  if (field.kind === "checkbox") return value === true ? t(locale, "form.yes") : t(locale, "form.no");
  if (field.kind === "select") {
    const selected = field.options?.find((item) => item.value === value);
    return selected ? text(selected.label, locale) : String(value);
  }
  if (Array.isArray(value)) {
    if (!value.length) return locale === "zh" ? "暂无记录" : "No records";
    return value.map((item, index) => typeof item === "string" ? item : `${index + 1}. ${Object.values(item).filter(Boolean).join(" · ")}`).join("\n");
  }
  return String(value);
}
