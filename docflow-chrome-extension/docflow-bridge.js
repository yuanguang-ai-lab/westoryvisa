(function initializeDocFlowBridge() {
  "use strict";

  const APP_SOURCE = "docflow-app";
  const EXTENSION_SOURCE = "docflow-extension";
  const version = chrome.runtime.getManifest().version;

  function post(type, payload = {}) {
    window.postMessage({
      source: EXTENSION_SOURCE,
      type,
      extensionVersion: version,
      ...payload
    }, window.location.origin);
  }

  async function announceReady(requestId = "") {
    let state = {};
    try {
      state = await chrome.runtime.sendMessage({ type: "docflow.getState" });
    } catch (_error) {
      state = {};
    }
    post("DOCFLOW_EXTENSION_READY", {
      requestId,
      active: Boolean(state && state.active),
      jobId: state && state.jobId ? state.jobId : "",
      workflowType: state && state.workflowType ? state.workflowType : "",
      taskState: state && state.state ? state.state : "idle",
      pageLabel: state && state.pageLabel ? state.pageLabel : "",
      completedFields: state && state.completedFields ? state.completedFields : 0,
      totalFields: state && state.totalFields ? state.totalFields : 0,
      message: state && state.message ? state.message : ""
    });
  }

  window.addEventListener("message", async (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const message = event.data || {};
    if (message.source !== APP_SOURCE) return;

    if (message.type === "DOCFLOW_EXTENSION_PING") {
      await announceReady(message.requestId || "");
      return;
    }

    if (message.type === "DOCFLOW_START_TASK") {
      try {
        const result = await chrome.runtime.sendMessage({
          type: "docflow.startTask",
          taskUrl: message.taskUrl,
          accessToken: message.accessToken,
          autoNext: message.autoNext !== false
        });
        if (!result || result.ok === false) {
          throw new Error(result && result.error ? result.error : "Chrome 扩展未接受任务");
        }
        post("DOCFLOW_TASK_ACCEPTED", {
          requestId: message.requestId || "",
          jobId: result.jobId || message.jobId || "",
          workflowType: result.workflowType || "",
          taskState: result.state || "claimed",
          completedFields: result.completedFields || 0,
          totalFields: result.totalFields || 0,
          message: result.message || "任务已与 Chrome 配对"
        });
      } catch (error) {
        post("DOCFLOW_TASK_ERROR", {
          requestId: message.requestId || "",
          jobId: message.jobId || "",
          message: error.message || "Chrome 扩展启动失败"
        });
      }
      return;
    }

    if (message.type === "DOCFLOW_RESUME_TASK") {
      try {
        const result = await chrome.runtime.sendMessage({ type: "docflow.resumeTask" });
        if (!result || result.ok === false) {
          throw new Error(result && result.error ? result.error : "Chrome 扩展未能识别当前页面");
        }
        post("DOCFLOW_TASK_STATUS", {
          requestId: message.requestId || "",
          jobId: result.jobId || message.jobId || "",
          workflowType: result.workflowType || "",
          taskState: result.state || "waiting_for_entry",
          pageLabel: result.pageLabel || "",
          completedFields: result.completedFields || 0,
          totalFields: result.totalFields || 0,
          message: result.message || "正在识别当前 CEAC 页面"
        });
      } catch (error) {
        post("DOCFLOW_TASK_ERROR", {
          requestId: message.requestId || "",
          jobId: message.jobId || "",
          message: error.message || "Chrome 扩展未能识别当前页面"
        });
      }
      return;
    }

    if (message.type === "DOCFLOW_STOP_TASK") {
      try {
        await chrome.runtime.sendMessage({ type: "docflow.stopTask" });
        post("DOCFLOW_TASK_STATUS", {
          jobId: message.jobId || "",
          taskState: "revoked",
          message: "任务已停止"
        });
      } catch (error) {
        post("DOCFLOW_TASK_ERROR", {
          jobId: message.jobId || "",
          message: error.message || "任务停止失败"
        });
      }
    }
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || !["DOCFLOW_TASK_ACCEPTED", "DOCFLOW_TASK_STATUS"].includes(message.type)) {
      return;
    }
    post(message.type, {
      jobId: message.jobId || "",
      workflowType: message.workflowType || "",
      taskState: message.state || "idle",
      pageLabel: message.pageLabel || "",
      completedFields: message.completedFields || 0,
      totalFields: message.totalFields || 0,
      message: message.message || "",
      statusCode: message.statusCode || "",
      failedActionIds: message.failedActionIds || [],
      missingFields: message.missingFields || [],
      currentRoute: message.currentRoute || null,
      observedRoutes: message.observedRoutes || []
    });
  });

  announceReady();
})();
