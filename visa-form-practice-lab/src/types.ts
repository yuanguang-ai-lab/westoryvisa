export type Locale = "zh" | "en";
export type BilingualText = { en: string; zh: string };
export type StepKind = "welcome" | "form" | "review" | "print" | "finished";
export type FieldKind = "text" | "textarea" | "date" | "email" | "tel" | "number" | "select" | "yesno" | "checkbox" | "repeater" | "stringList";
export type FieldCondition = { field: string; equals?: unknown; notEquals?: unknown; oneOf?: unknown[] };
export type FieldOption = { value: string; label: BilingualText };
export type RepeaterColumn = { key: string; label: BilingualText; type?: "text" | "date" | "select"; options?: FieldOption[] };

export type FieldConfig = {
  id: string;
  kind: FieldKind;
  label: BilingualText;
  hint?: BilingualText;
  required?: boolean;
  sensitive?: boolean;
  placeholder?: BilingualText;
  options?: FieldOption[];
  condition?: FieldCondition;
  columns?: RepeaterColumn[];
  addLabel?: BilingualText;
  fictionalRule?: "email" | "phone" | "passport" | "nationalId" | "address";
  maxLength?: number;
};

export type StepConfig = {
  id: string;
  kind: StepKind;
  title: BilingualText;
  shortTitle: BilingualText;
  description: BilingualText;
  help: BilingualText;
  fields: FieldConfig[];
};

export type PracticeData = Record<string, unknown>;

export type PracticeDraft = {
  id: string;
  schemaVersion: 1;
  practiceNumber: string;
  name: string;
  mode: "blank" | "example";
  createdAt: string;
  updatedAt: string;
  currentStep: number;
  acknowledged: true;
  data: PracticeData;
};

export type ValidationErrors = Record<string, string>;
