(function initializeAppointmentAgent() {
  "use strict";

  const AGENT_VERSION = "0.9.3";
  if (globalThis.__docflowAppointmentAgentVersion === AGENT_VERSION) return;
  globalThis.__docflowAppointmentAgentVersion = AGENT_VERSION;
  const core = globalThis.DocFlowAgentCore;
  let runningJobId = "";

  async function report(payload) {
    try {
      return await chrome.runtime.sendMessage({
        type: "docflow.pageStatus",
        url: location.href,
        title: document.title,
        ...payload
      });
    } catch (_error) {
      return null;
    }
  }

  function safetyState() {
    const url = new URL(location.href);
    const body = String(document.body?.innerText || "").slice(0, 40000);
    if (url.protocol !== "https:" || url.hostname !== "www.usvisascheduling.com") {
      return { safe: false, code: "wrong_domain", reason: "当前页面不是预约网站。" };
    }
    if (document.querySelector("iframe[src*='recaptcha'], .g-recaptcha, input[name*='captcha' i]")
      || /\bCAPTCHA\b|SECURITY CODE|VERIFICATION CODE|ONE[- ]TIME CODE/i.test(body)) {
      return { safe: false, code: "verification_required", reason: "检测到验证码或登录验证，请人工完成后再继续。" };
    }
    const route = `${url.pathname} ${document.title}`;
    if (/\/(?:payment|payments|fee|receipt|schedule|calendar|appointment-confirmation|confirm-appointment)(?:\/|$)/i.test(url.pathname)
      || /^(?:PAYMENT|MRV FEE|SCHEDULE APPOINTMENT|APPOINTMENT CONFIRMATION)$/i.test(document.title.trim())) {
      return {
        safe: false,
        code: "appointment_hard_stop",
        reason: "已到缴费、选时间或最终预约确认边界，请由顾问人工完成。"
      };
    }
    if (/PASSWORD|SIGN IN CODE|AUTHENTICATOR/i.test(route)) {
      return { safe: false, code: "credentials", reason: "登录凭据和验证信息必须由顾问人工输入。" };
    }
    return { safe: true, code: "safe", reason: "" };
  }

  function scoreControl(element, action) {
    const context = core.normalize([
      core.controlLabel(element),
      core.questionContextFor(element),
      element.id || "",
      element.name || ""
    ].join(" "));
    return (action.labelTerms || []).reduce((score, term) => (
      context.includes(core.normalize(term)) ? score + 1 : score
    ), 0) + (action.controlHints || []).reduce((score, hint) => (
      context.includes(core.normalize(hint)) ? score + 4 : score
    ), 0);
  }

  function commitInput(element) {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.blur();
  }

  async function applyHtmlDate(action) {
    const candidates = Array.from(document.querySelectorAll("input[type='date']"))
      .filter(core.isVisible)
      .map((element) => ({ element, score: scoreControl(element, action) }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score);
    const element = candidates[0]?.element;
    if (!element) return { status: "not_found", changed: false };
    const setter = Object.getOwnPropertyDescriptor(
      globalThis.HTMLInputElement?.prototype || {}, "value"
    )?.set;
    if (setter) setter.call(element, String(action.value));
    else element.value = String(action.value);
    commitInput(element);
    return element.value === String(action.value)
      ? { status: "filled", changed: true }
      : { status: "verification_failed", changed: false };
  }

  async function applyCustomSelect(action) {
    const candidates = Array.from(document.querySelectorAll(
      "input[role='combobox'], button[role='combobox'], [aria-haspopup='listbox']"
    )).filter(core.isVisible)
      .map((element) => ({ element, score: scoreControl(element, action) }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score);
    const control = candidates[0]?.element;
    if (!control) return { status: "not_found", changed: false };
    control.focus();
    control.click();
    await core.wait(220);
    const wanted = (action.optionAlternatives || action.optionTerms || [action.value])
      .map(core.normalize).filter(Boolean);
    const options = Array.from(document.querySelectorAll(
      "[role='option'], [role='listbox'] li, .dropdown-menu li, .dropdown-item"
    )).filter(core.isVisible);
    const option = options.find((item) => {
      const text = core.normalize(`${item.textContent || ""} ${item.getAttribute("data-value") || ""}`);
      return wanted.some((term) => text === term || text.includes(term));
    });
    if (!option) {
      control.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return { status: "not_found", changed: false };
    }
    option.click();
    await core.wait(180);
    const selected = core.normalize(`${control.value || ""} ${control.textContent || ""}`);
    return wanted.some((term) => selected.includes(term))
      ? { status: "filled", changed: true }
      : { status: "verification_failed", changed: true };
  }

  async function applyAction(action) {
    let result = await core.applyAction(action);
    if (!["not_found", "verification_failed"].includes(result.status)) return result;
    if (action.kind === "date") result = await applyHtmlDate(action);
    if (action.kind === "select_text" && result.status === "not_found") {
      result = await applyCustomSelect(action);
    }
    return result;
  }

  function failureLabel(item) {
    const reasons = {
      not_found: "未定位到控件",
      verification_failed: "写入后未能复读确认",
      invalid_value: "字段格式无效",
      error: "页面执行错误"
    };
    return `${item.label}（${reasons[item.status] || item.status}）`;
  }

  async function executeAssignment(assignment) {
    if (!assignment?.jobId || !assignment.page || runningJobId === assignment.jobId) return;
    runningJobId = assignment.jobId;
    const safety = safetyState();
    if (!safety.safe) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        reason: safety.reason,
        code: safety.code
      });
      runningJobId = "";
      return;
    }

    await report({
      jobId: assignment.jobId,
      state: "running",
      pageKey: assignment.page.key,
      pageLabel: assignment.page.label,
      reason: `开始填写 ${assignment.page.actions.length} 个预约资料字段`
    });

    const completedActionIds = [];
    const failed = [];
    const skipped = [];
    for (const action of assignment.page.actions) {
      let result;
      try {
        result = await applyAction(action);
      } catch (_error) {
        result = { status: "error", changed: false };
      }
      if (["filled", "already_set"].includes(result.status)) {
        completedActionIds.push(action.id);
        await report({
          jobId: assignment.jobId,
          state: "running",
          pageKey: assignment.page.key,
          pageLabel: assignment.page.label,
          completedActionIds,
          lastActionId: action.id
        });
      } else if (action.optionalOnPage && result.status === "not_found") {
        skipped.push(action.id);
      } else {
        failed.push({ id: action.id, label: action.label, status: result.status });
      }
      await core.wait(160);
    }

    if (failed.length || !completedActionIds.length) {
      const details = failed.length
        ? failed.slice(0, 4).map(failureLabel).join("；")
        : "当前页面没有匹配到任务中的资料字段";
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        failedActionIds: failed.map((item) => item.id),
        reason: `${details}。已暂停，未点击保存、继续或提交。`,
        code: "field_not_found"
      });
      runningJobId = "";
      return;
    }

    await report({
      jobId: assignment.jobId,
      state: "blocked",
      pageKey: assignment.page.key,
      pageLabel: assignment.page.label,
      completedActionIds,
      pageCompleted: true,
      reason: assignment.page.stopReason
        || `已填写 ${completedActionIds.length} 项${skipped.length ? `，本页未出现 ${skipped.length} 项可选字段` : ""}。请人工核对并继续。`,
      code: "manual_continue"
    });
    runningJobId = "";
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "DOCFLOW_APPLY_PAGE") {
      executeAssignment(message.assignment).catch(async () => {
        await report({
          jobId: message.assignment?.jobId,
          state: "failed",
          pageKey: message.assignment?.page?.key || "",
          pageLabel: document.title,
          reason: "预约资料页面执行发生错误，未继续导航。",
          code: "execution_error"
        });
        runningJobId = "";
      });
      sendResponse({ ok: true });
    }
    return false;
  });

  chrome.runtime.sendMessage({
    type: "docflow.pageReady",
    workflowType: "appointment",
    url: location.href,
    title: document.title
  }).then((response) => {
    if (response?.assignment) executeAssignment(response.assignment);
  }).catch(() => {});
})();
