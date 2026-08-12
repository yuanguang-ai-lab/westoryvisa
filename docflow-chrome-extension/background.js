"use strict";

const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const STORAGE_KEY = "docflowActiveTask";
const LOCAL_URL_PATTERNS = ["http://127.0.0.1/*", "http://localhost/*"];
const CEAC_URL_PATTERN = "https://ceac.state.gov/GenNIV/*";
const CEAC_START_URL = "https://ceac.state.gov/GenNIV/Default.aspx";
const APPOINTMENT_URL_PATTERN = "https://www.usvisascheduling.com/*";
const APPOINTMENT_START_URL = "https://www.usvisascheduling.com/";
const SENSITIVE_ID_PATTERN = /^(security|immigration|inadmissibility)\.|refusal|immigrant_petition|specialized_skills|military_service|paramilitary/i;

function taskWorkflowType(task) {
  return task?.workflowType === "appointment" ? "appointment" : "ds160";
}

function taskProfile(task) {
  if (taskWorkflowType(task) === "appointment") {
    return {
      workflowType: "appointment",
      allowedDomain: "www.usvisascheduling.com",
      urlPattern: APPOINTMENT_URL_PATTERN,
      startUrl: APPOINTMENT_START_URL,
      label: "预约网站",
      agentFiles: ["agent-core.js", "appointment-agent.js"]
    };
  }
  return {
    workflowType: "ds160",
    allowedDomain: "ceac.state.gov",
    urlPattern: CEAC_URL_PATTERN,
    startUrl: CEAC_START_URL,
    label: "CEAC",
    agentFiles: ["agent-core.js", "ceac-agent.js"]
  };
}

function taskAllowsUrl(task, rawUrl) {
  try {
    const url = new URL(String(rawUrl || ""));
    const profile = taskProfile(task);
    if (url.protocol !== "https:" || url.hostname !== profile.allowedDomain) return false;
    return profile.workflowType === "appointment" || url.pathname.startsWith("/GenNIV/");
  } catch (_error) {
    return false;
  }
}

function isLocalSender(sender) {
  try {
    const url = new URL(sender.url || sender.tab?.url || "");
    return url.protocol === "http:"
      && ["127.0.0.1", "localhost"].includes(url.hostname);
  } catch (_error) {
    return false;
  }
}

function isExtensionSender(sender) {
  return sender.id === chrome.runtime.id
    && String(sender.url || "").startsWith(`chrome-extension://${chrome.runtime.id}/`);
}

function validateTaskUrl(rawUrl) {
  const url = new URL(String(rawUrl || ""));
  const validHost = url.protocol === "http:"
    && ["127.0.0.1", "localhost"].includes(url.hostname);
  const validPath = /^\/api\/codex-agent\/jobs\/codex-agent-[0-9a-f]{24}$/.test(url.pathname);
  if (!validHost || !validPath) throw new Error("DocFlow 任务地址无效");
  return url.toString();
}

function validateTask(task) {
  const workflowType = taskWorkflowType(task);
  const validVersion = workflowType === "appointment" ? task?.version === 3 : task?.version === 2;
  if (!task || !validVersion || task.page !== "workflow") {
    throw new Error("DocFlow 任务版本不受支持");
  }
  const profile = taskProfile(task);
  if (task.safety?.allowedDomain !== profile.allowedDomain || !taskAllowsUrl(task, task.targetUrl)) {
    throw new Error("任务域名边界无效");
  }
  if (!Array.isArray(task.pages) || !task.pages.length) {
    throw new Error("任务没有可执行页面");
  }
  for (const page of task.pages) {
    if (!page.key || !Array.isArray(page.actions)) {
      throw new Error("任务页面结构无效");
    }
    for (const action of page.actions) {
      if (!action.id || !action.kind || SENSITIVE_ID_PATTERN.test(action.id)) {
        throw new Error("任务包含不允许自动处理的字段");
      }
      if (["save", "next", "submit", "captcha", "password", "payment", "schedule", "booking", "otp"].includes(action.kind)) {
        throw new Error("任务包含不允许的页面动作");
      }
    }
    if (workflowType === "appointment" && page.allowNext) {
      throw new Error("预约任务不得自动继续页面");
    }
  }
  if (workflowType === "appointment" && task.autoNext) {
    throw new Error("预约任务不得自动跳转");
  }
  return task;
}

async function getStoredState() {
  const result = await chrome.storage.session.get(STORAGE_KEY);
  const state = result[STORAGE_KEY] || null;
  if (state?.active && typeof state.armed !== "boolean") {
    redactState(state, "revoked", "扩展已更新，请返回 DocFlow 重新建立逐页填写任务");
    await chrome.storage.session.set({ [STORAGE_KEY]: state });
  }
  if (state?.active && state.task?.expiresAt
    && Date.parse(state.task.expiresAt) <= Date.now()) {
    redactState(state, "expired", "任务已过期，客户字段已从扩展会话清除");
    await chrome.storage.session.set({ [STORAGE_KEY]: state });
  }
  return state;
}

async function setStoredState(value) {
  await chrome.storage.session.set({ [STORAGE_KEY]: value });
}

function redactState(state, status, message) {
  for (const page of state?.task?.pages || []) {
    for (const action of page.actions || []) {
      action.value = "";
      delete action.duration;
    }
  }
  if (state) {
    state.active = false;
    state.status = status;
    state.message = message;
    state.accessToken = "";
  }
  return state;
}

function isHardStopPage(task, rawUrl, title) {
  const value = `${rawUrl || ""} ${title || ""}`;
  if (taskWorkflowType(task) === "appointment") {
    let path = "";
    try {
      path = new URL(String(rawUrl || "")).pathname;
    } catch (_error) {
      return true;
    }
    return /\/(?:payment|payments|fee|receipt|schedule|calendar|appointment-confirmation|confirm-appointment)(?:\/|$)/i.test(path)
      || /^(?:PAYMENT|MRV FEE|SCHEDULE APPOINTMENT|APPOINTMENT CONFIRMATION)$/i.test(String(title || "").trim());
  }
  return /node=security|security and background|signandsubmit|sign and submit|electronic signature|payment|final submission/i.test(value);
}

function observeCeacRoute(state, rawUrl, title, mappedPage = null) {
  if (!state) return null;
  let url;
  try {
    url = new URL(String(rawUrl || ""));
  } catch (_error) {
    return null;
  }
  if (!taskAllowsUrl(state.task, url.toString())) return null;
  const workflowType = taskWorkflowType(state.task);
  const observation = {
    path: url.pathname.slice(0, 180),
    node: workflowType === "ds160"
      ? String(url.searchParams.get("node") || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 80)
      : "",
    title: String(title || "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 100),
    mappedKey: mappedPage?.key || "",
    mapped: Boolean(mappedPage),
    observedAt: new Date().toISOString()
  };
  const key = `${observation.path}|${observation.node}`;
  const previous = Array.isArray(state.observedRoutes) ? state.observedRoutes : [];
  state.observedRoutes = [
    ...previous.filter((item) => `${item.path}|${item.node}` !== key),
    observation
  ].slice(-30);
  state.currentRoute = observation;
  return observation;
}

function publicState(state) {
  if (!state) return {
    active: false,
    extensionVersion: EXTENSION_VERSION,
    state: "idle"
  };
  return {
    active: Boolean(state.active),
    extensionVersion: EXTENSION_VERSION,
    workflowType: taskWorkflowType(state.task),
    jobId: state.task?.jobId || "",
    state: state.status || "idle",
    pageLabel: state.pageLabel || "",
    completedFields: (state.completedActionIds || []).length,
    totalFields: state.totalFields || 0,
    message: state.message || "",
    statusCode: state.statusCode || "",
    failedActionIds: state.failedActionIds || [],
    missingFields: state.missingFields || [],
    currentRoute: state.currentRoute || null,
    observedRoutes: state.observedRoutes || []
  };
}

async function notifyLocal(type, payload) {
  const tabs = await chrome.tabs.query({ url: LOCAL_URL_PATTERNS });
  await Promise.all(tabs.map(async (tab) => {
    try {
      await chrome.tabs.sendMessage(tab.id, { type, ...payload });
    } catch (_error) {
      // The DocFlow content bridge may not be ready in every local tab.
    }
  }));
}

function pageMatchesUrl(page, rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch (_error) {
    return false;
  }
  const node = String(url.searchParams.get("node") || "").toLowerCase();
  const path = (url.pathname.toLowerCase().replace(/\/+$/, "") || "/");
  const exactPaths = (page.exactPaths || []).map((item) => (
    String(item || "").toLowerCase().replace(/\/+$/, "") || "/"
  ));
  if (exactPaths.length && exactPaths.includes(path)) return true;
  const pathAndQuery = `${url.pathname}${url.search}`.toLowerCase();
  return (page.urlPatterns || []).some((pattern) => {
    const normalized = String(pattern || "").toLowerCase();
    if (normalized.startsWith("node=")) {
      return node === normalized.slice(5);
    }
    return pathAndQuery.includes(normalized);
  });
}

function pageForUrl(task, rawUrl) {
  return (task.pages || []).find((page) => pageMatchesUrl(page, rawUrl)) || null;
}

async function postRemoteStatus(state, status, pageLabel) {
  if (!state?.accessToken || !state.task?.statusUrl) return;
  try {
    const response = await fetch(state.task.statusUrl, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${state.accessToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        state: status,
        pageLabel: pageLabel || "",
        completedFields: (state.completedActionIds || []).length,
        reason: state.message || "",
        statusCode: state.statusCode || "",
        failedActionIds: state.failedActionIds || [],
        missingFields: state.missingFields || [],
        currentRoute: state.currentRoute || null,
        observedRoutes: state.observedRoutes || []
      })
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (_error) {
    await notifyLocal("DOCFLOW_TASK_STATUS", {
      ...publicState(state),
      state: "blocked",
      message: "本地状态同步失败，Chrome 已暂停；客户字段没有被重新发送。"
    });
  }
}

async function chooseCeacTab(preferredTabId = 0, preferStartPage = false) {
  const tabs = await chrome.tabs.query({ url: CEAC_URL_PATTERN });
  if (!tabs.length) return null;
  const preferred = tabs.find((tab) => tab.id === preferredTabId);
  if (preferred) return preferred;
  if (preferStartPage) {
    const startTabs = tabs.filter((tab) => tab.url === CEAC_START_URL);
    if (startTabs.length === 1) return startTabs[0];
    if (!startTabs.length) return null;
  }
  if (tabs.length > 1) {
    const activeTabs = tabs.filter((tab) => tab.active);
    if (activeTabs.length === 1) return activeTabs[0];
    throw new Error("检测到多个 CEAC 标签页，请只保留或激活要填写的那个页面");
  }
  return tabs[0];
}

async function chooseMappedCeacTab(task, preferredTabId = 0) {
  const tabs = await chrome.tabs.query({ url: CEAC_URL_PATTERN });
  const mapped = tabs.filter((tab) => pageForUrl(task, tab.url));
  if (!mapped.length) return null;
  const preferred = mapped.find((tab) => tab.id === preferredTabId);
  if (preferred) return preferred;
  if (mapped.length === 1) return mapped[0];
  const active = mapped.filter((tab) => tab.active);
  if (active.length === 1) return active[0];
  return mapped.sort((left, right) => Number(right.lastAccessed || 0)
    - Number(left.lastAccessed || 0))[0];
}

async function chooseAppointmentTab(preferredTabId = 0) {
  const tabs = await chrome.tabs.query({ url: APPOINTMENT_URL_PATTERN });
  if (!tabs.length) return null;
  const preferred = tabs.find((tab) => tab.id === preferredTabId);
  if (preferred) return preferred;
  if (tabs.length === 1) return tabs[0];
  const active = tabs.filter((tab) => tab.active);
  if (active.length === 1) return active[0];
  throw new Error("检测到多个预约网站标签页，请只保留或激活要填写的那个页面");
}

async function chooseMappedAppointmentTab(task, preferredTabId = 0) {
  const tabs = await chrome.tabs.query({ url: APPOINTMENT_URL_PATTERN });
  const mapped = tabs.filter((tab) => pageForUrl(task, tab.url));
  if (!mapped.length) return null;
  const preferred = mapped.find((tab) => tab.id === preferredTabId);
  if (preferred) return preferred;
  if (mapped.length === 1) return mapped[0];
  const active = mapped.filter((tab) => tab.active);
  if (active.length === 1) return active[0];
  return mapped.sort((left, right) => Number(right.lastAccessed || 0)
    - Number(left.lastAccessed || 0))[0];
}

async function chooseTaskTab(task, preferredTabId = 0, preferStartPage = false) {
  return taskWorkflowType(task) === "appointment"
    ? chooseAppointmentTab(preferredTabId)
    : chooseCeacTab(preferredTabId, preferStartPage);
}

async function chooseMappedTaskTab(task, preferredTabId = 0) {
  return taskWorkflowType(task) === "appointment"
    ? chooseMappedAppointmentTab(task, preferredTabId)
    : chooseMappedCeacTab(task, preferredTabId);
}

async function assignmentForTab(state, tab, options = {}) {
  if (!state?.active || !state.task || !tab?.url) return null;
  const page = pageForUrl(state.task, tab.url);
  if (!page) return null;
  const pageCompleted = Boolean(state.completedPages?.[page.key]);
  return {
    jobId: state.task.jobId,
    page,
    autoNext: taskWorkflowType(state.task) === "appointment" ? false : Boolean(state.autoNext),
    resumeState: pageCompleted
      ? (options.manualContinue ? "manual_continue" : "completed")
      : "new",
    completedFields: (state.completedActionIds || []).length,
    totalFields: state.totalFields
  };
}

async function ensureCeacAgent(tab) {
  if (!tab?.id || !String(tab.url || "").startsWith("https://ceac.state.gov/GenNIV/")) {
    return false;
  }
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["ceac-postback-monitor.js"],
    world: "MAIN"
  });
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["agent-core.js", "ceac-agent.js"]
  });
  return true;
}

async function ensureAppointmentAgent(tab) {
  if (!tab?.id || !String(tab.url || "").startsWith(APPOINTMENT_START_URL)) {
    return false;
  }
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["agent-core.js", "appointment-agent.js"]
  });
  return true;
}

async function ensureTaskAgent(task, tab) {
  return taskWorkflowType(task) === "appointment"
    ? ensureAppointmentAgent(tab)
    : ensureCeacAgent(tab);
}

async function dispatchToCeacTab(state, tab, options = {}) {
  const assignment = await assignmentForTab(state, tab, options);
  if (!assignment) return false;
  try {
    await ensureTaskAgent(state.task, tab);
    await chrome.tabs.sendMessage(tab.id, {
      type: "DOCFLOW_APPLY_PAGE",
      assignment
    });
    return true;
  } catch (_error) {
    return false;
  }
}

async function startTask(message, sender) {
  if (!isLocalSender(sender)) throw new Error("只能从本机 DocFlow 启动任务");
  const taskUrl = validateTaskUrl(message.taskUrl);
  const accessToken = String(message.accessToken || "");
  if (accessToken.length < 32) throw new Error("DocFlow 临时授权无效");

  const response = await fetch(taskUrl, {
    headers: { "Authorization": `Bearer ${accessToken}` },
    cache: "no-store"
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `任务读取失败（HTTP ${response.status}）`);
  const task = validateTask(payload);
  const profile = taskProfile(task);
  const state = {
    active: true,
    armed: false,
    task,
    taskUrl,
    accessToken,
    autoNext: profile.workflowType === "appointment" ? false : message.autoNext !== false,
    status: "waiting_for_entry",
    pageLabel: "",
    message: profile.workflowType === "appointment"
      ? "预约网站已打开，等待你人工登录并进入申请人资料页"
      : "CEAC 已打开，等待你人工进入第一张正式表格",
    totalFields: task.pages.reduce((total, page) => total + page.actions.length, 0),
    completedActionIds: [],
    failedActionIds: [],
    missingFields: [],
    statusCode: "",
    currentRoute: null,
    observedRoutes: [],
    completedPages: {},
    startedAt: new Date().toISOString()
  };
  await setStoredState(state);

  let tab;
  try {
    tab = await chooseMappedTaskTab(task);
    if (!tab) tab = await chooseTaskTab(task, 0, true);
    if (!tab) {
      tab = await chrome.tabs.create({ url: task.targetUrl || profile.startUrl, active: true });
    } else {
      await chrome.tabs.update(tab.id, { active: true });
      if (!pageForUrl(task, tab.url) && tab.url !== task.targetUrl) {
        await chrome.tabs.update(tab.id, { url: task.targetUrl || profile.startUrl });
      }
    }
  } catch (error) {
    redactState(state, "blocked", "Chrome 标签页配对失败，任务字段已清除");
    await setStoredState(state);
    throw error;
  }

  state.taskTabId = tab.id;
  state.ceacTabId = profile.workflowType === "ds160" ? tab.id : 0;
  observeCeacRoute(state, tab.url, tab.title, pageForUrl(task, tab.url));
  await setStoredState(state);
  await postRemoteStatus(state, "waiting_for_entry", `${profile.label} start`);

  await notifyLocal("DOCFLOW_TASK_ACCEPTED", publicState(state));
  return publicState(state);
}

async function resumeTask(sender) {
  if (!isLocalSender(sender)) throw new Error("只能从本机 DocFlow 开始识别页面");
  const state = await getStoredState();
  if (!state?.active || !state.task) throw new Error("没有可继续的 DocFlow 任务");
  const profile = taskProfile(state.task);

  const preferredTabId = state.taskTabId || state.ceacTabId || 0;
  const tab = await chooseMappedTaskTab(state.task, preferredTabId)
    || await chooseTaskTab(state.task, preferredTabId);
  if (!tab) {
    const opened = await chrome.tabs.create({
      url: state.task.targetUrl || profile.startUrl,
      active: true
    });
    state.taskTabId = opened.id;
    state.ceacTabId = profile.workflowType === "ds160" ? opened.id : 0;
    state.armed = false;
    state.status = "waiting_for_entry";
    state.pageLabel = `${profile.label} start`;
    state.message = profile.workflowType === "appointment"
      ? "已重新打开预约网站，请人工登录并进入申请人资料页后再开始填写"
      : "已重新打开 CEAC，请人工进入第一张正式表格后再开始识别";
    await setStoredState(state);
    await postRemoteStatus(state, "waiting_for_entry", state.pageLabel);
    await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
    return publicState(state);
  }

  await chrome.tabs.update(tab.id, { active: true });
  state.taskTabId = tab.id;
  state.ceacTabId = profile.workflowType === "ds160" ? tab.id : 0;
  const page = pageForUrl(state.task, tab.url);
  observeCeacRoute(state, tab.url, tab.title, page);
  if (!page) {
    state.armed = false;
    state.status = "waiting_for_entry";
    state.pageLabel = String(tab.title || `${profile.label} start`).slice(0, 100);
    state.message = profile.workflowType === "appointment"
      ? "当前还不是可填写的预约资料页。请先人工完成登录和验证，并进入 Applicant Details 或 Visa Options。"
      : "当前还不是可识别的 DS-160 表格页。请先在 CEAC 完成人工步骤并进入正式表格。";
    await setStoredState(state);
    await postRemoteStatus(state, "waiting_for_entry", state.pageLabel);
    await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
    return publicState(state);
  }

  state.armed = true;
  state.status = "running";
  state.statusCode = "";
  state.pageLabel = page.label;
  state.message = `已识别 ${page.label}，开始写入当前客户的${profile.workflowType === "appointment" ? "预约资料" : "档案字段"}`;
  await setStoredState(state);
  await postRemoteStatus(state, "running", page.label);
  const dispatched = await dispatchToCeacTab(state, tab, { manualContinue: true });
  if (!dispatched) {
    state.armed = false;
    state.status = "blocked";
    state.message = `当前${profile.label}页面尚未准备好，请刷新该资料页后重新开始识别。`;
    await setStoredState(state);
    await postRemoteStatus(state, "blocked", page.label);
  }
  await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
  return publicState(state);
}

async function updateFromPage(message, sender) {
  const state = await getStoredState();
  if (!state?.active || state.task?.jobId !== message.jobId) {
    return { ok: false, stopRequested: true };
  }
  if (!taskAllowsUrl(state.task, sender.tab?.url)) {
    throw new Error("页面状态来源无效");
  }
  const page = pageForUrl(state.task, sender.tab.url);
  if (!page || (message.pageKey && page.key !== message.pageKey)) {
    throw new Error("页面与当前任务不匹配");
  }
  const allowedActionIds = new Set(page.actions.map((action) => action.id));
  const completed = new Set(state.completedActionIds || []);
  for (const actionId of message.completedActionIds || []) {
    if (allowedActionIds.has(actionId)) completed.add(actionId);
  }
  state.completedActionIds = Array.from(completed);
  if (message.pageCompleted) {
    state.completedPages = { ...(state.completedPages || {}), [page.key]: true };
  }
  state.status = ["running", "blocked", "failed"].includes(message.state)
    ? message.state : "running";
  state.statusCode = state.status === "running"
    ? ""
    : String(message.code || "").replace(/[^a-z0-9_]/gi, "").slice(0, 64);
  state.pageLabel = page.label;
  state.message = String(message.reason || "Chrome Agent 正在处理当前页面").slice(0, 240);
  state.failedActionIds = state.status === "running" ? [] : Array.from(new Set(
    (message.failedActionIds || []).filter((actionId) => allowedActionIds.has(actionId))
  )).slice(0, 20);
  state.missingFields = state.status === "running" ? [] : Array.from(new Set(
    (message.missingFields || [])
      .map((label) => String(label || "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 120))
      .filter(Boolean)
  )).slice(0, 20);
  observeCeacRoute(state, sender.tab.url, sender.tab.title, page);
  await setStoredState(state);
  const now = Date.now();
  if (state.status !== "running" || message.pageCompleted
    || now - Number(state.lastRemoteSync || 0) > 1800) {
    state.lastRemoteSync = now;
    await setStoredState(state);
    await postRemoteStatus(state, state.status, page.label);
  }
  await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
  return { ok: true, stopRequested: !state.active };
}

async function handleCeacReady(message, sender) {
  const state = await getStoredState();
  if (!state?.active || !state.task) return { active: false };
  if (!taskAllowsUrl(state.task, sender.tab?.url)) {
    return { active: false };
  }
  const profile = taskProfile(state.task);
  if (message.workflowType && message.workflowType !== profile.workflowType) {
    return { active: false };
  }
  state.taskTabId = sender.tab.id;
  state.ceacTabId = profile.workflowType === "ds160" ? sender.tab.id : 0;
  const recoverableSafetyCodes = new Set([
    "application_error", "session_expired", "captcha"
  ]);
  const reportedSafety = message.safety && typeof message.safety === "object"
    ? message.safety : null;
  if (reportedSafety?.safe === false && recoverableSafetyCodes.has(reportedSafety.code)) {
    state.status = "blocked";
    state.statusCode = reportedSafety.code;
    state.pageLabel = String(message.title || "CEAC").slice(0, 100);
    state.message = String(reportedSafety.reason || "CEAC 页面需要人工恢复").slice(0, 240);
    observeCeacRoute(state, sender.tab.url, message.title || sender.tab.title, null);
    await setStoredState(state);
    await postRemoteStatus(state, "blocked", state.pageLabel);
    await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
    return { active: true, jobId: state.task.jobId, assignment: null };
  }
  const detectedPage = pageForUrl(state.task, sender.tab.url);
  const previousMappedKey = state.currentRoute?.mappedKey || "";
  if (state.armed && previousMappedKey && detectedPage?.key
    && previousMappedKey !== detectedPage.key) {
    state.completedPages = {
      ...(state.completedPages || {}),
      [previousMappedKey]: true
    };
  }
  observeCeacRoute(state, sender.tab.url, message.title || sender.tab.title, detectedPage);
  await setStoredState(state);
  if (!state.armed) {
    state.status = "waiting_for_entry";
    state.statusCode = "";
    state.pageLabel = String(
      detectedPage?.label || message.title || `${profile.label} start`
    ).slice(0, 100);
    state.message = detectedPage
      ? `已检测到 ${detectedPage.label}，请返回 DocFlow 点击“开始填写当前页面”`
      : profile.workflowType === "appointment"
        ? "预约网站已连接，请人工完成登录和安全验证并进入申请人资料页"
        : "CEAC 已连接，请人工完成验证码和初始步骤并进入第一张正式表格";
    await setStoredState(state);
    await postRemoteStatus(state, "waiting_for_entry", state.pageLabel);
    await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
    return { active: true, jobId: state.task.jobId, assignment: null };
  }
  if (isHardStopPage(state.task, sender.tab.url, message.title)) {
    state.status = "review_required";
    state.statusCode = "hard_stop";
    state.pageLabel = String(message.title || "Sensitive review").slice(0, 100);
    state.message = profile.workflowType === "appointment"
      ? "已到缴费、选时间或最终预约确认边界，Chrome Agent 已结束并清除任务字段。"
      : "已到敏感背景、声明、付款或最终提交边界，Chrome Agent 已结束并清除字段。";
    await postRemoteStatus(state, "review_required", state.pageLabel);
    redactState(state, "review_required", state.message);
    await setStoredState(state);
    await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
    return { active: false, jobId: state.task.jobId, assignment: null };
  }
  const assignment = await assignmentForTab(state, sender.tab);
  if (!assignment) {
    state.status = "blocked";
    state.statusCode = "unmapped_route";
    state.pageLabel = String(message.title || `${profile.label}页面`).slice(0, 100);
    const routeName = state.currentRoute?.node || state.currentRoute?.path || "当前页面";
    state.message = profile.workflowType === "appointment"
      ? `当前页面 ${routeName} 不在预约资料白名单内，请人工处理；进入 Applicant Details 或 Visa Options 后可再次开始填写。`
      : `已捕获未映射页面 ${routeName}。请人工完成并点击 Next；进入已映射页面后会自动继续。`;
    await setStoredState(state);
    await postRemoteStatus(state, "blocked", state.pageLabel);
    await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
    return { active: true, jobId: state.task.jobId, assignment: null };
  }
  return { active: true, jobId: state.task.jobId, assignment };
}

async function stopTask(sender) {
  if (!isLocalSender(sender) && !isExtensionSender(sender)) {
    throw new Error("只能从本机 DocFlow 或扩展面板停止任务");
  }
  const state = await getStoredState();
  if (!state) return publicState(null);
  state.active = false;
  redactState(state, "revoked", "任务已由顾问停止，客户字段已从扩展会话清除");
  await setStoredState(state);
  await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
  return publicState(state);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message.type !== "string") return { ok: false };
    if (message.type === "docflow.startTask") return startTask(message, sender);
    if (message.type === "docflow.resumeTask") return resumeTask(sender);
    if (message.type === "docflow.pageStatus") return updateFromPage(message, sender);
    if (["docflow.ceacReady", "docflow.pageReady"].includes(message.type)) {
      return handleCeacReady(message, sender);
    }
    if (message.type === "docflow.stopTask") return stopTask(sender);
    if (message.type === "docflow.getState") return publicState(await getStoredState());
    return { ok: false };
  })().then((result) => sendResponse({ ok: true, ...result })).catch((error) => {
    sendResponse({ ok: false, error: error.message || "Chrome 扩展操作失败" });
  });
  return true;
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const state = await getStoredState();
  if (!state?.active || (state.taskTabId || state.ceacTabId) !== tabId) return;
  const profile = taskProfile(state.task);
  state.status = "blocked";
  state.message = `${profile.label}标签页已关闭，任务已暂停`;
  await setStoredState(state);
  await notifyLocal("DOCFLOW_TASK_STATUS", publicState(state));
});
