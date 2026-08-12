import type { Locale, BilingualText } from "./types.ts";

const messages: Record<Locale, Record<string, string>> = {
  zh: {
    "app.name": "签证表格填写练习平台",
    "app.nameEn": "Visa Form Practice Lab",
    "banner": "非官方网站 · 仅供练习 · 请勿填写真实个人信息",
    "nav.home": "首页",
    "nav.drafts": "练习草稿",
    "nav.help": "帮助",
    "nav.privacy": "隐私",
    "nav.language": "English",
    "action.start": "开始练习",
    "action.example": "使用虚构示例预览",
    "action.continue": "保存并继续",
    "action.save": "保存草稿",
    "action.back": "返回",
    "action.clearSection": "清空本节",
    "action.clearAll": "清除全部练习数据",
    "action.cancel": "取消",
    "action.confirm": "确认",
    "action.edit": "编辑",
    "action.print": "打开浏览器打印",
    "action.home": "返回首页",
    "action.review": "返回 Review",
    "action.new": "创建新练习",
    "action.delete": "删除当前练习",
    "status.notStarted": "未开始",
    "status.started": "已开始",
    "status.complete": "已完成",
    "status.error": "存在错误",
    "status.saved": "已保存到此浏览器",
    "status.saving": "正在保存…",
    "status.unavailable": "浏览器本地存储不可用，草稿无法持久保存",
    "form.required": "必填",
    "form.optional": "选填",
    "form.fictional": "仅限虚构数据",
    "form.yes": "是 / Yes",
    "form.no": "否 / No",
    "form.select": "请选择",
    "form.add": "添加一项",
    "form.remove": "删除",
    "form.moveUp": "上移",
    "form.moveDown": "下移",
    "form.errorSummary": "请先修正以下问题",
    "form.simplified": "字段经过简化和重新组织，仅用于练习，不代表当前官方申请表的完整内容或法律要求。",
    "mode.example": "正在查看虚构示例",
    "privacy.local": "所有练习数据只保存在当前浏览器，不会上传到服务器。",
    "footer": "Unofficial educational simulation. Not affiliated with or endorsed by the U.S. Government.",
    "validation.required": "请完成此项。",
    "validation.email": "请使用 example.com 等保留域名，例如 alex@example.com，不要填写真实邮箱。",
    "validation.phone": "请使用明显虚构的 555 示例号码，例如 +1 202-555-0100。",
    "validation.passport": "仅输入以 DEMO 开头的虚构号码，例如 DEMO123456。",
    "validation.nationalId": "仅输入以 DEMO 开头的虚构识别号码。",
    "validation.address": "请使用明显虚构的 Example / Sample 地址。",
    "validation.pastArrival": "预计到达日期不能早于今天。",
    "validation.departure": "离开日期不能早于到达日期。",
    "validation.passportDates": "护照到期日期必须晚于签发日期。",
    "validation.explanation": "选择 Yes 后需要填写练习说明。",
    "toast.saved": "草稿已保存到此浏览器。",
    "toast.cleared": "练习数据已清除。",
    "toast.imported": "练习草稿已导入。",
    "toast.exported": "练习数据已导出到设备。"
  },
  en: {
    "app.name": "Visa Form Practice Lab",
    "app.nameEn": "签证表格填写练习平台",
    "banner": "Unofficial website · Practice only · Do not enter real personal information",
    "nav.home": "Home",
    "nav.drafts": "Drafts",
    "nav.help": "Help",
    "nav.privacy": "Privacy",
    "nav.language": "中文",
    "action.start": "Start practice",
    "action.example": "Preview fictional example",
    "action.continue": "Save and Continue",
    "action.save": "Save Draft",
    "action.back": "Back",
    "action.clearSection": "Clear This Section",
    "action.clearAll": "Clear all practice data",
    "action.cancel": "Cancel",
    "action.confirm": "Confirm",
    "action.edit": "Edit",
    "action.print": "Open browser print",
    "action.home": "Back to home",
    "action.review": "Back to Review",
    "action.new": "Create new practice",
    "action.delete": "Delete this practice",
    "status.notStarted": "Not started",
    "status.started": "In progress",
    "status.complete": "Complete",
    "status.error": "Has errors",
    "status.saved": "Saved in this browser",
    "status.saving": "Saving…",
    "status.unavailable": "Local browser storage is unavailable; drafts cannot persist",
    "form.required": "Required",
    "form.optional": "Optional",
    "form.fictional": "Fictional data only",
    "form.yes": "Yes / 是",
    "form.no": "No / 否",
    "form.select": "Select an option",
    "form.add": "Add item",
    "form.remove": "Remove",
    "form.moveUp": "Move up",
    "form.moveDown": "Move down",
    "form.errorSummary": "Please correct the following issues",
    "form.simplified": "Fields are simplified and reorganized for practice. They do not represent the complete current official form or legal requirements.",
    "mode.example": "Viewing a fictional example",
    "privacy.local": "All practice data stays in this browser and is never uploaded to a server.",
    "footer": "Unofficial educational simulation. Not affiliated with or endorsed by the U.S. Government.",
    "validation.required": "Please complete this field.",
    "validation.email": "Use a reserved domain such as alex@example.com. Do not enter a real email.",
    "validation.phone": "Use an obviously fictional 555 number, such as +1 202-555-0100.",
    "validation.passport": "Use a fictional number beginning with DEMO, such as DEMO123456.",
    "validation.nationalId": "Use a fictional identification number beginning with DEMO.",
    "validation.address": "Use an obviously fictional Example or Sample address.",
    "validation.pastArrival": "The intended arrival date cannot be in the past.",
    "validation.departure": "Departure cannot be earlier than arrival.",
    "validation.passportDates": "Passport expiration must be later than issuance.",
    "validation.explanation": "A practice explanation is required after selecting Yes.",
    "toast.saved": "Draft saved in this browser.",
    "toast.cleared": "Practice data cleared.",
    "toast.imported": "Practice draft imported.",
    "toast.exported": "Practice data exported to this device."
  }
};

export function detectLocale(): Locale {
  try {
    const stored = globalThis.localStorage?.getItem("vfpl_locale");
    if (stored === "zh" || stored === "en") return stored;
  } catch {
    // Continue with the browser language when storage is unavailable.
  }
  return globalThis.navigator?.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function t(locale: Locale, key: string, variables: Record<string, string | number> = {}): string {
  const template = messages[locale][key] ?? messages.en[key] ?? key.split(".").at(-1) ?? key;
  return Object.entries(variables).reduce(
    (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
    template
  );
}

export function text(value: BilingualText, locale: Locale): string {
  return value[locale] || value.en;
}

export function setLocale(locale: Locale): void {
  localStorage.setItem("vfpl_locale", locale);
}
