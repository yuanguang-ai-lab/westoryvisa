import { FORM_STEPS, STEPS } from "./config.ts";
import { t, text } from "./i18n.ts";
import type { FieldConfig, Locale, PracticeData, PracticeDraft, ValidationErrors } from "./types.ts";

const RESERVED_EMAIL_DOMAINS = new Set(["example.com", "example.org", "example.net"]);

export function isVisible(field: FieldConfig, data: PracticeData): boolean {
  const condition = field.condition;
  if (!condition) return true;
  const value = data[condition.field];
  if (Object.hasOwn(condition, "equals")) return value === condition.equals;
  if (Object.hasOwn(condition, "notEquals")) return value !== condition.notEquals;
  if (condition.oneOf) return condition.oneOf.includes(value);
  return true;
}

export function isEmpty(value: unknown): boolean {
  if (value === undefined || value === null || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

export function isSuspectedRealEmail(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)) return true;
  const domain = normalized.split("@").at(-1) || "";
  return !RESERVED_EMAIL_DOMAINS.has(domain);
}

export function cleanHiddenValues(data: PracticeData): PracticeData {
  const cleaned = structuredClone(data);
  let changed = true;
  while (changed) {
    changed = false;
    for (const step of FORM_STEPS) {
      for (const field of step.fields) {
        if (!isVisible(field, cleaned) && Object.hasOwn(cleaned, field.id)) {
          delete cleaned[field.id];
          changed = true;
        }
      }
    }
  }
  return cleaned;
}

function dateOnly(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function validateRepeater(field: FieldConfig, value: unknown, locale: Locale): string | null {
  if (!Array.isArray(value)) return field.required ? t(locale, "validation.required") : null;
  if (field.required && value.length === 0) return t(locale, "validation.required");
  for (const record of value) {
    if (!record || typeof record !== "object" || Array.isArray(record)) return t(locale, "validation.required");
    for (const column of field.columns || []) {
      if (isEmpty((record as Record<string, unknown>)[column.key])) return t(locale, "validation.required");
    }
    const start = String((record as Record<string, unknown>).start || "");
    const end = String((record as Record<string, unknown>).end || "");
    if (start && end && end < start) return locale === "zh" ? "结束日期不能早于开始日期。" : "End date cannot be earlier than start date.";
  }
  return null;
}

export function validateField(field: FieldConfig, data: PracticeData, locale: Locale, today = new Date()): string | null {
  if (!isVisible(field, data)) return null;
  const value = data[field.id];
  if (field.required && isEmpty(value)) return t(locale, "validation.required");
  if (isEmpty(value)) return null;
  if (field.kind === "repeater") return validateRepeater(field, value, locale);
  const stringValue = String(value).trim();
  if (field.maxLength && stringValue.length > field.maxLength) {
    return locale === "zh" ? `最多输入 ${field.maxLength} 个字符。` : `Enter no more than ${field.maxLength} characters.`;
  }
  if (field.fictionalRule === "email" && isSuspectedRealEmail(stringValue)) return t(locale, "validation.email");
  if (field.fictionalRule === "phone" && !/555|DEMO/i.test(stringValue)) return t(locale, "validation.phone");
  if (field.fictionalRule === "passport" && !/^DEMO/i.test(stringValue)) return t(locale, "validation.passport");
  if (field.fictionalRule === "nationalId" && !/^DEMO/i.test(stringValue)) return t(locale, "validation.nationalId");
  if (field.fictionalRule === "address" && !/EXAMPLE|SAMPLE|DEMO/i.test(stringValue)) return t(locale, "validation.address");
  if (field.id === "arrivalDate" && stringValue < dateOnly(today)) return t(locale, "validation.pastArrival");
  return null;
}

export function validateStep(stepIndex: number, data: PracticeData, locale: Locale, today = new Date()): ValidationErrors {
  const step = STEPS[stepIndex];
  if (!step || step.kind !== "form") return {};
  const errors: ValidationErrors = {};
  for (const field of step.fields) {
    const error = validateField(field, data, locale, today);
    if (error) errors[field.id] = error;
  }
  if (step.id === "passport") {
    const issue = String(data.passportIssueDate || "");
    const expiration = String(data.passportExpiration || "");
    if (issue && expiration && expiration <= issue) errors.passportExpiration = t(locale, "validation.passportDates");
  }
  if (step.id === "travel") {
    const arrival = String(data.arrivalDate || "");
    const departure = String(data.departureDate || "");
    if (arrival && departure && departure < arrival) errors.departureDate = t(locale, "validation.departure");
  }
  return errors;
}

export function sectionProgress(stepIndex: number, data: PracticeData, locale: Locale): { answered: number; total: number; errors: number } {
  const step = STEPS[stepIndex];
  if (!step || step.kind !== "form") return { answered: 0, total: 0, errors: 0 };
  const visible = step.fields.filter((field) => isVisible(field, data));
  const answered = visible.filter((field) => !isEmpty(data[field.id])).length;
  return { answered, total: visible.length, errors: Object.keys(validateStep(stepIndex, data, locale)).length };
}

export function stepStatus(stepIndex: number, data: PracticeData, locale: Locale): "notStarted" | "started" | "complete" | "error" {
  const step = STEPS[stepIndex];
  if (!step || step.kind !== "form") return "notStarted";
  const progress = sectionProgress(stepIndex, data, locale);
  if (progress.answered === 0) return "notStarted";
  if (progress.errors > 0) return "error";
  const requiredVisible = step.fields.filter((field) => field.required && isVisible(field, data));
  return requiredVisible.every((field) => !isEmpty(data[field.id])) ? "complete" : "started";
}

export function overallCompletion(data: PracticeData, locale: Locale): number {
  const counts = FORM_STEPS.reduce((result, step) => {
    const stepIndex = STEPS.indexOf(step);
    const visible = step.fields.filter((field) => isVisible(field, data));
    const completed = visible.filter((field) => !isEmpty(data[field.id]) && !validateField(field, data, locale)).length;
    return { complete: result.complete + completed, total: result.total + visible.length };
  }, { complete: 0, total: 0 });
  return counts.total ? Math.round((counts.complete / counts.total) * 100) : 0;
}

export function allFormErrors(data: PracticeData, locale: Locale): Record<string, ValidationErrors> {
  return Object.fromEntries(FORM_STEPS.map((step) => {
    const index = STEPS.indexOf(step);
    return [step.id, validateStep(index, data, locale)];
  }));
}

export function generatePracticeNumber(date = new Date(), random = Math.random()): string {
  const year = date.getFullYear();
  const code = Math.floor(random * 36 ** 4).toString(36).toUpperCase().padStart(4, "0");
  return `PRACTICE-${year}-${code}`;
}

export function maskPracticeValue(value: unknown): string {
  const textValue = Array.isArray(value) ? `${value.length} item(s)` : String(value ?? "");
  if (textValue.length <= 4) return "••••";
  return `${textValue.slice(0, 2)}${"•".repeat(Math.min(8, textValue.length - 4))}${textValue.slice(-2)}`;
}

export function sectionLabelForError(fieldId: string, locale: Locale): string {
  for (const step of FORM_STEPS) {
    const field = step.fields.find((candidate) => candidate.id === fieldId);
    if (field) return `${text(step.shortTitle, locale)} · ${text(field.label, locale)}`;
  }
  return fieldId;
}

export function draftCompletion(draft: PracticeDraft, locale: Locale): number {
  return overallCompletion(draft.data, locale);
}
