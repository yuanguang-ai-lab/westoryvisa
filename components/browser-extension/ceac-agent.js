(function initializeCeacAgent() {
  "use strict";

  const AGENT_VERSION = "1.0.17";
  if (globalThis.__docflowCeacAgentVersion === AGENT_VERSION
    && globalThis.__docflowCeacAgentCoreVersion === globalThis.DocFlowAgentCore?.version) return;
  // Removing the listener alone cannot cancel promises held by the previous
  // injected instance (including a delayed ceacReady assignment).
  globalThis.__docflowDisposeCeacAgent?.();
  globalThis.__docflowCeacAgentVersion = AGENT_VERSION;
  globalThis.__docflowCeacAgentCoreVersion = globalThis.DocFlowAgentCore?.version;
  document.documentElement?.setAttribute("data-docflow-agent-version", AGENT_VERSION);
  const core = globalThis.DocFlowAgentCore;
  let runningJobId = "";
  let executionEpoch = 0;
  let disposed = false;
  globalThis.__docflowDisposeCeacAgent = () => {
    disposed = true;
    executionEpoch += 1;
    runningJobId = "";
  };
  const PACING = Object.freeze({
    pageTimeout: 25000,
    pageStart: 300,
    normalField: 260,
    unchangedField: 80,
    branchMinimumWait: 650,
    branchQuietWindow: 350,
    branchCooldown: 280,
    beforeNext: 650,
    retryPass: 250
  });

  async function report(payload) {
    if (disposed) return { ok: false, stopRequested: true };
    let timer;
    const reportEpoch = executionEpoch;
    try {
      const response = await Promise.race([
        chrome.runtime.sendMessage({
          type: "docflow.pageStatus",
          url: location.href,
          title: document.title,
          ...payload
        }),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("status_timeout")), 10000);
        })
      ]);
      if (disposed) return { ok: false, stopRequested: true };
      if (!response?.ok || response.stopRequested) {
        if (reportEpoch === executionEpoch) {
          executionEpoch += 1;
          runningJobId = "";
          core.visualLog(payload.pageLabel || "当前任务", "填写已暂停，请检查本地控制台");
        }
        return { ...response, stopRequested: true };
      }
      return response;
    } catch (_error) {
      if (reportEpoch === executionEpoch) {
        executionEpoch += 1;
        runningJobId = "";
        core.visualLog(payload.pageLabel || "当前任务", "插件通信超时或中断，已停止写入和翻页");
      }
      return { ok: false, stopRequested: true };
    } finally {
      clearTimeout(timer);
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

  async function advanceToNext(assignment, completedActionIds, runEpoch) {
    if (runEpoch !== executionEpoch) return false;
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

    // A previous server validation summary can remain visible after all
    // controls have been corrected. Let native Next revalidate those old
    // messages once; newly appearing errors still block before the click.
    const newErrors = () => core.visibleValidationErrors().filter(
      error => !(assignment.initialValidationErrors || []).includes(error)
    );
    const errors = newErrors();
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
    if (runEpoch !== executionEpoch) return false;
    // Re-read the plan at the last possible point. A delayed postback may
    // replace a correct value with another non-empty value after the earlier
    // reconciliation, which a required-fields-only check cannot detect.
    const changedActions = assignment.page.actions.filter(action => !core.isActionSet(action));
    const finalAudit = core.requiredFieldAudit();
    const finalErrors = newErrors();
    if (changedActions.length || !finalAudit.complete || finalErrors.length) {
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds: completedActionIds.filter(id => !changedActions.some(action => action.id === id)),
        failedActionIds: changedActions.map(action => action.id),
        missingFields: finalAudit.missing.map(item => item.label),
        reason: "翻页前复核发现字段被页面刷新改写、必填项缺失或新的校验错误，已暂停且没有点击 Next。",
        code: "pre_next_verification_failed"
      });
      return false;
    }
    const beforeUrl = location.href;
    const beforeTitle = document.title;
    core.markNavigationPending();
    if (!core.requestNativeNext(nextButton)) {
      core.clearNavigationPending();
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        reason: "CEAC 主页面没有确认原生 Next 请求。已停止，避免触发未保存离页弹窗。",
        code: "next_bridge_unavailable"
      });
      return false;
    }

    // A successful full-page CEAC navigation destroys this script context.
    // If execution is still alive after the bounded watchdog and the route is
    // unchanged, the click did not advance. Do not leave the console in a
    // false "running" state and do not retry a submission automatically.
    await core.wait(12000);
    if (location.href === beforeUrl && document.title === beforeTitle) {
      core.clearNavigationPending();
      core.visualLog(assignment.page.label, "Next 未进入下一页");
      await report({
        jobId: assignment.jobId,
        state: "blocked",
        pageKey: assignment.page.key,
        pageLabel: assignment.page.label,
        completedActionIds,
        reason: core.visibleValidationErrors().length
          ? `网页重新校验后仍未通过：${core.visibleValidationErrors().join("；")}`
          : "当前页已通过复核，但 Next 在 12 秒内没有进入下一页。已停止自动重试，请检查网页响应。",
        code: "next_did_not_advance"
      });
      return false;
    }
    return true;
  }

  async function executeAssignment(assignment) {
    if (disposed || !assignment || !assignment.jobId || !assignment.page) return;
    if (runningJobId === assignment.jobId) return;
    runningJobId = assignment.jobId;
    const runEpoch = ++executionEpoch;
    assignment.initialValidationErrors = core.visibleValidationErrors();

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
      reason: `快速稳定模式：开始填写 ${assignment.page.actions.length} 个已确认字段`
    });
    if (runEpoch !== executionEpoch) return;

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
        if (runEpoch !== executionEpoch) {
          if (runningJobId === assignment.jobId) runningJobId = "";
          return;
        }
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
          await core.previewAction(action);
          if (runEpoch !== executionEpoch) return;
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
        core.visualActionResult(action, result);
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

    // CEAC uses ASP.NET postbacks for branch questions. A later Yes/No click
    // can redraw the form and silently discard text entered earlier on the
    // same page. The server executor re-audits the page after those postbacks;
    // do the same here before considering any action complete or clicking Next.
    for (let reconciliationPass = 0; reconciliationPass < 3; reconciliationPass += 1) {
      const lostActions = assignment.page.actions.filter(
        (action) => completed.has(action.id) && !core.isActionSet(action)
      );
      if (!lostActions.length) break;

      for (const action of lostActions) {
        if (runEpoch !== executionEpoch) {
          if (runningJobId === assignment.jobId) runningJobId = "";
          return;
        }
        completed.delete(action.id);
        let result;
        try {
          await core.previewAction(action);
          if (runEpoch !== executionEpoch) return;
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
          if (!settled.ready || !await verifyChoiceAfterRefresh(action)) {
            result = { status: "verification_failed", changed: result.changed };
          }
        }
        core.visualActionResult(action, result);
        if (["filled", "already_set"].includes(result.status)
          && core.isActionSet(action)) {
          completed.add(action.id);
          finalFailures.delete(action.id);
          await report({
            jobId: assignment.jobId,
            state: "running",
            pageKey: assignment.page.key,
            pageLabel: assignment.page.label,
            completedActionIds: Array.from(completed),
            lastActionId: action.id
          });
        } else {
          finalFailures.set(action.id, {
            id: action.id,
            label: action.label,
            status: result.status
          });
        }
        await core.wait(result.changed ? PACING.normalField : PACING.unchangedField);
      }
    }

    for (const action of assignment.page.actions) {
      if (completed.has(action.id) && !core.isActionSet(action)) {
        completed.delete(action.id);
        finalFailures.set(action.id, {
          id: action.id,
          label: action.label,
          status: "verification_failed"
        });
      }
    }
    const completedActionIds = Array.from(completed);
    const failed = assignment.page.actions
      .filter((action) => !completed.has(action.id))
      .map((action) => finalFailures.get(action.id) || ({
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

    if (runEpoch === executionEpoch) {
      await advanceToNext(assignment, completedActionIds, runEpoch);
      runningJobId = "";
    }
  }

  if (globalThis.__docflowCeacAgentMessageListener) {
    chrome.runtime.onMessage.removeListener(
      globalThis.__docflowCeacAgentMessageListener
    );
  }
  const messageListener = (message, _sender, sendResponse) => {
    if (message && message.type === "DOCFLOW_PAUSE_PAGE") {
      executionEpoch += 1;
      runningJobId = "";
      core.visualLog("当前任务", "已暂停");
      sendResponse({ ok: true });
      return false;
    }
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
  };
  globalThis.__docflowCeacAgentMessageListener = messageListener;
  chrome.runtime.onMessage.addListener(messageListener);

  const initialSafety = core.pageSafetyState();
  chrome.runtime.sendMessage({
    type: "docflow.ceacReady",
    url: location.href,
    title: document.title,
    safety: initialSafety
  }).then((response) => {
    if (disposed) return;
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
