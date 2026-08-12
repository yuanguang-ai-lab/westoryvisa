import { FORM_STEPS, STEPS } from "./config.ts";
import { displayFieldValue, renderFields } from "./form.ts";
import { icon } from "./icons.ts";
import { t, text } from "./i18n.ts";
import { draftCompletion } from "./validation.ts";
import { allFormErrors, isVisible, maskPracticeValue, stepStatus } from "./validation.ts";
import { bilingual, escapeHtml, renderApplicationFrame, renderErrorSummary, renderShell } from "./ui.ts";
import type { Locale, PracticeDraft, ValidationErrors } from "./types.ts";

const fullDisclaimer = {
  zh: "本网站是独立制作的表格填写练习工具，与美国政府、美国国务院、任何使领馆或签证服务机构不存在隶属、合作、授权或背书关系。本网站不能提交真实签证申请。请勿输入真实护照号码、身份证号码、家庭住址、电话号码、电子邮箱、旅行记录或其他敏感个人信息。",
  en: "This independently created form-practice tool is not affiliated with, partnered with, authorized by, or endorsed by the U.S. Government, the U.S. Department of State, any embassy, consulate, or visa service provider. It cannot submit a real visa application. Do not enter real passport numbers, identification numbers, addresses, phone numbers, email addresses, travel history, or other sensitive information."
};

export function renderHome(locale: Locale): string {
  const content = `<section class="home-hero">
      <div class="hero-copy">
        <span class="eyebrow">Unofficial educational simulation</span>
        <h1>${locale === "zh" ? "先熟悉长表单，\n再面对正式流程。" : "Practice the long form\nbefore the real process."}</h1>
        <p>${locale === "zh" ? "用完全虚构的示例资料，练习多步骤签证表格的信息分类、条件问题、草稿保存和复核流程。" : "Use entirely fictional data to practice information categories, conditional questions, local drafts and review in a long visa-style form."}</p>
        <div class="hero-actions"><a class="primary-button" href="#/start">${escapeHtml(t(locale, "action.start"))}${icon("arrowRight")}</a><a class="text-button" href="#/start?mode=example">${icon("eye")}${escapeHtml(t(locale, "action.example"))}</a></div>
        <div class="hero-trust"><span>${icon("lock")} ${locale === "zh" ? "无需账号" : "No account"}</span><span>${icon("save")} ${locale === "zh" ? "仅浏览器本地保存" : "Browser-only storage"}</span><span>${icon("shield")} ${locale === "zh" ? "禁止真实资料" : "Fictional data only"}</span></div>
      </div>
      <div class="product-visual" aria-label="${locale === "zh" ? "练习表单界面预览" : "Practice form interface preview"}">
        <div class="visual-window">
          <header><span class="visual-mark">VF</span><div><strong>Practice session</strong><small>PRACTICE-2026-DEMO</small></div><em>68%</em></header>
          <div class="visual-body"><aside>${[1,2,3,4,5,6].map((n) => `<span class="${n < 4 ? "done" : n === 4 ? "active" : ""}">${n < 4 ? icon("check") : n}</span>`).join("")}</aside><main><span class="visual-kicker">Travel information</span><h2>Build a fictional itinerary</h2><label><span>Purpose of Practice Trip</span><i>Educational Practice</i></label><label><span>Intended Arrival Date</span><i>2027-05-10</i></label><label><span>Practice Address</span><i>100 Example Avenue</i></label><div class="visual-next">Save and Continue ${icon("arrowRight")}</div></main></div>
        </div>
        <div class="visual-caption"><span class="caption-dot"></span><strong>${locale === "zh" ? "练习数据不会离开浏览器" : "Practice data never leaves your browser"}</strong></div>
      </div>
    </section>
    <section class="home-band feature-band"><div class="section-heading"><span class="eyebrow">Practice with clarity</span><h2>${locale === "zh" ? "不是代办，也不是申请入口。" : "Not a visa service. Not an application portal."}</h2><p>${locale === "zh" ? "这是一个安静、可重复、可清除的训练环境。" : "A calm, repeatable and erasable training environment."}</p></div><div class="feature-grid">
      <article>${icon("file")}<span>01</span><h3>${locale === "zh" ? "熟悉填写流程" : "Learn the flow"}</h3><p>${locale === "zh" ? "按重新组织的教学章节理解长表单的信息结构和条件分支。" : "Understand long-form structure and conditional branches through reorganized training sections."}</p></article>
      <article>${icon("save")}<span>02</span><h3>${locale === "zh" ? "浏览器本地保存" : "Local browser drafts"}</h3><p>${locale === "zh" ? "无需账号、后端或网络，刷新后仍可继续当前练习。" : "No account, backend or network is needed; continue after refresh."}</p></article>
      <article>${icon("shield")}<span>03</span><h3>${locale === "zh" ? "只用虚构资料" : "Fictional data only"}</h3><p>${locale === "zh" ? "示例域名、555 电话和 DEMO 编号帮助阻止真实个人信息。" : "Reserved domains, 555 numbers and DEMO identifiers help block real personal data."}</p></article>
    </div></section>
    <section class="home-band privacy-band"><div><span class="eyebrow">Privacy by architecture</span><h2>${locale === "zh" ? "数据不上传，才是真正的默认隐私。" : "Privacy begins by not uploading data."}</h2></div><div class="privacy-list"><p>${icon("lock")}<span><strong>${locale === "zh" ? "无登录系统" : "No login system"}</strong>${locale === "zh" ? "不创建账户，也不发送邮件或短信。" : "No accounts, email or SMS."}</span></p><p>${icon("globe")}<span><strong>${locale === "zh" ? "无远程请求" : "No remote requests"}</strong>${locale === "zh" ? "无追踪器、广告、远程字体或政府接口。" : "No trackers, ads, remote fonts or government APIs."}</span></p><p>${icon("trash")}<span><strong>${locale === "zh" ? "随时彻底清除" : "Erase at any time"}</strong>${locale === "zh" ? "一键删除全部本地练习草稿。" : "Delete every local practice draft in one action."}</span></p></div></section>
    <section class="home-band faq-band"><div class="section-heading"><span class="eyebrow">FAQ</span><h2>${locale === "zh" ? "开始前需要知道的事" : "Before you begin"}</h2></div><div class="faq-list">
      <details><summary>${locale === "zh" ? "这和真实 DS-160 完全一样吗？" : "Is this identical to the real DS-160?"}<span>${icon("plus")}</span></summary><p>${locale === "zh" ? "不是。字段经过简化、改写和重新组织，只用于熟悉长表单体验，不能替代当前官方说明。" : "No. Fields are simplified, rewritten and reorganized for long-form practice and do not replace current official guidance."}</p></details>
      <details><summary>${locale === "zh" ? "可以填写真实护照资料吗？" : "May I enter real passport details?"}<span>${icon("plus")}</span></summary><p>${locale === "zh" ? "不可以。系统会对真实邮箱、普通电话号码和非 DEMO 证件号进行阻止或提醒。" : "No. The simulator blocks or warns about real email domains, ordinary phone numbers and non-DEMO identifiers."}</p></details>
      <details><summary>${locale === "zh" ? "完成后会提交申请吗？" : "Does finishing submit an application?"}<span>${icon("plus")}</span></summary><p>${locale === "zh" ? "不会。完成页只代表本地练习结束，没有提交、付款、预约或政府连接。" : "No. Completion means only that local practice ended. There is no submission, payment, appointment or government connection."}</p></details>
    </div></section>
    <section class="full-disclaimer"><div>${icon("alert")}</div><div><span class="eyebrow">Important disclaimer</span><p>${escapeHtml(text(fullDisclaimer, locale))}</p></div></section>`;
  return renderShell(content, locale, "home", "home-page");
}

export function renderStart(locale: Locale, preferredMode: string): string {
  const content = `<section class="start-layout"><div class="start-copy"><a class="back-link" href="#/">${icon("arrowLeft")}${escapeHtml(t(locale, "nav.home"))}</a><span class="eyebrow">Before you start</span><h1>${locale === "zh" ? "只练流程，不留真实资料。" : "Practice the process, not real identity data."}</h1><p>${escapeHtml(text(fullDisclaimer, locale))}</p><div class="start-points"><p>${icon("save")}<span><strong>${locale === "zh" ? "仅保存在当前浏览器" : "Saved only in this browser"}</strong>${locale === "zh" ? "关闭浏览器仍可恢复；清除缓存后可能丢失。" : "You can resume after closing; clearing browser data may remove drafts."}</span></p><p>${icon("trash")}<span><strong>${locale === "zh" ? "随时清除" : "Clear at any time"}</strong>${locale === "zh" ? "草稿管理页提供单条删除和一键清除。" : "Drafts can be deleted individually or all at once."}</span></p><p>${icon("alert")}<span><strong>${locale === "zh" ? "不能提交" : "Cannot be submitted"}</strong>${locale === "zh" ? "练习结果不是签证申请，也不是确认凭证。" : "Practice output is not an application or confirmation document."}</span></p></div></div><div class="consent-panel"><span class="panel-number">01</span><h2>${locale === "zh" ? "确认练习边界" : "Confirm practice boundaries"}</h2><p>${locale === "zh" ? "进入后请始终使用 Alex Example、DEMO123456、alex@example.com 等虚构资料。" : "Always use fictional values such as Alex Example, DEMO123456 and alex@example.com."}</p><label class="consent-check"><input id="practiceConsent" type="checkbox"><span class="checkbox-mark">${icon("check")}</span><span>${locale === "zh" ? "我理解这是练习网站，并承诺只使用虚构资料" : "I understand this is a practice site and promise to use fictional data only"}</span></label><div class="consent-actions"><button class="primary-button" type="button" data-action="create-draft" data-mode="blank" disabled>${locale === "zh" ? "创建空白练习" : "Create blank practice"}${icon("arrowRight")}</button><button class="secondary-button ${preferredMode === "example" ? "recommended" : ""}" type="button" data-action="create-draft" data-mode="example" disabled>${icon("eye")}${locale === "zh" ? "载入虚构示例" : "Load fictional example"}</button></div><small>${locale === "zh" ? "两个选项都只会写入浏览器 localStorage。" : "Both options write only to browser localStorage."}</small></div></section>`;
  return renderShell(content, locale, "start", "start-page");
}

export function renderDrafts(drafts: PracticeDraft[], locale: Locale): string {
  const list = drafts.length ? `<div class="draft-grid">${drafts.map((draft) => `<article class="draft-card"><header><div><span class="mode-label ${draft.mode}">${draft.mode === "example" ? (locale === "zh" ? "虚构示例" : "Fictional example") : (locale === "zh" ? "空白练习" : "Blank practice")}</span><h2>${escapeHtml(draft.name)}</h2><small>${escapeHtml(draft.practiceNumber)}</small></div><strong>${draftCompletion(draft, locale)}%</strong></header><progress value="${draftCompletion(draft, locale)}" max="100">${draftCompletion(draft, locale)}%</progress><dl><div><dt>${locale === "zh" ? "创建" : "Created"}</dt><dd>${formatDate(draft.createdAt, locale)}</dd></div><div><dt>${locale === "zh" ? "最近保存" : "Last saved"}</dt><dd>${formatDate(draft.updatedAt, locale, true)}</dd></div></dl><div class="draft-primary-actions"><a class="primary-button" href="#/application/${encodeURIComponent(draft.id)}/${draft.currentStep}">${locale === "zh" ? "继续填写" : "Continue"}${icon("arrowRight")}</a><button class="icon-button" type="button" data-action="rename-draft" data-id="${escapeHtml(draft.id)}" aria-label="${locale === "zh" ? "重命名" : "Rename"}">${icon("edit")}</button></div><div class="draft-secondary-actions"><button type="button" data-action="duplicate-draft" data-id="${escapeHtml(draft.id)}">${icon("copy")}${locale === "zh" ? "复制" : "Duplicate"}</button><button type="button" data-action="export-draft" data-id="${escapeHtml(draft.id)}">${icon("download")}${locale === "zh" ? "导出" : "Export"}</button><button class="danger" type="button" data-action="delete-draft" data-id="${escapeHtml(draft.id)}">${icon("trash")}${locale === "zh" ? "删除" : "Delete"}</button></div></article>`).join("")}</div>` : `<section class="empty-state">${icon("file")}<h2>${locale === "zh" ? "还没有练习草稿" : "No practice drafts yet"}</h2><p>${locale === "zh" ? "创建空白练习，或载入 Alex Example 虚构示例。" : "Create a blank practice or load the fictional Alex Example record."}</p><a class="primary-button" href="#/start">${escapeHtml(t(locale, "action.start"))}${icon("arrowRight")}</a></section>`;
  const content = `<section class="page-heading"><div><span class="eyebrow">Local draft library</span><h1>${escapeHtml(t(locale, "nav.drafts"))}</h1><p>${escapeHtml(t(locale, "privacy.local"))}</p></div><a class="primary-button" href="#/start">${icon("plus")}${locale === "zh" ? "新建练习" : "New practice"}</a></section>${list}<section class="draft-tools"><div><h2>${locale === "zh" ? "导入与清理" : "Import and cleanup"}</h2><p>${locale === "zh" ? "导入文件最大 1MB，只接受本平台导出的 JSON。" : "Imports are limited to 1MB and must be JSON exported by this platform."}</p></div><div><label class="secondary-button" for="importDraftFile">${icon("upload")}${locale === "zh" ? "导入练习数据" : "Import practice data"}</label><input class="sr-only" id="importDraftFile" type="file" accept="application/json,.json"><button class="danger-button" type="button" data-action="clear-all-drafts" ${drafts.length ? "" : "disabled"}>${icon("trash")}${escapeHtml(t(locale, "action.clearAll"))}</button></div></section>`;
  return renderShell(content, locale, "drafts", "content-page");
}

export function renderHelp(locale: Locale): string {
  const items = locale === "zh" ? [
    ["如何开始", "在首页选择“开始练习”，勾选虚构资料承诺，然后创建空白练习或载入示例。"],
    ["如何切换语言", "使用右上角语言按钮。切换语言不会清空任何字段。"],
    ["如何保存与恢复", "停止输入约 800ms 后自动保存，也可点击“保存草稿”。从草稿管理页继续。"],
    ["如何清除数据", "可在草稿管理页删除单条，或使用“一键清除全部练习数据”。"],
    ["为什么不能填写真实信息", "浏览器本地数据仍可能被同设备其他用户看到，也可能出现在导出文件中。练习不需要真实身份。"],
    ["为什么不能代替正式申请", "字段经过教学化改写，顺序和内容不代表当前官方表格，也不产生任何资格判断。"],
    ["键盘与无障碍", "使用 Tab 在控件间移动，Enter 激活按钮，方向键操作单选项。错误摘要可直接跳到字段。"]
  ] : [
    ["How to begin", "Choose Start practice, accept the fictional-data promise, then create a blank session or load the example."],
    ["How to switch languages", "Use the language button in the top-right. Switching never clears form values."],
    ["How saving and recovery work", "Auto-save runs about 800ms after input stops. You can also use Save Draft and resume from Drafts."],
    ["How to clear data", "Delete one draft or use Clear all practice data from the Drafts page."],
    ["Why real information is prohibited", "Even local data may be visible to other users of the device or included in exports. Practice never needs real identity data."],
    ["Why this cannot replace a real application", "Fields are rewritten for education. Their order and content do not represent the current official form and no eligibility judgment is produced."],
    ["Keyboard and accessibility", "Use Tab to move, Enter to activate controls and arrow keys for radio choices. Error-summary links move focus to fields."]
  ];
  const content = `<section class="page-heading narrow"><div><span class="eyebrow">Guide</span><h1>${escapeHtml(t(locale, "nav.help"))}</h1><p>${locale === "zh" ? "使用这个练习平台时最常见的问题。" : "Common questions about using this practice platform."}</p></div></section><section class="help-layout"><nav aria-label="${locale === "zh" ? "帮助目录" : "Help contents"}">${items.map((item, index) => `<a href="#help-${index + 1}">${String(index + 1).padStart(2, "0")} · ${escapeHtml(item[0])}</a>`).join("")}</nav><div>${items.map((item, index) => `<article id="help-${index + 1}"><span>${String(index + 1).padStart(2, "0")}</span><h2>${escapeHtml(item[0])}</h2><p>${escapeHtml(item[1])}</p></article>`).join("")}</div></section>`;
  return renderShell(content, locale, "help", "content-page");
}

export function renderPrivacy(locale: Locale): string {
  const content = `<section class="privacy-hero"><div>${icon("lock")}</div><span class="eyebrow">Privacy by default</span><h1>${locale === "zh" ? "没有服务器数据，\n也就没有远端账户档案。" : "No server data.\nNo remote user profile."}</h1><p>${locale === "zh" ? "这个项目把隐私限制落实在架构中，而不只是写在声明里。" : "This project enforces privacy in its architecture, not only in a policy paragraph."}</p></section><section class="privacy-policy"><article><span>01</span><h2>${locale === "zh" ? "默认不需要账户" : "No account by default"}</h2><p>${locale === "zh" ? "不存在注册、登录、邮件或短信验证，也不会创建远端身份。" : "There is no registration, login, email or SMS verification, and no remote identity is created."}</p></article><article><span>02</span><h2>${locale === "zh" ? "内容只保存在本地" : "Content stays local"}</h2><p>${locale === "zh" ? "表单草稿写入当前浏览器的 localStorage，不主动上传到任何服务器。" : "Drafts are written to this browser's localStorage and are not actively uploaded anywhere."}</p></article><article><span>03</span><h2>${locale === "zh" ? "没有广告追踪器" : "No advertising trackers"}</h2><p>${locale === "zh" ? "不使用分析 SDK、设备指纹、行为录屏、广告脚本或远程日志。" : "No analytics SDK, fingerprinting, session recording, advertising script or remote logging is used."}</p></article><article><span>04</span><h2>${locale === "zh" ? "删除无法恢复" : "Deletion cannot be recovered"}</h2><p>${locale === "zh" ? "清除浏览器数据或删除草稿后，网站维护者无法帮助恢复。" : "After browser data is cleared or a draft is deleted, the site maintainer cannot restore it."}</p></article><article><span>05</span><h2>${locale === "zh" ? "导出文件由您保管" : "You control exported files"}</h2><p>${locale === "zh" ? "JSON 导出文件保存在您的设备上。导出前请再次确认没有真实个人信息。" : "JSON exports are saved to your device. Confirm they contain no real personal information before export."}</p></article><article><span>06</span><h2>${locale === "zh" ? "本地存储有局限" : "Local storage has limits"}</h2><p>${locale === "zh" ? "无痕模式、浏览器清理策略、缓存清除或设备损坏都可能导致草稿丢失。" : "Private mode, browser cleanup, cache deletion or device failure may remove drafts."}</p></article></section><section class="privacy-cta"><h2>${locale === "zh" ? "清除当前浏览器里的全部练习数据" : "Erase all practice data in this browser"}</h2><p>${locale === "zh" ? "此操作不可撤销。" : "This action cannot be undone."}</p><button class="danger-button" type="button" data-action="clear-all-drafts">${icon("trash")}${escapeHtml(t(locale, "action.clearAll"))}</button></section>`;
  return renderShell(content, locale, "privacy", "content-page");
}

export function renderApplication(draft: PracticeDraft, stepIndex: number, locale: Locale, errors: ValidationErrors, revealSensitive: boolean): string {
  const step = STEPS[stepIndex] || STEPS[0];
  let main = "";
  if (step.kind === "welcome") main = renderWelcomeStep(draft, locale);
  else if (step.kind === "form") main = renderFormStep(draft, stepIndex, locale, errors);
  else if (step.kind === "review") main = renderReview(draft, locale, revealSensitive);
  else if (step.kind === "print") main = renderPrint(draft, locale, revealSensitive);
  else main = renderFinished(draft, locale);
  return renderShell(renderApplicationFrame(draft, locale, stepIndex, main), locale, "application", "application-page");
}

function stepHeading(index: number, locale: Locale): string {
  const step = STEPS[index];
  return `<header class="form-page-heading"><span class="eyebrow">Step ${String(index + 1).padStart(2, "0")}</span><h1 tabindex="-1" id="stepTitle">${escapeHtml(text(step.title, locale))}</h1><p>${escapeHtml(text(step.description, locale))}</p></header>`;
}

function renderWelcomeStep(draft: PracticeDraft, locale: Locale): string {
  return `${stepHeading(0, locale)}<section class="welcome-session"><div class="session-number"><span>${locale === "zh" ? "内部练习编号" : "Internal practice number"}</span><strong>${escapeHtml(draft.practiceNumber)}</strong><small>PRACTICE prefix · Not a government identifier</small></div><div class="welcome-rule-grid"><article>${icon("shield")}<h2>${locale === "zh" ? "继续使用虚构资料" : "Keep data fictional"}</h2><p>${locale === "zh" ? "系统会阻止明显的真实邮箱、电话号码和证件号码。" : "The simulator blocks obvious real email, phone and identification data."}</p></article><article>${icon("save")}<h2>${locale === "zh" ? "自动保存到浏览器" : "Auto-saved locally"}</h2><p>${locale === "zh" ? "停止输入约 800ms 后自动保存，可随时回到草稿管理。" : "Changes save about 800ms after typing stops and remain available from Drafts."}</p></article><article>${icon("alert")}<h2>${locale === "zh" ? "没有真实提交" : "No real submission"}</h2><p>${locale === "zh" ? "完成只代表练习结束，不会连接、付款或提交。" : "Finishing only ends the practice; nothing is connected, paid or submitted."}</p></article></div><div class="simplified-notice">${icon("help")}<p>${escapeHtml(t(locale, "form.simplified"))}</p></div></section>${renderStepActions(0, locale, false)}`;
}

function renderFormStep(draft: PracticeDraft, index: number, locale: Locale, errors: ValidationErrors): string {
  const step = STEPS[index];
  const isSecurity = step.id === "security-practice";
  return `${stepHeading(index, locale)}${isSecurity ? `<div class="professional-note">${icon("alert")}<p>${locale === "zh" ? "本练习工具不会判断资格。正式申请时应阅读官方说明，并在需要时咨询具备资质的专业人士。" : "This practice tool does not determine eligibility. For a real application, read official guidance and consult a qualified professional when needed."}</p></div>` : ""}${renderErrorSummary(errors, step.fields, locale)}<form id="practiceForm" novalidate>${renderFields(step.fields, draft.data, locale, errors)}</form>${renderStepActions(index, locale, true)}`;
}

function renderStepActions(index: number, locale: Locale, canClear: boolean): string {
  return `<div class="form-actions"><div>${index > 0 ? `<button class="text-button" type="button" data-action="previous-step">${icon("arrowLeft")}${escapeHtml(t(locale, "action.back"))}</button>` : ""}${canClear ? `<button class="text-button danger-text" type="button" data-action="clear-section">${icon("trash")}${escapeHtml(t(locale, "action.clearSection"))}</button>` : ""}</div><div><button class="secondary-button" type="button" data-action="save-draft">${icon("save")}${escapeHtml(t(locale, "action.save"))}</button><button class="primary-button" type="button" data-action="continue-step">${escapeHtml(t(locale, "action.continue"))}${icon("arrowRight")}</button></div></div>`;
}

function renderReview(draft: PracticeDraft, locale: Locale, revealSensitive: boolean): string {
  const groupedErrors = allFormErrors(draft.data, locale);
  const errorCount = Object.values(groupedErrors).reduce((total, errors) => total + Object.keys(errors).length, 0);
  return `${stepHeading(STEPS.findIndex((step) => step.id === "review"), locale)}<div class="review-toolbar"><div class="review-summary ${errorCount ? "has-errors" : "complete"}">${errorCount ? icon("alert") : icon("check")}<span><strong>${errorCount ? (locale === "zh" ? `${errorCount} 项练习字段待完善` : `${errorCount} practice fields need attention`) : (locale === "zh" ? "所有可见必填项已完成" : "All visible required fields are complete")}</strong><small>${locale === "zh" ? "这只是本地练习状态，不代表真实申请完成度。" : "This is local practice status, not real application completion."}</small></span></div><button class="secondary-button" type="button" data-action="toggle-sensitive">${icon("eye")}${revealSensitive ? (locale === "zh" ? "隐藏练习值" : "Mask practice values") : (locale === "zh" ? "临时显示练习值" : "Temporarily show values")}</button></div><div class="review-sections">${FORM_STEPS.map((step) => {
    const index = STEPS.indexOf(step);
    const errors = groupedErrors[step.id] || {};
    const visibleFields = step.fields.filter((field) => isVisible(field, draft.data));
    return `<article class="review-section ${Object.keys(errors).length ? "has-errors" : ""}"><header><div><span>${Object.keys(errors).length ? icon("alert") : icon("check")}</span><div><h2>${escapeHtml(text(step.title, locale))}</h2><small>${Object.keys(errors).length ? (locale === "zh" ? `${Object.keys(errors).length} 项未完成` : `${Object.keys(errors).length} incomplete`) : (locale === "zh" ? "练习章节完整" : "Practice section complete")}</small></div></div><a href="#/application/${encodeURIComponent(draft.id)}/${index}">${icon("edit")}${escapeHtml(t(locale, "action.edit"))}</a></header><dl>${visibleFields.map((field) => {
      const raw = draft.data[field.id];
      const display = field.sensitive && !revealSensitive ? maskPracticeValue(raw) : displayFieldValue(field, raw, locale);
      return `<div class="${errors[field.id] ? "invalid" : ""}"><dt>${escapeHtml(text(field.label, locale))}${field.sensitive ? `<span>${icon("shield")}</span>` : ""}</dt><dd>${escapeHtml(display).replaceAll("\n", "<br>")}${errors[field.id] ? `<small>${escapeHtml(errors[field.id])}</small>` : ""}</dd></div>`;
    }).join("")}</dl></article>`;
  }).join("")}</div><div class="form-actions"><div><button class="text-button" type="button" data-action="previous-step">${icon("arrowLeft")}${escapeHtml(t(locale, "action.back"))}</button></div><div><button class="secondary-button" type="button" data-action="save-draft">${icon("save")}${escapeHtml(t(locale, "action.save"))}</button><button class="primary-button" type="button" data-action="continue-step">${locale === "zh" ? "查看打印练习副本" : "View print practice copy"}${icon("arrowRight")}</button></div></div>`;
}

function renderPrint(draft: PracticeDraft, locale: Locale, revealSensitive: boolean): string {
  return `${stepHeading(STEPS.findIndex((step) => step.id === "print"), locale)}<div class="print-toolbar"><div class="simplified-notice">${icon("alert")}<p>${locale === "zh" ? "打印文件由您自行保管。请确认其中没有真实个人信息。" : "You are responsible for the printed file. Confirm it contains no real personal information."}</p></div><button class="primary-button" type="button" data-action="print-page">${icon("print")}${escapeHtml(t(locale, "action.print"))}</button></div><article class="print-sheet"><header><div><strong>PRACTICE COPY</strong><span>NOT A VISA APPLICATION</span></div><dl><div><dt>Practice number</dt><dd>${escapeHtml(draft.practiceNumber)}</dd></div><div><dt>Generated</dt><dd>${formatDate(new Date().toISOString(), locale, true)}</dd></div></dl></header><div class="print-watermark">PRACTICE</div>${FORM_STEPS.map((step) => `<section><h2>${escapeHtml(text(step.title, locale))}</h2><dl>${step.fields.filter((field) => isVisible(field, draft.data)).map((field) => `<div><dt>${escapeHtml(text(field.label, locale))}</dt><dd>${escapeHtml(field.sensitive && !revealSensitive ? maskPracticeValue(draft.data[field.id]) : displayFieldValue(field, draft.data[field.id], locale)).replaceAll("\n", "<br>")}</dd></div>`).join("")}</dl></section>`).join("")}<footer>UNOFFICIAL TRAINING MATERIAL · Visa Form Practice Lab</footer></article><div class="form-actions"><div><button class="text-button" type="button" data-action="previous-step">${icon("arrowLeft")}${escapeHtml(t(locale, "action.back"))}</button></div><div><button class="secondary-button" type="button" data-action="toggle-sensitive">${icon("eye")}${revealSensitive ? (locale === "zh" ? "隐藏练习值" : "Mask values") : (locale === "zh" ? "显示练习值" : "Show values")}</button><button class="primary-button" type="button" data-action="continue-step">${locale === "zh" ? "完成本地练习" : "Finish local practice"}${icon("arrowRight")}</button></div></div>`;
}

function renderFinished(draft: PracticeDraft, locale: Locale): string {
  return `<section class="finished-view"><div class="finished-check">${icon("check")}</div><span class="eyebrow">Local practice complete</span><h1 tabindex="-1" id="stepTitle">${locale === "zh" ? "您已完成本地练习" : "You completed the local practice"}</h1><p>${locale === "zh" ? "没有申请被提交，也没有信息被发送到政府网站。当前草稿仍保存在这个浏览器中。" : "No application was submitted and no information was sent to a government website. This draft remains in your browser."}</p><div class="finished-number"><span>${locale === "zh" ? "内部练习编号" : "Internal practice number"}</span><strong>${escapeHtml(draft.practiceNumber)}</strong></div><div class="finished-actions"><a class="primary-button" href="#/application/${encodeURIComponent(draft.id)}/${STEPS.findIndex((step) => step.id === "review")}">${icon("edit")}${escapeHtml(t(locale, "action.review"))}</a><button class="secondary-button" type="button" data-action="print-page">${icon("print")}${locale === "zh" ? "打印练习副本" : "Print practice copy"}</button><a class="secondary-button" href="#/start">${icon("plus")}${escapeHtml(t(locale, "action.new"))}</a><button class="danger-button" type="button" data-action="delete-current-draft">${icon("trash")}${escapeHtml(t(locale, "action.delete"))}</button></div><small>NOT SUBMITTED · UNOFFICIAL TRAINING MATERIAL</small></section>`;
}

function formatDate(value: string, locale: Locale, includeTime = false): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", includeTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" }).format(date);
}
