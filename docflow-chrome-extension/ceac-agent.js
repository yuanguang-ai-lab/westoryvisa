(function initializeCeacAgent() {
  "use strict";

  const AGENT_VERSION = "0.9.3";
  if (globalThis.__docflowCeacAgentVersion === AGENT_VERSION) return;
  globalThis.__docflowCeacAgentVersion = AGENT_VERSION;
  const core = globalThis.DocFlowAgentCore;
  let runningJobId = "";
  const PACING = Object.freeze({
    pageTimeout: 25000,
    pageStart: 900,
    normalField: 780,
    unchangedField: 320,
    branchMinimumWait: 1900,
    branchQuietWindow: 900,
    branchCooldown: 1150,
    beforeNext: 1900,
    retryPass: 900
  });

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

  function missingFieldsReason(audit) {
    const labels = audit.missing.slice(0, 4).map((item) => item.label);
    const remaining = Math.max(0, audit.missing.length - labels.length);
    return `当前页还有 ${audit.missing.length} 个必填项未完成：${labels.join("；")}`
      + (remaining ? `；另有 ${remaining} 项` : "")
      + "。已暂停且没有点击 Next。";
  }

  async function verifyChoiceAfterRefresh(action) {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      if (core.isActionSet(action)) return true;
      await core.wait(450);
    }
    return false;
  }

  function failedFieldReason(item) {
    const labels = {
      not_found: "未定位到控件",
      verification_failed: "操作后未保持选中",
      invalid_value: "答案格式无效",
      error: "页面执行错误"
    };
    return `${item.label}（${labels[item.status] || item.status}）`;
  }

  async function advanceToNext(assignment, completedActionIds) {
    const settled = await core.waitForPageReady({
      timeout: PACING.pageTimeout,
      minimumWait: 1100,
      quietWindow: PACING.branchQuietWindow
    });
    if (!settled.ready) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        reason: settled.reason,
        code: settled.code
      });
      return false;
    }

    const audit = core.requiredFieldAudit();
    if (!audit.complete) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        pageCompleted: Boolean(assignment.page.manualReview),
        missingFields: audit.missing.map((item) => item.label),
        reason: missingFieldsReason(audit),
        code: "required_fields_missing"
      });
      return false;
    }

    const errors = core.visibleValidationErrors();
    if (errors.length) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        reason: `当前页仍有校验提示：${errors.join("；")}`,
        code: "validation_error"
      });
      return false;
    }

    const pageResult = await report({
      jobId: assignment.jobId,
      state: "running",
      pageKey: assignment.page.key,
      pageLabel: assignment.page.label,
      completedActionIds,
      reason: "当前页全部可见必填项已完成，准备进入下一页"
    });

    if (!assignment.autoNext) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        reason: "本页已完整填写并通过可见必填项校验。请由你在 CEAC 点击 Next；进入下一页后 Agent 会自动继续。",
        code: "auto_next_disabled"
      });
      return false;
    }
    if (pageResult && pageResult.stopRequested) return false;

    const nextButton = core.findNextButton();
    if (!nextButton) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        reason: "没有找到唯一且可见的 Next 按钮，已暂停。",
        code: "next_not_found"
      });
      return false;
    }
    if (core.navigationGuardActive()) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        reason: "Next 已经触发，正在等待 CEAC 响应；为避免重复提交，本次操作已暂停。",
        code: "navigation_pending"
      });
      return false;
    }

    nextButton.style.outline = "3px solid rgba(36, 96, 72, 0.42)";
    nextButton.style.outlineOffset = "3px";
    await core.wait(PACING.beforeNext);
    core.markNavigationPending();
    nextButton.click();
    return true;
  }

  async function executeAssignment(assignment) {
    if (!assignment || !assignment.jobId || !assignment.page) return;
    if (runningJobId === assignment.jobId) return;
    runningJobId = assignment.jobId;

    const safety = core.pageSafetyState();
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

    if (assignment.resumeState === "completed") {
      const audit = core.requiredFieldAudit();
      const errors = core.visibleValidationErrors();
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        missingFields: audit.missing.map((item) => item.label),
        reason: !audit.complete
          ? missingFieldsReason(audit)
          : errors.length
            ? `Next 后页面仍有校验错误：${errors.join("；")}`
            : "当前页已经填写并尝试 Next，但页面没有前进，请人工检查后从 DocFlow 继续。",
        code: "next_did_not_advance"
      });
      runningJobId = "";
      return;
    }

    await report({
      jobId: assignment.jobId,
      state: "running",
      pageKey: assignment.page.key,
      pageLabel: assignment.page.label,
      reason: `低速稳定模式：开始填写 ${assignment.page.actions.length} 个已确认字段`
    });

    const initialSettle = await core.waitForPageReady({
      timeout: PACING.pageTimeout,
      minimumWait: PACING.pageStart,
      quietWindow: 700
    });
    if (!initialSettle.ready) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        reason: initialSettle.reason,
        code: initialSettle.code
      });
      runningJobId = "";
      return;
    }

    const completed = new Set();
    let pending = [...assignment.page.actions];
    const finalFailures = new Map();
    for (let pass = 0; pass < 4 && pending.length; pass += 1) {
      const deferred = [];
      let passProgress = false;
      for (const action of pending) {
        const currentSafety = core.pageSafetyState();
        if (!currentSafety.safe) {
          await report({
            jobId: assignment.jobId,
            state: "blocked",
            pageKey: assignment.page.key,
            pageLabel: assignment.page.label,
            completedActionIds: Array.from(completed),
            reason: currentSafety.reason,
            code: currentSafety.code
          });
          runningJobId = "";
          return;
        }

        let result;
        try {
          result = await core.applyAction(action);
        } catch (_error) {
          result = { status: "error", changed: false };
        }
        if (result.status === "filled" && action.causesRefresh) {
          const settled = await core.waitForPageReady({
            timeout: PACING.pageTimeout,
            minimumWait: PACING.branchMinimumWait,
            quietWindow: PACING.branchQuietWindow
          });
          if (!settled.ready) {
            await report({
              jobId: assignment.jobId,
              state: "blocked",
              pageKey: assignment.page.key,
              pageLabel: assignment.page.label,
              completedActionIds: Array.from(completed),
              reason: settled.reason,
              code: settled.code
            });
            runningJobId = "";
            return;
          }
          if (!await verifyChoiceAfterRefresh(action)) {
            result = { status: "verification_failed", changed: result.changed };
          }
        }
        if (["filled", "already_set"].includes(result.status)) {
          completed.add(action.id);
          finalFailures.delete(action.id);
          passProgress = true;
          await report({
            jobId: assignment.jobId,
            state: "running",
            pageKey: assignment.page.key,
            pageLabel: assignment.page.label,
            completedActionIds: Array.from(completed),
            lastActionId: action.id
          });
        } else {
          const failure = { id: action.id, label: action.label, status: result.status };
          finalFailures.set(action.id, failure);
          deferred.push(action);
        }

        const cooldown = action.causesRefresh && result.changed
          ? PACING.branchCooldown
          : result.changed
            ? PACING.normalField
            : PACING.unchangedField;
        await core.wait(cooldown);
      }
      pending = deferred;
      if (pending.length && (passProgress || pass < 2)) {
        await core.wait(PACING.retryPass);
        await core.waitForDomStable(2600, 650);
      } else if (!passProgress) {
        break;
      }
    }
    const completedActionIds = Array.from(completed);
    const failed = pending.map((action) => finalFailures.get(action.id) || ({
      id: action.id, label: action.label, status: "not_found"
    }));

    if (failed.length) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        failedActionIds: failed.map((item) => item.id),
        reason: `有 ${failed.length} 个选择或字段未能确认：${failed.slice(0, 3).map(failedFieldReason).join("；")}。已暂停且没有点击 Next。`,
        code: "field_not_found"
      });
      runningJobId = "";
      return;
    }

    const audit = core.requiredFieldAudit();
    if (!audit.complete) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        pageCompleted: Boolean(assignment.page.manualReview),
        missingFields: audit.missing.map((item) => item.label),
        reason: missingFieldsReason(audit),
        code: "required_fields_missing"
      });
      runningJobId = "";
      return;
    }

    if ((assignment.page.manualReview || !assignment.page.allowNext)
      && assignment.resumeState !== "manual_continue") {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        pageCompleted: true,
        reason: assignment.page.stopReason
          || "本页可自动填写内容已完成。请确认敏感题后，从 DocFlow 点击继续自动填写。",
        code: "manual_review"
      });
      runningJobId = "";
      return;
    }

    await advanceToNext(assignment, completedActionIds);
    runningJobId = "";
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message && message.type === "DOCFLOW_APPLY_PAGE") {
      executeAssignment(message.assignment).catch(async () => {
        await report({
          jobId: message.assignment && message.assignment.jobId,
          state: "failed",
          pageKey: message.assignment && message.assignment.page
            ? message.assignment.page.key : "",
          pageLabel: document.title,
          reason: "页面执行发生错误，未继续导航。",
          code: "execution_error"
        });
        runningJobId = "";
      });
      sendResponse({ ok: true });
    }
    return false;
  });

  const initialSafety = core.pageSafetyState();
  chrome.runtime.sendMessage({
    type: "docflow.ceacReady",
    url: location.href,
    title: document.title,
    safety: initialSafety
  }).then((response) => {
    if (response && response.assignment) executeAssignment(response.assignment);
    if (response && response.active && !response.assignment) {
      const safety = core.pageSafetyState();
      if (!safety.safe) {
        report({
          jobId: response.jobId,
          state: "blocked",
          pageKey: "",
          pageLabel: document.title,
          reason: safety.reason,
          code: safety.code
        });
      }
    }
  }).catch(() => {});
})();
