const STORAGE_KEY = "docflowDs160Cases";
const SESSION_KEY = "docflowDs160Session";
const NAVIGATION_KEY = "docflowDs160Navigation";
const API_BASE = window.location.protocol === "file:" ? "" : DocFlowApi.apiBaseUrl;
const REQUIRED_API_VERSION = "2026-07-27-inline-intake-v17";
const REQUIRED_API_REVISION = 20;
const US_TRAVEL_DOCS_URL = "https://www.ustraveldocs.com/";

function versionAtLeast(version, minimum) {
  const current = String(version || "0").split(".").map((part) => Number.parseInt(part, 10) || 0);
  const required = String(minimum || "0").split(".").map((part) => Number.parseInt(part, 10) || 0);
  const length = Math.max(current.length, required.length);
  for (let index = 0; index < length; index += 1) {
    const currentPart = current[index] || 0;
    const requiredPart = required[index] || 0;
    if (currentPart > requiredPart) return true;
    if (currentPart < requiredPart) return false;
  }
  return true;
}

function apiRevisionFromHealth(healthData = {}) {
  const explicitRevision = Number.parseInt(healthData.apiRevision, 10);
  if (Number.isFinite(explicitRevision)) return explicitRevision;
  const match = String(healthData.apiVersion || "").match(/-v(\d+)$/i);
  return match ? Number.parseInt(match[1], 10) : 0;
}

function apiCompatibilityMessage() {
  if (!API_BASE) return "请通过“启动完整版本.command”打开网站，不要直接双击 index.html。";
  if (state.apiVersion) {
    return `当前地址连接的是后端 ${state.apiVersion}，本页面需要 ${REQUIRED_API_VERSION} 或更新版本。请打开启动脚本刚刚弹出的网页地址。`;
  }
  return "当前地址没有连接到 WestoryVisa 后端。请重新运行“启动完整版本.command”，并使用它自动打开的网页地址。";
}

const state = {
  user: null,
  applications: [],
  activeId: null,
  currentView: "login",
  previousView: null,
  modal: null,
  draftVisaType: "F1 学生签证",
  draftDate: "2026-08-11",
  draftCase: {},
  processingTimer: null,
  prefillLogIndex: 0,
  screenAgentRunId: 0,
  screenAgentRunning: false,
  desktopAgentTimer: null,
  codexAgentTimer: null,
  computerUseHandoffs: {
    ds160: null,
    appointment: null
  },
  openCoworkTimer: null,
  apiAvailable: false,
  apiVersion: "",
  apiRevision: 0,
  ocrService: null,
  translationService: null,
  mailService: null,
  screenAgentRuntime: null,
  membership: null,
  trial: null,
  membershipBypass: false,
  registrationVerification: { mode: "none", required: false },
  emailCodeTimer: null,
  emailCodeCooldownUntil: 0,
  authMode: "login",
  authError: "",
  activeQuestionSection: "",
  showAllQuestions: false,
  authDraft: {
    organizationName: "",
    name: "",
    phone: "",
    email: "",
    emailCode: "",
    password: "",
    confirmPassword: ""
  }
};

const VISA_OPTIONS = [
  { id: "b1b2", name: "B1/B2 访问签证", description: "适用于旅游、探亲、商务访问等 DS-160 资料整理场景。", icon: "B" },
  { id: "f1", name: "F1 学生签证", description: "适用于 I-20、SEVIS、学校信息和资金材料核对场景。", icon: "F" },
  { id: "f2", name: "F2 学生家属签证", description: "适用于 F-1 配偶或子女的 I-20、SEVIS 与主申请人信息核对。", icon: "F2" },
  { id: "j1", name: "J1 交流访问签证", description: "适用于 DS-2019、项目、学校或机构信息核查场景。", icon: "J" },
  { id: "j2", name: "J2 交流家属签证", description: "适用于 J-1 配偶或子女的 DS-2019、SEVIS 与主申请人信息核对。", icon: "J2" }
];

const APPOINTMENT_LOCATIONS = [
  { value: "BEIJING", label: "北京", detail: "U.S. Embassy Beijing" },
  { value: "SHANGHAI", label: "上海", detail: "U.S. Consulate Shanghai" },
  { value: "GUANGZHOU", label: "广州", detail: "U.S. Consulate Guangzhou" },
  { value: "SHENYANG", label: "沈阳", detail: "U.S. Consulate Shenyang" },
  { value: "WUHAN", label: "武汉", detail: "U.S. Consulate Wuhan" }
];

const APPOINTMENT_LANGUAGES = [
  { value: "zh-CN", label: "中文（中国）" },
  { value: "en-US", label: "English (United States)" }
];

const APPOINTMENT_PHONE_CODES = [
  { value: "+86", label: "中国 +86" },
  { value: "+1", label: "美国 / 加拿大 +1" },
  { value: "+852", label: "中国香港 +852" },
  { value: "+853", label: "中国澳门 +853" },
  { value: "+886", label: "中国台湾 +886" }
];

const APPOINTMENT_DELIVERY_OPTIONS = [
  { value: "PREMIUM_DELIVERY", label: "付费快递到家", detail: "Premium Delivery" },
  { value: "PREMIUM_LOCATION", label: "付费服务点领取", detail: "Premium Location" },
  { value: "PICK_UP", label: "指定网点自取", detail: "Pick Up" }
];

const APPOINTMENT_PAYMENT_OPTIONS = [
  { value: "ALIPAY_EWALLET", label: "支付宝", detail: "Alipay eWallet" },
  { value: "CARD_RMB", label: "银行卡", detail: "Credit / Debit Card (RMB)" },
  { value: "CITIC", label: "中信银行", detail: "柜台或 ATM" }
];

const VISA_RULES = {
  b1b2: {
    sections: ["申请信息", "基础信息", "护照信息", "地址 / 电话 / 社交媒体", "旅行信息", "同行人", "以往赴美记录", "美国联系人", "家庭信息", "工作 / 教育 / 培训", "补充经历", "安全与背景问题", "照片与协助填写"],
    excludedFieldIds: [
      "education.schoolName", "education.schoolAddress",
      "education.programName", "education.sevisId",
      "education.programNumber", "education.sponsorName"
    ],
    excludedDocumentSlots: [
      "I-20 / Enrollment Letter", "I-20 / 录取或在读证明",
      "DS-2019 / 交流项目材料", "DS-7002 / 培训实习计划"
    ],
    excludedAgentNames: ["SEVIS / Student Info Agent"],
    extraMissingQuestions: [
      { id: "travel.purposeDetail", label: "客户本次赴美的具体访问目的是什么？", answer: "" },
      { id: "travel.payer", label: "本次旅行费用由谁承担？", answer: "" }
    ]
  },
  f1: {
    sections: ["申请信息", "基础信息", "护照信息", "地址 / 电话 / 社交媒体", "旅行信息", "同行人", "以往赴美记录", "美国联系人", "家庭信息", "工作 / 教育 / 培训", "补充经历", "F/J 补充联系人", "SEVIS / 学生信息", "安全与背景问题", "照片与协助填写"],
    excludedFieldIds: ["education.programNumber", "education.sponsorName"],
    excludedDocumentSlots: [
      "Employment Letter", "在职证明",
      "DS-2019 / 交流项目材料", "DS-7002 / 培训实习计划"
    ],
    excludedAgentNames: [],
    extraMissingQuestions: [
      { id: "sevis.program", label: "客户的学校项目名称 / 专业是什么？", answer: "" },
      { id: "sevis.funding", label: "I-20 上显示的资金来源和金额是否已核对？", answer: "" }
    ]
  },
  f2: {
    sections: ["申请信息", "基础信息", "护照信息", "地址 / 电话 / 社交媒体", "旅行信息", "同行人", "以往赴美记录", "美国联系人", "家庭信息", "工作 / 教育 / 培训", "补充经历", "F/J 补充联系人", "SEVIS / 学生信息", "安全与背景问题", "照片与协助填写"],
    excludedFieldIds: ["education.programNumber", "education.sponsorName", "education.programCategory"],
    excludedDocumentSlots: [
      "Employment Letter", "在职证明",
      "DS-2019 / 交流项目材料", "DS-7002 / 培训实习计划"
    ],
    excludedAgentNames: [],
    extraMissingQuestions: []
  },
  j1: {
    sections: ["申请信息", "基础信息", "护照信息", "地址 / 电话 / 社交媒体", "旅行信息", "同行人", "以往赴美记录", "美国联系人", "家庭信息", "工作 / 教育 / 培训", "补充经历", "F/J 补充联系人", "SEVIS / 学生信息", "安全与背景问题", "照片与协助填写"],
    excludedFieldIds: [],
    excludedDocumentSlots: ["I-20 / Enrollment Letter", "I-20 / 录取或在读证明"],
    excludedAgentNames: [],
    extraMissingQuestions: [
      { id: "sevis.ds2019Program", label: "DS-2019 上的项目名称和项目编号是否已核对？", answer: "" },
      { id: "sevis.sponsor", label: "交流访问项目 sponsor / 机构信息是什么？", answer: "" }
    ]
  },
  j2: {
    sections: ["申请信息", "基础信息", "护照信息", "地址 / 电话 / 社交媒体", "旅行信息", "同行人", "以往赴美记录", "美国联系人", "家庭信息", "工作 / 教育 / 培训", "补充经历", "F/J 补充联系人", "SEVIS / 学生信息", "安全与背景问题", "照片与协助填写"],
    excludedFieldIds: [],
    excludedDocumentSlots: ["I-20 / Enrollment Letter", "I-20 / 录取或在读证明"],
    excludedAgentNames: [],
    extraMissingQuestions: []
  }
};

const DATE_OPTIONS = [
  { label: "两周后", value: "2026-07-23" },
  { label: "一个月后", value: "2026-08-09" },
  { label: "客户计划前一周", value: "2026-08-11" },
  { label: "下月初跟进", value: "2026-09-01" }
];

const STATUS_LABELS = {
  pending: "待处理",
  empty: "未上传",
  uploading: "上传中",
  uploaded: "待扫描",
  queued: "等待扫描",
  running: "整理中",
  completed: "已完成",
  completed_with_errors: "部分完成",
  interrupted: "处理已中断",
  failed: "处理失败",
  confirmed: "已确认",
  edited: "已编辑",
  "needs-review": "待人工核查",
  resolved: "已解决",
  unresolved: "待处理"
};

const RISK_LABELS = {
  high: "高风险",
  medium: "中风险",
  low: "低风险"
};

const SECTION_LABELS = {
  "Personal Information": "基础信息",
  "Passport Information": "护照信息",
  "Travel Information": "旅行信息",
  "Travel Companions": "同行人",
  "Previous U.S. Travel": "以往赴美记录",
  "U.S. Contact": "美国联系人",
  "Family Information": "家庭信息",
  "Work / Education": "工作 / 教育 / 培训",
  "Security and Background": "安全与背景问题",
  "SEVIS / Student Info": "SEVIS / 学生信息",
  "客户基础信息": "基础信息",
  "身份材料": "护照信息",
  "客户需求信息": "旅行信息",
  "个人信息": "基础信息",
  "安全与背景问题": "安全与背景问题"
};

const FIELD_LABELS = {
  "Surname": "姓（Surname）",
  "Given Names": "名（Given Names）",
  "Date of Birth": "出生日期",
  "Passport Number": "护照号码",
  "Passport Expiration Date": "护照有效期至",
  "Visa Type": "签证类型 / 访问目的",
  "Intended Arrival Date": "预计抵达美国日期",
  "U.S. Address": "美国停留地址",
  "School Name": "学校名称",
  "SEVIS ID": "SEVIS ID",
  "Employer Name": "雇主名称"
};

const CATEGORY_LABELS = {
  "Cross-document Conflicts": "跨材料冲突",
  "Missing Fields": "缺失信息",
  "Low Confidence Fields": "低置信度信息",
  "Sensitive History Questions": "安全与背景问题"
};

const CRITICAL_REVIEW_FIELD_IDS = new Set([
  "personal.surname", "personal.givenNames", "personal.dateOfBirth",
  "passport.number", "passport.expiration", "travel.visaType",
  "travel.arrivalDate", "contact.usAddress", "education.sevisId",
  "education.programNumber", "history.previousVisaNumber"
]);

const SLOT_LABELS = {
  "Passport": "护照",
  "National ID": "身份证 / National ID",
  "Visa Photo": "签证照片",
  "Travel Itinerary": "旅行行程单",
  "Hotel Booking": "酒店预订单",
  "Invitation Letter": "邀请函",
  "Employment Letter": "在职证明",
  "Bank Statement / Financial Support": "银行流水 / 资金支持材料",
  "I-20 / Enrollment Letter": "I-20 / 录取或在读证明",
  "Previous U.S. Visa": "过往美国签证",
  "Other Supporting Documents": "其他支持材料"
};

const AGENT_LABELS = {
  "OCR / Parsing Agent": "OCR / 文档解析 Agent",
  "Passport Document Agent": "护照信息 Agent",
  "Travel Itinerary Agent": "旅行信息 Agent",
  "Employment / Education Agent": "工作 / 教育 / 培训 Agent",
  "Financial Document Agent": "资金材料 Agent",
  "Evidence Extraction Agent": "证明材料抽取 Agent",
  "Consistency Validation Agent": "一致性校验 Agent",
  "Missing Info Question Agent": "缺失信息提问 Agent",
  "DS-160 Field Mapping Agent": "DS-160 字段映射 Agent",
  "Audit Report Agent": "核查清单 Agent",
  "Personal Information Agent": "基础信息 Agent",
  "Passport Information Agent": "护照信息 Agent",
  "Travel Information Agent": "旅行信息 Agent",
  "Previous U.S. Travel Agent": "以往赴美记录 Agent",
  "Family / Work Education Agent": "家庭 / 工作教育 Agent",
  "SEVIS / Student Info Agent": "SEVIS / 学生信息 Agent",
  "DS-160 Draft Mapping Agent": "DS-160 初稿映射 Agent",
  "Review Checklist Agent": "核查清单 Agent"
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function loadLocalState() {
  state.applications = [];
  state.user = null;
  state.authDraft = {
    organizationName: "",
    name: "",
    phone: "",
    email: "",
    emailCode: "",
    password: "",
    confirmPassword: ""
  };
  localStorage.removeItem("docflowDs160LastEmail");
  localStorage.removeItem(SESSION_KEY);
}

async function loadState() {
  loadLocalState();
  if (!API_BASE) return;
  try {
    const healthResponse = await DocFlowApi.request(`${API_BASE}/health`);
    if (!healthResponse.ok) return;
    const healthData = await healthResponse.json();
    state.apiVersion = healthData.apiVersion || "";
    state.apiRevision = apiRevisionFromHealth(healthData);
    state.apiAvailable = (
      healthData.auth === "cookie-v1"
      && state.apiRevision >= REQUIRED_API_REVISION
    );
    state.mailService = healthData.emailVerification || null;
    state.translationService = healthData.translation || null;
    state.screenAgentRuntime = healthData.screenAgent || null;
    state.membershipBypass = healthData.membershipBypass === true;
    state.registrationVerification = healthData.registrationVerification || { mode: "none", required: false };
    if (state.apiAvailable) {
      const sessionResponse = await DocFlowApi.request(`${API_BASE}/session`);
      if (sessionResponse.ok) {
        const sessionData = await sessionResponse.json();
        state.user = sessionData.user || null;
        if (state.user) {
          const billingResponse = await DocFlowApi.request(`${API_BASE}/billing`);
          if (billingResponse.ok) {
            const billingData = await billingResponse.json();
            state.membership = billingData.membership || null;
            state.trial = billingData.trial || null;
          }
        }
      }
    }
  } catch (error) {
    console.warn("Account API is unavailable.", error);
  }
}

async function loadApplicationsForCurrentOrganization() {
  if (!state.apiAvailable || !API_BASE || !state.user) return;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases`);
    if (response.status === 401) {
      state.user = null;
      state.applications = [];
      return;
    }
    if (response.status === 402) {
      window.location.replace("/membership?access=required");
      return;
    }
    if (!response.ok) throw new Error("客户档案读取失败");
    const data = await response.json();
    state.applications = Array.isArray(data.cases) ? data.cases : [];
  } catch (error) {
    console.warn("Organization case load failed", error);
  }
}

function persistLocal() {
  localStorage.removeItem("docflowDs160LastEmail");
}

function persist() {
  persistLocal();
}

function syncApplication(application) {
  if (!state.apiAvailable || !API_BASE || !application?.id) return Promise.resolve(null);
  return DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case: application })
  }).then(async (response) => {
    if (response.status === 401) logout({ expired: true });
    if (!response.ok) throw new Error("客户档案保存失败");
    return response.json();
  }).catch((error) => console.warn("Case save failed", error));
}

function getActiveApplication() {
  const application = state.applications.find((item) => item.id === state.activeId) || null;
  if (!application || !state.user) return application;
  const applicationOrgId = application.caseMeta?.organizationId;
  if (applicationOrgId && state.user.organizationId) {
    return applicationOrgId === state.user.organizationId ? application : null;
  }
  return normalizeOrgName(organizationNameForApplication(application)) === normalizeOrgName(state.user.identity) ? application : null;
}

function normalizeOrgName(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function organizationNameForApplication(application) {
  const caseMeta = application?.caseMeta || application?.partnerMeta || {};
  return caseMeta.organizationName || "";
}

function visibleApplications() {
  if (!state.user) return [];
  return state.applications.filter((application) => {
    const applicationOrgId = application.caseMeta?.organizationId;
    if (applicationOrgId && state.user.organizationId) return applicationOrgId === state.user.organizationId;
    return normalizeOrgName(organizationNameForApplication(application)) === normalizeOrgName(state.user.identity);
  });
}

async function logout({ expired = false } = {}) {
  if (state.apiAvailable && API_BASE) {
    try {
      await DocFlowApi.request(`${API_BASE}/logout`, { method: "POST" });
    } catch (error) {
      console.warn("Logout request failed", error);
    }
  }
  state.user = null;
  state.applications = [];
  state.activeId = null;
  state.draftCase = {};
  state.authMode = "login";
  state.authError = expired ? "登录已失效，请重新登录。" : "";
  state.authDraft = {
    organizationName: "",
    name: "",
    phone: "",
    email: "",
    emailCode: "",
    password: "",
    confirmPassword: ""
  };
  clearSavedNavigation();
  route("login");
}

function switchOrganization() {
  logout();
}

function saveApplication(application) {
  normalizeApplicationForVisa(application);
  application.caseMeta = application.caseMeta || {};
  application.caseMeta.status = caseStatus(application.currentStep || 0);
  const index = state.applications.findIndex((item) => item.id === application.id);
  application.lastUpdated = new Date().toISOString();
  if (index >= 0) {
    state.applications[index] = application;
  } else {
    state.applications.unshift(application);
  }
  persist();
  return syncApplication(application);
}

function normalizeApplicationForVisa(application) {
  const visaId = visaByName(application.visaType).id;
  const rules = visaRules(visaId);
  application.documents = (application.documents || []).filter((item) => !rules.excludedDocumentSlots.includes(item.slot));
  buildDocumentsForVisa(visaId).forEach((template) => {
    if (!(application.documents || []).some((item) => item.slot === template.slot)) {
      application.documents.push(template);
    }
  });
  application.extractedFields = (application.extractedFields || []).filter((field) => !rules.excludedFieldIds.includes(field.id));
  application.missingQuestions = (application.missingQuestions || []).filter((question) => !(visaId === "b1b2" && question.id.startsWith("sevis.")));
  application.validationResults = (application.validationResults || []).filter((item) => !(visaId === "b1b2" && (item.id.startsWith("student.") || item.category === "SEVIS / 学生信息")));
  application.agentTimeline = (application.agentTimeline || []).filter((agent) => !rules.excludedAgentNames.includes(agent.name));
  if (visaId === "b1b2") {
    application.prefillLog = (application.prefillLog || []).filter((item) => !String(item).includes("SEVIS"));
  }
}

function route(view, applicationId) {
  if (["appointment-account", "appointment"].includes(view)) view = "report";
  const viewChanged = state.currentView !== view || Boolean(applicationId && applicationId !== state.activeId);
  if (state.currentView === "prefill") stopScreenAgentRuntime();
  clearDesktopAgentPolling();
  clearCodexAgentPolling();
  clearOpenCoworkPolling();
  if (applicationId) {
    state.activeId = applicationId;
  }
  if (state.currentView !== view) {
    state.previousView = state.currentView;
  }
  if (view === "questions" && state.currentView !== "questions") {
    state.showAllQuestions = false;
  }
  state.currentView = view;
  state.modal = null;
  if (state.processingTimer) {
    clearInterval(state.processingTimer);
    state.processingTimer = null;
  }
  saveNavigation(view, state.activeId);
  render(view);
  if (viewChanged) {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }
}

function saveNavigation(view, applicationId) {
  try {
    if (!state.user || view === "login") {
      sessionStorage.removeItem(NAVIGATION_KEY);
      return;
    }
    sessionStorage.setItem(NAVIGATION_KEY, JSON.stringify({
      view,
      applicationId: applicationId || ""
    }));
  } catch (error) {
    console.warn("Navigation state could not be saved", error);
  }
}

function readSavedNavigation() {
  try {
    return JSON.parse(sessionStorage.getItem(NAVIGATION_KEY) || "null");
  } catch (error) {
    return null;
  }
}

function clearSavedNavigation() {
  try {
    sessionStorage.removeItem(NAVIGATION_KEY);
  } catch (error) {
    console.warn("Navigation state could not be cleared", error);
  }
}

function openModal(type, payload = {}) {
  state.modal = { type, payload };
  render(state.currentView);
}

function closeModal() {
  state.modal = null;
  document.onkeydown = null;
  render(state.currentView);
}

function goBack() {
  const current = state.currentView;
  const application = getActiveApplication();
  const fallback = current === "dashboard" ? "login" : "dashboard";
  const map = {
    dashboard: "login",
    create: "dashboard",
    documents: "create",
    processing: "documents",
    fields: "documents",
    questions: "fields",
    validation: "questions",
    preview: "validation",
    prefill: "preview",
    report: "preview"
  };
  const target = map[current] || fallback;
  route(target, application?.id);
}

function stepIndexForView(view) {
  const map = {
    dashboard: 0,
    create: 0,
    documents: 1,
    processing: 2,
    fields: 3,
    questions: 4,
    validation: 5,
    preview: 6,
    prefill: 6,
    report: 7
  };
  return map[view] ?? 0;
}

function progressForApplication(application) {
  const step = Math.min(Math.max(application.currentStep || 0, 0), STEP_LABELS.length - 1);
  return Math.round((step + 1) / STEP_LABELS.length * 100);
}

function renderWorkspacePortalHeader() {
  return `
    <header class="workspace-portal-header">
      <button class="workspace-portal-brand" type="button" onclick="route('dashboard')" aria-label="返回操作台首页"><span>WV</span><strong>WestoryVisa</strong></button>
      <nav class="workspace-portal-nav" aria-label="工作台导航">
        <button class="active" type="button" onclick="route('dashboard')">操作台</button>
        <a href="/membership">会员中心</a>
        <a href="/membership#account">个人中心</a>
        <a href="/membership#help">帮助中心</a>
      </nav>
      <a class="workspace-portal-membership" href="/membership">会员与账户</a>
    </header>
  `;
}

function render(view = "login") {
  state.currentView = view;
  const app = document.querySelector("#app");
  if (!state.user && view !== "login") {
    renderLogin(app);
    return;
  }

  if (view === "login") {
    renderLogin(app);
    return;
  }

  app.innerHTML = `
    <div class="workspace-root">
      ${renderWorkspacePortalHeader()}
      <div class="app-shell">
        ${renderSidebar(stepIndexForView(view))}
        <main class="content" id="content"></main>
      </div>
    </div>
    ${renderModal()}
  `;

  const content = document.querySelector("#content");
  const views = {
    dashboard: renderDashboard,
    create: renderCreateProject,
    documents: renderDocuments,
    processing: renderProcessing,
    fields: renderFields,
    questions: renderQuestions,
    validation: renderValidation,
    preview: renderPreview,
    prefill: renderPrefill,
    report: renderReport
  };
  views[view](content);
  content.insertAdjacentHTML("afterbegin", renderMobileTopNav(view));
  content.insertAdjacentHTML("beforeend", renderWorkspaceDisclosures());
  content.insertAdjacentHTML("beforeend", renderFlowDock(view));
  wireModalEvents();
}

function renderMobileTopNav(view) {
  if (view === "dashboard") return "";
  return `
    <div class="mobile-top-nav">
      <button class="icon-btn" type="button" onclick="goBack()" aria-label="返回上一页">${iconArrowLeft()}</button>
      <span>${escapeHtml(STEP_LABELS[stepIndexForView(view)] || "工作台")}</span>
      <button class="icon-btn" type="button" onclick="route('dashboard')" aria-label="返回机构工作台">${iconHome()}</button>
    </div>
  `;
}

function renderFlowDock(view) {
  if (view === "dashboard" || view === "login") return "";
  const step = stepIndexForView(view);
  return `
    <nav class="flow-dock" aria-label="流程导航">
      <button class="icon-text-btn" type="button" onclick="goBack()">${iconArrowLeft()} 上一步</button>
      <div>
        <span>${step + 1} / ${STEP_LABELS.length}</span>
        <strong>${escapeHtml(STEP_LABELS[step])}</strong>
      </div>
      <button class="icon-text-btn" type="button" onclick="route('dashboard')">${iconHome()} 工作台</button>
    </nav>
  `;
}

function renderWorkspaceDisclosures() {
  return `
    <footer class="page-disclosures workspace-disclosures" id="workspace-disclosures" aria-label="工作台使用说明">
      <ol>
        <li>WestoryVisa 是签证顾问及机构客户使用的软件辅助工具，并非美国政府、美国国务院、任何使领馆或签证签发机构的官方网站或授权代表；不提供法律意见，也不保证签证申请获批。</li>
        <li>材料识别、翻译、字段整理、信息核查、DS‑160 初稿和可见页面填写结果均须由申请人或其授权顾问人工核对。申请人应对最终提交信息的真实性、准确性、完整性和时效性承担责任。</li>
        <li>验证码、账户凭证、拒签或移民历史判断、安全与背景问题、电子签名、法律声明、政府费用支付及最终提交必须由申请人或经授权的顾问人工处理。<a href="https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.html" target="_blank" rel="noopener noreferrer">查看美国国务院 DS‑160 填写说明</a>。</li>
        <li>案件中可能包含身份、护照、联系方式、旅行、教育、工作和签证申请材料；相关信息仅用于账户管理、材料整理、字段核查、服务安全及履行合同。第三方处理及跨境传输说明见<a href="/privacy">隐私政策</a>。</li>
        <li>我们采用传输加密、权限隔离、访问日志和最小权限等合理安全措施，但任何网络系统均无法保证绝对安全。请妥善保管登录凭证、限制团队权限，并及时删除不再需要的导出文件。</li>
      </ol>
    </footer>
  `;
}

function renderModal() {
  if (!state.modal) return "";
  const modal = state.modal;
  const content = {
    visa: renderChoiceModal({
      title: "选择签证类型",
      description: "选择后会同步到客户档案，用于展示相应的 DS-160 模块和核查重点。",
      type: "visa",
      options: VISA_OPTIONS,
      selected: visaByName(state.draftVisaType).id
    }),
    date: renderDateModal(),
    deleteDocument: renderDeleteDocumentModal(modal.payload),
    ocrPreview: renderOcrPreviewModal(modal.payload)
  }[modal.type];

  return `
    <div class="modal-layer" data-modal-layer>
      <section class="modal-sheet" role="dialog" aria-modal="true">
        <div class="sheet-handle" aria-hidden="true"></div>
        ${content}
      </section>
    </div>
  `;
}

function wireModalEvents() {
  if (!state.modal) return;
  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", closeModal);
  });
  document.querySelector("[data-modal-layer]")?.addEventListener("click", (event) => {
    if (event.target.dataset.modalLayer !== undefined) closeModal();
  });
  document.onkeydown = closeModalOnEscape;

  document.querySelectorAll("[data-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      const type = button.closest("[data-choice-type]").dataset.choiceType;
      if (type === "visa") state.draftVisaType = visaById(button.dataset.choice).name;
      render(state.currentView);
    });
  });

  document.querySelectorAll("[data-confirm-choice]").forEach((button) => {
    button.addEventListener("click", closeModal);
  });

  document.querySelectorAll("[data-date-option]").forEach((button) => {
    button.addEventListener("click", () => {
      state.draftDate = button.dataset.dateOption;
      render(state.currentView);
    });
  });

  document.querySelector("[data-confirm-date]")?.addEventListener("click", () => {
    const input = document.querySelector("#manualDate");
    const value = input.value.trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      input.closest(".form-row").classList.add("error");
      return;
    }
    const application = getActiveApplication();
    const field = application?.extractedFields.find((item) => item.id === state.modal.payload.fieldId);
    if (field) {
      field.value = value;
      field.editedByUser = true;
      field.confirmed = true;
      field.autoVerified = false;
      saveApplication(application);
    }
    state.draftDate = value;
    closeModal();
  });

  document.querySelector("[data-confirm-document-delete]")?.addEventListener("click", deleteSelectedDocument);
}

function closeModalOnEscape(event) {
  if (event.key === "Escape" && state.modal) closeModal();
}

function renderChoiceModal({ title, description, type, options, selected }) {
  return `
    <header class="modal-header">
      <div>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(description)}</p>
      </div>
      <button class="icon-btn" type="button" data-close-modal aria-label="关闭">${iconClose()}</button>
    </header>
    <div class="choice-list" data-choice-type="${type}">
      ${options.map((option) => `
        <button class="choice-option ${option.id === selected ? "selected" : ""} ${option.disabled ? "disabled" : ""}" type="button" data-choice="${option.id}" ${option.disabled ? "disabled" : ""}>
          <span class="choice-icon">${escapeHtml(option.icon)}</span>
          <span>
            <strong>${escapeHtml(option.name)}</strong>
            <small>${escapeHtml(option.description)}</small>
          </span>
          <span class="choice-check">${option.id === selected ? iconCheck() : ""}</span>
        </button>
      `).join("")}
    </div>
    <footer class="modal-footer">
      <button class="btn secondary" type="button" data-close-modal>取消</button>
      <button class="btn" type="button" data-confirm-choice="${type}">确认选择</button>
    </footer>
  `;
}

function renderDateModal() {
  return `
    <header class="modal-header">
      <div>
        <h2>选择预计跟进日期</h2>
        <p>用于填写 DS-160 旅行信息模块。涉及真实行程时，请由顾问根据客户资料人工确认。</p>
      </div>
      <button class="icon-btn" type="button" data-close-modal aria-label="关闭">${iconClose()}</button>
    </header>
    <div class="date-picker">
      <div class="date-current">
        <span>当前选择</span>
        <strong>${escapeHtml(state.draftDate)}</strong>
      </div>
      <div class="quick-date-grid">
        ${DATE_OPTIONS.map((item) => `
          <button class="quick-date ${item.value === state.draftDate ? "selected" : ""}" type="button" data-date-option="${item.value}">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(item.value)}</strong>
          </button>
        `).join("")}
      </div>
      <div class="form-row">
        <label for="manualDate">手动输入预计抵达日期</label>
        <input id="manualDate" type="text" inputmode="numeric" placeholder="YYYY-MM-DD" value="${escapeHtml(state.draftDate)}">
      </div>
      <p class="component-note error-state">如果日期格式不是 YYYY-MM-DD，确认按钮会提示错误状态。</p>
    </div>
    <footer class="modal-footer">
      <button class="btn secondary" type="button" data-close-modal>取消</button>
      <button class="btn" type="button" data-confirm-date>确认日期</button>
    </footer>
  `;
}

function renderDeleteDocumentModal(payload = {}) {
  return `
    <header class="modal-header">
      <div>
        <h2>删除这份材料？</h2>
        <p>删除后，该文件的识别文字、字段来源和证据记录会一并移除，避免错误材料继续影响 DS-160 初稿。</p>
      </div>
      <button class="icon-btn" type="button" data-close-modal aria-label="关闭">${iconClose()}</button>
    </header>
    <div class="modal-content document-delete-summary">
      <span>即将删除</span>
      <strong>${escapeHtml(payload.fileName || "已上传材料")}</strong>
      <div class="auth-error" id="modalError" role="alert"></div>
    </div>
    <footer class="modal-footer">
      <button class="btn secondary" type="button" data-close-modal>取消</button>
      <button class="btn danger" type="button" data-confirm-document-delete>确认删除</button>
    </footer>
  `;
}

function renderOcrPreviewModal(payload = {}) {
  const fields = Array.isArray(payload.fields) ? payload.fields : [];
  const content = payload.loading
    ? `<div class="ocr-preview-loading"><span class="loading-dot"></span><strong>正在读取识别结果</strong></div>`
    : payload.error
      ? `<div class="inline-notice visible error">${escapeHtml(payload.error)}</div>`
      : `
        <div class="ocr-preview-summary">
          <span>${escapeHtml(payload.parserName || "文档解析服务")}</span>
          <strong>${fields.length} 个 DS-160 字段</strong>
        </div>
        ${fields.length ? `
          <div class="ocr-field-list">
            ${fields.map((field) => `
              <div>
                <span>${escapeHtml(field.label || field.id)}</span>
                <strong>${escapeHtml(field.value || "待确认")}</strong>
              </div>
            `).join("")}
          </div>
        ` : `<p class="component-note">已识别出文字，但当前材料中没有可安全映射的 DS-160 字段。系统不会猜测缺失信息。</p>`}
        <div class="ocr-text-block">
          <span>识别文字</span>
          <pre>${escapeHtml(payload.text || "未识别到可读取文字")}</pre>
        </div>
      `;
  return `
    <header class="modal-header">
      <div>
        <h2>材料识别结果</h2>
        <p>${escapeHtml(payload.fileName || "已上传材料")}</p>
      </div>
      <button class="icon-btn" type="button" data-close-modal aria-label="关闭">${iconClose()}</button>
    </header>
    <div class="modal-content ocr-preview-content">${content}</div>
    <footer class="modal-footer">
      <button class="btn" type="button" data-close-modal>完成</button>
    </footer>
  `;
}

function renderSidebar(activeIndex) {
  return `
    <aside class="sidebar">
      <button class="brand" type="button" onclick="goBack()" aria-label="返回上一页">
        <div class="brand-mark" lang="en">WV</div>
        <div>
        <div class="brand-title" lang="en">WestoryVisa</div>
          <div class="brand-subtitle">中介机构填写辅助工具</div>
        </div>
      </button>
      <nav class="stepper" aria-label="Workflow steps">
        ${STEP_LABELS.map((label, index) => `
          <button class="step-item ${index === activeIndex ? "active" : ""} ${index < activeIndex ? "done" : ""}" type="button" ${index <= activeIndex ? `onclick="route('${viewForStep(index)}')"` : "disabled"}>
            <span class="step-index">${index + 1}</span>
            <span>${label}</span>
          </button>
        `).join("")}
      </nav>
      <div class="sidebar-note">
        使用边界：工具只辅助资料整理、初稿生成和核查清单，不提供法律建议，不替代顾问人工判断。
        <button class="sidebar-home" type="button" onclick="logout()">${iconArrowLeft()} 退出账号</button>
      </div>
    </aside>
  `;
}

function renderLogin(container) {
  if (state.emailCodeTimer) {
    clearInterval(state.emailCodeTimer);
    state.emailCodeTimer = null;
  }
  const isRegister = state.authMode === "register";
  const requiresEmailVerification = isRegister && state.registrationVerification?.mode === "email";
  const draft = state.authDraft;
  container.innerHTML = `
    <div class="auth-shell">
      <section class="auth-panel">
        <div class="auth-copy">
          <div class="brand-line">
            <span class="brand-dot"></span>
            <span lang="en">WestoryVisa</span>
          </div>
          <div class="auth-kicker">面向文案老师和签证顾问的 DS-160 工作台</div>
          <h1><span lang="en">DS-160</span> 高效核查</h1>
          <p class="auth-lede">按 DS-160 真实结构整理客户资料，辅助生成可核查的填写初稿。中介人员负责专业判断与最终确认，系统负责减少重复输入、字段遗漏和资料来回确认。</p>
          <div class="auth-tabs" role="tablist" aria-label="账号入口">
            <button class="${isRegister ? "" : "active"}" type="button" role="tab" aria-selected="${!isRegister}" data-auth-mode="login">账号登录</button>
            <button class="${isRegister ? "active" : ""}" type="button" role="tab" aria-selected="${isRegister}" data-auth-mode="register">注册机构账号</button>
          </div>
          ${!state.apiAvailable ? `
            <div class="service-alert" role="status">
              ${escapeHtml(apiCompatibilityMessage())}
            </div>
          ` : ""}
          ${requiresEmailVerification && state.apiAvailable && state.mailService && !state.mailService.configured ? `
            <div class="service-alert" role="status">
              注册邮箱验证尚未配置。请先运行“配置邮箱验证.command”，再重新启动应用。
            </div>
          ` : ""}
          <form id="authForm" class="entry-form" novalidate>
            ${isRegister ? `
              <div class="auth-form-grid">
                <div class="form-row">
                  <label for="authOrganization">机构 / 团队名称</label>
                  <input id="authOrganization" required autocomplete="organization" value="${escapeHtml(draft.organizationName)}" placeholder="例如：上海 XX 留学服务中心">
                </div>
                <div class="form-row">
                  <label for="authName">联系人姓名</label>
                  <input id="authName" required autocomplete="name" value="${escapeHtml(draft.name)}" placeholder="文案老师 / 签证顾问姓名">
                </div>
              </div>
              <div class="form-row">
                <label for="authPhone">手机号 <span>用于辅助验证与账号找回</span></label>
                <input id="authPhone" required type="tel" autocomplete="tel" value="${escapeHtml(draft.phone)}" placeholder="例如：138 0000 0000">
              </div>
            ` : ""}
            <div class="form-row">
              <label for="authEmail">工作邮箱</label>
              <div class="${requiresEmailVerification ? "input-with-action" : ""}">
                <input id="authEmail" required type="email" autocomplete="email" value="${escapeHtml(draft.email)}" placeholder="name@company.com">
                ${requiresEmailVerification ? `<button class="btn secondary verification-send" id="sendEmailCode" type="button">发送验证码</button>` : ""}
              </div>
              ${requiresEmailVerification ? `<span class="field-note" id="emailVerificationHint">验证码有效期 10 分钟，仅用于本次机构账号注册。</span>` : ""}
            </div>
            ${requiresEmailVerification ? `
              <div class="form-row verification-code-row">
                <label for="authEmailCode">邮箱验证码</label>
                <input id="authEmailCode" required type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" value="${escapeHtml(draft.emailCode || "")}" placeholder="请输入六位验证码">
              </div>
            ` : ""}
            <div class="${isRegister ? "auth-form-grid" : ""}">
              <div class="form-row">
                <label for="authPassword">密码</label>
                <input id="authPassword" required type="password" minlength="8" autocomplete="${isRegister ? "new-password" : "current-password"}" placeholder="${isRegister ? "至少 8 位" : "请输入密码"}">
              </div>
              ${isRegister ? `
                <div class="form-row">
                  <label for="authConfirmPassword">确认密码</label>
                  <input id="authConfirmPassword" required type="password" minlength="8" autocomplete="new-password" placeholder="再次输入密码">
                </div>
              ` : ""}
            </div>
            <div id="authError" class="auth-error ${state.authError ? "visible" : ""}" role="alert">${escapeHtml(state.authError)}</div>
            <div class="actions">
              <button class="btn auth-submit" id="authSubmit" type="submit">${isRegister ? "创建账号并选择会员" : "登录并进入工作台"}</button>
              <a class="auth-product-link" href="product.html">查看产品详情 <span aria-hidden="true">→</span></a>
            </div>
            ${isRegister ? `<p class="auth-helper">当前测试阶段暂不进行验证码校验；手机号会作为后续账号验证与找回信息保存，请妥善保管账号密码。</p>` : ""}
          </form>
          <div class="disclaimer">
            每个账号拥有独立账号 Key，每份客户档案按机构隔离访问。Computer Use 仅辅助写入经核对的信息，不处理验证码、电子签名或最终提交。
          </div>
        </div>
        <div class="auth-visual" aria-label="WestoryVisa 工作台预览">
          <div class="visual-topline">
            <span lang="en">DS-160 draft workspace</span>
            <strong lang="en">86%</strong>
          </div>
          <div class="visual-document">
            <div>
              <span class="visual-label" lang="en">Information completeness</span>
              <strong class="zh-feature-line" lang="zh-CN">基础信息 · 护照 · 旅行 · 家庭 · 工作教育</strong>
            </div>
            <span class="badge confirmed">初稿已生成</span>
          </div>
          <div class="visual-document raised">
            <div>
              <span class="visual-label" lang="en">Review queue</span>
              <strong class="zh-feature-line" lang="zh-CN">拒签记录 · 赴美历史 · 背景问题 · SEVIS 信息</strong>
            </div>
            <span class="badge running">待人工核查</span>
          </div>
          <div class="visual-grid">
            <span></span><span></span><span></span><span></span>
          </div>
          <div class="visual-safety">敏感背景问题只做提醒，不自动代填；提交前由顾问逐项确认</div>
        </div>
      </section>
      ${renderProductSections()}
    </div>
    ${renderWorkspaceDisclosures()}
    ${renderModal()}
  `;

  document.querySelectorAll("[data-auth-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      captureAuthDraft();
      state.authMode = button.dataset.authMode;
      state.authError = "";
      render("login");
    });
  });
  document.querySelector("#authForm").addEventListener("submit", submitAuthForm);
  document.querySelector("#sendEmailCode")?.addEventListener("click", sendRegistrationEmailCode);
  wireModalEvents();
  if (requiresEmailVerification && state.emailCodeCooldownUntil > Date.now()) startEmailCodeCooldown();
}

function captureAuthDraft() {
  state.authDraft = {
    organizationName: document.querySelector("#authOrganization")?.value.trim() ?? state.authDraft.organizationName,
    name: document.querySelector("#authName")?.value.trim() ?? state.authDraft.name,
    phone: document.querySelector("#authPhone")?.value.trim() ?? state.authDraft.phone,
    email: document.querySelector("#authEmail")?.value.trim() ?? state.authDraft.email,
    emailCode: document.querySelector("#authEmailCode")?.value.trim() ?? state.authDraft.emailCode,
    password: document.querySelector("#authPassword")?.value ?? "",
    confirmPassword: document.querySelector("#authConfirmPassword")?.value ?? ""
  };
}

function showAuthError(message) {
  state.authError = message;
  const error = document.querySelector("#authError");
  if (error) {
    error.textContent = message;
    error.classList.toggle("visible", Boolean(message));
  }
}

async function sendRegistrationEmailCode() {
  captureAuthDraft();
  const emailInput = document.querySelector("#authEmail");
  const button = document.querySelector("#sendEmailCode");
  const hint = document.querySelector("#emailVerificationHint");
  if (!emailInput?.checkValidity()) {
    emailInput?.reportValidity();
    return;
  }
  if (!state.apiAvailable || !API_BASE) {
    showAuthError("邮箱验证服务尚未连接，请重新启动“启动完整版本.command”。");
    return;
  }

  button.disabled = true;
  button.textContent = "正在发送…";
  showAuthError("");
  try {
    const response = await DocFlowApi.request(`${API_BASE}/email-verification/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: state.authDraft.email })
    });
    const data = await response.json();
    if (!response.ok) {
      if (data.retryAfter) {
        state.emailCodeCooldownUntil = Date.now() + Number(data.retryAfter) * 1000;
        startEmailCodeCooldown();
      }
      throw new Error(data.error || "验证码发送失败，请稍后重试。");
    }
    state.emailCodeCooldownUntil = Date.now() + Number(data.retryAfter || 60) * 1000;
    if (hint) {
      hint.textContent = data.deliveryMode === "mailpit"
        ? "验证码已发送到本地 Mailpit 测试邮箱。"
        : "验证码已发送，请检查收件箱和垃圾邮件目录。";
      hint.classList.add("success-text");
    }
    startEmailCodeCooldown();
    document.querySelector("#authEmailCode")?.focus();
  } catch (error) {
    showAuthError(error.message || "验证码发送失败，请稍后重试。");
    if (state.emailCodeCooldownUntil <= Date.now()) {
      button.disabled = false;
      button.textContent = "重新发送";
    }
  }
}

function startEmailCodeCooldown() {
  if (state.emailCodeTimer) clearInterval(state.emailCodeTimer);
  const update = () => {
    const button = document.querySelector("#sendEmailCode");
    if (!button) {
      clearInterval(state.emailCodeTimer);
      state.emailCodeTimer = null;
      return;
    }
    const seconds = Math.max(0, Math.ceil((state.emailCodeCooldownUntil - Date.now()) / 1000));
    if (seconds <= 0) {
      button.disabled = false;
      button.textContent = "重新发送";
      clearInterval(state.emailCodeTimer);
      state.emailCodeTimer = null;
      return;
    }
    button.disabled = true;
    button.textContent = `${seconds} 秒后重发`;
  };
  update();
  state.emailCodeTimer = setInterval(update, 1000);
}

async function submitAuthForm(event) {
  event.preventDefault();
  captureAuthDraft();
  const isRegister = state.authMode === "register";
  const draft = state.authDraft;
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  if (isRegister && draft.password !== draft.confirmPassword) {
    showAuthError("两次输入的密码不一致。");
    document.querySelector("#authConfirmPassword")?.focus();
    return;
  }
  if (isRegister && draft.password.length < 8) {
    showAuthError("密码至少需要 8 位。");
    return;
  }
  if (isRegister && state.registrationVerification?.mode === "email" && !/^\d{6}$/.test(draft.emailCode || "")) {
    showAuthError("请输入邮件中的六位验证码。");
    document.querySelector("#authEmailCode")?.focus();
    return;
  }
  if (!state.apiAvailable || !API_BASE) {
    showAuthError("账号服务尚未连接，请先运行“启动数据库版.command”，再刷新页面。");
    return;
  }

  const submitButton = document.querySelector("#authSubmit");
  submitButton.disabled = true;
  submitButton.textContent = isRegister ? "正在创建账号…" : "正在登录…";
  showAuthError("");

  try {
    const payload = isRegister ? {
      organizationName: draft.organizationName,
      name: draft.name,
      phone: draft.phone,
      email: draft.email,
      emailCode: state.registrationVerification?.mode === "email" ? draft.emailCode : "",
      password: draft.password
    } : {
      email: draft.email,
      password: draft.password
    };
    const response = await DocFlowApi.request(`${API_BASE}/${isRegister ? "register" : "login"}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "账号操作失败，请稍后重试。");

    state.user = data.user;
    state.activeId = null;
    state.applications = [];
    state.draftCase = {};
    state.authDraft = { ...state.authDraft, emailCode: "", password: "", confirmPassword: "" };
    persistLocal();
    if (isRegister) {
      window.location.replace("/membership?auth=registered");
      return;
    }

    const billingResponse = await DocFlowApi.request(`${API_BASE}/billing`);
    if (billingResponse.ok) {
      const billingData = await billingResponse.json();
      state.membership = billingData.membership || null;
      state.trial = billingData.trial || null;
    }
    if (!state.membership?.active && !state.trial?.active && !state.membershipBypass) {
      window.location.replace("/membership?auth=logged-in");
      return;
    }
    await loadApplicationsForCurrentOrganization();
    route("dashboard");
  } catch (error) {
    showAuthError(error.message || "账号操作失败，请稍后重试。");
    submitButton.disabled = false;
    submitButton.textContent = isRegister ? "创建账号并选择会员" : "登录并进入工作台";
  }
}

function renderProductSections() {
  return `
    <section class="workspace-brief">
      <div>
        <span class="page-kicker">开始使用</span>
        <h2>登录后，直接处理客户档案。</h2>
        <p>整理材料、核查待确认项并生成 DS-160 初稿，全部在同一工作台完成。</p>
      </div>
      <a class="btn secondary" href="product.html">查看产品介绍</a>
    </section>
  `;
}

function viewForStep(step) {
  return ["create", "documents", "processing", "fields", "questions", "validation", "preview", "report"][step] || "dashboard";
}

function renderDashboard(container) {
  const applications = visibleApplications();
  const organizationName = state.user?.identity || "未选择机构";
  const accountName = state.user?.name || state.user?.email || "当前账号";
  container.innerHTML = `
    <div class="topbar">
      <div>
        <div class="page-kicker">机构工作台</div>
        <h1>客户 DS-160 档案</h1>
        <p class="muted">${escapeHtml(accountName)} · ${escapeHtml(organizationName)}。当前账号只能访问本机构的客户档案。</p>
      </div>
      <div class="topbar-actions">
        <button class="btn" id="newProject">创建客户档案</button>
        <a class="btn secondary" href="/analytics.html">落地页数据</a>
        <a class="btn secondary" href="/landing-page" target="_blank" rel="noopener">查看落地页</a>
        <a class="btn secondary" href="/">返回机构接入</a>
        <button class="btn secondary" id="logoutAccount">退出账号</button>
      </div>
    </div>
    <section class="overview-strip">
      <div><strong>${applications.length}</strong><span>客户档案</span></div>
      <div><strong>${applications.filter((item) => item.currentStep >= 3).length}</strong><span>待人工核查</span></div>
      <div><strong>${applications.filter((item) => item.currentStep >= 7).length}</strong><span>已生成清单</span></div>
    </section>
    <section class="grid ${applications.length ? "three" : ""}">
      ${applications.length ? applications.map(renderProjectCard).join("") : `
        <div class="panel empty-state">
          <h2>${escapeHtml(organizationName)} 还没有客户档案</h2>
          <p class="muted">创建第一个客户档案后，它只会出现在当前机构工作台里。其他中介公司登录时不会看到这份档案。</p>
          <button class="btn" id="emptyNewProject">创建客户档案</button>
        </div>
      `}
    </section>
  `;

  document.querySelector("#newProject")?.addEventListener("click", () => route("create"));
  document.querySelector("#logoutAccount")?.addEventListener("click", () => logout());
  document.querySelector("#emptyNewProject")?.addEventListener("click", () => route("create"));
  document.querySelectorAll("[data-open-project]").forEach((button) => {
    button.addEventListener("click", () => route(viewForStep(Number(button.dataset.step)), button.dataset.openProject));
  });
  document.querySelectorAll("[data-open-documents]").forEach((button) => {
    button.addEventListener("click", () => route("documents", button.dataset.openDocuments));
  });
}

function renderProjectCard(application) {
  const progress = progressForApplication(application);
  const caseMeta = application.caseMeta || application.partnerMeta || {};
  const documentCount = visibleDocumentEntries(application)
    .filter(({ documentItem }) => documentItem.fileName).length;
  return `
    <article class="card project-card">
      <div>
        <h3>${escapeHtml(application.applicantName)}</h3>
        <div class="project-meta">
          ${caseMeta.owner ? `<span>负责人：${escapeHtml(caseMeta.owner)}</span>` : ""}
          <span>${escapeHtml(application.visaType)}</span>
          <span>${escapeHtml(caseMeta.status || caseStatus(application.currentStep))}</span>
          <span>更新于 ${formatDate(application.lastUpdated)}</span>
        </div>
      </div>
      <div>
        <div class="actions" style="justify-content:space-between">
          <span class="small muted">填写进度</span>
          <strong class="small">${progress}%</strong>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>
      </div>
      <div class="project-card-actions">
        <button class="btn" data-open-project="${application.id}" data-step="${application.currentStep}">继续处理</button>
        <button class="btn secondary" data-open-documents="${application.id}">客户文档库 · ${documentCount}</button>
      </div>
    </article>
  `;
}

function getCreateDraft() {
  return {
    applicantName: "",
    organizationName: state.user?.identity || "",
    passportNumber: "",
    owner: state.user?.name || "",
    notes: "",
    ...state.draftCase
  };
}

function captureCreateDraft() {
  const form = document.querySelector("#projectForm");
  if (!form) return;
  state.draftCase = {
    applicantName: document.querySelector("#applicantName")?.value.trim() || "",
    organizationName: document.querySelector("#organizationName")?.value.trim() || "",
    passportNumber: document.querySelector("#passportNumber")?.value.trim() || "",
    owner: document.querySelector("#owner")?.value.trim() || "",
    notes: document.querySelector("#notes")?.value.trim() || ""
  };
}

function visaRules(visaIdOrName) {
  const visaId = VISA_OPTIONS.some((item) => item.id === visaIdOrName) ? visaIdOrName : visaByName(visaIdOrName).id;
  return VISA_RULES[visaId] || VISA_RULES.f1;
}

function buildDocumentsForVisa(visaId) {
  const rules = visaRules(visaId);
  return UPLOAD_SLOTS
    .filter((slot) => !rules.excludedDocumentSlots.includes(slot))
    .map((slot) => ({
      slot,
      fileName: "",
      scanStatus: "empty",
      scanMessage: "等待上传真实文件"
    }));
}

function buildExtractedFieldsForVisa(visaType) {
  const rules = visaRules(visaType);
  return clone(BASE_EXTRACTED_FIELDS)
    .filter((field) => !rules.excludedFieldIds.includes(field.id))
    .map((field) => {
      const isVisaType = field.id === "travel.visaType";
      return {
        ...field,
        value: isVisaType ? visaType : "",
        sourceDocument: isVisaType ? "客户档案" : "等待材料扫描",
        confidence: isVisaType ? 1 : 0,
        evidence: "",
        sourcePage: null,
        extractionMethod: isVisaType ? "manual" : "",
        confirmed: false,
        editedByUser: false
      };
    });
}

function buildMissingQuestionsForVisa() {
  return [];
}

function buildValidationResultsForVisa(visaId) {
  if (visaId === "b1b2") {
    return [
      {
        id: "b1b2.travelPurpose",
        type: "review",
        severity: "medium",
        category: "旅行信息",
        message: "B1/B2 需要重点核查访问目的、预计停留时间、费用承担人和在美停留地址是否一致。",
        requiresUserResolution: true,
        resolved: false
      }
    ];
  }
  return [
    {
      id: "student.sevis",
      type: "review",
      severity: "high",
      category: "SEVIS / 学生信息",
      message: "F/M/J 类签证需要核查 SEVIS ID、学校或项目名称、学校地址与 I-20 / DS-2019 是否一致。",
      requiresUserResolution: true,
      resolved: false
    }
  ];
}

function buildAgentTimelineForVisa(visaId) {
  const rules = visaRules(visaId);
  return AGENT_STEPS
    .filter((name) => !rules.excludedAgentNames.includes(name))
    .map((name) => ({ name, status: "pending", output: "" }));
}

function visibleSectionsForApplication(application) {
  return visaRules(application.visaType).sections;
}

function visibleFieldsForApplication(application) {
  const rules = visaRules(application.visaType);
  return (application.extractedFields || []).filter((field) => !rules.excludedFieldIds.includes(field.id));
}

function visibleDocumentEntries(application) {
  const rules = visaRules(application.visaType);
  return (application.documents || [])
    .map((documentItem, index) => ({ documentItem, index }))
    .filter(({ documentItem }) => !rules.excludedDocumentSlots.includes(documentItem.slot));
}

function visibleQuestionsForApplication(application) {
  if ((application.branchQuestionnaire || []).length) return [];
  const visaId = visaByName(application.visaType).id;
  return (application.missingQuestions || []).filter((question) => {
    if (visaId === "b1b2" && question.id.startsWith("sevis.")) return false;
    return true;
  });
}

function visibleValidationResultsForApplication(application) {
  const visaId = visaByName(application.visaType).id;
  return (application.validationResults || [])
    .filter((item) => {
      if (visaId === "b1b2" && (item.id.startsWith("student.") || item.category === "SEVIS / 学生信息")) return false;
      return true;
    })
    .map((item) => {
      if (visaId === "b1b2" && item.id === "conflict.nameOrder") {
        return {
          ...item,
          message: "护照显示 ZHANG WEI，但客户问卷显示 WEI ZHANG。请确认 DS-160 中应填写的姓名顺序。"
        };
      }
      return item;
    });
}

function renderCreateProject(container) {
  const draft = getCreateDraft();
  container.innerHTML = `
    <div class="topbar">
      <div>
        <div class="breadcrumb">
          <button type="button" onclick="route('dashboard')">${iconHome()} 工作台</button>
          <span>/</span>
          <span>创建客户档案</span>
        </div>
        <div class="page-kicker">客户档案</div>
        <h1>创建客户档案</h1>
        <p class="muted">只需建立客户归属和签证类别。护照号等资料可在上传后自动识别，不必在这里重复录入。</p>
      </div>
      <button class="btn secondary" id="backDashboard">${iconArrowLeft()} 返回工作台</button>
    </div>
    <section class="panel">
      <form id="projectForm" class="grid two">
        <div class="form-row">
          <label for="applicantName">客户姓名</label>
          <input id="applicantName" required value="${escapeHtml(draft.applicantName)}" placeholder="例如：张伟 / ZHANG WEI">
        </div>
        <div class="form-row">
          <label for="organizationName">机构 / 团队名称</label>
          <input id="organizationName" required readonly autocomplete="organization" value="${escapeHtml(draft.organizationName)}" aria-describedby="organizationLockNote">
          <small id="organizationLockNote" class="field-note">客户档案固定归属当前登录机构。</small>
        </div>
        <div class="form-row">
          <label for="passportNumber">护照号 <span>可稍后自动识别</span></label>
          <input id="passportNumber" value="${escapeHtml(draft.passportNumber)}" placeholder="无需重复录入，可留空">
        </div>
        <div class="form-row">
          <label for="owner">负责人</label>
          <input id="owner" required autocomplete="name" value="${escapeHtml(draft.owner)}" placeholder="文案老师 / 签证顾问姓名">
        </div>
        <div class="form-row">
          <label>签证类型</label>
          <button class="select-trigger" type="button" id="visaTypeTrigger">
            <span>
              <strong>${escapeHtml(visaByName(state.draftVisaType).name)}</strong>
              <small>${escapeHtml(visaByName(state.draftVisaType).description)}</small>
            </span>
            ${iconChevronDown()}
          </button>
        </div>
        <div class="form-row full">
          <label for="notes">内部备注</label>
          <textarea id="notes" placeholder="可记录客户材料缺口、顾问提醒、已沟通事项、二审关注点等">${escapeHtml(draft.notes)}</textarea>
        </div>
        <div class="disclaimer form-wide">
          系统将保存为本地客户档案。关键字段、敏感背景问题和最终 DS-160 内容仍需文案老师 / 签证顾问确认。
        </div>
        <div class="inline-notice form-wide" id="createNotice" role="status"></div>
        <div class="actions form-wide">
          <button class="btn" type="submit" id="createProjectButton">创建客户档案</button>
        </div>
      </form>
    </section>
  `;

  document.querySelector("#backDashboard").addEventListener("click", () => route("dashboard"));
  document.querySelectorAll("#projectForm input, #projectForm select, #projectForm textarea").forEach((input) => {
    input.addEventListener("input", captureCreateDraft);
    input.addEventListener("change", captureCreateDraft);
  });
  document.querySelector("#visaTypeTrigger").addEventListener("click", () => {
    captureCreateDraft();
    openModal("visa");
  });
  document.querySelector("#projectForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    captureCreateDraft();
    const visaType = state.draftVisaType;
    const visaId = visaByName(visaType).id;
    const application = {
      id: `app-${Date.now()}`,
      applicantName: document.querySelector("#applicantName").value.trim(),
      email: document.querySelector("#owner").value.trim(),
      visaType,
      caseMeta: {
        organizationName: state.user.identity,
        organizationId: state.user.organizationId,
        passportNumber: document.querySelector("#passportNumber").value.trim(),
        owner: document.querySelector("#owner").value.trim(),
        ownerUserId: state.user.id,
        ownerEmail: state.user.email,
        accountKeyId: state.user.accountKeyId,
        status: "资料收集中",
        notes: document.querySelector("#notes").value.trim()
      },
      currentStep: 1,
      documents: buildDocumentsForVisa(visaId),
      extractedFields: buildExtractedFieldsForVisa(visaType),
      missingQuestions: buildMissingQuestionsForVisa(visaId),
      validationResults: buildValidationResultsForVisa(visaId),
      agentTimeline: buildAgentTimelineForVisa(visaId),
      auditReport: {},
      prefillLog: [],
      createdAt: new Date().toISOString(),
      lastUpdated: new Date().toISOString()
    };
    const passportField = application.extractedFields.find((field) => field.id === "passport.number");
    if (passportField) {
      passportField.value = application.caseMeta.passportNumber;
      passportField.sourceDocument = "客户档案";
      passportField.confidence = 1;
      passportField.extractionMethod = "manual";
    }
    state.activeId = application.id;
    const submitButton = document.querySelector("#createProjectButton");
    submitButton.disabled = true;
    submitButton.textContent = "正在创建…";
    const saved = await saveApplication(application);
    if (!saved?.case) {
      const notice = document.querySelector("#createNotice");
      notice.textContent = "客户档案未能写入数据库，请确认本地服务器仍在运行后重试。";
      notice.className = "inline-notice form-wide visible error";
      submitButton.disabled = false;
      submitButton.textContent = "创建客户档案";
      return;
    }
    replaceApplication(saved.case);
    state.draftCase = {};
    route("documents", saved.case.id);
  });
}

function renderDocuments(container) {
  const application = getActiveApplication();
  const visibleDocuments = visibleDocumentEntries(application);
  const uploadedDocuments = visibleDocuments.filter(({ documentItem }) => documentItem.fileName);
  const uploadedCount = uploadedDocuments.length;
  const hasActiveUpload = visibleDocuments.some(({ documentItem }) => ["uploading", "queued", "running"].includes(documentItem.scanStatus));
  container.innerHTML = `
    ${renderAppHeader(application, "收集客户资料", "文件将安全保存到当前机构的客户档案，并进行版面解析、中英文识别和 DS-160 字段映射。支持 PDF、PNG、JPG 和 TIFF，单个文件不超过 25 MB。")}
    <section class="ocr-service-strip">
      <div>
        <strong>${escapeHtml(state.ocrService?.providerLabel || "文档扫描服务")}</strong>
        <span id="ocrServiceMessage">${escapeHtml(state.ocrService?.message || "正在检查安装与运行状态")}</span>
      </div>
      <div class="ocr-service-actions">
        <span class="badge ${state.ocrService?.available ? "completed" : "pending"}" id="ocrServiceBadge">
          ${state.ocrService ? (state.ocrService.available ? "服务可用" : "尚未启动") : "正在检查"}
        </span>
        <button class="service-start-button" type="button" id="startOcrService" ${state.ocrService?.available ? "hidden" : ""}>启动扫描服务</button>
      </div>
    </section>
    ${renderKnownInformationPanel(application)}
    ${renderMaterialLibrary(application, uploadedDocuments)}
    <section class="grid two document-upload-grid">
      ${visibleDocuments.map(({ documentItem, index }) => `
        <article class="card upload-slot">
          <div class="upload-slot-heading">
            <h3>
              <span>${escapeHtml(localizeSlot(documentItem.slot))}</span>
              ${isOptionalDocumentSlot(documentItem.slot) ? "<em>选传</em>" : ""}
            </h3>
            <div class="upload-slot-controls">
              <span class="badge ${documentStatusClass(documentItem.scanStatus)}">${statusLabel(documentItem.scanStatus || (documentItem.fileName ? "uploaded" : "empty"))}</span>
              ${documentItem.fileName ? `
                <button class="icon-btn document-delete" type="button" data-delete-document="${index}" aria-label="删除 ${escapeHtml(documentItem.fileName)}" title="删除已上传文件" ${["uploading", "queued", "running"].includes(documentItem.scanStatus) ? "disabled" : ""}>${iconTrash()}</button>
              ` : ""}
            </div>
          </div>
          <div class="upload-row">
            <input type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" data-document-index="${index}" aria-label="${escapeHtml(localizeSlot(documentItem.slot))} 文件" ${["uploading", "running"].includes(documentItem.scanStatus) ? "disabled" : ""}>
          </div>
          <div class="document-file-meta">
            <strong>${documentItem.fileName ? escapeHtml(documentItem.fileName) : "尚未选择文件"}</strong>
            ${documentItem.fileSize ? `<span>${formatFileSize(documentItem.fileSize)}</span>` : ""}
          </div>
          <div class="small muted">${escapeHtml(documentDisplayMessage(documentItem))}</div>
          ${documentItem.scanStatus === "completed" ? `
            <button class="document-result-link" type="button" data-view-ocr="${index}">查看识别文字与映射字段</button>
          ` : ""}
        </article>
      `).join("")}
    </section>
    <div class="inline-notice" id="documentsNotice" role="status"></div>
    <div class="actions" style="margin-top:18px">
      <button class="btn" id="startReview" ${!uploadedCount || hasActiveUpload ? "disabled" : ""}>开始扫描并生成初稿</button>
      <button class="btn secondary" id="retryOcrService">重新检查扫描服务</button>
      <button class="btn secondary" id="continueWithoutScan">暂不扫描，进入字段核查</button>
      <button class="btn secondary" id="saveDocuments">保存草稿</button>
    </div>
  `;

  document.querySelectorAll("[data-document-index]").forEach((input) => {
    input.addEventListener("change", async () => {
      const index = Number(input.dataset.documentIndex);
      const file = input.files[0];
      if (file) await uploadDocumentFile(application, index, file, container);
    });
  });
  document.querySelectorAll("[data-delete-document]").forEach((button) => {
    button.addEventListener("click", () => {
      const documentItem = application.documents[Number(button.dataset.deleteDocument)];
      openModal("deleteDocument", {
        applicationId: application.id,
        documentId: documentItem.id,
        fileName: documentItem.fileName
      });
    });
  });
  document.querySelectorAll("[data-view-ocr]").forEach((button) => {
    button.addEventListener("click", () => {
      const documentItem = application.documents[Number(button.dataset.viewOcr)];
      openDocumentOcrPreview(application, documentItem);
    });
  });
  const knownInformationInput = document.querySelector("#consultantKnownInformation");
  knownInformationInput?.addEventListener("input", () => {
    application.knownInformation = application.knownInformation || {};
    application.knownInformation.text = knownInformationInput.value;
    const analyzeButton = document.querySelector("#analyzeKnownInformation");
    if (analyzeButton) analyzeButton.disabled = !knownInformationInput.value.trim();
  });
  document.querySelector("#analyzeKnownInformation")?.addEventListener("click", () => (
    analyzeConsultantInformation(application, container)
  ));
  document.querySelector("#reviewKnownInformation")?.addEventListener("click", () => (
    enterFieldReview(application)
  ));
  document.querySelector("#saveDocuments").addEventListener("click", async () => {
    await saveApplication(application);
    showDocumentsNotice("客户资料草稿已保存。", "success");
  });
  document.querySelector("#startReview")?.addEventListener("click", () => startDocumentScan(application));
  document.querySelector("#startOcrService")?.addEventListener("click", async () => {
    try {
      const ready = await startOcrServiceFromPage();
      if (ready) showDocumentsNotice("文档扫描服务已启动，可以开始生成初稿。", "success");
    } catch (error) {
      showDocumentsNotice(error.message || "扫描服务启动失败");
    }
  });
  document.querySelector("#retryOcrService")?.addEventListener("click", () => {
    refreshOcrServiceStatus();
    refreshTranslationServiceStatus();
  });
  document.querySelector("#continueWithoutScan")?.addEventListener("click", () => enterFieldReview(application));
  refreshOcrServiceStatus();
  refreshTranslationServiceStatus();
}

function isOptionalDocumentSlot(slot) {
  return ["Invitation Letter", "Previous U.S. Visa", "Other Supporting Documents", "邀请函", "过往美国签证", "其他支持材料"]
    .includes(String(slot || "").trim());
}

function renderKnownInformationPanel(application) {
  const known = application.knownInformation || {};
  const parsedFields = Array.isArray(known.parsedFields) ? known.parsedFields : [];
  const parsedQuestions = Array.isArray(known.parsedQuestions) ? known.parsedQuestions : [];
  const recognizedEntries = Array.isArray(known.recognizedEntries) ? known.recognizedEntries : [];
  const providers = Array.isArray(known.analysisProviders) ? known.analysisProviders : [];
  const semanticAddedCount = Number(known.semanticAddedCount || 0);
  const parsedQuestionCount = Number(known.parsedQuestionCount || parsedQuestions.length || 0);
  const recognizedGroupCount = Number(
    known.recognizedGroupCount || parsedFields.length + parsedQuestionCount
  );
  const recognizedValueCount = Number(
    known.recognizedValueCount || recognizedGroupCount
  );
  const parsedRecordCount = Number(known.parsedRecordCount || 0);
  const recognizedSourceCount = Number(
    known.recognizedSourceCount || known.answeredQaCount || recognizedEntries.length || 0
  );
  const matchedSourceCount = Number(
    known.matchedSourceCount || known.matchedQaCount
    || recognizedEntries.filter((item) => item.matched).length
  );
  const warnings = Array.isArray(known.warnings) ? known.warnings : [];
  const translatedCount = parsedFields.filter((field) => field.originalValue).length;
  const hasResults = recognizedGroupCount > 0 || recognizedSourceCount > 0;
  const deepSeekReady = Boolean(state.translationService?.deepSeek);
  const libreTranslateReady = Boolean(state.translationService?.libreTranslate);
  const translationReady = deepSeekReady || libreTranslateReady;
  const sourceLabel = (provider) => ({
    ollama_structured: "本地语义补漏",
    ollama: "本地模型翻译",
    local_dictionary: "DS-160 词典",
    local_glossary: "DS-160 词典",
    local_glossary_transliteration: "词典与拼音",
    local_transliteration: "拼音转写",
    libretranslate: "LibreTranslate 中译英",
    deepseek: "DeepSeek 中译英",
    original: "原文可直接使用",
    normalizer: "格式规范化",
    address_parser: "地址结构化"
  }[provider] || "规则识别");
  return `
    <section class="known-information-panel" aria-labelledby="knownInformationTitle">
      <header>
        <div>
          <span class="page-kicker">顾问已知信息</span>
          <h2 id="knownInformationTitle">粘贴客户已经提供的文字</h2>
          <p>可直接粘贴微信、邮件或笔记中的连续文字。系统先扫描 DS-160 字段，再用本地语义模型补漏，最后统一转换为可核查的英文值。</p>
        </div>
        ${hasResults ? `<span class="badge completed">${recognizedSourceCount || recognizedGroupCount} 条资料已读取</span>` : ""}
      </header>
      <textarea id="consultantKnownInformation" rows="5" placeholder="例如：姓名张明，身份证号……，家庭住址山东省青岛市市南区香港中路 10 号，邮编 266071，手机号……">${escapeHtml(known.text || "")}</textarea>
      ${!translationReady ? `
        <div class="known-information-warning" id="translationServiceStatus">
          DeepSeek 中译英尚未连接。规则仍会识别证件号、日期和固定选项，但中文地址、学校、公司及职责暂不能保证为正常英文语序。请联系管理员检查 DeepSeek 服务配置。
        </div>
      ` : `
        <div class="known-information-service-ready" id="translationServiceStatus">${deepSeekReady ? "DeepSeek" : "LibreTranslate"} 中译英已连接 · 中文原文会保留用于核对</div>
      `}
      <div class="known-information-actions">
        <span>系统会先区分题目与回答，再整理直接字段、条件问答和重复记录；原文与识别证据始终保留。</span>
        <button class="btn secondary" type="button" id="analyzeKnownInformation" ${String(known.text || "").trim() ? "" : "disabled"}>识别、翻译并整理</button>
      </div>
      ${warnings.map((warning) => `<div class="known-information-warning">${escapeHtml(warning)}</div>`).join("")}
      ${!hasResults && known.updatedAt ? `
        <div class="known-information-empty">
          <strong>原文已保存，暂未写入字段</strong>
          <span>可继续补充姓名、证件号、日期、地址、学校、工作或旅行事实，再重新整理。</span>
        </div>
      ` : ""}
      ${hasResults ? `
        <div class="known-information-summary" aria-label="文字资料识别摘要">
          <div><strong>${recognizedSourceCount}</strong><span>原始回答条目</span></div>
          <div><strong>${recognizedGroupCount}</strong><span>DS-160 数据组</span></div>
          <div><strong>${parsedFields.length}</strong><span>直接字段</span></div>
          <div><strong>${parsedQuestionCount}</strong><span>条件问答 · ${parsedRecordCount} 条记录</span></div>
          <div><strong>${recognizedValueCount}</strong><span>已识别具体值</span></div>
        </div>
        ${recognizedEntries.length ? `
        <details class="known-source-entry-results">
          <summary>
            <span>逐条核对原始回答</span>
            <strong>${matchedSourceCount} / ${recognizedSourceCount} 条已匹配到 DS-160</strong>
          </summary>
          <div class="known-source-entry-list">
            ${recognizedEntries.map((entry, index) => `
              <article>
                <span>${escapeHtml(String(entry.number || index + 1))}</span>
                <div>
                  <strong>${escapeHtml(entry.question || "未命名信息")}</strong>
                  <p>${escapeHtml(entry.answer || "")}</p>
                </div>
                <em class="${entry.matched ? "matched" : "preserved"}">${entry.matched ? "已匹配" : "已保留原文"}</em>
              </article>
            `).join("")}
          </div>
        </details>
        ` : ""}
        ${parsedFields.length ? `
        <div class="known-information-results">
          ${parsedFields.slice(0, 12).map((field) => `
            <div>
              <span>${escapeHtml(field.label || field.id)}</span>
              <strong>${escapeHtml(field.value || "")}</strong>
              ${field.originalValue ? `<small>原文：${escapeHtml(field.originalValue)}</small>` : ""}
              <em>${escapeHtml(sourceLabel(field.translationProvider))}</em>
            </div>
          `).join("")}
          ${parsedFields.length > 12 ? `<p>另有 ${parsedFields.length - 12} 项已写入字段核查。</p>` : ""}
        </div>
        ` : ""}
        ${parsedQuestions.length ? `
        <details class="known-questionnaire-results" open>
          <summary>
            <span>问答、人员与经历明细</span>
            <strong>${parsedQuestions.length} 组 · ${parsedRecordCount} 条重复记录</strong>
          </summary>
          <div class="known-questionnaire-list">
            ${parsedQuestions.map((question) => `
              <article>
                <header>
                  <div>
                    <span>${escapeHtml(question.section || "DS-160 问答")}</span>
                    <h4>${escapeHtml(question.label || question.id || "已识别问答")}</h4>
                  </div>
                  ${question.answer ? `<strong>${escapeHtml(question.answer)}</strong>` : ""}
                </header>
                ${Number(question.confidence || 1) < 0.95 ? `<p class="known-questionnaire-inference">根据已提供上下文整理，建议顾问核对该选项。</p>` : ""}
                ${(question.details || []).length ? `
                  <div class="known-questionnaire-values">
                    ${(question.details || []).map((item) => `
                      <div>
                        <span>${escapeHtml(item.label || item.id || "明细")}</span>
                        <strong>${escapeHtml(item.value || "")}</strong>
                        ${item.originalValue && item.originalValue !== item.value ? `<small>原文：${escapeHtml(item.originalValue)}</small>` : ""}
                      </div>
                    `).join("")}
                  </div>
                ` : ""}
                ${(question.records || []).length ? `
                  <div class="known-questionnaire-records">
                    ${(question.records || []).map((record, index) => `
                      <div>
                        <b>${escapeHtml(`${question.recordLabel || "记录"} ${index + 1}`)}</b>
                        ${(record || []).map((item) => `
                          <span><em>${escapeHtml(item.label || item.id || "明细")}</em>${escapeHtml(item.value || "")}</span>
                        `).join("")}
                      </div>
                    `).join("")}
                  </div>
                ` : ""}
              </article>
            `).join("")}
          </div>
        </details>
        ` : ""}
        <div class="known-information-review-action">
          <span>${providers.includes("numbered_qa_parser") ? "已按编号问答边界识别" : providers.includes("ollama_structured") ? "已使用本地语义识别" : "已使用规则识别"}${translatedCount ? `，${translatedCount} 项保留中英对照` : ""}${semanticAddedCount ? `，语义补漏 ${semanticAddedCount} 项` : ""}。</span>
          <button class="btn secondary" type="button" id="reviewKnownInformation">查看全部字段与问答</button>
        </div>
      ` : ""}
    </section>
  `;
}

async function analyzeConsultantInformation(application, container) {
  const input = document.querySelector("#consultantKnownInformation");
  const button = document.querySelector("#analyzeKnownInformation");
  const text = input?.value.trim() || "";
  if (!text || !button) return;
  button.disabled = true;
  button.textContent = "正在扫描、翻译并核对…";
  try {
    const response = await DocFlowApi.request(
      `${API_BASE}/cases/${encodeURIComponent(application.id)}/known-information`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      }
    );
    const data = await response.json();
    if (response.status === 401) {
      await logout({ expired: true });
      return;
    }
    if (!response.ok) throw new Error(data.error || "顾问已知信息识别失败");
    if (data.translation) state.translationService = data.translation;
    replaceApplication(data.case);
    renderDocuments(container);
    const translationNote = data.translationReviewCount
      ? `，其中 ${data.translationReviewCount} 项英文转写建议在字段核查页确认`
      : "";
    const semanticNote = data.semanticAddedCount
      ? `，本地语义模型补充 ${data.semanticAddedCount} 项`
      : "";
    const questionNote = data.parsedQuestionCount
      ? `，同步 ${data.parsedQuestionCount} 个问答分支`
      : "";
    const schoolNote = data.schoolLookupCount
      ? `，定位并整理 ${data.schoolLookupCount} 所学校的英文名与结构化地址`
      : "";
    const schoolReviewNote = data.schoolLookupReviewCount
      ? `，其中 ${data.schoolLookupReviewCount} 所仍需核对具体校址`
      : "";
    const warningNote = data.warnings?.length ? `；${data.warnings.join("；")}` : "";
    const sourceNote = data.recognizedSourceCount
      ? `已读取 ${data.recognizedSourceCount} 条原始回答，其中 ${data.matchedSourceCount || 0} 条已匹配；`
      : "";
    const resultMessage = data.parsedCount || data.parsedQuestionCount
      ? `${sourceNote}已整理 ${data.recognizedGroupCount || (data.parsedCount + data.parsedQuestionCount)} 组 DS-160 信息，共 ${data.recognizedValueCount || "多"} 个具体值${questionNote}${schoolNote}${schoolReviewNote}${semanticNote}${translationNote}${warningNote}。`
      : `已保存原文，但暂未识别到可可靠写入的字段${warningNote}。`;
    showDocumentsNotice(resultMessage, data.parsedCount || data.parsedQuestionCount ? "success" : "warning");
  } catch (error) {
    button.disabled = false;
    button.textContent = "识别、翻译并整理";
    showDocumentsNotice(error.message || "顾问已知信息识别失败");
  }
}

function documentDisplayMessage(documentItem) {
  const message = String(documentItem.scanMessage || "").trim();
  if (message && !(message === "可选材料，尚未上传" && !isOptionalDocumentSlot(documentItem.slot))) {
    return message;
  }
  if (documentItem.fileName) return "已上传，等待扫描";
  return isOptionalDocumentSlot(documentItem.slot) ? "选传材料，按实际情况上传" : "等待上传材料";
}

function documentFileUrl(applicationId, documentId) {
  return `${API_BASE}/cases/${encodeURIComponent(applicationId)}/documents/${encodeURIComponent(documentId)}/file`;
}

function renderMaterialLibrary(application, uploadedDocuments) {
  return `
    <section class="material-library" aria-labelledby="materialLibraryTitle">
      <header class="material-library-heading">
        <div>
          <span class="page-kicker">客户材料库</span>
          <h2 id="materialLibraryTitle">已保存原始文件</h2>
          <p>原件保存在当前机构的客户档案中，仅登录本机构账号后可以查看。</p>
        </div>
        <strong>${uploadedDocuments.length} 份</strong>
      </header>
      <div class="material-library-list">
        ${uploadedDocuments.length ? uploadedDocuments.map(({ documentItem, index }) => `
          <div class="material-library-row">
            <div class="material-library-file">
              <span class="material-file-type">${escapeHtml((String(documentItem.fileName).split(".").pop() || "文件").toUpperCase())}</span>
              <span>
                <strong>${escapeHtml(documentItem.fileName)}</strong>
                <small>${escapeHtml(localizeSlot(documentItem.slot))}${documentItem.fileSize ? ` · ${formatFileSize(documentItem.fileSize)}` : ""}</small>
              </span>
            </div>
            <div class="material-library-actions">
              <span class="badge ${documentStatusClass(documentItem.scanStatus)}">${statusLabel(documentItem.scanStatus || "uploaded")}</span>
              ${documentItem.scanStatus === "completed" ? `<button class="icon-text-btn" type="button" data-view-ocr="${index}">查看识别结果</button>` : ""}
              <a class="icon-text-btn" href="${escapeHtml(documentFileUrl(application.id, documentItem.id))}" target="_blank" rel="noopener">查看原件</a>
            </div>
          </div>
        `).join("") : `
          <div class="material-library-empty">
            <strong>尚未保存客户材料</strong>
            <span>上传后，原件、识别文字与字段来源会统一保留在这里。</span>
          </div>
        `}
      </div>
    </section>
  `;
}

function documentStatusClass(status) {
  if (status === "completed") return "completed";
  if (["uploading", "queued", "running"].includes(status)) return "running";
  if (status === "failed") return "high-risk";
  if (status === "uploaded") return "needs-review";
  return "pending";
}

function formatFileSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function replaceApplication(nextApplication) {
  if (!nextApplication?.id) return;
  const index = state.applications.findIndex((item) => item.id === nextApplication.id);
  if (index >= 0) state.applications[index] = nextApplication;
  else state.applications.unshift(nextApplication);
  state.activeId = nextApplication.id;
}

async function refreshOcrServiceStatus() {
  if (!state.apiAvailable) return state.ocrService;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/ocr/health`);
    if (!response.ok) throw new Error("扫描服务检查失败");
    state.ocrService = await response.json();
  } catch (error) {
    state.ocrService = { available: false };
  }
  const badge = document.querySelector("#ocrServiceBadge");
  if (badge) {
    badge.className = `badge ${state.ocrService.available ? "completed" : "pending"}`;
    badge.textContent = state.ocrService.available
      ? "服务可用"
      : (state.ocrService.installed === false ? "尚未安装" : "尚未启动");
  }
  const serviceMessage = document.querySelector("#ocrServiceMessage");
  if (serviceMessage) serviceMessage.textContent = state.ocrService.message || "版面解析 · 中英文识别 · DS-160 字段映射";
  const serviceButton = document.querySelector("#startOcrService");
  if (serviceButton) {
    serviceButton.hidden = Boolean(state.ocrService.available);
    serviceButton.disabled = Boolean(state.ocrService.starting);
    serviceButton.textContent = state.ocrService.starting
      ? "正在启动…"
      : (state.ocrService.remote ? "检查 MinerU 配置" : "启动扫描服务");
  }
  const application = getActiveApplication();
  const startButton = document.querySelector("#startReview");
  if (application && startButton) {
    const documents = visibleDocumentEntries(application).map(({ documentItem }) => documentItem);
    const hasUploadedFile = documents.some((item) => item.fileName);
    const hasActiveUpload = documents.some((item) => ["uploading", "queued", "running"].includes(item.scanStatus));
    startButton.disabled = !hasUploadedFile || hasActiveUpload || startButton.dataset.busy === "true";
    startButton.title = !hasUploadedFile
      ? "请先上传至少一份客户材料"
      : (hasActiveUpload ? "请等待当前材料上传完成" : (state.ocrService.available ? "" : "请先完成文档解析服务配置"));
  }
  return state.ocrService;
}

function updateTranslationServiceUI() {
  const status = document.querySelector("#translationServiceStatus");
  if (!status) return;
  const providerName = state.translationService?.deepSeek ? "DeepSeek" : "LibreTranslate";
  const ready = Boolean(
    state.translationService?.deepSeek || state.translationService?.libreTranslate
  );
  status.className = ready
    ? "known-information-service-ready"
    : "known-information-warning";
  status.textContent = ready
    ? `${providerName} 中译英已连接 · 中文原文会保留用于核对`
    : "DeepSeek 中译英尚未连接。规则仍会识别证件号、日期和固定选项，但中文地址、学校、公司及职责暂不能保证为正常英文语序。请联系管理员检查 DeepSeek 服务配置。";
}

async function refreshTranslationServiceStatus() {
  if (!state.apiAvailable) return state.translationService;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/health`, { cache: "no-store" });
    if (!response.ok) throw new Error("翻译服务检查失败");
    const health = await response.json();
    state.translationService = health.translation || null;
  } catch (error) {
    state.translationService = { libreTranslate: false };
  }
  updateTranslationServiceUI();
  return state.translationService;
}

async function startOcrServiceFromPage() {
  const serviceButton = document.querySelector("#startOcrService");
  const badge = document.querySelector("#ocrServiceBadge");
  const message = document.querySelector("#ocrServiceMessage");
  if (serviceButton) {
    serviceButton.disabled = true;
    serviceButton.textContent = "正在启动…";
  }
  if (badge) {
    badge.className = "badge running";
    badge.textContent = "启动中";
  }
  if (message) message.textContent = "正在检查并准备文档解析服务";

  const response = await DocFlowApi.request(`${API_BASE}/ocr/start`, { method: "POST" });
  const data = await response.json();
  if (response.status === 401) {
    await logout({ expired: true });
    throw new Error("登录状态已失效，请重新登录");
  }
  if (!response.ok) throw new Error(data.error || "扫描服务启动失败");
  state.ocrService = data;

  if (state.ocrService.remote && !state.ocrService.available) {
    throw new Error(state.ocrService.message || "MinerU 后端配置尚未完成");
  }

  if (!state.ocrService.available) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      await refreshOcrServiceStatus();
      if (state.ocrService?.available) break;
    }
  }

  await refreshOcrServiceStatus();
  if (!state.ocrService?.available) {
    throw new Error(state.ocrService?.message || "扫描服务仍在启动，请稍后重试");
  }
  return true;
}

async function enterFieldReview(application) {
  if (state.processingTimer) {
    clearInterval(state.processingTimer);
    state.processingTimer = null;
  }
  application.currentStep = Math.max(3, Number(application.currentStep || 0));
  const result = await saveApplication(application);
  if (result?.case) replaceApplication(result.case);
  route("fields", application.id);
}

function showDocumentsNotice(message, type = "error") {
  const notice = document.querySelector("#documentsNotice");
  if (!notice) return;
  notice.textContent = message;
  notice.className = `inline-notice visible ${type}`;
}

async function uploadDocumentFile(application, index, file, container) {
  const documentItem = application.documents[index];
  const previousDocument = { ...documentItem };
  const documentId = documentItem.id || `${application.id}-doc-${index}`;
  documentItem.id = documentId;
  documentItem.fileName = file.name;
  documentItem.fileSize = file.size;
  documentItem.scanStatus = "uploading";
  documentItem.scanMessage = "正在安全上传文件";
  renderDocuments(container);

  const formData = new FormData();
  formData.append("file", file, file.name);
  try {
    const response = await DocFlowApi.request(
      `${API_BASE}/cases/${encodeURIComponent(application.id)}/documents/${encodeURIComponent(documentId)}`,
      { method: "POST", body: formData }
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "文件上传失败");
    replaceApplication(data.case);
    renderDocuments(container);
    showDocumentsNotice(`${file.name} 已上传，可继续补充材料或开始扫描。`, "success");
  } catch (error) {
    Object.assign(documentItem, previousDocument);
    documentItem.scanStatus = previousDocument.fileName ? previousDocument.scanStatus : "failed";
    documentItem.scanMessage = error.message || "文件上传失败";
    renderDocuments(container);
    showDocumentsNotice(documentItem.scanMessage);
  }
}

async function deleteSelectedDocument() {
  const payload = state.modal?.payload;
  const button = document.querySelector("[data-confirm-document-delete]");
  if (!payload?.applicationId || !payload?.documentId || !button) return;
  button.disabled = true;
  button.textContent = "正在删除…";
  try {
    const response = await DocFlowApi.request(
      `${API_BASE}/cases/${encodeURIComponent(payload.applicationId)}/documents/${encodeURIComponent(payload.documentId)}`,
      { method: "DELETE" }
    );
    const data = await response.json();
    if (response.status === 401) {
      await logout({ expired: true });
      return;
    }
    if (!response.ok) throw new Error(data.error || "材料删除失败");
    replaceApplication(data.case);
    state.modal = null;
    render(state.currentView);
    showDocumentsNotice(`${payload.fileName || "材料"} 已删除，关联的识别结果也已清理。`, "success");
  } catch (error) {
    button.disabled = false;
    button.textContent = "确认删除";
    const errorElement = document.querySelector("#modalError");
    if (errorElement) {
      errorElement.textContent = error.message || "材料删除失败";
      errorElement.classList.add("visible");
    }
  }
}

async function openDocumentOcrPreview(application, documentItem) {
  const payload = {
    applicationId: application.id,
    documentId: documentItem.id,
    fileName: documentItem.fileName,
    loading: true
  };
  openModal("ocrPreview", payload);
  try {
    const response = await DocFlowApi.request(
      `${API_BASE}/cases/${encodeURIComponent(application.id)}/documents/${encodeURIComponent(documentItem.id)}/ocr`
    );
    const data = await response.json();
    if (response.status === 401) {
      await logout({ expired: true });
      return;
    }
    if (!response.ok) throw new Error(data.error || "无法读取识别结果");
    if (state.modal?.type !== "ocrPreview" || state.modal.payload.documentId !== documentItem.id) return;
    state.modal.payload = { ...payload, ...data, loading: false };
    render(state.currentView);
  } catch (error) {
    if (state.modal?.type !== "ocrPreview") return;
    state.modal.payload = { ...payload, loading: false, error: error.message || "无法读取识别结果" };
    render(state.currentView);
  }
}

async function startDocumentScan(application) {
  const button = document.querySelector("#startReview");
  if (!button) return;
  button.dataset.busy = "true";
  button.disabled = true;
  try {
    if (!state.ocrService?.available) {
      button.textContent = "正在启动扫描服务…";
      await startOcrServiceFromPage();
    }
    button.textContent = "正在提交扫描…";
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/scan`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "无法启动文档扫描");
    application.currentStep = 2;
    application.agentTimeline = buildAgentTimelineForVisa(visaByName(application.visaType).id);
    route("processing", application.id);
  } catch (error) {
    button.dataset.busy = "false";
    button.disabled = false;
    button.textContent = "开始扫描并生成初稿";
    showDocumentsNotice(error.message || "无法启动文档扫描");
  }
}

function renderProcessing(container) {
  const application = getActiveApplication();
  container.innerHTML = `
    ${renderAppHeader(application, "按 DS-160 模块整理资料", "系统正在读取真实材料并映射到 DS-160 字段。处理期间可以返回工作台，扫描会在后台继续。")}
    <section class="panel scan-progress-panel">
      <div class="scan-progress-heading">
        <div>
          <span class="page-kicker">真实文档处理</span>
          <h2 id="scanStatusTitle">准备扫描</h2>
        </div>
        <strong id="scanProgressValue">0%</strong>
      </div>
      <div class="progress-track"><div class="progress-fill" id="scanProgressBar" style="width:0%"></div></div>
      <div class="timeline" id="documentScanTimeline"></div>
    </section>
    <section class="panel" style="margin-top:18px">
      <div class="timeline" id="timeline">
        ${(application.agentTimeline || []).map(renderTimelineRow).join("")}
      </div>
    </section>
    <div class="inline-notice" id="scanNotice" role="status"></div>
    <div class="actions processing-actions" style="margin-top:18px">
      <button class="btn" id="viewCurrentFields">进入字段核查</button>
      <button class="btn secondary" id="backToDocuments">返回资料页</button>
    </div>
  `;
  document.querySelector("#viewCurrentFields")?.addEventListener("click", () => enterFieldReview(application));
  document.querySelector("#backToDocuments")?.addEventListener("click", () => route("documents", application.id));
  pollScanStatus(application);
}

function renderTimelineRow(agent) {
  return `
    <div class="timeline-row ${agent.status}">
      <span class="agent-dot"></span>
      <div>
        <strong>${escapeHtml(localizeAgent(agent.name))}</strong>
        <div class="small muted">${agent.output || "等待上一环节完成"}</div>
      </div>
      <span class="badge ${agent.status}">${statusLabel(agent.status)}</span>
    </div>
  `;
}

function renderDocumentScanRow(documentItem) {
  return `
    <div class="timeline-row ${documentItem.scanStatus}">
      <span class="agent-dot"></span>
      <div>
        <strong>${escapeHtml(localizeSlot(documentItem.slot))}</strong>
        <div class="small muted">${escapeHtml(documentItem.scanMessage || documentItem.fileName || "等待扫描")}</div>
      </div>
      <span class="badge ${documentStatusClass(documentItem.scanStatus)}">${statusLabel(documentItem.scanStatus)}</span>
    </div>
  `;
}

function pollScanStatus(application) {
  let polling = false;
  const poll = async () => {
    if (polling || state.currentView !== "processing") return;
    polling = true;
    try {
      const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/scan-status`);
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 404) {
          clearInterval(state.processingTimer);
          state.processingTimer = null;
          throw new Error("当前后端不包含扫描状态接口。请重新启动完整版本，或直接进入字段核查。");
        }
        throw new Error(data.error || "读取扫描状态失败");
      }
      document.querySelector("#scanProgressValue").textContent = `${data.progress}%`;
      document.querySelector("#scanProgressBar").style.width = `${data.progress}%`;
      document.querySelector("#scanStatusTitle").textContent = {
        running: "正在扫描与映射",
        completed: "扫描与字段映射完成",
        completed_with_errors: "部分材料已完成",
        interrupted: "扫描已中断",
        failed: "文档扫描未完成"
      }[data.status] || "等待扫描";
      document.querySelector("#documentScanTimeline").innerHTML = data.documents.map(renderDocumentScanRow).join("");

      if (["completed", "completed_with_errors"].includes(data.status) && data.case) {
        replaceApplication(data.case);
        clearInterval(state.processingTimer);
        state.processingTimer = null;
        setTimeout(() => route("fields", data.case.id), 450);
      } else if (["failed", "interrupted"].includes(data.status)) {
        clearInterval(state.processingTimer);
        state.processingTimer = null;
        const notice = document.querySelector("#scanNotice");
        notice.textContent = data.status === "interrupted"
          ? "扫描流程已中断。请返回资料页重新开始，已上传文件不会丢失。"
          : "所有材料均未成功解析。请确认扫描服务和文件格式后返回资料页重试。";
        notice.className = "inline-notice visible error";
      }
    } catch (error) {
      const notice = document.querySelector("#scanNotice");
      if (notice) {
        notice.textContent = error.message || "扫描状态读取失败";
        notice.className = "inline-notice visible error";
      }
    } finally {
      polling = false;
    }
  };
  poll();
  state.processingTimer = setInterval(poll, 1200);
}

function renderFieldValueControl(field) {
  const original = field.originalValue
    ? `<small class="field-original-value">中文原文：${escapeHtml(field.originalValue)}</small>`
    : "";
  if (field.id === "travel.arrivalDate") {
    return `
      <div class="field-value-stack">
      <button class="select-trigger compact" type="button" data-date-field="${field.id}" data-current-date="${escapeHtml(field.value)}">
        <span>
          <strong>${escapeHtml(field.value || "待补充")}</strong>
          <small>点击选择预计抵达日期</small>
        </span>
        ${iconCalendar()}
      </button>
      ${original}
      </div>
    `;
  }
  return `<div class="field-value-stack"><input data-field-value="${field.id}" value="${escapeHtml(field.value)}" placeholder="待扫描或人工补充">${original}</div>`;
}

function renderFields(container) {
  const application = getActiveApplication();
  const allFields = visibleFieldsForApplication(application);
  const reviewFields = allFields.filter((field) => fieldNeedsPriorityReview(field, application));
  const systemVerifiedFields = allFields.filter((field) => (
    !fieldNeedsPriorityReview(field, application) && isFieldSystemVerified(field, application)
  ));
  const readyCount = allFields.filter((field) => field.confirmed || isFieldSystemVerified(field, application)).length;
  container.innerHTML = `
    ${renderAppHeader(application, "关键字段复核", "这里只核查已经从材料中提取出的关键、低置信度或冲突内容。材料中没有的信息不会要求中介代填，下一步会自动整理成客户补充链接。")} 
    <section class="review-summary-strip">
      <div><strong>${readyCount}</strong><span>已确认或系统校验</span></div>
      <div><strong>${reviewFields.length}</strong><span>关键待复核</span></div>
      <div><strong>${systemVerifiedFields.length}</strong><span>无需逐项点击</span></div>
    </section>
    <section class="panel table-wrap priority-review-table">
      <div class="review-table-heading">
        <div>
          <span class="page-kicker">重点复核</span>
          <h2>只确认会影响初稿的内容</h2>
        </div>
        <span>${reviewFields.length} 项</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>DS-160 字段</th>
            <th>填写建议</th>
            <th>来源材料</th>
            <th>置信度</th>
            <th>风险</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${reviewFields.length ? reviewFields.map(renderFieldReviewRow).join("") : `
            <tr><td colspan="7"><div class="review-complete-state"><strong>没有需要逐项处理的已提取字段</strong><span>材料中缺少的信息会在下一步自动整理成客户补充表。</span></div></td></tr>
          `}
        </tbody>
      </table>
    </section>
    ${systemVerifiedFields.length ? `
      <details class="system-verified-panel">
        <summary>
          <span><strong>查看或修改系统已校验字段</strong><small>展开后可直接修改；保存后会记录为顾问人工编辑</small></span>
          <span>${systemVerifiedFields.length} 项</span>
        </summary>
        <div class="system-verified-list">
          ${systemVerifiedFields.map((field) => `
            <div>
              <span><strong>${escapeHtml(localizeField(field.label))}</strong><small>${escapeHtml(field.sourceDocument || "上传材料")} · ${Math.round(Number(field.confidence || 0) * 100)}%</small></span>
              <label class="system-field-editor">
                <span class="sr-only">修改 ${escapeHtml(localizeField(field.label))}</span>
                <input data-system-field-value="${field.id}" value="${escapeHtml(field.value)}">
              </label>
              <button class="icon-text-btn" type="button" data-save-system-field="${field.id}">保存修改</button>
            </div>
          `).join("")}
        </div>
      </details>
    ` : ""}
    <div class="actions" style="margin-top:18px">
      <button class="btn" id="continueQuestions">确认关键字段并继续</button>
      <button class="btn secondary" id="backToMaterials">返回客户材料</button>
    </div>
  `;

  document.querySelectorAll("[data-field-value]").forEach((input) => {
    input.addEventListener("change", () => {
      const field = application.extractedFields.find((item) => item.id === input.dataset.fieldValue);
      field.value = input.value;
      field.editedByUser = true;
      field.confirmed = true;
      field.autoVerified = false;
      saveApplication(application);
      renderFields(container);
    });
  });
  document.querySelectorAll("[data-date-field]").forEach((button) => {
    button.addEventListener("click", () => {
      state.draftDate = button.dataset.currentDate;
      openModal("date", { fieldId: button.dataset.dateField });
    });
  });
  document.querySelectorAll("[data-confirm-field]").forEach((button) => {
    button.addEventListener("click", () => {
      const field = application.extractedFields.find((item) => item.id === button.dataset.confirmField);
      if (!String(field?.value || "").trim()) return;
      field.confirmed = true;
      field.autoVerified = false;
      saveApplication(application);
      renderFields(container);
    });
  });
  document.querySelectorAll("[data-save-system-field]").forEach((button) => {
    button.addEventListener("click", () => {
      const field = application.extractedFields.find((item) => item.id === button.dataset.saveSystemField);
      if (!field) return;
      const input = document.querySelector(`[data-system-field-value="${CSS.escape(field.id)}"]`);
      if (!String(input?.value || "").trim()) return;
      field.value = input.value.trim();
      field.editedByUser = true;
      field.confirmed = true;
      field.autoVerified = false;
      field.requiresUserConfirmation = true;
      field.reviewReason = "顾问已修改系统识别结果";
      saveApplication(application);
      renderFields(container);
    });
  });
  document.querySelector("#backToMaterials").addEventListener("click", () => route("documents", application.id));
  document.querySelector("#continueQuestions").addEventListener("click", async () => {
    reviewFields.forEach((field) => {
      if (String(field.value || "").trim()) {
        field.confirmed = true;
        field.autoVerified = false;
      }
    });
    application.currentStep = 4;
    await saveApplication(application);
    route("questions");
  });
}

function renderFieldReviewRow(field) {
  return `
    <tr>
      <td data-label="DS-160 字段"><strong>${escapeHtml(localizeField(field.label))}</strong><div class="small muted">${escapeHtml(localizeSection(field.section))}</div></td>
      <td data-label="填写建议">${renderFieldValueControl(field)}</td>
      <td data-label="来源材料">
        <strong>${escapeHtml(field.sourceDocument || "待补充")}</strong>
        ${field.sourcePage ? `<div class="small muted">第 ${escapeHtml(field.sourcePage)} 页 · ${escapeHtml(field.extractionMethod || "规则映射")}</div>` : ""}
        ${field.evidence ? `
          <details class="field-evidence">
            <summary>查看识别证据</summary>
            <p>${escapeHtml(field.evidence)}</p>
          </details>
        ` : ""}
      </td>
      <td data-label="置信度">${renderConfidence(field.confidence)}</td>
      <td data-label="风险"><span class="badge ${field.riskLevel === "high" ? "high-risk" : field.riskLevel}">${riskLabel(field.riskLevel)}</span></td>
      <td data-label="状态"><span class="badge ${field.confirmed ? "confirmed" : "needs-review"}">${field.confirmed ? (field.editedByUser ? "已编辑" : "已确认") : "重点复核"}</span><div class="small muted review-reason">${escapeHtml(field.reviewReason || "关键字段或识别结果需要确认")}</div></td>
      <td data-label="操作"><button class="btn secondary" data-confirm-field="${field.id}" ${String(field.value || "").trim() ? "" : "disabled"}>${String(field.value || "").trim() ? "确认" : "待补充"}</button></td>
    </tr>
  `;
}

function fieldHasUnresolvedConflict(field, application) {
  return visibleValidationResultsForApplication(application).some((item) => (
    !item.resolved
    && item.type === "conflict"
    && String(item.id || "").includes(field.id)
  ));
}

function isFieldSystemVerified(field, application) {
  if (field.confirmed || field.editedByUser || !String(field.value || "").trim()) return false;
  if (field.autoVerified === false && field.reviewReason === "顾问选择人工复核") return false;
  if (CRITICAL_REVIEW_FIELD_IDS.has(field.id) || fieldHasUnresolvedConflict(field, application)) return false;
  if (field.autoVerified === true) return true;
  const threshold = { high: 0.94, medium: 0.86, low: 0.8 }[field.riskLevel] || 0.86;
  return Number(field.confidence || 0) >= threshold;
}

function fieldNeedsPriorityReview(field, application) {
  if (field.confirmed) return false;
  if (!String(field.value || "").trim()) return false;
  if (isFieldSystemVerified(field, application)) return false;
  if (CRITICAL_REVIEW_FIELD_IDS.has(field.id)) return true;
  if (fieldHasUnresolvedConflict(field, application)) return true;
  return true;
}

function renderQuestions(container) {
  if (state.showAllQuestions) {
    renderFullQuestions(container);
    return;
  }
  renderPriorityQuestions(container);
}

function intakeLinkStorageKey(applicationId) {
  return `docflow-intake-link:${applicationId}`;
}

function storedIntakeUrl(applicationId) {
  return localStorage.getItem(intakeLinkStorageKey(applicationId)) || "";
}

function renderClientIntakePanel(application, pendingItems) {
  const meta = application.intakeMeta || { status: "not_created" };
  const storedUrl = storedIntakeUrl(application.id);
  const isLocalRuntime = ["127.0.0.1", "localhost", "::1", ""].includes(window.location.hostname);
  const isSubmitted = meta.status === "submitted";
  const isPending = meta.status === "pending";
  const pendingCount = pendingItems.length;
  const pendingPreview = pendingItems.slice(0, 6);
  const hasPendingItems = pendingCount > 0;
  const statusLabelText = isSubmitted && hasPendingItems
    ? "仍有资料待补充"
    : isSubmitted
      ? "客户已提交"
      : isPending
        ? "等待客户填写"
        : "尚未生成";
  const statusClass = isSubmitted && !hasPendingItems
    ? "completed"
    : isPending
      ? "running"
      : hasPendingItems
        ? "needs-review"
        : "pending";
  return `
    <section class="client-intake-panel">
      <div class="client-intake-copy">
        <span class="page-kicker">客户补充链接</span>
        <h2>只向客户询问材料中没有的信息</h2>
        <p>护照、行程、I-20 / DS-2019 等材料已经提供的内容不会重复提问。剩余问题会转换成通俗中文表单，客户提交后直接写回当前档案。</p>
        <div class="client-intake-stats">
          <span><strong>${pendingCount}</strong> 项当前待补充</span>
          <span class="badge ${statusClass}">${statusLabelText}</span>
        </div>
        ${hasPendingItems ? `
          <div class="client-intake-pending-list" aria-label="当前待补充字段">
            ${pendingPreview.map((item) => `<span>${escapeHtml(item.label)}</span>`).join("")}
            ${pendingCount > pendingPreview.length ? `<span class="client-intake-more">另有 ${pendingCount - pendingPreview.length} 项将在客户表单中显示</span>` : ""}
          </div>
        ` : '<div class="client-intake-complete-copy">当前客户补充项已经收齐。</div>'}
      </div>
      <div class="client-intake-actions">
        ${isSubmitted && hasPendingItems ? `
          <div class="intake-submitted-state needs-more">
            <span>
              <strong>上一份问卷已提交，但档案仍缺少 ${pendingCount} 项</strong>
              <small>请生成一份新的补充链接。新问卷只会询问上方列出的缺失内容。</small>
            </span>
          </div>
          <button class="btn" type="button" id="regenerateIntakeLink">生成新的补充链接</button>
          <button class="icon-text-btn" type="button" id="refreshIntakeStatus">刷新档案</button>
        ` : isSubmitted ? `
          <div class="intake-submitted-state">
            ${iconCheck()}
            <span>
              <strong>补充资料已回流</strong>
              <small>${meta.submittedAt ? `提交于 ${formatDate(meta.submittedAt)}` : "请刷新查看最新内容"}</small>
              ${meta.respondentName ? `<small class="${meta.identityMatch === false ? "identity-warning" : ""}">填写姓名：${escapeHtml(meta.respondentName)}${meta.identityMatch === false ? " · 与档案姓名不一致，请核对是否发错链接" : " · 已与档案核对"}</small>` : ""}
            </span>
          </div>
          <button class="btn secondary" type="button" id="refreshIntakeStatus">刷新档案</button>
          <button class="icon-text-btn" type="button" id="regenerateIntakeLink">重新生成补充链接</button>
        ` : isPending && storedUrl ? `
          <label class="intake-link-field" for="clientIntakeUrl">客户专属链接</label>
          <div class="intake-link-copy-row">
            <input id="clientIntakeUrl" readonly value="${escapeHtml(storedUrl)}" aria-label="客户专属补充链接">
            <button class="btn" type="button" id="copyIntakeLink">复制链接</button>
          </div>
          <small>${isLocalRuntime ? "当前是本地测试链接，只能在这台电脑上打开；部署到 HTTPS 公网地址后才能发给异地客户。" : "链接 30 天内有效。重新生成后，旧链接立即失效。"}</small>
          <button class="icon-text-btn" type="button" id="regenerateIntakeLink">重新生成</button>
        ` : `
          ${isPending ? '<p class="component-note">出于安全原因，服务器只保存链接令牌的哈希。当前浏览器没有保留明文链接，请重新生成。</p>' : ""}
          <button class="btn" type="button" id="createIntakeLink">生成客户补充链接</button>
          <small>${isLocalRuntime ? "本地版用于流程测试。正式发送前需要将网站与数据库部署到 HTTPS 公网地址。" : "无需客户注册账号；链接只可访问这份补充表，不会展示原始材料或机构工作台。"}</small>
        `}
        <div class="inline-notice" id="intakeLinkNotice" role="status"></div>
      </div>
    </section>
  `;
}

function buildClientIntakeUrl(token) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = `intake=${encodeURIComponent(token)}`;
  return url.toString();
}

function showIntakeLinkNotice(message, type = "success") {
  const notice = document.querySelector("#intakeLinkNotice");
  if (!notice) return;
  notice.textContent = message;
  notice.className = `inline-notice visible ${type}`;
}

async function generateClientIntakeLink(application, container) {
  const button = document.querySelector("#createIntakeLink") || document.querySelector("#regenerateIntakeLink");
  if (button) {
    button.disabled = true;
    button.textContent = "正在生成…";
  }
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/intake-link`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "补充链接生成失败");
    const url = buildClientIntakeUrl(data.token);
    localStorage.setItem(intakeLinkStorageKey(application.id), url);
    application.intakeMeta = {
      status: data.status,
      expiresAt: data.expiresAt,
      createdAt: new Date().toISOString()
    };
    renderQuestions(container);
    showIntakeLinkNotice("客户补充链接已生成，可以直接复制发送。", "success");
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "重新生成";
    }
    showIntakeLinkNotice(error.message || "补充链接生成失败", "error");
  }
}

async function copyClientIntakeLink(application) {
  const url = storedIntakeUrl(application.id);
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
  } catch (error) {
    const input = document.querySelector("#clientIntakeUrl");
    input?.select();
    document.execCommand("copy");
  }
  showIntakeLinkNotice("链接已复制，可以发送给客户填写。", "success");
}

function wireClientIntakePanel(application, container) {
  document.querySelector("#createIntakeLink")?.addEventListener("click", () => generateClientIntakeLink(application, container));
  document.querySelector("#regenerateIntakeLink")?.addEventListener("click", () => generateClientIntakeLink(application, container));
  document.querySelector("#copyIntakeLink")?.addEventListener("click", () => copyClientIntakeLink(application));
  document.querySelector("#refreshIntakeStatus")?.addEventListener("click", async () => {
    await loadApplicationsForCurrentOrganization();
    renderQuestions(container);
  });
}

function renderPriorityQuestions(container) {
  const application = getActiveApplication();
  const questionnaire = (application.branchQuestionnaire || []).filter((item) => (
    item.visible !== false && !BROWSER_WORKFLOW_RUNTIME_ONLY_QUESTION_IDS.has(item.id)
  ));
  if (!questionnaire.length) {
    renderLegacyQuestions(container, application);
    return;
  }
  const autoDetermined = questionnaire.filter((item) => item.autoDetermined && ["yes", "no"].includes(item.answer));
  const important = questionnaire.filter((item) => (
    (item.sensitive && ["yes", "no"].includes(item.answer))
    || item.status === "客户已补充"
    || item.status === "信息待补充"
    || String(item.source || "").includes("冲突")
  ));
  const sensitivePending = questionnaire.filter((item) => item.sensitive && item.status !== "已核查");
  const customerPending = questionnaire.filter((item) => (
    !item.sensitive && ["待客户确认", "信息待补充"].includes(item.status)
  ));
  const clientIntakePending = browserWorkflowPreflightIssues(application);

  container.innerHTML = `
    ${renderAppHeader(application, "客户补充与重点复核", "材料中没有的信息由系统整理成客户补充链接。客户提交后自动回流到本档案，顾问只核查关键字段、冲突和敏感问题。")} 
    <section class="questionnaire-summary priority-question-summary">
      <div><strong>${autoDetermined.length}</strong><span>材料自动判断</span></div>
      <div><strong>${important.length}</strong><span>重要项待复核</span></div>
      <div><strong>${sensitivePending.length}</strong><span>背景题待最终确认</span></div>
    </section>
    ${renderClientIntakePanel(application, clientIntakePending)}
    <section class="priority-question-workspace">
      <header class="priority-question-heading">
        <div>
          <span class="page-kicker">扫描判断结果</span>
          <h2>优先处理重要内容</h2>
          <p>系统不会把材料未提及的信息擅自填为 No；客户补充内容会在这里集中呈现。</p>
        </div>
        <button class="btn secondary" type="button" id="showAllQuestions">查看完整问题清单</button>
      </header>
      <div class="priority-question-list">
        ${important.length ? important.map((question) => `
          <div class="priority-question-row ${question.sensitive ? "sensitive" : ""}">
            <span>
              <strong>${escapeHtml(question.label)}</strong>
              <small>${escapeHtml(question.section)} · ${escapeHtml(question.source || "待客户确认")}</small>
            </span>
            <span class="priority-question-answer">${escapeHtml(branchAnswerDisplay(question))}</span>
            <span class="badge ${branchStatusClass(question.status)}">${escapeHtml(question.status)}</span>
            <button class="icon-text-btn" type="button" data-open-priority-question="${question.id}">核对</button>
          </div>
        `).join("") : `
          <div class="review-complete-state"><strong>目前没有高优先级分支</strong><span>可以继续风险复核；完整问题清单仍会保留在客户档案中。</span></div>
        `}
      </div>
      <div class="background-question-summary">
        <div>
          <strong>背景与历史问题</strong>
          <span>${sensitivePending.length} 项等待顾问最终确认，${clientIntakePending.length} 项资料需要通过客户补充链接收集。</span>
        </div>
        <button class="icon-text-btn" type="button" id="openBackgroundQuestions">进入完整清单</button>
      </div>
    </section>
    <div class="actions" style="margin-top:18px">
      <button class="btn" id="continuePriorityValidation">进入风险复核</button>
      <button class="btn secondary" id="backToFields">返回关键字段</button>
    </div>
  `;

  const showFull = (questionId = "") => {
    const question = questionnaire.find((item) => item.id === questionId);
    state.activeQuestionSection = question?.section || "健康与背景";
    state.showAllQuestions = true;
    renderQuestions(container);
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  };
  document.querySelector("#showAllQuestions").addEventListener("click", () => showFull());
  document.querySelector("#openBackgroundQuestions").addEventListener("click", () => showFull());
  document.querySelectorAll("[data-open-priority-question]").forEach((button) => {
    button.addEventListener("click", () => showFull(button.dataset.openPriorityQuestion));
  });
  document.querySelector("#backToFields").addEventListener("click", () => route("fields", application.id));
  document.querySelector("#continuePriorityValidation").addEventListener("click", async () => {
    application.currentStep = 5;
    await persistBranchQuestionnaire(application);
    route("validation");
  });
  wireClientIntakePanel(application, container);
}

function renderFullQuestions(container) {
  const application = getActiveApplication();
  const questionnaire = (application.branchQuestionnaire || []).filter((item) => (
    item.visible !== false && !BROWSER_WORKFLOW_RUNTIME_ONLY_QUESTION_IDS.has(item.id)
  ));
  if (!questionnaire.length) {
    renderLegacyQuestions(container, application);
    return;
  }

  const sections = [...new Set(questionnaire.map((item) => item.section))];
  if (!sections.includes(state.activeQuestionSection)) {
    state.activeQuestionSection = sections.find((section) => questionnaire.some((item) => (
      item.section === section && !["已回答", "已核查"].includes(item.status)
    ))) || sections[0];
  }
  const activeQuestions = questionnaire.filter((item) => item.section === state.activeQuestionSection);
  const completedCount = questionnaire.filter((item) => ["已回答", "已核查"].includes(item.status)).length;
  const sensitiveCount = questionnaire.filter((item) => item.sensitive && item.status !== "已核查").length;
  const clientIntakePending = browserWorkflowPreflightIssues(application);
  const clientIntakePreview = clientIntakePending.slice(0, 6);

  container.innerHTML = `
    ${renderAppHeader(application, "DS-160 条件问答", "按 DS-160 分支逐项确认。材料可辅助预填客观字段；历史、健康、犯罪、移民与安全问题不会由系统推断或默认选择 No。")}
    <div class="branch-rule-notice">
      <div><strong>完整问题清单</strong><span>分支会随签证类别和已选答案动态变化；最终以客户当次 CEAC 页面为准。</span></div>
      <button class="icon-text-btn" type="button" id="showPriorityQuestions">返回重点视图</button>
    </div>
    <section class="questionnaire-summary">
      <div><strong>${completedCount} / ${questionnaire.length}</strong><span>已回答或已核查</span></div>
      <div><strong>${sensitiveCount}</strong><span>敏感题待顾问核查</span></div>
      <div><strong>${clientIntakePending.length}</strong><span>资料字段待客户补充</span></div>
    </section>
    ${clientIntakePending.length ? `
      <section class="questionnaire-intake-alert" role="status">
        <div>
          <strong>还有 ${clientIntakePending.length} 项资料字段不在条件问答列表中</strong>
          <span>${escapeHtml(clientIntakePreview.map((item) => item.label).join("；"))}${clientIntakePending.length > clientIntakePreview.length ? `；另有 ${clientIntakePending.length - clientIntakePreview.length} 项会出现在客户补充表中。` : ""}</span>
        </div>
        <button class="btn secondary" type="button" id="showPriorityForMissing">返回客户补充链接</button>
      </section>
    ` : ""}
    <section class="branch-workspace">
      <nav class="branch-section-nav" aria-label="DS-160 问题模块">
        ${sections.map((section) => {
          const items = questionnaire.filter((item) => item.section === section);
          const pending = items.filter((item) => !["已回答", "已核查"].includes(item.status)).length;
          return `
            <button class="branch-section-button ${section === state.activeQuestionSection ? "active" : ""}" type="button" data-question-section="${escapeHtml(section)}">
              <span>${escapeHtml(section)}</span>
              <small>${pending ? `${pending} 项待处理` : "已完成"}</small>
            </button>
          `;
        }).join("")}
      </nav>
      <form id="branchQuestionsForm" class="branch-question-content">
        <header class="branch-section-header">
          <div>
            <span class="page-kicker">条件问答</span>
            <h2>${escapeHtml(state.activeQuestionSection)}</h2>
          </div>
          <span>${activeQuestions.length} 项</span>
        </header>
        <div class="branch-question-list">
          ${activeQuestions.map(renderBranchQuestion).join("")}
        </div>
        <div class="inline-notice" id="branchNotice" role="status"></div>
        <footer class="branch-form-actions">
          <button class="btn secondary" type="button" id="saveQuestionSection">保存当前模块</button>
          <button class="btn secondary" type="button" id="nextQuestionSection">${state.activeQuestionSection === sections[sections.length - 1] ? "复核全部问题" : "下一模块"}</button>
          <button class="btn" type="submit">进入风险复核</button>
        </footer>
      </form>
    </section>
  `;

  document.querySelector("#showPriorityQuestions").addEventListener("click", async () => {
    captureBranchQuestionForm(application);
    await persistBranchQuestionnaire(application);
    state.showAllQuestions = false;
    renderQuestions(container);
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  });
  document.querySelector("#showPriorityForMissing")?.addEventListener("click", async () => {
    captureBranchQuestionForm(application);
    await persistBranchQuestionnaire(application);
    state.showAllQuestions = false;
    renderQuestions(container);
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  });

  document.querySelectorAll("[data-question-section]").forEach((button) => {
    button.addEventListener("click", async () => {
      captureBranchQuestionForm(application);
      await persistBranchQuestionnaire(application);
      state.activeQuestionSection = button.dataset.questionSection;
      renderQuestions(container);
    });
  });

  document.querySelectorAll("[data-branch-answer]").forEach((button) => {
    button.addEventListener("click", async () => {
      captureBranchQuestionForm(application);
      const question = application.branchQuestionnaire.find((item) => item.id === button.dataset.branchQuestion);
      if (!question) return;
      question.answer = button.dataset.branchAnswer;
      question.confirmedByUser = false;
      question.source = "客户确认";
      question.updatedAt = new Date().toISOString();
      await persistBranchQuestionnaire(application);
      renderQuestions(container);
    });
  });

  document.querySelectorAll("[data-branch-select]").forEach((select) => {
    select.addEventListener("change", async () => {
      captureBranchQuestionForm(application);
      const question = application.branchQuestionnaire.find((item) => item.id === select.dataset.branchSelect);
      if (!question) return;
      question.answer = select.value;
      question.confirmedByUser = false;
      question.source = "客户确认";
      question.updatedAt = new Date().toISOString();
      await persistBranchQuestionnaire(application);
      renderQuestions(container);
    });
  });

  document.querySelectorAll('select[data-branch-field="schoolLevel"], select[data-branch-field="level"]').forEach((select) => {
    select.addEventListener("change", async () => {
      captureBranchQuestionForm(application);
      await persistBranchQuestionnaire(application);
      renderQuestions(container);
    });
  });

  document.querySelectorAll("[data-add-branch-record]").forEach((button) => {
    button.addEventListener("click", () => {
      captureBranchQuestionForm(application);
      const question = application.branchQuestionnaire.find((item) => item.id === button.dataset.addBranchRecord);
      if (!question) return;
      question.records = question.records || [];
      question.records.push(Object.fromEntries((question.recordFields || []).map((field) => [field.id, ""])));
      question.source = "客户确认";
      renderQuestions(container);
    });
  });

  document.querySelectorAll("[data-remove-branch-record]").forEach((button) => {
    button.addEventListener("click", () => {
      captureBranchQuestionForm(application);
      const question = application.branchQuestionnaire.find((item) => item.id === button.dataset.branchQuestion);
      if (!question) return;
      question.records.splice(Number(button.dataset.removeBranchRecord), 1);
      question.source = "客户确认";
      renderQuestions(container);
    });
  });

  document.querySelectorAll("[data-confirm-branch-question]").forEach((button) => {
    button.addEventListener("click", async () => {
      captureBranchQuestionForm(application);
      const question = application.branchQuestionnaire.find((item) => item.id === button.dataset.confirmBranchQuestion);
      if (!question || !["yes", "no"].includes(question.answer)) return;
      question.confirmedByUser = true;
      question.updatedAt = new Date().toISOString();
      await persistBranchQuestionnaire(application);
      renderQuestions(container);
    });
  });

  document.querySelector("#saveQuestionSection").addEventListener("click", async () => {
    captureBranchQuestionForm(application);
    const saved = await persistBranchQuestionnaire(application);
    const notice = document.querySelector("#branchNotice");
    if (notice) {
      notice.textContent = saved ? "当前模块已保存，分支状态和缺失项已重新计算。" : "保存失败，请确认本地服务器仍在运行。";
      notice.className = `inline-notice visible ${saved ? "success" : "error"}`;
    }
  });

  document.querySelector("#nextQuestionSection").addEventListener("click", async () => {
    captureBranchQuestionForm(application);
    const currentIndex = sections.indexOf(state.activeQuestionSection);
    if (currentIndex < sections.length - 1) {
      await persistBranchQuestionnaire(application);
      state.activeQuestionSection = sections[currentIndex + 1];
      renderQuestions(container);
    } else {
      application.currentStep = 5;
      await persistBranchQuestionnaire(application);
      route("validation");
    }
  });

  document.querySelector("#branchQuestionsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    captureBranchQuestionForm(application);
    application.currentStep = 5;
    await persistBranchQuestionnaire(application);
    route("validation");
  });
}

function renderLegacyQuestions(container, application) {
  container.innerHTML = `
    ${renderAppHeader(application, "待确认项与客户补充", "该模块仍有信息待客户补充，建议先标记为待确认，不要自动填写。")}
    <form id="questionsForm" class="panel grid">
      ${visibleQuestionsForApplication(application).map((question) => `
        <div class="form-row">
          <label for="${question.id}">${escapeHtml(localizeQuestion(question.label))}</label>
          <textarea id="${question.id}" data-question="${question.id}" placeholder="请输入客户已确认的信息，或记录需要客户补充的说明">${escapeHtml(question.answer)}</textarea>
        </div>
      `).join("")}
      <div class="actions"><button class="btn" type="submit">进入风险复核</button></div>
    </form>
  `;
  document.querySelector("#questionsForm").addEventListener("submit", (event) => {
    event.preventDefault();
    document.querySelectorAll("[data-question]").forEach((textarea) => {
      const question = application.missingQuestions.find((item) => item.id === textarea.dataset.question);
      question.answer = textarea.value.trim();
    });
    application.currentStep = 5;
    saveApplication(application);
    route("validation");
  });
}

function renderBranchQuestion(question) {
  const activeDetails = branchActiveDetails(question);
  const showRecords = question.answerType === "records" || (
    (question.recordFields || []).length && (question.triggerValues || []).includes(question.answer)
  );
  return `
    <article class="branch-question ${question.sensitive ? "sensitive" : ""}">
      <header class="branch-question-header">
        <div>
          <div class="branch-question-title-row">
            <h3>${escapeHtml(question.label)}</h3>
            ${question.sensitive ? '<span class="badge high-risk">必须人工确认</span>' : ""}
          </div>
          ${question.englishLabel ? `<p lang="en">${escapeHtml(question.englishLabel)}</p>` : ""}
        </div>
        <span class="badge ${branchStatusClass(question.status)}">${escapeHtml(question.status)}</span>
      </header>
      ${question.guidance ? `<p class="branch-guidance">${escapeHtml(question.guidance)}</p>` : ""}
      ${question.autoDetermined ? `
        <div class="question-auto-evidence">
          <span>材料自动判断 · ${Math.round(Number(question.answerConfidence || 0) * 100)}%</span>
          <strong>${escapeHtml(question.source || "上传材料")}</strong>
          ${question.answerEvidence ? `<small>${escapeHtml(question.answerEvidence)}</small>` : ""}
        </div>
      ` : ""}
      ${question.clientResponse ? `
        <div class="client-response-note">
          <span>客户通过补充链接提交</span>
          <p>${escapeHtml(question.clientResponse)}</p>
        </div>
      ` : ""}
      ${renderBranchAnswerControl(question)}
      ${activeDetails.length ? `
        <div class="branch-detail-grid">
          ${activeDetails.map((field) => renderBranchDetailField(question, field)).join("")}
        </div>
      ` : ""}
      ${showRecords ? renderBranchRecords(question) : ""}
      ${question.sensitive ? `
        <div class="sensitive-confirm-row">
          <span>系统只提取材料中的明确答案，不会因材料未提及而默认选择 No。</span>
          <button class="btn secondary" type="button" data-confirm-branch-question="${question.id}" ${!["yes", "no"].includes(question.answer) ? "disabled" : ""}>
            ${question.confirmedByUser ? "顾问已核查" : "标记顾问已核查"}
          </button>
        </div>
      ` : ""}
      ${(question.evidenceSources || []).length ? `
        <details class="question-evidence">
          <summary>查看建议核对资料</summary>
          <div>${question.evidenceSources.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
        </details>
      ` : ""}
    </article>
  `;
}

function renderBranchAnswerControl(question) {
  if (question.answerType === "yes_no") {
    return `
      <div class="answer-segmented" role="group" aria-label="${escapeHtml(question.label)}">
        ${(question.choices || []).map((choice) => `
          <button class="${question.answer === choice.value ? "selected" : ""}" type="button" data-branch-question="${question.id}" data-branch-answer="${choice.value}" aria-pressed="${question.answer === choice.value}">
            ${escapeHtml(choice.label)}
          </button>
        `).join("")}
      </div>
    `;
  }
  if (question.answerType === "select") {
    return `
      <div class="form-row branch-select-row">
        <label for="branch-${question.id}">选择当前答案</label>
        <select id="branch-${question.id}" data-branch-select="${question.id}">
          <option value="">请选择</option>
          ${(question.choices || []).map((choice) => `<option value="${choice.value}" ${question.answer === choice.value ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}
        </select>
      </div>
    `;
  }
  return "";
}

function branchActiveDetails(question) {
  if (question.answerType === "details") {
    return (question.detailFields || []).filter((field) => (
      conditionalQuestionFieldVisible(field, question.details || {})
    ));
  }
  if ((question.triggerValues || []).length && !(question.triggerValues || []).includes(question.answer)) return [];
  return (question.detailFields || []).filter((field) => (
    (!(field.when || []).length || field.when.includes(question.answer))
    && conditionalQuestionFieldVisible(field, question.details || {})
  ));
}

function conditionalQuestionFieldVisible(field, values) {
  const condition = field.hideWhen || {};
  const sourceField = condition.field;
  const hiddenValues = (condition.values || []).map(String);
  if (!sourceField || !hiddenValues.length) return true;
  return !hiddenValues.includes(String(values?.[sourceField] || ""));
}

function branchActiveRecordFields(question, record) {
  return (question.recordFields || []).filter((field) => (
    conditionalQuestionFieldVisible(field, record || {})
  ));
}

function renderBranchDetailField(question, field) {
  const value = question.details?.[field.id] || "";
  const placeholder = field.placeholder || (field.type === "date" ? "YYYY-MM-DD" : "请输入客户已确认的信息");
  return `
    <div class="form-row ${field.type === "textarea" ? "full" : ""}">
      <label>${escapeHtml(field.label)}${field.required ? " *" : ""}</label>
      ${(field.choices || []).length
        ? `<select data-branch-detail data-branch-question="${question.id}" data-branch-field="${field.id}"><option value="">请选择</option>${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice.value)}" ${choice.value === value ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}</select>`
        : field.type === "textarea"
        ? `<textarea data-branch-detail data-branch-question="${question.id}" data-branch-field="${field.id}" placeholder="${escapeHtml(placeholder)}">${escapeHtml(value)}</textarea>`
        : `<input type="${field.type === "email" ? "email" : "text"}" ${field.type === "date" ? 'inputmode="numeric"' : ""} data-branch-detail data-branch-question="${question.id}" data-branch-field="${field.id}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">`
      }
    </div>
  `;
}

function renderBranchRecords(question) {
  const records = question.records || [];
  return `
    <div class="branch-records">
      <div class="branch-record-heading">
        <strong>${escapeHtml(question.recordLabel || "记录")}</strong>
        <button class="icon-text-btn" type="button" data-add-branch-record="${question.id}">+ 添加一项</button>
      </div>
      ${records.length ? records.map((record, recordIndex) => `
        <article class="branch-record">
          <header>
            <strong>${escapeHtml(question.recordLabel || "记录")} ${recordIndex + 1}</strong>
            <button class="icon-btn" type="button" data-branch-question="${question.id}" data-remove-branch-record="${recordIndex}" aria-label="删除本条记录">${iconClose()}</button>
          </header>
          <div class="branch-detail-grid">
            ${branchActiveRecordFields(question, record).map((field) => `
              <div class="form-row ${field.type === "textarea" ? "full" : ""}">
                <label>${escapeHtml(field.label)}${field.required ? " *" : ""}</label>
                ${(field.choices || []).length
                  ? `<select data-branch-record data-branch-question="${question.id}" data-record-index="${recordIndex}" data-branch-field="${field.id}"><option value="">请选择</option>${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice.value)}" ${choice.value === record[field.id] ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}</select>`
                  : field.type === "textarea"
                  ? `<textarea data-branch-record data-branch-question="${question.id}" data-record-index="${recordIndex}" data-branch-field="${field.id}" placeholder="请输入客户已确认的信息">${escapeHtml(record[field.id] || "")}</textarea>`
                  : `<input type="${field.type === "email" ? "email" : "text"}" ${field.type === "date" ? 'inputmode="numeric" placeholder="YYYY-MM-DD"' : 'placeholder="请输入"'} data-branch-record data-branch-question="${question.id}" data-record-index="${recordIndex}" data-branch-field="${field.id}" value="${escapeHtml(record[field.id] || "")}">`
                }
              </div>
            `).join("")}
          </div>
        </article>
      `).join("") : '<p class="branch-empty-records">尚未添加记录。选择适用答案后，请按客户真实情况逐项添加。</p>'}
    </div>
  `;
}

function captureBranchQuestionForm(application) {
  document.querySelectorAll("[data-branch-detail]").forEach((input) => {
    const question = application.branchQuestionnaire.find((item) => item.id === input.dataset.branchQuestion);
    if (!question) return;
    question.details = question.details || {};
    const nextValue = input.value.trim();
    const previousValue = String(question.details[input.dataset.branchField] || "").trim();
    if (nextValue === previousValue) return;
    question.details[input.dataset.branchField] = nextValue;
    question.confirmedByUser = false;
    question.source = "客户确认";
    question.updatedAt = new Date().toISOString();
  });
  document.querySelectorAll("[data-branch-record]").forEach((input) => {
    const question = application.branchQuestionnaire.find((item) => item.id === input.dataset.branchQuestion);
    const index = Number(input.dataset.recordIndex);
    if (!question?.records?.[index]) return;
    const nextValue = input.value.trim();
    const previousValue = String(question.records[index][input.dataset.branchField] || "").trim();
    if (nextValue === previousValue) return;
    question.records[index][input.dataset.branchField] = nextValue;
    question.confirmedByUser = false;
    question.source = "客户确认";
    question.updatedAt = new Date().toISOString();
  });
  (application.branchQuestionnaire || []).forEach((question) => {
    if (question.id === "work.primary_occupation" && question.details?.schoolLevel === "secondary") {
      delete question.details.courseOfStudy;
    }
    if (question.id === "work.education_secondary_or_above") {
      (question.records || []).forEach((record) => {
        if (record.level === "secondary") delete record.course;
      });
    }
  });
}

async function persistBranchQuestionnaire(application) {
  const result = await saveApplication(application);
  if (result?.case) {
    replaceApplication(result.case);
    return true;
  }
  return false;
}

function branchStatusClass(status) {
  if (status === "已核查") return "confirmed";
  if (["已回答", "客户已补充"].includes(status)) return "completed";
  if (status === "需顾问判断") return "high-risk";
  if (status === "信息待补充") return "medium";
  return "needs-review";
}

function priorityValidationResults(application) {
  return visibleValidationResultsForApplication(application).filter((item) => {
    if (item.resolved) return false;
    if (String(item.id || "").startsWith("ocr.missing.") && application.intakeMeta?.status !== "submitted") {
      return false;
    }
    if (String(item.id || "").startsWith("ocr.low.")) {
      const fieldId = String(item.id).replace("ocr.low.", "");
      const field = visibleFieldsForApplication(application).find((candidate) => candidate.id === fieldId);
      return field ? fieldNeedsPriorityReview(field, application) : false;
    }
    if (item.severity === "low" && item.type !== "conflict") return false;
    return true;
  });
}

function renderValidation(container) {
  const application = getActiveApplication();
  const allResults = visibleValidationResultsForApplication(application);
  const priorityResults = priorityValidationResults(application);
  const grouped = groupBy(priorityResults, "category");
  container.innerHTML = `
    ${renderAppHeader(application, "重点风险复核", "系统已隐藏低影响和已处理项目，只保留材料冲突、关键缺失、明确的 Yes，以及需要顾问最终确认的背景问题。")} 
    <div class="validation-focus-bar"><strong>${priorityResults.length} 项需要关注</strong><span>${Math.max(0, allResults.length - priorityResults.length)} 项已处理或无需阻塞当前流程</span></div>
    <div class="validation-edit-bar">
      <span>发现识别值有误时，可返回字段核查直接修改，修改会自动保留。</span>
      <button class="btn secondary" type="button" id="editReviewedFields">修改识别字段</button>
    </div>
    <section class="grid two">
      ${priorityResults.length ? Object.entries(grouped).map(([category, items]) => `
        <div class="panel validation-group">
          <h2>${escapeHtml(localizeCategory(category))}</h2>
          ${items.map((item) => `
            <div class="validation-item">
              <div class="actions" style="justify-content:space-between">
                <span class="badge ${item.severity}">${riskLabel(item.severity)}</span>
                <span class="badge ${item.resolved ? "resolved" : "unresolved"}">${item.resolved ? "已解决" : "需顾问判断"}</span>
              </div>
              <p>${escapeHtml(localizeValidationMessage(item.message))}</p>
              ${item.requiresUserResolution ? `<button class="btn secondary" data-resolve="${item.id}">${String(item.id).startsWith("branch.") ? "返回条件问答" : "标记已核查"}</button>` : ""}
            </div>
          `).join("")}
        </div>
      `).join("") : `<div class="review-complete-state validation-complete"><strong>没有阻塞初稿的重点风险</strong><span>可以进入 DS-160 初稿预览；完整处理记录会保留在核查报告中。</span></div>`}
    </section>
    <div class="actions" style="margin-top:18px">
      <button class="btn" id="continuePreview">查看 DS-160 初稿</button>
    </div>
  `;

  document.querySelectorAll("[data-resolve]").forEach((button) => {
    button.addEventListener("click", () => {
      const result = application.validationResults.find((item) => item.id === button.dataset.resolve);
      if (String(result?.id || "").startsWith("branch.")) {
        const questionId = String(result.id).replace("branch.yes.", "").replace("branch.missing.", "");
        const question = (application.branchQuestionnaire || []).find((item) => item.id === questionId);
        state.activeQuestionSection = question?.section || "健康与背景";
        route("questions");
        return;
      }
      result.resolved = true;
      saveApplication(application);
      renderValidation(container);
    });
  });
  document.querySelector("#editReviewedFields")?.addEventListener("click", () => {
    route("fields", application.id);
  });
  document.querySelector("#continuePreview").addEventListener("click", () => {
    application.currentStep = 6;
    saveApplication(application);
    route("preview");
  });
}

function renderPreview(container) {
  const application = getActiveApplication();
  const sections = visibleSectionsForApplication(application);
  const screenAgentReady = Boolean(state.screenAgentRuntime?.available);
  container.innerHTML = `
    ${renderAppHeader(application, "DS-160 初稿预览", "按 DS-160 模块展示可复核的填写初稿。敏感背景问题仅显示提醒，不自动代填。")}
    <div class="safety-box" style="margin-bottom:16px">
      初稿仅供中介人员核查。Computer Use 可以在可见 Chrome 中辅助写入 CEAC，但验证码、敏感背景判断、电子签名和最终提交必须由人工完成。
    </div>
    <section class="grid two">
      ${sections.map((section) => renderDsSection(application, section)).join("")}
    </section>
    ${renderBranchPreview(application)}
    ${!screenAgentReady ? `
      <div class="screen-agent-runtime-alert" role="status">
        <strong>当前是预览模式，不能控制 Chrome</strong>
        <span>${escapeHtml(state.screenAgentRuntime?.message || "请从 Finder 启动 Screen Agent 版本。")}</span>
      </div>
    ` : ""}
    <div class="actions" style="margin-top:18px">
      <button class="btn" id="prefillForm">进入 Computer Use 执行台</button>
      <button class="btn secondary" id="generateReport">导出核查清单</button>
    </div>
  `;
  document.querySelector("#prefillForm").addEventListener("click", () => route("prefill"));
  document.querySelector("#generateReport").addEventListener("click", () => {
    buildAuditReport(application);
    application.currentStep = 7;
    saveApplication(application);
    route("report");
  });
}

function renderBranchPreview(application) {
  const questions = (application.branchQuestionnaire || []).filter((item) => item.visible !== false);
  if (!questions.length) return "";
  const grouped = groupBy(questions, "section");
  return `
    <section class="branch-preview" style="margin-top:18px">
      <div class="branch-preview-heading">
        <div>
          <span class="page-kicker">条件分支</span>
          <h2>DS-160 条件问答摘要</h2>
        </div>
        <span>${questions.filter((item) => ["已回答", "已核查"].includes(item.status)).length} / ${questions.length} 已处理</span>
      </div>
      ${Object.entries(grouped).map(([section, items]) => `
        <details class="branch-preview-section">
          <summary>
            <strong>${escapeHtml(section)}</strong>
            <span>${items.filter((item) => ["已回答", "已核查"].includes(item.status)).length} / ${items.length}</span>
          </summary>
          <div>
            ${items.map((item) => `
              <div class="branch-preview-row">
                <span>${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(branchAnswerDisplay(item))}</strong>
                <span class="badge ${branchStatusClass(item.status)}">${escapeHtml(item.status)}</span>
              </div>
            `).join("")}
          </div>
        </details>
      `).join("")}
    </section>
  `;
}

function branchAnswerDisplay(question) {
  if (question.answerType === "records") return `${(question.records || []).length} 条记录`;
  if (question.answerType === "details") {
    return Object.values(question.details || {}).some((value) => String(value || "").trim()) ? "已填写" : "待补充";
  }
  const choice = (question.choices || []).find((item) => item.value === question.answer);
  return choice?.label || "待客户确认";
}

function renderDsSection(application, section) {
  if (section === "安全与背景问题") {
    return `
      <article class="panel ds-section">
        <h2>${section}</h2>
        ${["健康相关问题", "犯罪记录", "移民违规", "安全相关问题", "特殊组织 / 军事 / 执法 / 专业技能", "过往拒签、拒绝入境或撤回入境申请"].map((label) => `
          <div class="field-pair">
            <div class="field-label">${label}</div>
            <div><span class="badge high-risk">需顾问逐项确认</span></div>
          </div>
        `).join("")}
      </article>
    `;
  }

  const sectionMap = {
    "基础信息": "基础信息",
    "护照信息": "护照信息",
    "旅行信息": "旅行信息",
    "工作 / 教育 / 培训": "工作 / 教育 / 培训",
    "SEVIS / 学生信息": "SEVIS / 学生信息"
  };
  const sectionHints = {
    "同行人": ["是否有人同行", "同行人姓名", "与申请人的关系", "是否作为团队或组织出行"],
    "以往赴美记录": ["是否曾去过美国", "过往赴美日期", "是否持有或曾持有美国签证", "签证号码", "拒签 / 拒绝入境 / 移民申请记录"],
    "家庭信息": ["父母姓名和出生日期", "父母是否在美国", "配偶信息", "子女信息", "在美直系亲属或其他亲属情况"],
    "工作 / 教育 / 培训": ["当前职业", "当前雇主 / 学校", "职位 / 专业", "地址和联系方式", "过往工作和教育经历", "特殊培训 / 语言能力 / 旅行国家记录"]
  };
  const sourceSection = sectionMap[section] || section;
  const fields = visibleFieldsForApplication(application).filter((field) => localizeSection(field.section) === sourceSection);
  const questionFields = section === "美国联系人" ? application.missingQuestions.slice(0, 1) : section === "工作 / 教育 / 培训" ? application.missingQuestions.slice(2) : [];
  return `
    <article class="panel ds-section">
      <h2>${section}</h2>
      ${fields.map((field) => `
        <div class="field-pair">
          <div class="field-label">${escapeHtml(localizeField(field.label))}</div>
          <div>${escapeHtml(field.value)} ${field.confirmed ? '<span class="badge confirmed">已确认</span>' : '<span class="badge needs-review">待人工核查</span>'}</div>
        </div>
      `).join("")}
      ${questionFields.map((question) => `
        <div class="field-pair">
          <div class="field-label">${escapeHtml(localizeQuestion(question.label))}</div>
          <div>${escapeHtml(question.answer || "缺失")}</div>
        </div>
      `).join("")}
      ${!fields.length && !questionFields.length ? (sectionHints[section] || ["该模块暂无可用资料"]).map((label) => `
        <div class="field-pair">
          <div class="field-label">${escapeHtml(label)}</div>
          <div><span class="badge needs-review">待录入 / 待确认</span></div>
        </div>
      `).join("") : ""}
    </article>
  `;
}

const SCREEN_AGENT_FIELD_SPECS = [
  { id: "personal.surname", label: "Surname", section: "personal", sectionLabel: "Personal Information" },
  { id: "personal.givenNames", label: "Given Names", section: "personal", sectionLabel: "Personal Information" },
  { id: "personal.dateOfBirth", label: "Date of Birth", section: "personal", sectionLabel: "Personal Information" },
  { id: "personal.placeOfBirth", label: "Place of Birth", section: "personal", sectionLabel: "Personal Information" },
  { id: "passport.number", label: "Passport Number", section: "passport", sectionLabel: "Passport Information" },
  { id: "passport.issueDate", label: "Issue Date", section: "passport", sectionLabel: "Passport Information" },
  { id: "passport.expiration", label: "Expiration Date", section: "passport", sectionLabel: "Passport Information" },
  { id: "travel.visaType", label: "Purpose of Trip", section: "travel", sectionLabel: "Travel Information" },
  { id: "travel.arrivalDate", label: "Intended Date of Arrival", section: "travel", sectionLabel: "Travel Information" },
  { id: "contact.usAddress", label: "Address Where You Will Stay", section: "travel", sectionLabel: "Travel Information" },
  { id: "contact.organizationName", label: "U.S. Contact Organization", section: "contact", sectionLabel: "U.S. Contact" },
  { id: "contact.phone", label: "U.S. Contact Phone", section: "contact", sectionLabel: "U.S. Contact" },
  { id: "work.employerName", label: "Present Employer / School", section: "work", sectionLabel: "Work / Education" },
  { id: "education.schoolName", label: "School Name", section: "student", sectionLabel: "Student / Exchange" },
  { id: "education.sevisId", label: "SEVIS ID", section: "student", sectionLabel: "Student / Exchange" },
  { id: "education.programNumber", label: "Program Number", section: "student", sectionLabel: "Student / Exchange" }
];

function screenAgentFieldValue(application, fieldId) {
  if (fieldId === "travel.visaType") return application.visaType || "";
  return String((application.extractedFields || []).find((field) => field.id === fieldId)?.value || "").trim();
}

function buildScreenAgentPlan(application) {
  const usableSpecs = SCREEN_AGENT_FIELD_SPECS.filter((spec) => {
    if (!screenAgentFieldValue(application, spec.id)) return false;
    if (spec.section === "student" && !visibleSectionsForApplication(application).includes("SEVIS / 学生信息")) return false;
    return true;
  });
  const actions = [];
  [...new Set(usableSpecs.map((spec) => spec.section))].forEach((section) => {
    const sectionSpecs = usableSpecs.filter((spec) => spec.section === section);
    actions.push({
      type: "navigate",
      section,
      label: `打开 ${sectionSpecs[0].sectionLabel}`
    });
    sectionSpecs.forEach((spec) => actions.push({
      type: "fill",
      fieldId: spec.id,
      section,
      label: spec.label,
      value: screenAgentFieldValue(application, spec.id)
    }));
  });
  actions.push({
    type: "gate",
    section: "security",
    label: "Security and Background",
    reason: "敏感背景问题需要顾问根据客户真实情况逐项确认"
  });
  return actions;
}

function ensureScreenAgentState(application, options = {}) {
  if (!application.screenAgent || typeof application.screenAgent !== "object" || Array.isArray(application.screenAgent)) {
    application.screenAgent = {};
  }
  const agent = application.screenAgent;
  agent.sessionId = agent.sessionId || `agent-${Date.now().toString(36)}`;
  agent.status = agent.status || "idle";
  agent.actionIndex = Number.isFinite(Number(agent.actionIndex)) ? Number(agent.actionIndex) : 0;
  agent.filledValues = agent.filledValues && typeof agent.filledValues === "object" ? agent.filledValues : {};
  agent.logs = Array.isArray(agent.logs) ? agent.logs : [];
  agent.pausedReason = agent.pausedReason || "";
  agent.startedAt = agent.startedAt || "";
  agent.completedAt = agent.completedAt || "";
  if (options.recoverRunning && agent.status === "running") {
    agent.status = "paused";
    agent.pausedReason = "页面离开或刷新，等待人工继续";
  }
  return agent;
}

function screenAgentSelectorValue(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function screenAgentStatusMeta(status) {
  return {
    idle: { label: "准备就绪", badge: "pending" },
    running: { label: "正在操作", badge: "running" },
    paused: { label: "已暂停", badge: "needs-review" },
    handoff: { label: "等待人工接管", badge: "high-risk" },
    completed: { label: "演示已完成", badge: "completed" }
  }[status] || { label: "准备就绪", badge: "pending" };
}

function addScreenAgentLog(application, type, message, metadata = {}) {
  const agent = ensureScreenAgentState(application);
  agent.logs.push({
    id: `log-${Date.now()}-${agent.logs.length}`,
    at: new Date().toISOString(),
    type,
    message,
    ...metadata
  });
  agent.logs = agent.logs.slice(-120);
  application.prefillLog = agent.logs.map((item) => item.message);
}

function renderScreenAgentLogs(logs) {
  if (!logs.length) {
    return '<div class="agent-log-empty"><strong>等待启动</strong><span>Agent 的观察、定位、输入和暂停动作会记录在这里。</span></div>';
  }
  return logs.map((item) => `
    <div class="agent-log-row ${escapeHtml(item.type || "info")}">
      <span class="agent-log-dot"></span>
      <div><strong>${escapeHtml(item.message)}</strong><small>${escapeHtml(formatAgentLogTime(item.at))}</small></div>
    </div>
  `).join("");
}

function formatAgentLogTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).format(date);
}

function renderMockAgentSection(section, specs, filledValues) {
  if (!specs.length) return "";
  const sectionLabel = specs[0].sectionLabel;
  return `
    <section class="mock-ds-section" data-agent-section="${escapeHtml(section)}">
      <header><span>${escapeHtml(sectionLabel)}</span><small>Mock DS-160</small></header>
      <div class="mock-ds-fields">
        ${specs.map((spec) => {
          const value = filledValues[spec.id] || "";
          return `
            <label class="mock-ds-field ${value ? "agent-complete" : ""}" data-agent-field-row="${escapeHtml(spec.id)}">
              <span>${escapeHtml(spec.label)}</span>
              <input type="text" readonly tabindex="-1" data-agent-field="${escapeHtml(spec.id)}" value="${escapeHtml(value)}" placeholder="Not entered">
              <i aria-hidden="true">${iconCheck()}</i>
            </label>
          `;
        }).join("")}
      </div>
    </section>
  `;
}

function travelBrowserUseRows(application) {
  const fields = new Map((application.extractedFields || []).map((field) => [field.id, field.value]));
  const questions = new Map((application.branchQuestionnaire || []).map((item) => [item.id, item]));
  const specific = questions.get("travel.specific_plans") || {};
  const payer = questions.get("travel.payer") || {};
  const details = specific.details || {};
  const value = (detailId, fieldId) => String(details[detailId] || fields.get(fieldId) || "").trim();
  const answerLabel = (question) => {
    const choice = (question.choices || []).find((item) => item.value === question.answer);
    return choice?.label || question.answer || "";
  };
  const stayDuration = details.stayLength && details.stayUnit
    ? `${details.stayLength} ${details.stayUnit}`
    : value("stayDuration", "travel.stayDuration");
  const structuredAddress = [
    details.usStreet1, details.usStreet2, details.usCity,
    details.usState, details.usPostalCode
  ].filter(Boolean).join(", ");
  return [
    { label: "签证类别", value: application.visaType },
    { label: "是否有具体旅行计划", value: answerLabel(specific) },
    { label: "预计抵达日期", value: value("arrivalDate", "travel.arrivalDate") },
    { label: "抵达航班", value: value("arrivalFlight", "travel.arrivalFlight") },
    { label: "抵达城市", value: value("arrivalCity", "travel.arrivalCity") },
    { label: "离境日期", value: value("departureDate", "travel.departureDate") },
    { label: "离境航班", value: value("departureFlight", "travel.departureFlight") },
    { label: "停留时长", value: stayDuration },
    { label: "在美停留地址", value: structuredAddress || value("usAddress", "contact.usAddress") },
    { label: "费用承担人", value: answerLabel(payer) }
  ].filter((item) => String(item.value || "").trim());
}

function browserWorkflowRows(application) {
  const sensitivePrefixes = ["security.", "immigration.", "inadmissibility."];
  const fields = (application.extractedFields || [])
    .filter((field) => String(field.value || "").trim())
    .filter((field) => !sensitivePrefixes.some((prefix) => field.id.startsWith(prefix)))
    .slice(0, 16)
    .map((field) => ({
      label: localizeField(field.label || field.id),
      value: String(field.value || "").trim(),
      section: localizeSection(field.section || "字段")
    }));
  const answers = (application.branchQuestionnaire || [])
    .filter((question) => question.visible !== false && !question.sensitive)
    .filter((question) => question.answer && question.answer !== "unknown")
    .slice(0, 8)
    .map((question) => {
      const choice = (question.choices || []).find((item) => item.value === question.answer);
      return {
        label: question.label,
        value: choice?.label || question.answer,
        section: question.section
      };
    });
  return [...fields, ...answers];
}

const BROWSER_WORKFLOW_RUNTIME_ONLY_QUESTION_IDS = new Set([
  "photo.upload_result"
]);

const BROWSER_WORKFLOW_COUNTRY_FIELD_IDS = new Set([
  "application.consulateCountry",
  "personal.birthCountry",
  "personal.nationality",
  "contact.homeCountry",
  "passport.issuingAuthority",
  "passport.issueCountry"
]);

const BROWSER_WORKFLOW_STATE_FIELD_IDS = new Set([
  "travel.usState",
  "contact.usState",
  "education.schoolState"
]);

const BROWSER_WORKFLOW_SELECT_FIELD_IDS = new Set([
  ...BROWSER_WORKFLOW_COUNTRY_FIELD_IDS,
  ...BROWSER_WORKFLOW_STATE_FIELD_IDS,
  "personal.sex"
]);

const BROWSER_WORKFLOW_COUNTRY_ALIASES = new Map([
  ["CHN", "CHINA"], ["CHINESE", "CHINA"], ["PRC", "CHINA"],
  ["P.R.C.", "CHINA"], ["P.R. CHINA", "CHINA"],
  ["PEOPLE'S REPUBLIC OF CHINA", "CHINA"], ["PEOPLES REPUBLIC OF CHINA", "CHINA"],
  ["中国", "CHINA"], ["中华人民共和国", "CHINA"],
  ["USA", "UNITED STATES OF AMERICA"], ["US", "UNITED STATES OF AMERICA"],
  ["U.S.", "UNITED STATES OF AMERICA"], ["UNITED STATES", "UNITED STATES OF AMERICA"],
  ["GBR", "UNITED KINGDOM"], ["UK", "UNITED KINGDOM"]
]);

function canonicalBrowserWorkflowFieldValue(fieldId, rawValue) {
  const value = String(rawValue || "").replace(/\s+/g, " ").trim();
  if (!value) return "";
  const normalized = value.toUpperCase();
  if (BROWSER_WORKFLOW_COUNTRY_FIELD_IDS.has(fieldId)) {
    return BROWSER_WORKFLOW_COUNTRY_ALIASES.get(normalized) || normalized;
  }
  if (fieldId === "personal.sex") {
    return ({ M: "MALE", F: "FEMALE", 男: "MALE", 女: "FEMALE" })[normalized] || normalized;
  }
  return BROWSER_WORKFLOW_STATE_FIELD_IDS.has(fieldId) ? normalized : value;
}

function browserWorkflowFieldValueIsUsable(fieldId, rawValue) {
  const value = canonicalBrowserWorkflowFieldValue(fieldId, rawValue);
  if (!value) return false;
  if (!BROWSER_WORKFLOW_SELECT_FIELD_IDS.has(fieldId)) return true;
  if (/(^|\b)(DEMO|DUMMY|EXAMPLE|FAKE|MOCK|PLACEHOLDER|SAMPLE|TEST)(\b|$)/i.test(value)) return false;
  if (["1", "N/A", "NA", "NONE", "UNKNOWN", "DO NOT KNOW", "DOES NOT APPLY", "NOT APPLICABLE", "NATIONAL", "NATIONALITY"].includes(value)) return false;
  if (fieldId === "personal.sex") return ["MALE", "FEMALE"].includes(value);
  return /^[A-Z][A-Z .,'()&/\-]{1,79}$/.test(value);
}

function browserWorkflowBlockingQuestions(application) {
  const blockingStatuses = new Set(["待客户确认", "信息待补充"]);
  return (application.branchQuestionnaire || []).filter((question) => (
    question
    && question.visible !== false
    && !BROWSER_WORKFLOW_RUNTIME_ONLY_QUESTION_IDS.has(question.id)
    && blockingStatuses.has(String(question.status || ""))
  ));
}

const BROWSER_WORKFLOW_REQUIRED_FIELD_GROUPS = [
  { label: "护照英文姓", ids: ["personal.surname"] },
  { label: "护照英文名", ids: ["personal.givenNames"] },
  { label: "完整母语姓名", ids: ["personal.nativeName"] },
  { label: "出生性别", ids: ["personal.sex"] },
  { label: "出生日期", ids: ["personal.dateOfBirth"] },
  { label: "出生城市", ids: ["personal.birthCity", "personal.placeOfBirth"] },
  { label: "出生省、州或地区", ids: ["personal.birthRegion"] },
  { label: "出生国家或地区", ids: ["personal.birthCountry"] },
  { label: "当前国籍", ids: ["personal.nationality"] },
  { label: "本国身份证号码", ids: ["personal.nationalId"] },
  { label: "家庭地址第一行", ids: ["contact.homeStreet1", "contact.homeAddress"] },
  { label: "家庭地址城市", ids: ["contact.homeCity"] },
  { label: "家庭地址省、州或地区", ids: ["contact.homeRegion"] },
  { label: "家庭地址邮编", ids: ["contact.homePostalCode"] },
  { label: "家庭地址国家或地区", ids: ["contact.homeCountry"] },
  { label: "主要电话号码", ids: ["contact.primaryPhone"] },
  { label: "次要电话号码或不适用", ids: ["contact.secondaryPhone"] },
  { label: "工作电话号码或不适用", ids: ["contact.workPhone"] },
  { label: "当前常用 Email", ids: ["contact.email"] },
  { label: "护照号码", ids: ["passport.number"] },
  { label: "护照签发国家或机构", ids: ["passport.issuingAuthority"] },
  { label: "护照签发城市", ids: ["passport.issueCity"] },
  { label: "护照签发省、州或地区", ids: ["passport.issueRegion"] },
  { label: "护照签发国家或地区", ids: ["passport.issueCountry"] },
  { label: "护照签发日期", ids: ["passport.issueDate"] },
  { label: "护照到期日期", ids: ["passport.expiration"] },
  { label: "SEVIS ID", ids: ["education.sevisId"], visas: ["f1", "f2", "j1", "j2"] },
  { label: "美国学校名称", ids: ["education.schoolName"], visas: ["f1", "f2"] },
  { label: "课程或专业名称", ids: ["education.programName"], visas: ["f1", "f2"] },
  { label: "美国学校地址第一行", ids: ["education.schoolStreet1", "education.schoolAddress"], visas: ["f1", "f2"] },
  { label: "美国学校地址城市", ids: ["education.schoolCity"], visas: ["f1", "f2"] },
  { label: "美国学校所在州", ids: ["education.schoolState"], visas: ["f1", "f2"] },
  { label: "美国学校邮编", ids: ["education.schoolPostalCode"], visas: ["f1", "f2"] },
  { label: "DS-2019 Program Number", ids: ["education.programNumber"], visas: ["j1", "j2"] },
  { label: "交流项目 Sponsor", ids: ["education.sponsorName"], visas: ["j1", "j2"] }
];

function browserWorkflowMissingFields(application) {
  const fields = new Map((application.extractedFields || []).map((field) => [field.id, field]));
  const visaId = visaByName(application.visaType).id;
  return BROWSER_WORKFLOW_REQUIRED_FIELD_GROUPS.map((group) => {
    if (group.visas && !group.visas.includes(visaId)) return false;
    const hasUsableValue = group.ids.some((fieldId) => (
      browserWorkflowFieldValueIsUsable(fieldId, fields.get(fieldId)?.value)
    ));
    if (hasUsableValue) return null;
    const hasRawValue = group.ids.some((fieldId) => String(fields.get(fieldId)?.value || "").trim());
    return { ...group, invalidValue: hasRawValue };
  }).filter(Boolean);
}

function browserWorkflowPreflightIssues(application) {
  return [
    ...browserWorkflowBlockingQuestions(application).map((question) => ({
      type: "question",
      id: question.id,
      label: question.label
    })),
    ...browserWorkflowMissingFields(application).map((field) => ({
      type: "field",
      id: field.ids[0],
      label: field.invalidValue
        ? `${field.label}（当前值无法匹配 CEAC 选项）`
        : field.label
    }))
  ];
}

function codexAgentFlowStep(agent) {
  if (["expired", "revoked", "failed"].includes(agent.state)) return 0;
  if (["review_required", "completed"].includes(agent.state)) return 4;
  if (["claimed", "running", "blocked"].includes(agent.state)) return 3;
  if (agent.state === "waiting_for_entry") return 2;
  if (agent.state === "prepared") return 1;
  return 0;
}

function codexAgentStatusMeta(status) {
  return {
    idle: { label: "未准备", badge: "pending" },
    prepared: { label: "任务已准备", badge: "pending" },
    claimed: { label: "Computer Use 已接收", badge: "running" },
    waiting_for_entry: { label: "等待进入表格", badge: "needs-review" },
    running: { label: "正在填写", badge: "running" },
    review_required: { label: "等待人工核对", badge: "confirmed" },
    completed: { label: "任务已完成", badge: "confirmed" },
    blocked: { label: "需要人工处理", badge: "needs-review" },
    failed: { label: "本次未完成", badge: "needs-review" },
    expired: { label: "任务已过期", badge: "needs-review" },
    revoked: { label: "任务已撤销", badge: "needs-review" }
  }[status] || { label: "未准备", badge: "pending" };
}

function renderPrefill(container) {
  const application = getActiveApplication();
  const agent = application.codexAgent || { state: "idle" };
  const rows = browserWorkflowRows(application);
  const preflightIssues = browserWorkflowPreflightIssues(application);
  const closed = ["review_required", "completed", "expired", "revoked"].includes(agent.state) || agent.closed;
  const activeJob = Boolean(agent.jobId && !closed);
  const handoff = state.computerUseHandoffs.ds160;
  const handoffReady = Boolean(activeJob && handoff?.jobId === agent.jobId);
  const agentStarted = ["claimed", "running", "blocked"].includes(agent.state);
  const needsFreshHandoff = Boolean(activeJob && !handoffReady && !agentStarted);
  const canPrepare = Boolean(
    state.apiAvailable && API_BASE && rows.length
    && !preflightIssues.length
    && (!activeJob || needsFreshHandoff)
  );
  const canResume = Boolean(
    activeJob
    && handoffReady
    && !preflightIssues.length
    && agent.state !== "running"
  );
  const manualNextPending = agent.statusCode === "auto_next_disabled";
  const showResume = Boolean(activeJob && handoffReady && !agentStarted && !manualNextPending);
  const showStartGate = showResume || manualNextPending;
  const observedRoutes = Array.isArray(agent.observedRoutes) ? agent.observedRoutes : [];
  const mappedRouteCount = observedRoutes.filter((route) => route.mapped).length;
  const currentRouteLabel = agent.currentRoute?.node || agent.currentRoute?.title || "尚未捕获表格路径";
  const meta = codexAgentStatusMeta(agent.state);
  const progress = agent.totalFields
    ? Math.round(((agent.completedFields || 0) / agent.totalFields) * 100)
    : 0;
  const flowStep = codexAgentFlowStep(agent);
  const autoNextEnabled = agent.jobId ? Boolean(agent.autoNext) : true;
  container.innerHTML = `
    ${renderAppHeader(application, "Computer Use 逐页填写", "WestoryVisa 准备当前客户字段计划。你人工完成验证码并进入正式表格后，再把可见页面交给 Codex Computer Use 稳健填写。")} 
    <section class="screen-agent-banner browser-use-banner codex-agent-banner">
      <div><span class="agent-live-dot ${["claimed", "running"].includes(agent.state) ? "active" : ""}"></span><strong>Codex Computer Use</strong><span>系统级可见操作 · 无需 Chrome 扩展</span></div>
      <div><span class="badge ${meta.badge}" id="codexAgentStatus">${meta.label}</span><span id="codexAgentProgressText">${progress}%</span></div>
    </section>
    <section class="screen-agent-runtime-alert ${state.apiAvailable ? "ready" : "blocked"}" role="status">
      <strong>${needsFreshHandoff ? "本机授权已随刷新失效，请重新准备" : "Computer Use 执行通道已就绪"}</strong>
      <span>${needsFreshHandoff ? "为避免把一次性令牌写入数据库，页面刷新后需要撤销旧任务并生成新的本机交接。客户字段仍保留在档案中。" : "WestoryVisa 只准备短时字段任务并打开 CEAC；实际点击、输入、下拉选择与页面复读由 Codex Desktop 的 Computer Use 完成。"}</span>
    </section>
    ${preflightIssues.length ? `
      <section class="screen-agent-runtime-alert workflow-preflight-alert" role="alert">
        <div>
          <strong>当前档案还有 ${preflightIssues.length} 项资料未收齐，暂不能开始逐页填写</strong>
          <span>${escapeHtml(preflightIssues.slice(0, 4).map((item) => item.label).join("；"))}${preflightIssues.length > 4 ? `；另有 ${preflightIssues.length - 4} 项` : ""}。补齐后系统才会建立 Computer Use 任务，避免 CEAC 页面留下空项。</span>
        </div>
        <button class="btn secondary" type="button" id="resolveWorkflowQuestions">返回客户问题补充</button>
      </section>
    ` : ""}
    <section class="codex-start-gate" id="codexStartGate" ${showStartGate ? "" : "hidden"}>
      <div>
        <span>${manualNextPending ? "当前页已填写完成" : "进入正式表格后"}</span>
        <strong>${manualNextPending ? "请回到 CEAC 点击 Next；下一页打开后再让 Computer Use 继续。" : "点击后会复制当前任务的短时启动指令；回到 Codex 发送后才会读取和填写页面。"}</strong>
      </div>
      ${manualNextPending
        ? '<span class="browser-use-target-state">请在 CEAC 点击 Next</span>'
        : `<button class="btn" type="button" id="resumeCodexAgent" ${canResume ? "" : "disabled"}>${agent.state === "waiting_for_entry" ? "再次复制 Codex 启动指令" : "我已进入表格，交给 Computer Use"}</button>`}
    </section>
    <section class="browser-use-workspace">
      <div class="browser-use-main">
        <div class="browser-use-target">
          <div><span class="page-kicker">Current Scope</span><h2>当前客户的逐页字段计划</h2></div>
          <span class="browser-use-target-state">60 分钟本机任务</span>
        </div>
        <div class="browser-use-flow" aria-label="Computer Use 执行步骤">
          ${[
            ["整理字段", "生成当前档案白名单"],
            ["准备任务", "打开官方网站起始页"],
            ["人工进入", "验证码与初始步骤"],
            ["可见填写", "逐项复读并受控 Next"],
            ["人工核查", "敏感或未映射页暂停"]
          ].map(([title, description], index) => `
            <div class="browser-use-flow-step ${index < flowStep ? "completed" : index === flowStep ? "active" : ""}" data-codex-step="${index}">
              <span data-codex-step-marker>${index < flowStep ? iconCheck() : index + 1}</span>
              <div><strong>${title}</strong><small>${description}</small></div>
            </div>
          `).join("")}
        </div>
        <div class="browser-use-scope">
          <header><div><span class="page-kicker">Field Plan</span><h2>可交接信息预览</h2></div><strong>${rows.length} 项预览</strong></header>
          <div class="browser-use-field-list">
            ${rows.map((item) => `
              <div><span>${escapeHtml(item.section || item.label)} · ${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>
            `).join("") || '<p class="empty-copy">当前档案还没有可交接的已收集信息。</p>'}
          </div>
        </div>
      </div>
      <aside class="screen-agent-console browser-use-console codex-agent-console">
        <div class="agent-console-heading">
          <div><span class="page-kicker">Local Computer</span><h2>当前执行状态</h2></div>
          <span class="agent-session-id">WORKFLOW V4</span>
        </div>
        <div class="agent-progress-block">
          <div><span>字段进度</span><strong id="codexAgentProgressValue">${progress}%</strong></div>
          <div class="progress-track"><div class="progress-fill" id="codexAgentProgress" style="width:${progress}%"></div></div>
          <p id="codexAgentMessage">${escapeHtml(String(agent.message || "准备好后从这里打开 CEAC"))}</p>
        </div>
        <div class="agent-runtime-grid">
          <div><span>Agent</span><strong>Computer Use</strong></div>
          <div><span>Browser</span><strong>当前 Chrome</strong></div>
          <div><span>节奏</span><strong>0.9–1.5s</strong></div>
          <div><span>Next</span><strong>核验后</strong></div>
        </div>
        <div class="codex-target-site">
          <span>目标网站</span>
          <a href="https://ceac.state.gov/GenNIV/Default.aspx" target="_blank" rel="noopener noreferrer">ceac.state.gov/GenNIV</a>
        </div>
        <div class="codex-route-coverage">
          <div><span>已记录页面路径</span><strong id="codexObservedRouteCount">${observedRoutes.length}</strong></div>
          <small id="codexObservedRouteMeta">已映射 ${mappedRouteCount} · 当前 ${escapeHtml(currentRouteLabel)}</small>
        </div>
        <div class="codex-handoff-note" id="codexPromptNotice">
          <strong id="codexHandoffTitle">${agent.pageLabel ? `当前：${escapeHtml(agent.pageLabel)}` : handoffReady ? "CEAC 已打开，等待你进入表格" : "尚未准备本机任务"}</strong>
          <span id="codexHandoffCopy">${handoffReady ? "人工完成申请地点、验证码与找回信息。进入第一张正式表格后，再点击上方按钮把短时任务交给 Codex。" : "一次性令牌只保存在当前页面内存中，不写入客户数据库；任务关闭后服务器会擦除字段值。"}</span>
        </div>
        <label class="agent-auto-next-toggle">
          <span><strong>普通页面连续填写</strong><small>开启后，当前页全部复读无误且无报错时才点击 Next</small></span>
          <input type="checkbox" id="autoNextToggle" ${agent.jobId && !closed ? "disabled" : ""} ${autoNextEnabled ? "checked" : ""}>
          <i aria-hidden="true"></i>
        </label>
        <div class="desktop-agent-actions browser-use-actions codex-agent-actions">
          <button class="btn" type="button" id="prepareCodexAgent" ${canPrepare ? "" : "disabled"} ${activeJob && !needsFreshHandoff ? "hidden" : ""}>${needsFreshHandoff ? "重新准备 Computer Use 任务" : "准备任务并打开 CEAC"}</button>
          <button class="btn secondary" type="button" id="revokeCodexAgent" ${activeJob ? "" : "disabled"} ${activeJob ? "" : "hidden"}>停止当前任务</button>
        </div>
      </aside>
    </section>
    <div class="screen-agent-safety-note browser-use-boundary">
      <strong>不会跨越的边界</strong>
      <span>Computer Use 不处理验证码、登录凭据、拒签或移民历史判断、安全与背景问题、电子签名、法律声明、付款和最终提交；不使用脚本注入，也不绕过网站限制。顾问可以随时停止并人工接管。</span>
    </div>
    <div class="actions" style="margin-top:18px">
      <button class="btn" id="generateReport">生成 Agent 审计报告</button>
      <button class="btn secondary" id="backPreview">返回 DS-160 初稿</button>
    </div>
  `;

  document.querySelector("#prepareCodexAgent")?.addEventListener("click", () => startComputerUseAgent(application));
  document.querySelector("#resumeCodexAgent")?.addEventListener("click", () => handoffToComputerUse(application));
  document.querySelector("#revokeCodexAgent")?.addEventListener("click", () => revokeCodexAgent(application));
  document.querySelector("#resolveWorkflowQuestions")?.addEventListener("click", async () => {
    if (activeJob) await revokeCodexAgent(application, { renderAfter: false });
    route("questions");
  });
  document.querySelector("#backPreview")?.addEventListener("click", () => route("preview"));
  document.querySelector("#generateReport")?.addEventListener("click", async () => {
    buildAuditReport(application);
    application.currentStep = 7;
    await saveApplication(application);
    route("report");
  });
  if (agent.jobId && !closed) startCodexAgentPolling(application);
}

function clearCodexAgentPolling() {
  if (state.codexAgentTimer) {
    window.clearInterval(state.codexAgentTimer);
    state.codexAgentTimer = null;
  }
}

function updateCodexAgentMessage(message, type = "") {
  const target = document.querySelector("#codexAgentMessage, #appointmentAgentMessage");
  if (!target) return;
  target.textContent = message;
  target.className = type;
}

async function copyPrivateText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("浏览器未允许复制，请检查剪贴板权限");
}

function computerUsePrompt(handoff, workflowType = "ds160") {
  const destination = workflowType === "appointment"
    ? "当前已登录的美国签证预约资料页面"
    : "当前已经进入正式表格的 CEAC DS-160 页面";
  const navigation = workflowType === "appointment"
    ? "不要点击 Save、Continue、Next，不处理付款、选位或最终预约。"
    : "普通页面只有在字段全部复读无误且没有必填报错时，才按任务中的 autoNext 规则继续。";
  return [
    `请使用 Computer Use 执行 WestoryVisa 当前 ${workflowType === "appointment" ? "预约资料" : "DS-160"} 任务。`,
    `只操作${destination}，不要使用 Chrome 扩展、DOM 注入、Playwright、Selenium 或第三方 RPA。`,
    workflowType === "appointment"
      ? "我授权将任务中列出的申请人身份、护照、联系、签证类别与预约资料填写到该预约官网的当前资料页。"
      : "我授权将任务中列出的客户身份、护照、联系、旅行、家庭、工作与教育资料填写到 CEAC 的当前 DS-160 申请。",
    `任务地址：${handoff.taskUrl}`,
    `一次性令牌：${handoff.accessToken}`,
    "先使用 Authorization: Bearer <一次性令牌> 从上述 127.0.0.1 任务地址读取 JSON；令牌只能发送到这个本机地址，不要在回复、日志或文件中回显。",
    "确认 executor=codex-computer-use、目标域名与 safety.allowedDomain 一致，并严格按 pages/actions 和 interactionPolicy 操作。",
    "开始写入前向任务返回的 statusUrl 更新 running；结束时只更新 review_required、blocked 或 failed，不回显客户敏感值。",
    "每次只执行一个可见操作，随后重新读取页面并复读字段；Yes/No、下拉框或 Does Not Apply 触发动态字段后，等待页面稳定再重新检查。",
    "按任务 interactionPolicy 使用稳健节奏，不要连续快速写入。",
    navigation,
    "遇到验证码、登录凭据、敏感背景判断、电子签名、法律声明、付款或最终提交时立即停下交给我。",
  ].join("\n");
}

async function markComputerUseWaiting(handoff) {
  const response = await DocFlowApi.request(`${handoff.taskUrl}/status`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${handoff.accessToken}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ state: "waiting_for_entry", completedFields: 0 })
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Computer Use 任务已失效，请重新准备");
  return data;
}

async function startComputerUseAgent(application) {
  const button = document.querySelector("#prepareCodexAgent");
  const preflightIssues = browserWorkflowPreflightIssues(application);
  if (preflightIssues.length) {
    updateCodexAgentMessage(
      `当前档案还有 ${preflightIssues.length} 项资料未收齐，请先补充客户问题。`,
      "error"
    );
    return;
  }
  if (!state.apiAvailable || !API_BASE) {
    updateCodexAgentMessage("请先通过本地服务器打开 WestoryVisa。", "error");
    return;
  }
  if (button) {
    button.disabled = true;
    button.textContent = "正在准备…";
  }
  let preparedJobId = "";
  try {
    state.computerUseHandoffs.ds160 = null;
    const previousAgent = application.codexAgent;
    const previousClosed = ["review_required", "completed", "expired", "revoked"]
      .includes(previousAgent?.state) || previousAgent?.closed;
    if (previousAgent?.jobId && !previousClosed) {
      await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(previousAgent.jobId)}`, {
        method: "DELETE"
      }).catch(() => {});
      clearCodexAgentPolling();
    }
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/prepare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        autoNext: document.querySelector("#autoNextToggle")?.checked !== false
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Computer Use 任务准备失败");
    if (!data.accessToken || !data.taskUrl) throw new Error("服务器没有返回本机 Computer Use 授权");
    preparedJobId = data.jobId;
    const autoNext = document.querySelector("#autoNextToggle")?.checked === true;
    state.computerUseHandoffs.ds160 = {
      jobId: data.jobId,
      taskUrl: data.taskUrl,
      accessToken: data.accessToken
    };
    application.codexAgent = {
      jobId: data.jobId,
      workflowType: "ds160",
      state: data.state || "prepared",
      message: data.message || "任务已准备，等待你进入 CEAC 正式表格",
      completedFields: 0,
      totalFields: data.totalFields || 0,
      expiresAt: data.expiresAt || "",
      createdAt: new Date().toISOString(),
      pageLabel: "",
      failedActionIds: [],
      missingFields: [],
      statusCode: "",
      currentRoute: null,
      observedRoutes: [],
      autoNext,
      closed: false
    };
    addScreenAgentLog(application, "info", "已准备 Codex Computer Use 逐页字段任务");
    await saveApplication(application);
    if (!data.browserOpened) {
      window.open("https://ceac.state.gov/GenNIV/Default.aspx", "_blank", "noopener,noreferrer");
    }
    application.codexAgent.message = "CEAC 已打开。请人工完成申请地点、验证码和初始步骤，再回到这里交给 Computer Use。";
    render("prefill");
    startCodexAgentPolling(application);
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "重新准备 Computer Use 任务";
    }
    state.computerUseHandoffs.ds160 = null;
    updateCodexAgentMessage(error.message || "Computer Use 任务准备失败", "error");
    if (preparedJobId) {
      DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(preparedJobId)}`, {
        method: "DELETE"
      }).catch(() => {});
    }
  }
}

async function handoffToComputerUse(application) {
  const agent = application.codexAgent;
  const button = document.querySelector("#resumeCodexAgent");
  if (!agent?.jobId) {
    updateCodexAgentMessage("请先打开 CEAC 并建立当前客户任务。", "error");
    return;
  }
  const handoff = state.computerUseHandoffs.ds160;
  if (!handoff || handoff.jobId !== agent.jobId) {
    updateCodexAgentMessage("当前页面没有可用的一次性授权，请重新准备 Computer Use 任务。", "error");
    return;
  }
  if (button) {
    button.disabled = true;
    button.textContent = "正在准备 Codex 指令…";
  }
  try {
    const status = await markComputerUseWaiting(handoff);
    await copyPrivateText(computerUsePrompt(handoff, "ds160"));
    agent.state = status.state || "waiting_for_entry";
    agent.message = "启动指令已复制。回到 Codex 发送后，Computer Use 会从当前可见表格开始逐项填写。";
    await saveApplication(application);
    updateCodexAgentUI(application);
    if (button) {
      button.disabled = false;
      button.textContent = "再次复制 Codex 启动指令";
    }
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "重新复制 Codex 启动指令";
    }
    updateCodexAgentMessage(error.message || "Computer Use 交接失败，请重新准备任务", "error");
  }
}

function startCodexAgentPolling(application) {
  clearCodexAgentPolling();
  refreshCodexAgent(application, { quiet: true });
  state.codexAgentTimer = window.setInterval(() => {
    refreshCodexAgent(application, { quiet: true });
  }, 5000);
}

async function refreshCodexAgent(application, options = {}) {
  const agent = application.codexAgent;
  if (!agent?.jobId || !state.apiAvailable || !API_BASE) return;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(agent.jobId)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Codex 状态读取失败");
    agent.state = data.state || agent.state;
    agent.message = data.message || agent.message;
    agent.completedFields = data.completedFields || 0;
    agent.totalFields = data.totalFields || agent.totalFields || 0;
    agent.expiresAt = data.expiresAt || agent.expiresAt || "";
    agent.pageLabel = data.pageLabel || agent.pageLabel || "";
    agent.failedActionIds = Array.isArray(data.failedActionIds) ? data.failedActionIds : [];
    agent.missingFields = Array.isArray(data.missingFields) ? data.missingFields : [];
    agent.statusCode = data.statusCode || "";
    agent.currentRoute = data.currentRoute || null;
    agent.observedRoutes = Array.isArray(data.observedRoutes) ? data.observedRoutes : [];
    agent.closed = Boolean(data.closed);
    updateCodexAgentUI(application);
    if (["review_required", "completed", "expired", "revoked"].includes(agent.state) || agent.closed) {
      clearCodexAgentPolling();
      await saveApplication(application);
    }
  } catch (error) {
    if (!options.quiet) updateCodexAgentMessage(error.message || "Codex 状态读取失败", "error");
  }
}

async function revokeCodexAgent(application, options = {}) {
  const agent = application.codexAgent;
  if (!agent?.jobId || !state.apiAvailable || !API_BASE) return;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(agent.jobId)}`, {
      method: "DELETE"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Codex 任务撤销失败");
    clearCodexAgentPolling();
    agent.state = "revoked";
    agent.message = data.message || "Codex 任务已撤销";
    agent.closed = true;
    state.computerUseHandoffs.ds160 = null;
    addScreenAgentLog(application, "warning", "顾问停止了 Computer Use 逐页填写任务");
    await saveApplication(application);
    if (options.renderAfter !== false) render("prefill");
  } catch (error) {
    updateCodexAgentMessage(error.message || "Codex 任务撤销失败", "error");
  }
}

function updateCodexAgentUI(application) {
  const agent = application.codexAgent || { state: "idle" };
  const preflightIssues = browserWorkflowPreflightIssues(application);
  const closed = ["review_required", "completed", "expired", "revoked"].includes(agent.state) || agent.closed;
  const activeJob = Boolean(agent.jobId && !closed);
  const handoff = state.computerUseHandoffs.ds160;
  const handoffReady = Boolean(activeJob && handoff?.jobId === agent.jobId);
  const agentStarted = ["claimed", "running", "blocked"].includes(agent.state);
  const needsFreshHandoff = Boolean(activeJob && !handoffReady && !agentStarted);
  const canResume = Boolean(
    activeJob
    && handoffReady
    && !preflightIssues.length
    && agent.state !== "running"
  );
  const manualNextPending = agent.statusCode === "auto_next_disabled";
  const showResume = Boolean(activeJob && handoffReady && !agentStarted && !manualNextPending);
  const showStartGate = showResume || manualNextPending;
  const meta = codexAgentStatusMeta(agent.state);
  const progress = agent.totalFields
    ? Math.round(((agent.completedFields || 0) / agent.totalFields) * 100)
    : 0;
  const badge = document.querySelector("#codexAgentStatus");
  if (badge) {
    badge.className = `badge ${meta.badge}`;
    badge.textContent = meta.label;
  }
  const bar = document.querySelector("#codexAgentProgress");
  if (bar) bar.style.width = `${progress}%`;
  ["#codexAgentProgressText", "#codexAgentProgressValue"].forEach((selector) => {
    const target = document.querySelector(selector);
    if (target) target.textContent = `${progress}%`;
  });
  const flowStep = codexAgentFlowStep(agent);
  document.querySelectorAll("[data-codex-step]").forEach((item) => {
    const index = Number(item.dataset.codexStep);
    item.classList.toggle("completed", index < flowStep);
    item.classList.toggle("active", index === flowStep);
    const marker = item.querySelector("[data-codex-step-marker]");
    if (marker) marker.innerHTML = index < flowStep ? iconCheck() : String(index + 1);
  });
  const liveDot = document.querySelector(".codex-agent-banner .agent-live-dot");
  if (liveDot) liveDot.classList.toggle("active", ["claimed", "waiting_for_entry", "running"].includes(agent.state));
  const message = String(agent.message || "等待准备 Computer Use 任务");
  updateCodexAgentMessage(message);

  const handoffTitle = document.querySelector("#codexHandoffTitle");
  const handoffCopy = document.querySelector("#codexHandoffCopy");
  if (handoffTitle) {
    handoffTitle.textContent = agent.pageLabel
      ? `当前：${agent.pageLabel}`
      : agent.state === "waiting_for_entry"
        ? "任务指令已准备，等待 Codex 接收"
        : "等待准备 CEAC 任务";
  }
  if (handoffCopy) {
    handoffCopy.textContent = agent.state === "waiting_for_entry"
      ? "请回到 Codex 发送已复制的启动指令。只有发送后，Computer Use 才会读取当前可见表格。"
      : agent.state === "running"
        ? "Computer Use 每次只做一个可见操作并重新读取页面；动态字段稳定后才继续。"
        : manualNextPending
          ? "本页已通过可见必填项检查。请在 CEAC 点击 Next，下一页载入后再继续任务。"
          : agent.state === "blocked"
            ? "请按状态提示处理当前页面，完成后可重新识别；不会自动处理敏感问题或最终提交。"
          : handoffReady
            ? "人工进入正式表格前，Computer Use 不会读取页面或写入字段。"
            : "一次性授权不写入数据库；页面刷新后需要重新准备任务。";
  }

  const prepareButton = document.querySelector("#prepareCodexAgent");
  if (prepareButton) {
    prepareButton.hidden = activeJob && !needsFreshHandoff;
    prepareButton.disabled = (activeJob && !needsFreshHandoff)
      || !state.apiAvailable || Boolean(preflightIssues.length);
    prepareButton.textContent = needsFreshHandoff
      ? "重新准备 Computer Use 任务"
      : "准备任务并打开 CEAC";
  }
  const startGate = document.querySelector("#codexStartGate");
  const renderedManualGate = Boolean(startGate && !startGate.querySelector("#resumeCodexAgent"));
  if (startGate && manualNextPending !== renderedManualGate) {
    render("prefill");
    return;
  }
  if (startGate) startGate.hidden = !showStartGate;
  const resumeButton = document.querySelector("#resumeCodexAgent");
  if (resumeButton) {
    resumeButton.disabled = !canResume;
    resumeButton.textContent = agent.state === "waiting_for_entry"
      ? "再次复制 Codex 启动指令"
      : "我已进入表格，交给 Computer Use";
  }
  const observedRouteCount = document.querySelector("#codexObservedRouteCount");
  const observedRouteMeta = document.querySelector("#codexObservedRouteMeta");
  const observedRoutes = Array.isArray(agent.observedRoutes) ? agent.observedRoutes : [];
  if (observedRouteCount) observedRouteCount.textContent = String(observedRoutes.length);
  if (observedRouteMeta) {
    const mappedCount = observedRoutes.filter((route) => route.mapped).length;
    const routeName = agent.currentRoute?.node || agent.currentRoute?.title || "尚未捕获表格路径";
    observedRouteMeta.textContent = `已映射 ${mappedCount} · 当前 ${routeName}`;
  }
  const stopButton = document.querySelector("#revokeCodexAgent");
  if (stopButton) {
    stopButton.hidden = !activeJob;
    stopButton.disabled = !activeJob;
  }
}

function desktopAgentStatusMeta(status) {
  return {
    idle: { label: "未启动", badge: "pending" },
    prepared: { label: "任务已准备", badge: "pending" },
    starting: { label: "启动 Chrome", badge: "running" },
    checking_permissions: { label: "检查环境", badge: "running" },
    opening: { label: "打开 CEAC", badge: "running" },
    waiting_for_travel_page: { label: "等待 Travel 页", badge: "needs-review" },
    running: { label: "正在填写", badge: "running" },
    review_required: { label: "等待人工核对", badge: "confirmed" },
    handoff: { label: "等待人工核对", badge: "high-risk" },
    blocked: { label: "需要处理", badge: "needs-review" },
    stopped: { label: "已急停", badge: "needs-review" }
  }[status] || { label: "未启动", badge: "pending" };
}

function clearOpenCoworkPolling() {
  if (state.openCoworkTimer) {
    window.clearInterval(state.openCoworkTimer);
    state.openCoworkTimer = null;
  }
}

function clearDesktopAgentPolling() {
  if (state.desktopAgentTimer) {
    window.clearInterval(state.desktopAgentTimer);
    state.desktopAgentTimer = null;
  }
}

async function launchDesktopScreenAgent(application, plan) {
  const button = document.querySelector("#launchDesktopScreenAgent");
  if (!state.screenAgentRuntime?.available) {
    updateDesktopAgentMessage(state.screenAgentRuntime?.message || "请从 Finder 启动 Screen Agent 版本。", "error");
    return;
  }
  if (!state.apiAvailable || !API_BASE) {
    updateDesktopAgentMessage("请先通过“启动完整版本.command”或“启动Screen Agent演示.command”打开网站。", "error");
    return;
  }
  if (button) {
    button.disabled = true;
    button.textContent = "正在启动...";
  }
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/screen-agent/run`, {
      method: "POST"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Browser Use 启动失败");
    application.desktopScreenAgent = {
      jobId: data.jobId,
      state: data.state || "starting",
      message: data.message || "Browser Use 正在启动可见 Chrome",
      targetUrl: data.targetUrl || "",
      completedFields: 0,
      totalFields: data.totalFields || 0,
      importedLogs: [],
      startedAt: new Date().toISOString()
    };
    addScreenAgentLog(application, "info", "已将 Travel 页字段计划交给 Browser Use");
    await saveApplication(application);
    updateDesktopScreenAgentUI(application);
    startDesktopAgentPolling(application, plan);
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "重新启动 Browser Use";
    }
    updateDesktopAgentMessage(error.message || "桌面 Agent 启动失败", "error");
  }
}

function startDesktopAgentPolling(application, plan) {
  clearDesktopAgentPolling();
  refreshDesktopScreenAgent(application, plan);
  state.desktopAgentTimer = window.setInterval(() => {
    refreshDesktopScreenAgent(application, plan);
  }, 1400);
}

async function refreshDesktopScreenAgent(application, plan) {
  const desktop = application.desktopScreenAgent;
  if (!desktop?.jobId || !state.apiAvailable || !API_BASE) return;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/screen-agent/${encodeURIComponent(desktop.jobId)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "桌面 Agent 状态读取失败");
    desktop.state = data.state || desktop.state;
    desktop.message = data.message || desktop.message;
    desktop.completedFields = data.completedFields || 0;
    desktop.totalFields = data.totalFields || desktop.totalFields || 0;
    desktop.targetUrl = data.targetUrl || desktop.targetUrl || "";
    desktop.importedLogs = Array.isArray(desktop.importedLogs) ? desktop.importedLogs : [];
    (data.logs || []).forEach((item) => {
      const key = `${item.at || ""}|${item.type || ""}|${item.message || ""}`;
      if (desktop.importedLogs.includes(key)) return;
      desktop.importedLogs.push(key);
      addScreenAgentLog(application, item.type || "info", `Browser Use：${item.message || "状态已更新"}`);
    });
    desktop.importedLogs = desktop.importedLogs.slice(-120);
    updateDesktopScreenAgentUI(application);
    if (["review_required", "handoff", "blocked", "stopped"].includes(desktop.state)) {
      clearDesktopAgentPolling();
      await saveApplication(application);
    }
  } catch (error) {
    clearDesktopAgentPolling();
    updateDesktopAgentMessage(error.message || "桌面 Agent 状态读取失败", "error");
  }
}

async function stopDesktopScreenAgent(application, plan) {
  const desktop = application.desktopScreenAgent;
  if (!desktop?.jobId || !state.apiAvailable || !API_BASE) return;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/screen-agent/${encodeURIComponent(desktop.jobId)}/stop`, {
      method: "POST"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "急停失败");
    desktop.state = "stopped";
    desktop.message = "Browser Use 已由顾问停止";
    addScreenAgentLog(application, "warning", "顾问停止了 Browser Use 会话");
    clearDesktopAgentPolling();
    await saveApplication(application);
    updateDesktopScreenAgentUI(application);
  } catch (error) {
    updateDesktopAgentMessage(error.message || "停止失败，请关闭 Browser Use 打开的 Chrome 窗口", "error");
  }
}

function updateDesktopScreenAgentUI(application) {
  const desktop = application.desktopScreenAgent || { state: "idle" };
  const canLaunch = Boolean(state.apiAvailable && state.screenAgentRuntime?.available);
  const meta = desktopAgentStatusMeta(desktop.state);
  const badge = document.querySelector("#desktopAgentStatus");
  if (badge) {
    badge.className = `badge ${meta.badge}`;
    badge.textContent = meta.label;
  }
  const progress = desktop.totalFields
    ? Math.round(((desktop.completedFields || 0) / desktop.totalFields) * 100)
    : 0;
  const bar = document.querySelector("#desktopAgentProgress");
  if (bar) bar.style.width = `${progress}%`;
  ["#screenAgentProgressText", "#screenAgentProgressValue"].forEach((selector) => {
    const target = document.querySelector(selector);
    if (target) target.textContent = `${progress}%`;
  });
  const liveDot = document.querySelector(".browser-use-banner .agent-live-dot");
  if (liveDot) {
    liveDot.classList.toggle(
      "active",
      ["starting", "waiting_for_travel_page", "running"].includes(desktop.state)
    );
  }
  const flowStep = browserUseFlowStep(desktop.state);
  document.querySelectorAll("[data-browser-use-step]").forEach((item) => {
    const index = Number(item.dataset.browserUseStep);
    item.classList.toggle("completed", index < flowStep);
    item.classList.toggle("active", index === flowStep);
    const marker = item.querySelector("[data-browser-use-step-marker]");
    if (marker) marker.innerHTML = index < flowStep ? iconCheck() : String(index + 1);
  });
  updateDesktopAgentMessage(desktop.message || "等待启动 Browser Use");
  const active = ["starting", "checking_permissions", "opening", "waiting_for_travel_page", "running", "review_required"].includes(desktop.state);
  const launch = document.querySelector("#launchDesktopScreenAgent");
  const stop = document.querySelector("#stopDesktopScreenAgent");
  if (launch) {
    launch.disabled = active || !canLaunch;
    launch.textContent = !canLaunch
      ? "请从 Finder 启动"
      : desktop.state === "blocked"
      ? "重新启动受控 Chrome"
      : desktop.state === "review_required"
        ? "等待人工核对"
        : active
          ? "Browser Use 运行中"
          : "启动受控 Chrome 并填写";
  }
  if (stop) stop.disabled = !active;
  const logs = document.querySelector("#screenAgentLogs");
  if (logs) {
    logs.innerHTML = renderScreenAgentLogs(application.screenAgent?.logs || []);
    logs.scrollTop = logs.scrollHeight;
  }
}

function updateDesktopAgentMessage(message, type = "") {
  const target = document.querySelector("#desktopAgentMessage");
  if (!target) return;
  target.textContent = message;
  target.className = type;
}

function stopScreenAgentRuntime() {
  state.screenAgentRunId += 1;
  state.screenAgentRunning = false;
}

function screenAgentWait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function moveScreenAgentCursor(target, runId) {
  if (!target || runId !== state.screenAgentRunId) return false;
  target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  await screenAgentWait(360);
  if (runId !== state.screenAgentRunId) return false;
  const cursor = document.querySelector("#agentScreenCursor");
  const stage = document.querySelector(".agent-browser-stage");
  if (!cursor || !stage) return false;
  const targetRect = target.getBoundingClientRect();
  const stageRect = stage.getBoundingClientRect();
  const x = Math.max(18, Math.min(stageRect.width - 30, targetRect.left - stageRect.left + Math.min(42, targetRect.width * 0.5)));
  const y = Math.max(18, Math.min(stageRect.height - 30, targetRect.top - stageRect.top + Math.min(24, targetRect.height * 0.5)));
  cursor.classList.add("visible");
  cursor.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  await screenAgentWait(280);
  return runId === state.screenAgentRunId;
}

async function typeScreenAgentValue(target, value, runId) {
  target.value = "";
  target.closest(".mock-ds-field")?.classList.add("agent-active");
  target.focus({ preventScroll: true });
  const frameCount = Math.min(28, Math.max(6, value.length));
  for (let frame = 1; frame <= frameCount; frame += 1) {
    if (runId !== state.screenAgentRunId) return false;
    target.value = value.slice(0, Math.ceil((frame / frameCount) * value.length));
    target.dispatchEvent(new Event("input", { bubbles: true }));
    await screenAgentWait(22);
  }
  target.closest(".mock-ds-field")?.classList.remove("agent-active");
  target.closest(".mock-ds-field")?.classList.add("agent-complete");
  return true;
}

async function startScreenAgent(application, plan) {
  const agent = ensureScreenAgentState(application);
  if (["handoff", "completed"].includes(agent.status) || state.screenAgentRunning) return;
  if (!agent.startedAt) {
    agent.startedAt = new Date().toISOString();
    addScreenAgentLog(application, "info", "Screen Observer 已读取当前 Mock DS-160 视图");
    addScreenAgentLog(application, "info", `DS-160 Mapper 已生成 ${plan.filter((action) => action.type === "fill").length} 个可执行字段动作`);
  } else {
    addScreenAgentLog(application, "info", "人工已允许 Agent 从暂停位置继续");
  }
  agent.status = "running";
  agent.pausedReason = "";
  state.screenAgentRunning = true;
  state.screenAgentRunId += 1;
  const runId = state.screenAgentRunId;
  updateScreenAgentUI(application, plan);
  await saveApplication(application);

  while (agent.actionIndex < plan.length && runId === state.screenAgentRunId) {
    const action = plan[agent.actionIndex];
    const currentAction = document.querySelector("#screenAgentCurrentAction");
    if (currentAction) currentAction.textContent = action.type === "fill" ? `正在定位 ${action.label}` : action.label;

    if (action.type === "navigate") {
      const tab = document.querySelector(`[data-agent-section-tab="${screenAgentSelectorValue(action.section)}"]`);
      document.querySelectorAll("[data-agent-section-tab]").forEach((item) => item.classList.toggle("active", item === tab));
      if (!await moveScreenAgentCursor(tab, runId)) return;
      tab?.classList.add("agent-clicked");
      await screenAgentWait(180);
      tab?.classList.remove("agent-clicked");
      document.querySelector(`[data-agent-section="${screenAgentSelectorValue(action.section)}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      addScreenAgentLog(application, "info", action.label);
    }

    if (action.type === "fill") {
      const target = document.querySelector(`[data-agent-field="${screenAgentSelectorValue(action.fieldId)}"]`);
      if (!await moveScreenAgentCursor(target, runId)) return;
      if (!await typeScreenAgentValue(target, action.value, runId)) return;
      agent.filledValues[action.fieldId] = action.value;
      addScreenAgentLog(application, "success", `已填写 ${action.label}`, { fieldId: action.fieldId });
    }

    if (action.type === "gate") {
      const gate = document.querySelector("#agentSafetyGate");
      document.querySelectorAll("[data-agent-section-tab]").forEach((item) => item.classList.toggle("active", item.dataset.agentSectionTab === "security"));
      await moveScreenAgentCursor(gate, runId);
      agent.status = "handoff";
      agent.pausedReason = action.reason;
      agent.actionIndex += 1;
      addScreenAgentLog(application, "warning", "Safety Guard 已阻止自动填写安全与背景问题");
      addScreenAgentLog(application, "warning", "已请求顾问人工接管；未触碰声明、付款或最终提交");
      state.screenAgentRunning = false;
      await saveApplication(application);
      updateScreenAgentUI(application, plan);
      return;
    }

    agent.actionIndex += 1;
    await saveApplication(application);
    updateScreenAgentUI(application, plan);
    await screenAgentWait(180);
  }
  state.screenAgentRunning = false;
}

function pauseScreenAgent(application, plan) {
  if (!state.screenAgentRunning) return;
  stopScreenAgentRuntime();
  const agent = ensureScreenAgentState(application);
  agent.status = "paused";
  agent.pausedReason = "已由顾问暂停，点击继续运行可从当前动作恢复";
  addScreenAgentLog(application, "warning", "顾问暂停了 Agent 执行");
  saveApplication(application);
  updateScreenAgentUI(application, plan);
}

function resetScreenAgent(application, container) {
  stopScreenAgentRuntime();
  application.screenAgent = {
    sessionId: `agent-${Date.now().toString(36)}`,
    status: "idle",
    actionIndex: 0,
    filledValues: {},
    logs: [],
    pausedReason: "",
    startedAt: "",
    completedAt: ""
  };
  application.prefillLog = [];
  saveApplication(application);
  renderPrefill(container);
}

async function completeScreenAgentHandoff(application, plan) {
  const agent = ensureScreenAgentState(application);
  if (agent.status !== "handoff") return;
  agent.status = "completed";
  agent.completedAt = new Date().toISOString();
  agent.pausedReason = "人工接管演示已完成；本地 Agent 未执行最终提交";
  addScreenAgentLog(application, "success", "顾问已接管敏感问题，Screen Agent 会话安全结束");
  await saveApplication(application);
  updateScreenAgentUI(application, plan);
}

function updateScreenAgentUI(application, plan) {
  const agent = ensureScreenAgentState(application);
  const status = screenAgentStatusMeta(agent.status);
  const progress = plan.length ? Math.round((agent.actionIndex / plan.length) * 100) : 0;
  const badge = document.querySelector("#screenAgentStatusBadge");
  if (badge) {
    badge.className = `badge ${status.badge}`;
    badge.textContent = status.label;
  }
  const progressText = document.querySelector("#screenAgentProgressText");
  const progressValue = document.querySelector("#screenAgentProgressValue");
  const progressBar = document.querySelector("#screenAgentProgressBar");
  if (progressText) progressText.textContent = `${progress}%`;
  if (progressValue) progressValue.textContent = `${progress}%`;
  if (progressBar) progressBar.style.width = `${progress}%`;
  const currentAction = document.querySelector("#screenAgentCurrentAction");
  if (currentAction && agent.status !== "running") currentAction.textContent = agent.pausedReason || "等待启动 Agent";
  const logs = document.querySelector("#screenAgentLogs");
  if (logs) {
    logs.innerHTML = renderScreenAgentLogs(agent.logs);
    logs.scrollTop = logs.scrollHeight;
  }
  const startButton = document.querySelector("#startScreenAgent");
  if (startButton) {
    startButton.disabled = state.screenAgentRunning || ["handoff", "completed"].includes(agent.status);
    startButton.textContent = agent.status === "paused" ? "继续界面演示" : agent.status === "completed" ? "演示已完成" : "播放界面内演示";
  }
  const pauseButton = document.querySelector("#pauseScreenAgent");
  if (pauseButton) pauseButton.disabled = !state.screenAgentRunning;
  const handoffButton = document.querySelector("#confirmAgentHandoff");
  if (handoffButton) handoffButton.hidden = agent.status !== "handoff";
  document.querySelector(".agent-live-dot")?.classList.toggle("active", agent.status === "running");
  document.querySelectorAll("[data-agent-field]").forEach((input) => {
    const value = agent.filledValues[input.dataset.agentField] || "";
    if (value && input.value !== value) input.value = value;
    input.closest(".mock-ds-field")?.classList.toggle("agent-complete", Boolean(value));
  });
}

function renderReport(container) {
  const application = getActiveApplication();
  buildAuditReport(application);
  saveApplication(application);
  const report = application.auditReport;
  container.innerHTML = `
    ${renderAppHeader(application, "DS-160 核查清单", "供文案老师、签证顾问和二审人员复核的本地清单，覆盖资料来源、已确认字段、待补充项和安全边界。")}
    <section class="grid two">
      <article class="panel report-section">
        <h2>客户档案摘要</h2>
        <div class="field-pair"><span class="field-label">客户姓名</span><span>${escapeHtml(report.applicantName)}</span></div>
        <div class="field-pair"><span class="field-label">签证类型</span><span>${escapeHtml(report.visaType)}</span></div>
        <div class="field-pair"><span class="field-label">状态</span><span><span class="badge confirmed">Ready for manual review</span> <span class="badge pending">Not submitted</span></span></div>
      </article>
      ${renderReportList("档案与负责人信息", report.caseSummary)}
      ${renderReportList("已承接材料", report.uploadedDocuments)}
      ${renderReportList("DS-160 初稿字段", report.draftFields)}
      ${renderReportList("已确认或系统校验字段", report.confirmedFields)}
      ${renderReportList("文案老师编辑过的信息", report.editedFields)}
      ${renderReportList("待客户补充信息", report.missingFields)}
      ${renderReportList("DS-160 条件问答", report.branchAnswers)}
      ${renderReportList("已核查冲突项", report.resolvedConflicts)}
      ${renderReportList("需顾问确认的安全背景问题", report.unresolvedSensitiveQuestions)}
      ${renderReportList("Agent 处理日志", report.agentProcessingLog)}
      ${renderReportList("安全与使用边界", report.safetyBoundaries)}
    </section>
    <div class="actions" style="margin-top:18px">
      <button class="btn secondary" id="saveProgress">保存客户档案</button>
      <button class="btn secondary" id="exportPdf">导出 PDF</button>
      <button class="btn secondary" id="downloadJson">下载 JSON</button>
      <button class="btn secondary" id="backDashboard">返回工作台</button>
    </div>
    <div class="inline-notice" id="reportNotice" role="status"></div>
  `;
  document.querySelector("#saveProgress").addEventListener("click", () => {
    saveApplication(application);
    const button = document.querySelector("#saveProgress");
    button.textContent = "已保存";
    setTimeout(() => {
      if (document.querySelector("#saveProgress")) button.textContent = "保存客户档案";
    }, 1400);
  });
  document.querySelector("#exportPdf").addEventListener("click", async () => {
    const button = document.querySelector("#exportPdf");
    button.disabled = true;
    button.textContent = "正在生成 PDF…";
    try {
      await saveApplication(application);
      await exportAuditReportPdf(application);
      button.textContent = "PDF 已导出";
      showReportNotice("PDF 已生成，包含当前客户档案、上传材料、DS-160 初稿字段和待处理事项。", "success");
    } catch (error) {
      button.disabled = false;
      button.textContent = "导出 PDF";
      showReportNotice(error.message || "PDF 生成失败，请稍后重试。", "error");
    }
  });
  document.querySelector("#downloadJson").addEventListener("click", () => downloadCaseJson(application));
  document.querySelector("#backDashboard").addEventListener("click", () => route("dashboard"));
}

function appointmentVisaClass(application) {
  const id = visaByName(application.visaType).id;
  return { b1b2: "B1/B2", f1: "F-1", f2: "F-2", j1: "J-1", j2: "J-2" }[id] || "";
}

function appointmentPostCategory(application) {
  const id = visaByName(application.visaType).id;
  return {
    b1b2: "BUSINESS / TOURISM",
    f1: "STUDENTS - OTHER STUDENTS",
    f2: "STUDENTS - DEPENDENTS",
    j1: "EXCHANGE VISITORS",
    j2: "EXCHANGE VISITOR DEPENDENTS"
  }[id] || "";
}

function appointmentSourceField(application, fieldId, preferOriginal = false) {
  const field = (application.extractedFields || []).find((item) => item.id === fieldId);
  if (!field) return "";
  const value = preferOriginal && field.originalValue ? field.originalValue : field.value;
  return String(value || "").trim();
}

function splitAppointmentPhone(rawValue) {
  const normalized = String(rawValue || "").replace(/[\s()-]/g, "").trim();
  const code = APPOINTMENT_PHONE_CODES.find((item) => normalized.startsWith(item.value));
  return {
    countryCode: code?.value || "+86",
    number: code ? normalized.slice(code.value.length) : normalized.replace(/^00?86/, "")
  };
}

function appointmentOptionButtons(options, selectedValue, dataAttribute, ariaLabel) {
  return `
    <div class="appointment-choice-grid" role="listbox" aria-label="${escapeHtml(ariaLabel)}">
      ${options.map((option) => `
        <button type="button" role="option" aria-selected="${selectedValue === option.value}" class="appointment-choice ${selectedValue === option.value ? "selected" : ""}" ${dataAttribute}="${escapeHtml(option.value)}">
          <strong>${escapeHtml(option.label)}</strong>
          <small>${escapeHtml(option.detail)}</small>
        </button>
      `).join("")}
    </div>
  `;
}

function ensureAppointmentPreparation(application) {
  const current = application.appointmentPreparation || {};
  const sourcePhone = splitAppointmentPhone(screenAgentFieldValue(application, "contact.primaryPhone"));
  const sourceEmail = screenAgentFieldValue(application, "contact.email") || application.email || "";
  application.appointmentPreparation = {
    accountReady: false,
    accountReadyAt: "",
    accountRegistrationOpenedAt: "",
    portalUsername: "",
    accountSecurityComplete: false,
    ds160ConfirmationNumber: "",
    schedulingEmail: sourceEmail,
    contactEmail: sourceEmail,
    preferredLanguage: "zh-CN",
    countryOfApplication: "CHINA",
    countryOfBirth: screenAgentFieldValue(application, "personal.birthCountry"),
    homePhoneCountryCode: "+86",
    homePhone: "",
    mobilePhoneCountryCode: sourcePhone.countryCode,
    mobilePhone: sourcePhone.number,
    primaryPhone: screenAgentFieldValue(application, "contact.primaryPhone"),
    mailingStreet: appointmentSourceField(application, "contact.homeStreet1", true)
      || appointmentSourceField(application, "contact.homeAddress", true),
    mailingCity: appointmentSourceField(application, "contact.homeCity", true),
    mailingState: appointmentSourceField(application, "contact.homeRegion", true),
    mailingPostalCode: appointmentSourceField(application, "contact.homePostalCode", true),
    applicationLocation: "",
    postVisaCategory: appointmentPostCategory(application),
    visaPriority: "REGULAR",
    sevisId: screenAgentFieldValue(application, "education.sevisId"),
    schoolName: screenAgentFieldValue(application, "education.schoolName"),
    schoolZipCode: screenAgentFieldValue(application, "education.schoolPostalCode"),
    deliveryOption: "",
    deliveryStreet1: appointmentSourceField(application, "contact.homeStreet1", true)
      || appointmentSourceField(application, "contact.homeAddress", true),
    deliveryStreet2: appointmentSourceField(application, "contact.homeStreet2", true),
    deliveryStreet3: "",
    deliveryCity: appointmentSourceField(application, "contact.homeCity", true),
    deliveryState: appointmentSourceField(application, "contact.homeRegion", true),
    deliveryPostalCode: appointmentSourceField(application, "contact.homePostalCode", true),
    pickupLocation: "",
    paymentMethodPreference: "ALIPAY_EWALLET",
    legacyReceiptAvailable: false,
    legacyReceiptReference: "",
    dependents: [],
    ...current,
    dependents: Array.isArray(current.dependents) ? current.dependents : []
  };
  const preparation = application.appointmentPreparation;
  const knownFallbacks = {
    schedulingEmail: sourceEmail,
    contactEmail: sourceEmail,
    countryOfBirth: screenAgentFieldValue(application, "personal.birthCountry"),
    mobilePhone: sourcePhone.number,
    primaryPhone: screenAgentFieldValue(application, "contact.primaryPhone"),
    mailingStreet: appointmentSourceField(application, "contact.homeStreet1", true)
      || appointmentSourceField(application, "contact.homeAddress", true),
    mailingCity: appointmentSourceField(application, "contact.homeCity", true),
    mailingState: appointmentSourceField(application, "contact.homeRegion", true),
    mailingPostalCode: appointmentSourceField(application, "contact.homePostalCode", true),
    postVisaCategory: appointmentPostCategory(application),
    sevisId: screenAgentFieldValue(application, "education.sevisId"),
    schoolName: screenAgentFieldValue(application, "education.schoolName"),
    schoolZipCode: screenAgentFieldValue(application, "education.schoolPostalCode")
  };
  Object.entries(knownFallbacks).forEach(([key, value]) => {
    if (!String(preparation[key] || "").trim() && String(value || "").trim()) {
      preparation[key] = value;
    }
  });
  if (!String(preparation.deliveryStreet1 || "").trim()) preparation.deliveryStreet1 = preparation.mailingStreet || "";
  if (!String(preparation.deliveryCity || "").trim()) preparation.deliveryCity = preparation.mailingCity || "";
  if (!String(preparation.deliveryState || "").trim()) preparation.deliveryState = preparation.mailingState || "";
  if (!String(preparation.deliveryPostalCode || "").trim()) preparation.deliveryPostalCode = preparation.mailingPostalCode || "";
  return preparation;
}

function renderAppointmentAccount(container) {
  const application = getActiveApplication();
  const preparation = ensureAppointmentPreparation(application);
  const accountReady = Boolean(preparation.accountReady);
  const suggestedEmail = preparation.schedulingEmail || application.email || "";
  const firstName = screenAgentFieldValue(application, "personal.givenNames");
  const lastName = screenAgentFieldValue(application, "personal.surname");
  container.innerHTML = `
    ${renderAppHeader(application, "预约账号准备", "这一页仅供机构顾问使用。客户问卷不会询问预约用户名、联系邮箱、验证码或密保设置。")}
    <section class="appointment-account-intro">
      <div>
        <span class="page-kicker">Consultant Only</span>
        <h2>开户注册信息由顾问准备</h2>
        <p>护照英文姓名会从客户档案自动带入。预约用户名、注册邮箱、联系邮箱和官网安全步骤由机构顾问补充，不再发送给客户填写。</p>
      </div>
      <a class="btn appointment-official-link" id="openUsTravelDocs" href="${US_TRAVEL_DOCS_URL}" target="_blank" rel="noopener noreferrer">
        开始注册预约账户 ${iconExternalLink()}
      </a>
    </section>

    <section class="appointment-account-layout">
      <div class="appointment-account-process">
        <div class="section-heading-row">
          <div><span class="page-kicker">Account Flow</span><h2>官网开户顺序</h2></div>
          <span class="badge ${accountReady ? "confirmed" : "pending"}">${accountReady ? "账户已准备" : "等待开户注册"}</span>
        </div>
        <ol class="appointment-account-steps">
          <li><span>1</span><div><strong>进入官方预约系统</strong><p>在 USTravelDocs 选择中国、非移民签证并进入 Sign up。</p></div></li>
          <li><span>2</span><div><strong>设置用户名与注册邮箱</strong><p>用户名不是邮箱；邮箱用于接收 Microsoft 发送的验证代码。</p></div></li>
          <li><span>3</span><div><strong>完成账户安全设置</strong><p>密码、邮箱验证码和 3 个密保问题由顾问在官网完成，WestoryVisa 只保存完成状态。</p></div></li>
          <li><span>4</span><div><strong>建立个人档案</strong><p>核对 First Name、Last Name，并填写 Contact Email、Preferred Language 与 Country。</p></div></li>
          <li><span>5</span><div><strong>开始新申请</strong><p>进入 Applicant Details 后，WestoryVisa 再带入客户资料和预约选项。</p></div></li>
        </ol>
        <div class="appointment-known-data" aria-label="已从客户档案读取的账户姓名">
          <div><span>First Name</span><strong>${firstName ? escapeHtml(firstName) : "待客户档案补齐"}</strong></div>
          <div><span>Last Name</span><strong>${lastName ? escapeHtml(lastName) : "待客户档案补齐"}</strong></div>
          <p>姓名必须与护照完全一致，进入预约系统后通常不能直接修改。</p>
        </div>
      </div>

      <aside class="appointment-account-confirmation">
        <span class="page-kicker">Consultant Input</span>
        <h2>顾问补充的账号资料</h2>
        <p>以下内容只出现在机构工作台，不会进入客户补充问卷。共同办理的家属仍需各自的 DS-160 确认号。</p>
        <form id="appointmentAccountForm">
          <div class="form-row">
            <label for="appointmentPortalUsername">预约系统用户名</label>
            <input id="appointmentPortalUsername" required autocomplete="off" value="${escapeHtml(preparation.portalUsername || "")}" placeholder="不能使用邮箱地址">
            <small>用于官网后续登录，可使用容易区分客户的字母或拼音组合。</small>
          </div>
          <div class="form-row">
            <label for="appointmentAccountEmail">注册邮箱 / Primary Email</label>
            <input id="appointmentAccountEmail" type="email" required autocomplete="email" value="${escapeHtml(suggestedEmail)}" placeholder="name@example.com">
          </div>
          <div class="form-row">
            <label for="appointmentContactEmail">联系邮箱 / Contact Email</label>
            <input id="appointmentContactEmail" type="email" required autocomplete="email" value="${escapeHtml(preparation.contactEmail || suggestedEmail)}" placeholder="可与注册邮箱相同">
          </div>
          <div class="grid two appointment-account-profile-grid">
            <div class="form-row">
              <label for="appointmentPreferredLanguage">系统界面语言</label>
              <select id="appointmentPreferredLanguage">
                ${APPOINTMENT_LANGUAGES.map((item) => `<option value="${item.value}" ${preparation.preferredLanguage === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
              </select>
            </div>
            <div class="form-row">
              <label for="appointmentCountryOfApplication">递交国家</label>
              <input id="appointmentCountryOfApplication" value="CHINA" readonly aria-readonly="true">
            </div>
          </div>
          <label class="appointment-account-check">
            <input id="appointmentAccountSecurityComplete" type="checkbox" ${preparation.accountSecurityComplete ? "checked" : ""}>
            <span>${iconCheck()}</span>
            <strong>密码、邮箱验证码和 3 个密保问题已由顾问在官网完成</strong>
          </label>
          <label class="appointment-account-check">
            <input id="appointmentAccountReady" type="checkbox" ${accountReady ? "checked" : ""}>
            <span>${iconCheck()}</span>
            <strong>我已创建账户，或已有能够正常登录的预约账户</strong>
          </label>
          <div class="inline-notice" id="appointmentAccountNotice" role="status"></div>
          <button class="btn" type="submit">${accountReady ? "进入预约资料" : "确认账户并继续"}</button>
        </form>
      </aside>
    </section>

    <section class="appointment-account-safety" role="note">
      <strong>为什么不保存密码和密保答案</strong>
      <span>这些内容属于预约账户凭据。WestoryVisa 只记录“顾问已完成”，不读取密码、验证码或密保答案，也不会把它们写入客户档案或数据库。</span>
    </section>
    <div class="actions appointment-footer-actions">
      <button class="btn secondary" type="button" id="backAccountReport">返回核查清单</button>
      <button class="btn secondary" type="button" id="backAccountDashboard">返回工作台</button>
    </div>
  `;

  const captureAccountPreparation = () => {
    preparation.portalUsername = document.querySelector("#appointmentPortalUsername")?.value.trim() || "";
    preparation.schedulingEmail = document.querySelector("#appointmentAccountEmail")?.value.trim() || "";
    preparation.contactEmail = document.querySelector("#appointmentContactEmail")?.value.trim() || "";
    preparation.preferredLanguage = document.querySelector("#appointmentPreferredLanguage")?.value || "zh-CN";
    preparation.countryOfApplication = "CHINA";
    preparation.accountSecurityComplete = Boolean(document.querySelector("#appointmentAccountSecurityComplete")?.checked);
    preparation.accountReady = Boolean(document.querySelector("#appointmentAccountReady")?.checked);
  };
  document.querySelector("#openUsTravelDocs")?.addEventListener("click", () => {
    captureAccountPreparation();
    preparation.accountRegistrationOpenedAt = new Date().toISOString();
    saveApplication(application);
  });
  document.querySelector("#appointmentAccountForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    captureAccountPreparation();
    const username = preparation.portalUsername;
    const email = preparation.schedulingEmail;
    const contactEmail = preparation.contactEmail;
    const ready = preparation.accountReady;
    const notice = document.querySelector("#appointmentAccountNotice");
    if (username.length < 3 || username.includes("@")) {
      notice.textContent = "请填写至少 3 个字符且不含 @ 的预约系统用户名。";
      notice.className = "inline-notice visible error";
      document.querySelector("#appointmentPortalUsername")?.focus();
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      notice.textContent = "请填写可用于注册预约账户的有效邮箱。";
      notice.className = "inline-notice visible error";
      document.querySelector("#appointmentAccountEmail")?.focus();
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contactEmail)) {
      notice.textContent = "请填写个人档案中的有效联系邮箱。";
      notice.className = "inline-notice visible error";
      document.querySelector("#appointmentContactEmail")?.focus();
      return;
    }
    if (!preparation.accountSecurityComplete) {
      notice.textContent = "请先由顾问在官网完成密码、邮箱验证和密保问题设置。";
      notice.className = "inline-notice visible error";
      document.querySelector("#appointmentAccountSecurityComplete")?.focus();
      return;
    }
    if (!ready) {
      notice.textContent = "请先在官网完成开户注册或确认已有账户。";
      notice.className = "inline-notice visible error";
      document.querySelector("#appointmentAccountReady")?.focus();
      return;
    }
    preparation.accountReadyAt = preparation.accountReadyAt || new Date().toISOString();
    application.currentStep = Math.max(application.currentStep || 0, 9);
    await saveApplication(application);
    route("appointment");
  });
  document.querySelector("#backAccountReport")?.addEventListener("click", () => route("report"));
  document.querySelector("#backAccountDashboard")?.addEventListener("click", () => route("dashboard"));
}

function appointmentIdentityRows(application) {
  return [
    ["护照英文姓", screenAgentFieldValue(application, "personal.surname")],
    ["护照英文名", screenAgentFieldValue(application, "personal.givenNames")],
    ["出生日期", screenAgentFieldValue(application, "personal.dateOfBirth")],
    ["出生国家", screenAgentFieldValue(application, "personal.birthCountry")],
    ["当前国籍", screenAgentFieldValue(application, "personal.nationality")],
    ["护照号码", screenAgentFieldValue(application, "passport.number")],
    ["护照有效期", screenAgentFieldValue(application, "passport.expiration")],
    ["签证类别", appointmentVisaClass(application)]
  ];
}

function appointmentPreflightIssues(application) {
  const preparation = ensureAppointmentPreparation(application);
  const required = [
    ["appointmentAccount", "可用的预约账户", preparation.accountReady],
    ["portalUsername", "预约系统用户名", preparation.portalUsername],
    ["personal.surname", "护照英文姓", screenAgentFieldValue(application, "personal.surname")],
    ["personal.givenNames", "护照英文名", screenAgentFieldValue(application, "personal.givenNames")],
    ["personal.dateOfBirth", "出生日期", screenAgentFieldValue(application, "personal.dateOfBirth")],
    ["countryOfBirth", "出生国家", preparation.countryOfBirth],
    ["personal.nationality", "当前国籍", screenAgentFieldValue(application, "personal.nationality")],
    ["passport.number", "护照号码", screenAgentFieldValue(application, "passport.number")],
    ["ds160ConfirmationNumber", "DS-160 确认号", preparation.ds160ConfirmationNumber],
    ["schedulingEmail", "注册邮箱", preparation.schedulingEmail],
    ["contactEmail", "联系邮箱", preparation.contactEmail],
    ["preferredLanguage", "系统界面语言", preparation.preferredLanguage],
    ["countryOfApplication", "递交国家", preparation.countryOfApplication],
    ["homePhone", "家庭电话", preparation.homePhone],
    ["mobilePhone", "手机号码", preparation.mobilePhone],
    ["mailingStreet", "中文邮寄街道地址", preparation.mailingStreet],
    ["mailingCity", "中文邮寄城市", preparation.mailingCity],
    ["mailingState", "中文邮寄省份", preparation.mailingState],
    ["mailingPostalCode", "邮寄地址邮编", preparation.mailingPostalCode],
    ["applicationLocation", "使领馆", preparation.applicationLocation],
    ["postVisaCategory", "预约系统签证细类", preparation.postVisaCategory],
    ["visaPriority", "签证优先级", preparation.visaPriority],
    ["deliveryOption", "护照递送方式", preparation.deliveryOption],
    ["visaClass", "签证类别", appointmentVisaClass(application)]
  ];
  if (preparation.deliveryOption === "PREMIUM_DELIVERY") {
    required.push(["deliveryStreet1", "递送街道地址", preparation.deliveryStreet1]);
    required.push(["deliveryCity", "递送城市", preparation.deliveryCity]);
    required.push(["deliveryState", "递送省份", preparation.deliveryState]);
    required.push(["deliveryPostalCode", "递送地址邮编", preparation.deliveryPostalCode]);
  } else if (["PREMIUM_LOCATION", "PICK_UP"].includes(preparation.deliveryOption)) {
    required.push(["pickupLocation", "领取服务点", preparation.pickupLocation]);
  }
  if (["f1", "f2", "j1", "j2"].includes(visaByName(application.visaType).id)) {
    required.push(["sevisId", "SEVIS ID", preparation.sevisId]);
    required.push(["schoolName", "学校或项目名称", preparation.schoolName]);
    required.push(["schoolZipCode", "学校或项目邮编", preparation.schoolZipCode]);
  }
  const issues = required
    .filter(([, , value]) => !String(value || "").trim())
    .map(([id, label]) => ({ id, label }));
  const confirmation = String(preparation.ds160ConfirmationNumber || "").trim();
  if (confirmation && !/^[A-Z0-9]{8,20}$/i.test(confirmation)) {
    issues.push({ id: "ds160ConfirmationNumber", label: "DS-160 确认号格式" });
  }
  const email = String(preparation.schedulingEmail || "").trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    issues.push({ id: "schedulingEmail", label: "有效的注册邮箱" });
  }
  const contactEmail = String(preparation.contactEmail || "").trim();
  if (contactEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contactEmail)) {
    issues.push({ id: "contactEmail", label: "有效的联系邮箱" });
  }
  for (const [id, label, value] of [
    ["homePhone", "家庭电话不含国家代码", preparation.homePhone],
    ["mobilePhone", "手机号码不含国家代码", preparation.mobilePhone]
  ]) {
    if (value && (/^\+|^00/.test(String(value).trim()) || String(value).replace(/\D/g, "").length < 5)) {
      issues.push({ id, label });
    }
  }
  return issues;
}

function captureAppointmentPreparation(application) {
  const preparation = ensureAppointmentPreparation(application);
  const form = document.querySelector("#appointmentPreparationForm");
  if (!form) return preparation;
  preparation.portalUsername = document.querySelector("#appointmentPortalUsernameSummary")?.value.trim() || preparation.portalUsername || "";
  preparation.ds160ConfirmationNumber = document.querySelector("#appointmentDs160")?.value.trim().toUpperCase() || "";
  preparation.schedulingEmail = document.querySelector("#appointmentEmail")?.value.trim() || "";
  preparation.contactEmail = document.querySelector("#appointmentContactEmailSummary")?.value.trim() || "";
  preparation.preferredLanguage = document.querySelector("#appointmentLanguage")?.value || "zh-CN";
  preparation.countryOfApplication = "CHINA";
  preparation.countryOfBirth = document.querySelector("#appointmentBirthCountry")?.value.trim() || "";
  preparation.homePhoneCountryCode = document.querySelector("#appointmentHomePhoneCode")?.value || "+86";
  preparation.homePhone = document.querySelector("#appointmentHomePhone")?.value.trim() || "";
  preparation.mobilePhoneCountryCode = document.querySelector("#appointmentMobilePhoneCode")?.value || "+86";
  preparation.mobilePhone = document.querySelector("#appointmentMobilePhone")?.value.trim() || "";
  preparation.primaryPhone = `${preparation.mobilePhoneCountryCode}${preparation.mobilePhone}`;
  preparation.mailingStreet = document.querySelector("#appointmentMailingStreet")?.value.trim() || "";
  preparation.mailingCity = document.querySelector("#appointmentMailingCity")?.value.trim() || "";
  preparation.mailingState = document.querySelector("#appointmentMailingState")?.value.trim() || "";
  preparation.mailingPostalCode = document.querySelector("#appointmentMailingPostalCode")?.value.trim() || "";
  preparation.applicationLocation = document.querySelector("#appointmentLocation")?.value.trim().toUpperCase() || "";
  preparation.postVisaCategory = document.querySelector("#appointmentPostVisaCategory")?.value.trim().toUpperCase() || "";
  preparation.visaPriority = document.querySelector("#appointmentVisaPriority")?.value || "REGULAR";
  preparation.sevisId = document.querySelector("#appointmentSevis")?.value.trim().toUpperCase() || "";
  preparation.schoolName = document.querySelector("#appointmentSchool")?.value.trim() || "";
  preparation.schoolZipCode = document.querySelector("#appointmentSchoolZip")?.value.trim() || "";
  preparation.deliveryOption = document.querySelector("#appointmentDeliveryOption")?.value || "";
  preparation.deliveryStreet1 = document.querySelector("#appointmentDeliveryStreet1")?.value.trim() || "";
  preparation.deliveryStreet2 = document.querySelector("#appointmentDeliveryStreet2")?.value.trim() || "";
  preparation.deliveryStreet3 = document.querySelector("#appointmentDeliveryStreet3")?.value.trim() || "";
  preparation.deliveryCity = document.querySelector("#appointmentDeliveryCity")?.value.trim() || "";
  preparation.deliveryState = document.querySelector("#appointmentDeliveryState")?.value.trim() || "";
  preparation.deliveryPostalCode = document.querySelector("#appointmentDeliveryPostalCode")?.value.trim() || "";
  preparation.pickupLocation = document.querySelector("#appointmentPickupLocation")?.value.trim() || "";
  preparation.paymentMethodPreference = document.querySelector("#appointmentPaymentPreference")?.value || "ALIPAY_EWALLET";
  preparation.legacyReceiptAvailable = Boolean(document.querySelector("#appointmentLegacyReceiptAvailable")?.checked);
  preparation.legacyReceiptReference = document.querySelector("#appointmentLegacyReceiptReference")?.value.trim() || "";
  preparation.dependents = Array.from(document.querySelectorAll("[data-appointment-dependent]")).map((row) => ({
    firstName: row.querySelector("[data-dependent-first-name]")?.value.trim() || "",
    lastName: row.querySelector("[data-dependent-last-name]")?.value.trim() || "",
    dateOfBirth: row.querySelector("[data-dependent-date-of-birth]")?.value.trim() || "",
    visaClass: row.querySelector("[data-dependent-visa-class]")?.value.trim().toUpperCase() || "",
    passportNumber: row.querySelector("[data-dependent-passport]")?.value.trim().toUpperCase() || "",
    ds160ConfirmationNumber: row.querySelector("[data-dependent-ds160]")?.value.trim().toUpperCase() || "",
    email: row.querySelector("[data-dependent-email]")?.value.trim() || ""
  }));
  return preparation;
}

function appointmentAgentStatusMeta(status) {
  return {
    idle: { label: "尚未连接", badge: "pending" },
    prepared: { label: "任务已准备", badge: "pending" },
    claimed: { label: "Computer Use 已接收", badge: "running" },
    waiting_for_entry: { label: "等待人工登录", badge: "needs-review" },
    running: { label: "正在填写", badge: "running" },
    blocked: { label: "等待人工继续", badge: "needs-review" },
    review_required: { label: "已交回人工", badge: "confirmed" },
    completed: { label: "已完成", badge: "confirmed" },
    failed: { label: "本次未完成", badge: "needs-review" },
    expired: { label: "任务已过期", badge: "needs-review" },
    revoked: { label: "任务已停止", badge: "pending" }
  }[status] || { label: "尚未连接", badge: "pending" };
}

function renderAppointmentDependents(preparation) {
  if (!preparation.dependents.length) {
    return '<p class="empty-copy">当前没有共同预约家属。家属资料只由顾问在这里补充，不会加入客户问卷，也不会自动创建账号。</p>';
  }
  return preparation.dependents.map((dependent, index) => `
    <div class="appointment-dependent" data-appointment-dependent data-dependent-index="${index}">
      <div class="appointment-dependent-title">
        <strong>家属 ${index + 1}</strong>
        <button class="icon-text-btn" type="button" data-remove-dependent="${index}">移除</button>
      </div>
      <div class="grid two">
        <div class="form-row"><label>First Name</label><input data-dependent-first-name value="${escapeHtml(dependent.firstName || dependent.name || "")}" placeholder="护照 Given Names"></div>
        <div class="form-row"><label>Last Name</label><input data-dependent-last-name value="${escapeHtml(dependent.lastName || "")}" placeholder="护照 Surname"></div>
        <div class="form-row"><label>出生日期</label><input type="date" data-dependent-date-of-birth value="${escapeHtml(dependent.dateOfBirth || "")}"></div>
        <div class="form-row"><label>签证类别</label><input data-dependent-visa-class value="${escapeHtml(dependent.visaClass || "")}" placeholder="例如 F-2 / J-2"></div>
        <div class="form-row"><label>护照号码</label><input data-dependent-passport value="${escapeHtml(dependent.passportNumber || "")}"></div>
        <div class="form-row"><label>DS-160 确认号</label><input data-dependent-ds160 value="${escapeHtml(dependent.ds160ConfirmationNumber || "")}"></div>
        <div class="form-row"><label>独立邮箱</label><input type="email" data-dependent-email value="${escapeHtml(dependent.email || "")}" placeholder="每位家属使用独立邮箱"></div>
      </div>
    </div>
  `).join("");
}

function renderAppointmentPreparation(container) {
  const application = getActiveApplication();
  const preparation = ensureAppointmentPreparation(application);
  const agent = application.appointmentAgent || { state: "idle" };
  const meta = appointmentAgentStatusMeta(agent.state);
  const issues = appointmentPreflightIssues(application);
  const closed = ["review_required", "completed", "expired", "revoked"].includes(agent.state) || agent.closed;
  const activeJob = Boolean(agent.jobId && !closed);
  const handoff = state.computerUseHandoffs.appointment;
  const handoffReady = Boolean(activeJob && handoff?.jobId === agent.jobId);
  const agentStarted = ["claimed", "running", "blocked"].includes(agent.state);
  const needsFreshHandoff = Boolean(activeJob && !handoffReady && !agentStarted);
  const canPrepare = state.apiAvailable && preparation.accountReady && !issues.length
    && (!activeJob || needsFreshHandoff);
  const canResume = activeJob && handoffReady && !agentStarted;
  const progress = agent.totalFields
    ? Math.round(((agent.completedFields || 0) / agent.totalFields) * 100)
    : 0;
  const isStudentOrExchange = ["f1", "f2", "j1", "j2"].includes(visaByName(application.visaType).id);

  container.innerHTML = `
    ${renderAppHeader(application, "预约资料准备", "按预约官网真实分组整理资料。系统自动复用客户档案，缺失项只由机构顾问补充。")}
    <section class="appointment-account-statusbar ${preparation.accountReady ? "ready" : "blocked"}">
      <div>
        <span class="page-kicker">Appointment Account</span>
        <strong>${preparation.accountReady ? "预约账户已准备" : "请先完成预约账户注册"}</strong>
        <small>${preparation.accountReady ? escapeHtml(preparation.schedulingEmail || "已确认账户") : "返回上一步打开 USTravelDocs 并完成人工验证。"}</small>
      </div>
      <button class="btn secondary" type="button" id="reviewAppointmentAccount">${preparation.accountReady ? "查看账户入口" : "返回开户注册"}</button>
    </section>
    <section class="appointment-boundary" role="note">
      <div><span class="page-kicker">执行边界</span><h2>资料可复用，关键操作由人工完成</h2></div>
      <div class="appointment-boundary-items">
        <span>${iconCheck()} 自动带入核对过的申请人资料</span>
        <span>${iconCheck()} 顾问补充账号、电话、邮寄与递送信息</span>
        <span class="manual">人工：登录、验证码、缴费、选位、最终预约</span>
      </div>
    </section>

    <section class="appointment-layout">
      <div class="appointment-main">
        <section class="appointment-section">
          <div class="section-heading-row">
            <div><span class="page-kicker">Case Data</span><h2>从客户档案带入</h2></div>
            <button class="btn secondary" type="button" id="reviewAppointmentFields">返回字段核查</button>
          </div>
          <div class="appointment-identity-list">
            ${appointmentIdentityRows(application).map(([label, value]) => `
              <div class="${value ? "" : "missing"}"><span>${escapeHtml(label)}</span><strong>${value ? escapeHtml(value) : "待补充"}</strong></div>
            `).join("")}
          </div>
        </section>

        <form id="appointmentPreparationForm" class="appointment-section appointment-data-form">
          <div class="section-heading-row">
            <div><span class="page-kicker">Consultant Workspace</span><h2>顾问补充的预约资料</h2><p class="muted">这一整组内容不会出现在客户补充问卷中。</p></div>
            <span class="badge ${issues.length ? "needs-review" : "confirmed"}">${issues.length ? `${issues.length} 项待补充` : "资料齐全"}</span>
          </div>

          <section class="appointment-data-group">
            <div class="appointment-data-group-heading"><span>01</span><div><h3>账号与个人档案</h3><p>对应 Profile 页面。护照姓名已在上方自动带入。</p></div></div>
            <div class="grid two">
              <div class="form-row">
                <label for="appointmentPortalUsernameSummary">预约系统用户名</label>
                <input id="appointmentPortalUsernameSummary" value="${escapeHtml(preparation.portalUsername || "")}" placeholder="由顾问设置，不能使用邮箱">
              </div>
              <div class="form-row">
                <label for="appointmentEmail">Primary Email</label>
                <input id="appointmentEmail" type="email" required autocomplete="email" value="${escapeHtml(preparation.schedulingEmail || "")}" placeholder="预约账户注册邮箱">
              </div>
              <div class="form-row">
                <label for="appointmentContactEmailSummary">Contact Email</label>
                <input id="appointmentContactEmailSummary" type="email" required value="${escapeHtml(preparation.contactEmail || preparation.schedulingEmail || "")}" placeholder="可与 Primary Email 相同">
              </div>
              <div class="form-row">
                <label for="appointmentLanguage">Preferred Language</label>
                <select id="appointmentLanguage">
                  ${APPOINTMENT_LANGUAGES.map((item) => `<option value="${item.value}" ${preparation.preferredLanguage === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
                </select>
              </div>
              <div class="form-row">
                <label>Country of Application</label>
                <input value="CHINA" readonly aria-readonly="true">
              </div>
              <div class="form-row">
                <label for="appointmentBirthCountry">Country of Birth</label>
                <input id="appointmentBirthCountry" value="${escapeHtml(preparation.countryOfBirth || "")}" placeholder="例如 CHINA">
              </div>
            </div>
          </section>

          <section class="appointment-data-group">
            <div class="appointment-data-group-heading"><span>02</span><div><h3>联系方式与邮寄地址</h3><p>官网要求家庭电话和手机各一项；号码框内不要重复输入国家代码。</p></div></div>
            <div class="grid two">
              <div class="form-row">
                <label for="appointmentHomePhone">Home Phone</label>
                <div class="appointment-phone-input">
                  <select id="appointmentHomePhoneCode" aria-label="家庭电话国家代码">
                    ${APPOINTMENT_PHONE_CODES.map((item) => `<option value="${item.value}" ${preparation.homePhoneCountryCode === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
                  </select>
                  <input id="appointmentHomePhone" inputmode="tel" value="${escapeHtml(preparation.homePhone || "")}" placeholder="不含 +86">
                </div>
              </div>
              <div class="form-row">
                <label for="appointmentMobilePhone">Mobile Phone</label>
                <div class="appointment-phone-input">
                  <select id="appointmentMobilePhoneCode" aria-label="手机国家代码">
                    ${APPOINTMENT_PHONE_CODES.map((item) => `<option value="${item.value}" ${preparation.mobilePhoneCountryCode === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
                  </select>
                  <input id="appointmentMobilePhone" inputmode="tel" value="${escapeHtml(preparation.mobilePhone || "")}" placeholder="不含 +86">
                </div>
              </div>
              <div class="form-row appointment-field-span-two"><label for="appointmentMailingStreet">Mailing Street · 中文街道地址</label><input id="appointmentMailingStreet" value="${escapeHtml(preparation.mailingStreet || "")}" placeholder="请按预约系统要求填写中文地址"></div>
              <div class="form-row"><label for="appointmentMailingCity">Mailing City</label><input id="appointmentMailingCity" value="${escapeHtml(preparation.mailingCity || "")}"></div>
              <div class="form-row"><label for="appointmentMailingState">Mailing State / Province</label><input id="appointmentMailingState" value="${escapeHtml(preparation.mailingState || "")}"></div>
              <div class="form-row"><label for="appointmentMailingPostalCode">Mailing Zip / Postal Code</label><input id="appointmentMailingPostalCode" inputmode="numeric" value="${escapeHtml(preparation.mailingPostalCode || "")}"></div>
            </div>
          </section>

          <section class="appointment-data-group">
            <div class="appointment-data-group-heading"><span>03</span><div><h3>Visa Options</h3><p>Country 固定为 China，Visa Type 固定为 Non-Immigrant；顾问确认使领馆和系统显示的细类。</p></div></div>
            <div class="grid two">
              <div class="form-row">
                <label>Embassy / Consulate / OFC</label>
                <input id="appointmentLocation" type="hidden" value="${escapeHtml(preparation.applicationLocation || "")}">
                <div class="appointment-location-grid" role="listbox" aria-label="选择使领馆">
                  ${APPOINTMENT_LOCATIONS.map((location) => `
                    <button type="button" role="option" aria-selected="${preparation.applicationLocation === location.value}" class="appointment-location ${preparation.applicationLocation === location.value ? "selected" : ""}" data-appointment-location="${location.value}">
                      <strong>${location.label}</strong><small>${location.detail}</small>
                    </button>
                  `).join("")}
                </div>
              </div>
              <div class="appointment-fixed-values">
                <div><span>Country</span><strong>CHINA</strong></div>
                <div><span>Visa Type</span><strong>NON-IMMIGRANT</strong></div>
                <div><span>Visa Class</span><strong>${escapeHtml(appointmentVisaClass(application))}</strong></div>
              </div>
              <div class="form-row">
                <label for="appointmentPostVisaCategory">Post Visa Category</label>
                <input id="appointmentPostVisaCategory" value="${escapeHtml(preparation.postVisaCategory || appointmentPostCategory(application))}" placeholder="以所选领馆页面实际选项为准">
              </div>
              <div class="form-row">
                <label for="appointmentVisaPriority">Visa Priority</label>
                <select id="appointmentVisaPriority"><option value="REGULAR" ${preparation.visaPriority === "REGULAR" ? "selected" : ""}>Regular</option></select>
              </div>
            </div>
          </section>

          <section class="appointment-data-group">
            <div class="appointment-data-group-heading"><span>04</span><div><h3>DS-160${isStudentOrExchange ? " 与 SEVIS" : " 确认"}</h3><p>确认号必须与面谈预约记录完全一致。</p></div></div>
            <div class="grid two">
              <div class="form-row appointment-field-span-two">
                <label for="appointmentDs160">DS-160 Confirmation Number</label>
                <input id="appointmentDs160" required autocomplete="off" value="${escapeHtml(preparation.ds160ConfirmationNumber || "")}" placeholder="确认页上的字母和数字">
              </div>
              ${isStudentOrExchange ? `
                <div class="form-row"><label for="appointmentSevis">SEVIS ID</label><input id="appointmentSevis" value="${escapeHtml(preparation.sevisId || "")}" placeholder="I-20 / DS-2019 上以 N 开头的编号"></div>
                <div class="form-row"><label for="appointmentSchool">University / Program Name</label><input id="appointmentSchool" value="${escapeHtml(preparation.schoolName || "")}" placeholder="以 I-20 / DS-2019 为准"></div>
                <div class="form-row"><label for="appointmentSchoolZip">University Zip Code</label><input id="appointmentSchoolZip" inputmode="numeric" value="${escapeHtml(preparation.schoolZipCode || "")}"></div>
              ` : ""}
            </div>
          </section>

          <section class="appointment-data-group">
            <div class="appointment-data-group-heading"><span>05</span><div><h3>护照递送方式</h3><p>顾问先确认客户采用快递或自取；付费方式仍需在官网核对价格。</p></div></div>
            <input id="appointmentDeliveryOption" type="hidden" value="${escapeHtml(preparation.deliveryOption || "")}">
            ${appointmentOptionButtons(APPOINTMENT_DELIVERY_OPTIONS, preparation.deliveryOption, "data-appointment-delivery", "选择护照递送方式")}
            <div id="appointmentDeliveryAddressFields" class="grid two appointment-conditional-fields" ${preparation.deliveryOption === "PREMIUM_DELIVERY" ? "" : "hidden"}>
              <div class="form-row appointment-field-span-two"><label for="appointmentDeliveryStreet1">Document Delivery Street</label><input id="appointmentDeliveryStreet1" value="${escapeHtml(preparation.deliveryStreet1 || "")}" placeholder="中文地址第一行"></div>
              <div class="form-row appointment-field-span-two"><label for="appointmentDeliveryStreet2">Document Delivery Street 2 · 可选</label><input id="appointmentDeliveryStreet2" value="${escapeHtml(preparation.deliveryStreet2 || "")}"></div>
              <div class="form-row appointment-field-span-two"><label for="appointmentDeliveryStreet3">Document Delivery Street 3 · 可选</label><input id="appointmentDeliveryStreet3" value="${escapeHtml(preparation.deliveryStreet3 || "")}"></div>
              <div class="form-row"><label for="appointmentDeliveryCity">Document Delivery City</label><input id="appointmentDeliveryCity" value="${escapeHtml(preparation.deliveryCity || "")}"></div>
              <div class="form-row"><label for="appointmentDeliveryState">Document Delivery State</label><input id="appointmentDeliveryState" value="${escapeHtml(preparation.deliveryState || "")}"></div>
              <div class="form-row"><label for="appointmentDeliveryPostalCode">Document Delivery Postal Code</label><input id="appointmentDeliveryPostalCode" inputmode="numeric" value="${escapeHtml(preparation.deliveryPostalCode || "")}"></div>
            </div>
            <div id="appointmentPickupFields" class="appointment-conditional-fields" ${["PREMIUM_LOCATION", "PICK_UP"].includes(preparation.deliveryOption) ? "" : "hidden"}>
              <div class="form-row"><label for="appointmentPickupLocation">领取服务点</label><input id="appointmentPickupLocation" value="${escapeHtml(preparation.pickupLocation || "")}" placeholder="由顾问按官网可选网点填写"></div>
            </div>
          </section>

          <section class="appointment-data-group appointment-manual-group">
            <div class="appointment-data-group-heading"><span>06</span><div><h3>缴费与预约</h3><p>这里只记录顾问的办理偏好，不读取支付账户，也不会选择可预约日期。</p></div></div>
            <div class="grid two">
              <div class="form-row">
                <label for="appointmentPaymentPreference">预期缴费方式</label>
                <select id="appointmentPaymentPreference">
                  ${APPOINTMENT_PAYMENT_OPTIONS.map((item) => `<option value="${item.value}" ${preparation.paymentMethodPreference === item.value ? "selected" : ""}>${escapeHtml(item.label)} · ${escapeHtml(item.detail)}</option>`).join("")}
                </select>
              </div>
              <label class="appointment-account-check appointment-receipt-check">
                <input id="appointmentLegacyReceiptAvailable" type="checkbox" ${preparation.legacyReceiptAvailable ? "checked" : ""}>
                <span>${iconCheck()}</span><strong>已有需要人工绑定的历史缴费收据</strong>
              </label>
              <div class="form-row appointment-field-span-two" id="appointmentLegacyReceiptFields" ${preparation.legacyReceiptAvailable ? "" : "hidden"}>
                <label for="appointmentLegacyReceiptReference">历史收据参考号</label>
                <input id="appointmentLegacyReceiptReference" value="${escapeHtml(preparation.legacyReceiptReference || "")}" placeholder="仅供顾问核对，不包含支付密码或卡片信息">
              </div>
            </div>
            <div class="appointment-manual-timeline">
              <span>人工缴费</span><i></i><span>等待入账</span><i></i><span>人工选择日期与时间</span><i></i><span>下载预约确认函</span>
            </div>
          </section>

          ${issues.length ? `<div class="inline-notice visible error">开始前请补齐：${escapeHtml(issues.map((item) => item.label).join("、"))}</div>` : ""}
          <div class="actions"><button class="btn secondary" type="submit" id="saveAppointmentPreparation">保存预约资料</button></div>
        </form>

        <section class="appointment-section">
          <div class="section-heading-row">
            <div><span class="page-kicker">Dependents</span><h2>共同预约家属</h2><p class="muted">每位家属应有自己的 DS-160 确认号和可用邮箱。</p></div>
            <button class="btn secondary" type="button" id="addAppointmentDependent">添加家属</button>
          </div>
          <div class="appointment-dependent-list">${renderAppointmentDependents(preparation)}</div>
        </section>
      </div>

      <aside class="appointment-agent-panel">
        <div class="agent-console-heading">
          <div><span class="page-kicker">Local Browser</span><h2>预约填写 Agent</h2></div>
          <span class="badge ${meta.badge}" id="appointmentAgentStatus">${meta.label}</span>
        </div>
        <div class="agent-progress-block">
          <div><span>字段进度</span><strong id="appointmentAgentProgressValue">${progress}%</strong></div>
          <div class="progress-track"><div class="progress-fill" id="appointmentAgentProgress" style="width:${progress}%"></div></div>
          <p id="appointmentAgentMessage">${escapeHtml(agent.message || "资料保存后，可准备 Computer Use 任务。")}</p>
        </div>
        <div class="appointment-agent-steps">
          <div><span>1</span><strong>人工登录</strong><small>密码、验证码只在预约网站输入</small></div>
          <div><span>2</span><strong>资料带入</strong><small>Profile、Applicant Details、Visa Options 与 SEVIS</small></div>
          <div><span>3</span><strong>顾问核对</strong><small>联系方式、中文地址与护照递送方式</small></div>
          <div><span>4</span><strong>人工办理</strong><small>缴费、选时间与最终确认</small></div>
        </div>
        <div class="screen-agent-runtime-alert ${state.apiAvailable ? "ready" : "blocked"}">
          <strong>${needsFreshHandoff ? "本机授权已随刷新失效" : "Computer Use 执行通道已就绪"}</strong>
          <span>${needsFreshHandoff ? "请重新准备一次短时任务，不会丢失已保存的预约资料。" : "无需安装 Chrome 扩展。人工登录并进入资料页后，再把当前可见页面交给 Codex Computer Use。"}</span>
        </div>
        <div class="appointment-agent-actions">
          <button class="btn" type="button" id="prepareAppointmentAgent" ${canPrepare ? "" : "disabled"} ${activeJob && !needsFreshHandoff ? "hidden" : ""}>${needsFreshHandoff ? "重新准备 Computer Use 任务" : "准备任务并打开预约网站"}</button>
          <button class="btn" type="button" id="resumeAppointmentAgent" ${canResume ? "" : "disabled"} ${activeJob && handoffReady && !agentStarted ? "" : "hidden"}>${agent.state === "waiting_for_entry" ? "再次复制 Codex 启动指令" : "我已登录，交给 Computer Use"}</button>
          <button class="btn secondary" type="button" id="stopAppointmentAgent" ${activeJob ? "" : "hidden"}>停止任务</button>
        </div>
        <p class="appointment-agent-footnote">不会读取或保存登录密码、短信/邮箱验证码、支付信息，也不会抢号、选时段或确认预约。</p>
      </aside>
    </section>
    <div class="actions appointment-footer-actions">
      <button class="btn secondary" type="button" id="backAppointmentAccount">返回预约账号</button>
      <button class="btn secondary" type="button" id="backAppointmentDashboard">返回工作台</button>
    </div>
  `;

  document.querySelector("#appointmentPreparationForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    captureAppointmentPreparation(application);
    application.currentStep = Math.max(application.currentStep || 0, 9);
    await saveApplication(application);
    render("appointment");
  });
  document.querySelectorAll("[data-appointment-location]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("#appointmentLocation").value = button.dataset.appointmentLocation;
      document.querySelectorAll("[data-appointment-location]").forEach((item) => {
        const selected = item === button;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-selected", String(selected));
      });
    });
  });
  document.querySelectorAll("[data-appointment-delivery]").forEach((button) => {
    button.addEventListener("click", () => {
      const selectedValue = button.dataset.appointmentDelivery;
      document.querySelector("#appointmentDeliveryOption").value = selectedValue;
      document.querySelectorAll("[data-appointment-delivery]").forEach((item) => {
        const selected = item === button;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      document.querySelector("#appointmentDeliveryAddressFields").hidden = selectedValue !== "PREMIUM_DELIVERY";
      document.querySelector("#appointmentPickupFields").hidden = !["PREMIUM_LOCATION", "PICK_UP"].includes(selectedValue);
    });
  });
  document.querySelector("#appointmentLegacyReceiptAvailable")?.addEventListener("change", (event) => {
    document.querySelector("#appointmentLegacyReceiptFields").hidden = !event.target.checked;
  });
  document.querySelector("#addAppointmentDependent")?.addEventListener("click", () => {
    captureAppointmentPreparation(application);
    application.appointmentPreparation.dependents.push({
      firstName: "", lastName: "", dateOfBirth: "", visaClass: "",
      passportNumber: "", ds160ConfirmationNumber: "", email: ""
    });
    render("appointment");
  });
  document.querySelectorAll("[data-remove-dependent]").forEach((button) => {
    button.addEventListener("click", () => {
      captureAppointmentPreparation(application);
      application.appointmentPreparation.dependents.splice(Number(button.dataset.removeDependent), 1);
      render("appointment");
    });
  });
  document.querySelector("#reviewAppointmentFields")?.addEventListener("click", () => route("fields"));
  document.querySelector("#reviewAppointmentAccount")?.addEventListener("click", () => route("appointment-account"));
  document.querySelector("#prepareAppointmentAgent")?.addEventListener("click", () => startAppointmentAgent(application));
  document.querySelector("#resumeAppointmentAgent")?.addEventListener("click", () => resumeAppointmentAgent(application));
  document.querySelector("#stopAppointmentAgent")?.addEventListener("click", () => revokeAppointmentAgent(application));
  document.querySelector("#backAppointmentAccount")?.addEventListener("click", () => route("appointment-account"));
  document.querySelector("#backAppointmentDashboard")?.addEventListener("click", () => route("dashboard"));
  if (activeJob) startAppointmentAgentPolling(application);
}

async function startAppointmentAgent(application) {
  captureAppointmentPreparation(application);
  const issues = appointmentPreflightIssues(application);
  if (issues.length) {
    updateAppointmentAgentMessage(`请先补齐：${issues.map((item) => item.label).join("、")}`, "error");
    return;
  }
  if (!state.apiAvailable || !API_BASE) {
    updateAppointmentAgentMessage("请通过本地完整版本打开 WestoryVisa。", "error");
    return;
  }
  const button = document.querySelector("#prepareAppointmentAgent");
  if (button) {
    button.disabled = true;
    button.textContent = "正在连接…";
  }
  let preparedJobId = "";
  try {
    state.computerUseHandoffs.appointment = null;
    await saveApplication(application);
    const previous = application.appointmentAgent;
    const previousClosed = ["review_required", "completed", "expired", "revoked"].includes(previous?.state) || previous?.closed;
    if (previous?.jobId && !previousClosed) {
      await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(previous.jobId)}`, { method: "DELETE" }).catch(() => {});
    }
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/appointment-agent/prepare`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约辅助任务准备失败");
    if (!data.accessToken || !data.taskUrl) throw new Error("服务器没有返回本机 Computer Use 授权");
    preparedJobId = data.jobId;
    state.computerUseHandoffs.appointment = {
      jobId: data.jobId,
      taskUrl: data.taskUrl,
      accessToken: data.accessToken
    };
    application.appointmentAgent = {
      jobId: data.jobId,
      workflowType: "appointment",
      state: data.state || "prepared",
      message: data.message || "任务已准备，正在打开预约网站",
      completedFields: 0,
      totalFields: data.totalFields || 0,
      expiresAt: data.expiresAt || "",
      createdAt: new Date().toISOString(),
      pageLabel: "",
      statusCode: "",
      failedActionIds: [],
      missingFields: [],
      currentRoute: null,
      observedRoutes: [],
      autoNext: false,
      closed: false
    };
    await saveApplication(application);
    if (!data.browserOpened) {
      window.open(US_TRAVEL_DOCS_URL, "_blank", "noopener,noreferrer");
    }
    application.appointmentAgent.message = "预约网站已打开。请人工登录并进入 Applicant Details 或 Visa Options，再交给 Computer Use。";
    render("appointment");
    startAppointmentAgentPolling(application);
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "准备任务并打开预约网站";
    }
    state.computerUseHandoffs.appointment = null;
    updateAppointmentAgentMessage(error.message || "预约辅助任务准备失败", "error");
    if (preparedJobId) {
      DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(preparedJobId)}`, { method: "DELETE" }).catch(() => {});
    }
  }
}

async function resumeAppointmentAgent(application) {
  const agent = application.appointmentAgent;
  if (!agent?.jobId) {
    updateAppointmentAgentMessage("请先打开预约网站并建立当前客户任务。", "error");
    return;
  }
  const handoff = state.computerUseHandoffs.appointment;
  if (!handoff || handoff.jobId !== agent.jobId) {
    updateAppointmentAgentMessage("当前页面没有可用的一次性授权，请重新准备 Computer Use 任务。", "error");
    return;
  }
  const button = document.querySelector("#resumeAppointmentAgent");
  if (button) {
    button.disabled = true;
    button.textContent = "正在准备 Codex 指令…";
  }
  try {
    const status = await markComputerUseWaiting(handoff);
    await copyPrivateText(computerUsePrompt(handoff, "appointment"));
    agent.state = status.state || "waiting_for_entry";
    agent.message = "启动指令已复制。回到 Codex 发送后，Computer Use 会填写当前可见的预约资料页。";
    await saveApplication(application);
    updateAppointmentAgentUI(application);
  } catch (error) {
    updateAppointmentAgentMessage(error.message || "Computer Use 交接失败，请重新准备任务", "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "再次复制 Codex 启动指令";
    }
  }
}

function startAppointmentAgentPolling(application) {
  clearCodexAgentPolling();
  refreshAppointmentAgent(application, { quiet: true });
  state.codexAgentTimer = window.setInterval(() => {
    refreshAppointmentAgent(application, { quiet: true });
  }, 5000);
}

async function refreshAppointmentAgent(application, options = {}) {
  const agent = application.appointmentAgent;
  if (!agent?.jobId || !state.apiAvailable || !API_BASE) return;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(agent.jobId)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约任务状态读取失败");
    Object.assign(agent, {
      state: data.state || agent.state,
      message: data.message || agent.message,
      completedFields: data.completedFields || 0,
      totalFields: data.totalFields || agent.totalFields || 0,
      expiresAt: data.expiresAt || agent.expiresAt || "",
      pageLabel: data.pageLabel || agent.pageLabel || "",
      failedActionIds: Array.isArray(data.failedActionIds) ? data.failedActionIds : [],
      missingFields: Array.isArray(data.missingFields) ? data.missingFields : [],
      statusCode: data.statusCode || "",
      currentRoute: data.currentRoute || null,
      observedRoutes: Array.isArray(data.observedRoutes) ? data.observedRoutes : [],
      closed: Boolean(data.closed)
    });
    updateAppointmentAgentUI(application);
    if (["review_required", "completed", "expired", "revoked"].includes(agent.state) || agent.closed) {
      clearCodexAgentPolling();
      await saveApplication(application);
    }
  } catch (error) {
    if (!options.quiet) updateAppointmentAgentMessage(error.message || "预约任务状态读取失败", "error");
  }
}

async function revokeAppointmentAgent(application) {
  const agent = application.appointmentAgent;
  if (!agent?.jobId || !state.apiAvailable || !API_BASE) return;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/cases/${encodeURIComponent(application.id)}/codex-agent/${encodeURIComponent(agent.jobId)}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约任务停止失败");
    clearCodexAgentPolling();
    agent.state = "revoked";
    agent.message = data.message || "预约辅助任务已停止";
    agent.closed = true;
    state.computerUseHandoffs.appointment = null;
    await saveApplication(application);
    render("appointment");
  } catch (error) {
    updateAppointmentAgentMessage(error.message || "预约任务停止失败", "error");
  }
}

function updateAppointmentAgentMessage(message, type = "") {
  const target = document.querySelector("#appointmentAgentMessage");
  if (!target) return;
  target.textContent = message;
  target.className = type;
}

function updateAppointmentAgentUI(application) {
  const agent = application.appointmentAgent || { state: "idle" };
  const meta = appointmentAgentStatusMeta(agent.state);
  const progress = agent.totalFields
    ? Math.round(((agent.completedFields || 0) / agent.totalFields) * 100)
    : 0;
  const badge = document.querySelector("#appointmentAgentStatus");
  if (badge) {
    badge.className = `badge ${meta.badge}`;
    badge.textContent = meta.label;
  }
  const bar = document.querySelector("#appointmentAgentProgress");
  if (bar) bar.style.width = `${progress}%`;
  const value = document.querySelector("#appointmentAgentProgressValue");
  if (value) value.textContent = `${progress}%`;
  updateAppointmentAgentMessage(agent.message || "等待连接预约网站");
  const closed = ["review_required", "completed", "expired", "revoked"].includes(agent.state) || agent.closed;
  const activeJob = Boolean(agent.jobId && !closed);
  const handoff = state.computerUseHandoffs.appointment;
  const handoffReady = Boolean(activeJob && handoff?.jobId === agent.jobId);
  const agentStarted = ["claimed", "running", "blocked"].includes(agent.state);
  const needsFreshHandoff = Boolean(activeJob && !handoffReady && !agentStarted);
  const resumeButton = document.querySelector("#resumeAppointmentAgent");
  if (resumeButton) {
    resumeButton.hidden = !activeJob || !handoffReady || agentStarted;
    resumeButton.disabled = !activeJob || !handoffReady || agentStarted;
    resumeButton.textContent = agent.state === "waiting_for_entry"
      ? "再次复制 Codex 启动指令"
      : "我已登录，交给 Computer Use";
  }
  const stopButton = document.querySelector("#stopAppointmentAgent");
  if (stopButton) stopButton.hidden = !activeJob;
  const prepareButton = document.querySelector("#prepareAppointmentAgent");
  if (prepareButton) {
    prepareButton.hidden = activeJob && !needsFreshHandoff;
    prepareButton.textContent = needsFreshHandoff
      ? "重新准备 Computer Use 任务"
      : "准备任务并打开预约网站";
  }
}

function renderReportList(title, items) {
  return `
    <article class="panel report-section">
      <h2>${escapeHtml(title)}</h2>
      <ul class="report-list">
        ${(items.length ? items : ["暂无记录"]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </article>
  `;
}

function branchQuestionReportLines(question) {
  const lines = [`${question.section} · ${question.label}：${branchAnswerDisplay(question)}（${question.status}）`];
  const detailLabels = new Map((question.detailFields || []).map((field) => [field.id, field.label]));
  Object.entries(question.details || {}).forEach(([fieldId, value]) => {
    if (String(value || "").trim()) lines.push(`  ${detailLabels.get(fieldId) || fieldId}：${value}`);
  });
  const recordLabels = new Map((question.recordFields || []).map((field) => [field.id, field.label]));
  (question.records || []).forEach((record, index) => {
    const values = Object.entries(record || {})
      .filter(([, value]) => String(value || "").trim())
      .map(([fieldId, value]) => `${recordLabels.get(fieldId) || fieldId}：${value}`);
    if (values.length) lines.push(`  ${question.recordLabel || "记录"} ${index + 1} · ${values.join("；")}`);
  });
  if (question.clientResponse) lines.push(`  客户补充说明：${question.clientResponse}`);
  return lines;
}

function buildAuditReport(application) {
  const caseMeta = application.caseMeta || application.partnerMeta || {};
  const branchQuestions = (application.branchQuestionnaire || []).filter((item) => item.visible !== false);
  const unansweredBranchQuestions = branchQuestions.filter((question) => ["待客户确认", "信息待补充"].includes(question.status));
  const unansweredBySection = groupBy(unansweredBranchQuestions, "section");
  const answeredBranchQuestions = branchQuestions.filter((question) => (
    question.autoDetermined
    || Boolean(question.answer)
    || Object.values(question.details || {}).some((value) => String(value || "").trim())
    || (question.records || []).some((record) => Object.values(record || {}).some((value) => String(value || "").trim()))
    || Boolean(question.clientResponse)
    || ["已回答", "已核查"].includes(question.status)
  ));
  const unresolvedSensitive = branchQuestions.filter((question) => question.sensitive && question.status !== "已核查");
  const sensitiveYes = unresolvedSensitive.filter((question) => question.answer === "yes");
  const sensitiveAnswered = unresolvedSensitive.filter((question) => ["yes", "no"].includes(question.answer));
  application.auditReport = {
    applicantName: application.applicantName,
    visaType: application.visaType,
    caseSummary: [
      caseMeta.organizationName ? `所属机构 / 团队：${caseMeta.organizationName}` : "",
      caseMeta.owner ? `负责人：${caseMeta.owner}` : "",
      caseMeta.passportNumber ? `护照号：${caseMeta.passportNumber}` : "",
      caseMeta.status ? `客户状态：${caseMeta.status}` : "",
      caseMeta.notes ? `内部备注：${caseMeta.notes}` : ""
    ].filter(Boolean),
    uploadedDocuments: visibleDocumentEntries(application).map(({ documentItem }) => documentItem).filter((item) => item.fileName).map((item) => `${localizeSlot(item.slot)}：${item.fileName}（${statusLabel(item.scanStatus || "uploaded")}）`),
    draftFields: visibleFieldsForApplication(application).filter((field) => String(field.value || "").trim()).map((field) => {
      const stateLabel = field.confirmed ? "顾问已确认" : isFieldSystemVerified(field, application) ? "系统已校验" : "待重点复核";
      return `${localizeSection(field.section)} · ${localizeField(field.label)}：${field.value}（${stateLabel}）`;
    }),
    confirmedFields: visibleFieldsForApplication(application).filter((field) => field.confirmed || isFieldSystemVerified(field, application)).map((field) => `${localizeField(field.label)}：${field.value}`),
    editedFields: visibleFieldsForApplication(application).filter((field) => field.editedByUser).map((field) => `${localizeField(field.label)}：${field.value}`),
    missingFields: [
      ...visibleFieldsForApplication(application)
        .filter((field) => CRITICAL_REVIEW_FIELD_IDS.has(field.id) && !String(field.value || "").trim())
        .map((field) => `${localizeSection(field.section)} · ${localizeField(field.label)}`),
      ...visibleQuestionsForApplication(application).filter((question) => !question.answer).map((question) => localizeQuestion(question.label)),
      ...Object.entries(unansweredBySection).map(([section, questions]) => `${section}：${questions.length} 项待客户补充或确认`)
    ],
    branchAnswers: [
      ...answeredBranchQuestions.flatMap(branchQuestionReportLines),
      unansweredBranchQuestions.length ? `另有 ${unansweredBranchQuestions.length} 项尚未从材料中获得明确答案，已按模块汇总到待补充项。` : ""
    ].filter(Boolean),
    resolvedConflicts: visibleValidationResultsForApplication(application).filter((item) => item.resolved).map((item) => localizeValidationMessage(item.message)),
    unresolvedSensitiveQuestions: [
      unresolvedSensitive.length ? `${unresolvedSensitive.length} 项背景与历史问题尚未完成顾问最终确认。` : "",
      sensitiveAnswered.length ? `其中 ${sensitiveAnswered.length} 项已从材料提取明确答案、等待最终核对。` : "",
      ...sensitiveYes.map((question) => `${question.label}：Yes（需重点复核事实说明）`)
    ].filter(Boolean),
    agentProcessingLog: [
      ...(application.agentTimeline || []).map((agent) => `${localizeAgent(agent.name)}：${statusLabel(agent.status)}`),
      ...(application.screenAgent?.logs || []).map((item) => `Screen Agent · ${formatAgentLogTime(item.at)}：${item.message}`)
    ],
    safetyBoundaries: SAFETY_BOUNDARIES
  };
}

function showReportNotice(message, type) {
  const notice = document.querySelector("#reportNotice");
  if (!notice) return;
  notice.textContent = message;
  notice.className = `inline-notice visible ${type}`;
}

async function exportAuditReportPdf(application) {
  buildAuditReport(application);
  const report = application.auditReport;
  const sections = [
    { title: "客户档案摘要", items: [
      `客户姓名：${report.applicantName || "未填写"}`,
      `签证类型：${report.visaType || "未填写"}`,
      "档案状态：待人工终审 / 未提交",
      ...report.caseSummary
    ] },
    { title: "已保存客户材料", items: report.uploadedDocuments },
    { title: "DS-160 初稿字段", items: report.draftFields },
    { title: "文案老师编辑记录", items: report.editedFields },
    { title: "待补充与重要复核项", items: report.missingFields },
    { title: "DS-160 条件问答", items: report.branchAnswers },
    { title: "已处理冲突", items: report.resolvedConflicts },
    { title: "未完成的背景问题", items: report.unresolvedSensitiveQuestions },
    { title: "文档处理日志", items: report.agentProcessingLog },
    { title: "安全与使用边界", items: report.safetyBoundaries }
  ];
  const pages = renderPdfReportPages(application, sections);
  const pdfBytes = buildCanvasImagePdf(pages);
  const blob = new Blob([pdfBytes], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${sanitizeFilename(application.applicantName || "ds160-case")}-DS160-核查报告.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1200);
}

function renderPdfReportPages(application, sections) {
  const width = 1240;
  const height = 1754;
  const left = 92;
  const right = width - 92;
  const bottom = height - 116;
  const pages = [];
  let page;

  const newPage = () => {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("当前浏览器无法创建 PDF 画布");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.textBaseline = "top";
    context.fillStyle = "#111111";
    context.font = '600 22px Inter, "Helvetica Neue", Arial, sans-serif';
    context.fillText("WestoryVisa", left, 58);
    context.fillStyle = "#777773";
    context.font = '400 18px "Noto Sans SC", "PingFang SC", sans-serif';
    context.fillText("机构客户档案 · 本地核查报告", right - 292, 61);
    context.strokeStyle = "#deddd8";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(left, 99);
    context.lineTo(right, 99);
    context.stroke();
    page = { canvas, context, y: 134 };
    pages.push(page);
  };

  const ensureSpace = (required) => {
    if (!page || page.y + required > bottom) newPage();
  };

  newPage();
  page.context.fillStyle = "#111111";
  page.context.font = '500 46px "Noto Serif SC", "Source Han Serif SC", "PingFang SC", serif';
  page.context.fillText("DS-160 客户核查报告", left, page.y);
  page.y += 66;
  page.context.fillStyle = "#555551";
  page.context.font = '400 21px "Noto Sans SC", "PingFang SC", sans-serif';
  page.context.fillText(
    `${application.applicantName || "未命名客户"} · ${application.visaType || "签证类型待补充"} · ${new Intl.DateTimeFormat("zh-CN", { dateStyle: "long", timeStyle: "short" }).format(new Date())}`,
    left,
    page.y
  );
  page.y += 62;

  sections.forEach((section) => {
    ensureSpace(86);
    page.context.fillStyle = "#111111";
    page.context.font = '600 24px "Noto Sans SC", "PingFang SC", sans-serif';
    page.context.fillText(section.title, left, page.y);
    page.y += 45;
    const items = section.items?.length ? section.items : ["暂无记录"];
    items.forEach((rawItem) => {
      const text = String(rawItem || "暂无记录");
      page.context.font = '400 19px "Noto Sans SC", "PingFang SC", sans-serif';
      const lines = wrapCanvasText(page.context, text, right - left - 42);
      lines.forEach((line, lineIndex) => {
        ensureSpace(34);
        page.context.fillStyle = lineIndex === 0 ? "#111111" : "#4f4f4b";
        if (lineIndex === 0) {
          page.context.beginPath();
          page.context.arc(left + 6, page.y + 12, 3, 0, Math.PI * 2);
          page.context.fill();
        }
        page.context.fillText(line, left + 30, page.y);
        page.y += 31;
      });
      page.y += 8;
    });
    page.y += 25;
  });

  pages.forEach((item, index) => {
    const context = item.context;
    context.strokeStyle = "#deddd8";
    context.beginPath();
    context.moveTo(left, height - 82);
    context.lineTo(right, height - 82);
    context.stroke();
    context.fillStyle = "#777773";
    context.font = '400 15px Inter, "PingFang SC", sans-serif';
    context.fillText("仅供机构内部人工复核 · 未提交至美国政府网站", left, height - 58);
    context.fillText(`第 ${index + 1} / ${pages.length} 页`, right - 92, height - 58);
  });
  return pages.map((item) => item.canvas);
}

function wrapCanvasText(context, value, maxWidth) {
  const tokens = String(value || "").match(/[A-Za-z0-9_@./:+-]+|\s+|./gu) || [""];
  const lines = [];
  let line = "";
  tokens.forEach((token) => {
    const candidate = line + token;
    if (line && context.measureText(candidate).width > maxWidth) {
      lines.push(line.trimEnd());
      line = token.trimStart();
    } else {
      line = candidate;
    }
  });
  if (line || !lines.length) lines.push(line.trimEnd());
  return lines;
}

function buildCanvasImagePdf(canvases) {
  const encoder = new TextEncoder();
  const ascii = (value) => encoder.encode(value);
  const images = canvases.map((canvas) => dataUrlBytes(canvas.toDataURL("image/jpeg", 0.9)));
  const objects = new Map();
  const pageIds = canvases.map((_, index) => 3 + index * 3);
  objects.set(1, { body: ascii("<< /Type /Catalog /Pages 2 0 R >>") });
  objects.set(2, { body: ascii(`<< /Type /Pages /Count ${pageIds.length} /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] >>`) });

  canvases.forEach((canvas, index) => {
    const pageId = pageIds[index];
    const imageId = pageId + 1;
    const contentId = pageId + 2;
    const image = images[index];
    const content = ascii("q\n595.28 0 0 841.89 0 0 cm\n/Im0 Do\nQ\n");
    objects.set(pageId, {
      body: ascii(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] /Resources << /XObject << /Im0 ${imageId} 0 R >> >> /Contents ${contentId} 0 R >>`)
    });
    objects.set(imageId, {
      dictionary: `/Type /XObject /Subtype /Image /Width ${canvas.width} /Height ${canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode`,
      stream: image
    });
    objects.set(contentId, { dictionary: "", stream: content });
  });

  const chunks = [ascii("%PDF-1.4\n%WestoryVisa\n")];
  const offsets = new Array(objects.size + 1).fill(0);
  let length = chunks[0].length;
  const push = (chunk) => {
    chunks.push(chunk);
    length += chunk.length;
  };
  for (let id = 1; id <= objects.size; id += 1) {
    const object = objects.get(id);
    offsets[id] = length;
    push(ascii(`${id} 0 obj\n`));
    if (object.stream) {
      push(ascii(`<< ${object.dictionary} /Length ${object.stream.length} >>\nstream\n`));
      push(object.stream);
      push(ascii("\nendstream\nendobj\n"));
    } else {
      push(object.body);
      push(ascii("\nendobj\n"));
    }
  }
  const xrefOffset = length;
  push(ascii(`xref\n0 ${objects.size + 1}\n0000000000 65535 f \n`));
  offsets.slice(1).forEach((offset) => push(ascii(`${String(offset).padStart(10, "0")} 00000 n \n`)));
  push(ascii(`trailer\n<< /Size ${objects.size + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`));
  return concatenateBytes(chunks, length);
}

function dataUrlBytes(dataUrl) {
  const binary = atob(dataUrl.split(",")[1] || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function concatenateBytes(chunks, totalLength) {
  const output = new Uint8Array(totalLength);
  let offset = 0;
  chunks.forEach((chunk) => {
    output.set(chunk, offset);
    offset += chunk.length;
  });
  return output;
}

function downloadCaseJson(application) {
  buildAuditReport(application);
  const payload = {
    exportedAt: new Date().toISOString(),
    applicantName: application.applicantName,
    visaType: application.visaType,
    caseMeta: application.caseMeta || {},
    documents: application.documents || [],
    ds160Fields: visibleFieldsForApplication(application),
    missingQuestions: visibleQuestionsForApplication(application),
    validationResults: visibleValidationResultsForApplication(application),
    branchQuestionnaire: application.branchQuestionnaire || [],
    auditReport: application.auditReport || {}
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${sanitizeFilename(application.applicantName || "ds160-case")}-ds160-draft.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function sanitizeFilename(value) {
  return String(value).trim().replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, "_") || "ds160-case";
}

function renderAppHeader(application, title, subtitle) {
  const caseMeta = application.caseMeta || application.partnerMeta || {};
  return `
    <div class="topbar">
      <div>
        <div class="breadcrumb">
          <button type="button" onclick="route('dashboard')">${iconHome()} 工作台</button>
          <span>/</span>
          <button type="button" onclick="goBack()">${iconArrowLeft()} 上一步</button>
        </div>
        <div class="page-kicker">${escapeHtml(application.visaType)}</div>
        <h1>${title}</h1>
        <p class="muted">${subtitle}</p>
        <div class="project-meta">
          <span>${escapeHtml(application.applicantName)}</span>
          ${caseMeta.owner ? `<span>负责人：${escapeHtml(caseMeta.owner)}</span>` : ""}
          ${caseMeta.status ? `<span>${escapeHtml(caseMeta.status)}</span>` : ""}
          <span>${escapeHtml(application.visaType)}</span>
          <span>更新于 ${formatDate(application.lastUpdated)}</span>
        </div>
      </div>
      <div class="topbar-actions">
        <button class="icon-text-btn" onclick="goBack()" type="button">${iconArrowLeft()} 返回</button>
        <button class="btn secondary" onclick="route('dashboard')" type="button">${iconHome()} 返回工作台</button>
      </div>
    </div>
  `;
}

function renderConfidence(value) {
  const numericValue = Number(value);
  const percent = Number.isFinite(numericValue) ? Math.max(0, Math.min(100, Math.round(numericValue * 100))) : 0;
  return `
    <div class="confidence">
      <strong>${percent}%</strong>
      <div class="progress-track"><div class="progress-fill" style="width:${percent}%"></div></div>
    </div>
  `;
}

function groupBy(items, key) {
  return items.reduce((groups, item) => {
    groups[item[key]] = groups[item[key]] || [];
    groups[item[key]].push(item);
    return groups;
  }, {});
}

function formatDate(dateString) {
  if (!dateString) return "今天";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(dateString));
}

function titleCase(value) {
  return value.replace(/-/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusLabel(value) {
  return STATUS_LABELS[value] || titleCase(value);
}

function caseStatus(step) {
  if (step >= 7) return "已完成";
  if (step >= 6) return "初稿已生成";
  if (step >= 3) return "待人工核查";
  if (step >= 1) return "资料收集中";
  return "未开始";
}

function riskLabel(value) {
  return RISK_LABELS[value] || titleCase(value);
}

function localizeSection(value) {
  return SECTION_LABELS[value] || value;
}

function localizeField(value) {
  return FIELD_LABELS[value] || value;
}

function localizeCategory(value) {
  return CATEGORY_LABELS[value] || value;
}

function localizeSlot(value) {
  return SLOT_LABELS[value] || value;
}

function localizeAgent(value) {
  return AGENT_LABELS[value] || value;
}

function localizeQuestion(value) {
  const questions = {
    "What is your U.S. contact phone number?": "客户的美国联系人电话是多少？",
    "Have you previously traveled to the United States?": "客户是否曾经去过美国？",
    "What is your current employer or school address?": "客户当前雇主或学校的地址是什么？"
  };
  return questions[value] || value;
}

function localizeValidationMessage(value) {
  const messages = {
    "Passport shows ZHANG WEI, but I-20 shows WEI ZHANG. Please confirm the correct DS-160 name format.": "护照显示 ZHANG WEI，但 I-20 显示 WEI ZHANG。请确认 DS-160 中应填写的姓名顺序。",
    "U.S. contact phone number is not present in uploaded documents.": "上传材料中没有找到美国联系人电话。",
    "U.S. Address confidence is below review threshold and should be checked manually.": "美国停留地址的识别置信度低于复核阈值，建议人工确认。",
    "Previous visa refusal history must be answered by the applicant. AI will not answer this field.": "过往拒签、拒绝入境、移民申请等敏感问题必须由顾问根据客户真实情况逐项确认，系统不会自动代填。"
  };
  return messages[value] || value;
}

function localizePrefillLog(value) {
  const logs = {
    "Filled surname": "已登记客户姓氏信息",
    "Filled given names": "已登记客户名字信息",
    "Selected visa type": "已选择签证类型 / 访问目的",
    "Filled passport number": "已填写护照号码",
    "Filled arrival date": "已填写预计抵达美国日期",
    "Paused at sensitive history section": "已在安全与背景问题区暂停"
  };
  return logs[value] || value;
}

function visaById(id) {
  return VISA_OPTIONS.find((item) => item.id === id) || VISA_OPTIONS[1];
}

function visaByName(name) {
  return VISA_OPTIONS.find((item) => item.name === name) || VISA_OPTIONS[1];
}

function iconArrowLeft() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/><path d="M9 12h12"/></svg>';
}

function iconExternalLink() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5"/><path d="m11 13 8-8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>';
}

function iconHome() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10.5V20h13v-9.5"/><path d="M9.5 20v-5h5v5"/></svg>';
}

function iconClose() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12"/><path d="M18 6 6 18"/></svg>';
}

function iconTrash() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 10v6M14 10v6"/></svg>';
}

function iconChevronDown() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
}

function iconCalendar() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v4"/><path d="M17 3v4"/><path d="M4.5 8h15"/><path d="M6 5h12a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z"/></svg>';
}

function iconCheck() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>';
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const publicIntakeState = {
  token: "",
  data: null,
  sectionIndex: 0,
  respondentName: "",
  values: { fields: {}, questions: {} },
  submitting: false,
  draftSaveTimer: null,
  draftSaving: false
};

function publicIntakeSections(data) {
  const found = new Set([
    ...(data.fields || []).map((item) => item.section),
    ...(data.questions || []).map((item) => item.section)
  ]);
  const preferred = [
    "申请信息", "基础信息", "护照信息", "地址 / 电话 / 社交媒体", "旅行信息", "同行人",
    "在美停留地址", "以往赴美记录", "美国联系人", "家庭信息", "工作 / 教育 / 培训",
    "补充经历", "F/J 补充联系人", "SEVIS / 学生信息", "健康与背景", "犯罪背景",
    "国家安全与人权", "移民记录", "其他背景问题", "照片与协助填写"
  ];
  return [
    ...preferred.filter((section) => found.has(section)),
    ...[...found].filter((section) => !preferred.includes(section))
  ];
}

function initializePublicIntakeValues(data) {
  const draft = data.draft && typeof data.draft === "object" ? data.draft : {};
  publicIntakeState.respondentName = draft.respondentName || "";
  publicIntakeState.sectionIndex = Number.isFinite(Number(draft.sectionIndex)) ? Number(draft.sectionIndex) : 0;
  publicIntakeState.values = {
    fields: Object.fromEntries((data.fields || []).map((field) => [field.id, draft.fields?.[field.id] || ""])),
    questions: Object.fromEntries((data.questions || []).map((question) => [question.id, {
      answer: draft.questions?.[question.id]?.answer || question.currentAnswer || "",
      details: { ...(draft.questions?.[question.id]?.details || {}) },
      records: (
        draft.questions?.[question.id]?.records?.length
          ? draft.questions[question.id].records
          : (question.currentRecords || [])
      ).map((record) => ({ ...record })),
      clientResponse: draft.questions?.[question.id]?.clientResponse || ""
    }]))
  };
}

function publicQuestionAnswer(question) {
  return publicIntakeState.values.questions[question.id]?.answer || question.currentAnswer || "";
}

function publicQuestionVisible(question, questionsById, visited = new Set()) {
  if (!question.parentQuestionId) return true;
  if (visited.has(question.id)) return false;
  const nextVisited = new Set(visited);
  nextVisited.add(question.id);
  const parent = questionsById.get(question.parentQuestionId);
  if (!parent || !publicQuestionVisible(parent, questionsById, nextVisited)) return false;
  const answer = parent ? publicQuestionAnswer(parent) : "";
  return Boolean(answer) && (question.parentValues || ["yes"]).includes(answer);
}

function publicActiveDetailFields(question) {
  const answer = publicQuestionAnswer(question);
  const values = publicIntakeState.values.questions[question.id]?.details || {};
  if (question.answerType === "details") {
    return (question.detailFields || []).filter((field) => conditionalQuestionFieldVisible(field, values));
  }
  if ((question.triggerValues || []).length && !(question.triggerValues || []).includes(answer)) return [];
  return (question.detailFields || []).filter((field) => (
    (!(field.when || []).length || field.when.includes(answer))
    && conditionalQuestionFieldVisible(field, values)
  ));
}

function publicChoiceLabel(question, choice) {
  const labels = {
    "companions.has_companions": {
      yes: "有同行人",
      no: "没有同行人"
    },
    "companions.is_group": {
      yes: "是团体或组织出行",
      no: "不是团体出行"
    },
    "contact.social_media": {
      yes: "使用过，填写至少一个账号",
      no: "过去五年没有使用过"
    }
  };
  return labels[question.id]?.[choice.value] || choice.label;
}

function publicRecordHasValue(record) {
  return Object.values(record || {}).some((value) => String(value || "").trim());
}

function publicActiveRecordFields(question, record) {
  return (question.recordFields || []).filter((field) => (
    conditionalQuestionFieldVisible(field, record || {})
  ));
}

function publicRecordComplete(question, record) {
  return publicActiveRecordFields(question, record).every((field) => (
    !field.required || String(record?.[field.id] || "").trim()
  ));
}

function normalizePublicDs160Value(value, finalize = false) {
  const raw = String(value || "");
  if (!finalize) return raw;
  const cleaned = raw.trim();
  return cleaned.toUpperCase() === "D" ? "DOES NOT APPLY" : cleaned;
}

function publicQuestionValidationIssue(question, questionsById) {
  if (!publicQuestionVisible(question, questionsById)) return "";
  const values = publicIntakeState.values.questions[question.id] || {};
  const answer = publicQuestionAnswer(question);
  if (["yes_no", "select"].includes(question.answerType) && !answer) {
    return `请选择“${question.prompt}”的答案。`;
  }
  const missingDetail = publicActiveDetailFields(question).find((field) => (
    field.required && !String(values.details?.[field.id] || "").trim()
  ));
  if (missingDetail) return `请填写“${missingDetail.label}”。`;

  const requiresRecords = question.answerType === "records" || (
    (question.recordFields || []).length && (question.triggerValues || []).includes(answer)
  );
  if (!requiresRecords) return "";
  const enteredRecords = (values.records || []).filter(publicRecordHasValue);
  const completeRecords = enteredRecords.filter((record) => publicRecordComplete(question, record));
  const minimum = Math.max(0, Number(question.minRecords ?? 1));
  if (completeRecords.length < minimum) {
    if (question.id === "contact.social_media") {
      return "选择一个使用过的平台并填写用户名即可继续。";
    }
    if (question.id === "companions.people") {
      return "如果没有同行人，请在上一题选择“没有同行人”；如有同行人，请至少完整填写一位。";
    }
    return `请至少完整填写 ${minimum} 条“${question.recordLabel}”。`;
  }
  if (question.id !== "contact.social_media" && enteredRecords.some((record) => !publicRecordComplete(question, record))) {
    return `请补全尚未填写完整的“${question.recordLabel}”，或删除该空白记录。`;
  }
  return "";
}

function publicSectionValidationIssue(section) {
  const fields = (publicIntakeState.data?.fields || []).filter((item) => item.section === section);
  const missingField = fields.find((field) => (
    field.required && !browserWorkflowFieldValueIsUsable(
      field.id, publicIntakeState.values.fields[field.id]
    )
  ));
  if (missingField) {
    const hasValue = String(publicIntakeState.values.fields[missingField.id] || "").trim();
    return {
      message: hasValue && BROWSER_WORKFLOW_SELECT_FIELD_IDS.has(missingField.id)
        ? `“${missingField.label}”无法匹配 DS-160 的下拉选项，请填写官网使用的英文名称，例如 CHINA。`
        : `请填写“${missingField.label}”。`,
      fieldId: missingField.id
    };
  }
  const questions = publicIntakeState.data?.questions || [];
  const questionsById = new Map(questions.map((question) => [question.id, question]));
  for (const question of questions.filter((item) => item.section === section)) {
    const issue = publicQuestionValidationIssue(question, questionsById);
    if (issue) return { message: issue, questionId: question.id };
  }
  return null;
}

function showPublicIntakeIssue(issue) {
  const notice = document.querySelector("#publicIntakeNotice");
  if (notice) {
    notice.textContent = issue.message;
    notice.className = "inline-notice visible error";
  }
  const target = issue.fieldId
    ? document.querySelector(`[data-public-field="${CSS.escape(issue.fieldId)}"]`)
    : document.querySelector(`[data-public-question="${CSS.escape(issue.questionId)}"]`);
  target?.scrollIntoView({
    behavior: "smooth",
    block: "center"
  });
  target?.focus();
}

function renderPublicField(field) {
  const value = publicIntakeState.values.fields[field.id] || "";
  const note = field.hint ? `<small>${escapeHtml(field.hint)}</small>` : "";
  const required = field.required ? 'required aria-required="true"' : "";
  const label = `${escapeHtml(field.label)}${field.required ? " *" : ""}`;
  if (field.inputType === "textarea") {
    return `
      <div class="public-form-row full">
        <label for="public-field-${field.id}">${label}</label>
        <textarea id="public-field-${field.id}" data-public-field="${field.id}" placeholder="${escapeHtml(field.placeholder || "请填写")}" ${required}>${escapeHtml(value)}</textarea>
        ${note}
      </div>
    `;
  }
  if (field.inputType === "select") {
    return `
      <div class="public-form-row">
        <label for="public-field-${field.id}">${label}</label>
        <select id="public-field-${field.id}" data-public-field="${field.id}" ${required}>
          <option value="">请选择</option>
          ${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice.value)}" ${choice.value === value ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}
        </select>
        ${note}
      </div>
    `;
  }
  const inputType = ["email", "tel"].includes(field.inputType) ? field.inputType : "text";
  return `
    <div class="public-form-row">
      <label for="public-field-${field.id}">${label}</label>
      <input id="public-field-${field.id}" data-public-field="${field.id}" type="${inputType}" ${field.inputType === "date" ? 'inputmode="numeric"' : ""} value="${escapeHtml(value)}" placeholder="${escapeHtml(field.placeholder || (field.inputType === "date" ? "YYYY-MM-DD" : "请填写"))}" ${required}>
      ${note}
    </div>
  `;
}

function renderPublicSocialMediaRecords(question, values) {
  const platformField = (question.recordFields || []).find((field) => field.id === "platform");
  const records = new Map((values.records || []).map((record) => [record.platform, record.handle || ""]));
  return `
    <div class="public-social-media">
      <div class="public-social-media-heading">
        <strong>选择平台并填写账号标识</strong>
        <span>只填一个完整账号即可；多个账号也可以添加。不要填写密码。</span>
      </div>
      <div class="public-social-platform-grid">
        ${(platformField?.choices || []).map((choice, index) => {
          const selected = records.has(choice.value);
          return `
            <div class="public-social-platform ${selected ? "selected" : ""}">
              <input id="social-platform-${index}" type="checkbox" data-public-social-platform="${question.id}" value="${escapeHtml(choice.value)}" ${selected ? "checked" : ""}>
              <label for="social-platform-${index}">${escapeHtml(choice.label)}</label>
              <input type="text" data-public-social-handle="${question.id}" data-social-platform-value="${escapeHtml(choice.value)}" value="${escapeHtml(records.get(choice.value) || "")}" placeholder="用户名 / Handle" ${selected ? "required" : "disabled"}>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

function renderPublicSocialMediaOverview(question) {
  const platformField = (question.recordFields || []).find((field) => field.id === "platform");
  return `
    <div class="public-social-overview" aria-label="DS-160 页面列出的社交媒体平台">
      <strong>先查看页面列出的平台</strong>
      <div>${(platformField?.choices || []).map((choice) => `<span>${escapeHtml(choice.label)}</span>`).join("")}</div>
    </div>
  `;
}

function ensurePublicRecordRows(question, values) {
  const minimum = Math.max(0, Number(question.minRecords ?? 1));
  values.records = Array.isArray(values.records) ? values.records : [];
  while (values.records.length < minimum) values.records.push({});
  return values.records;
}

function renderPublicRecordField(question, field, record, recordIndex) {
  const value = record?.[field.id] || "";
  const inputId = `public-record-${question.id}-${recordIndex}-${field.id}`;
  if ((field.choices || []).length) {
    return `
      <div class="public-form-row">
        <label for="${escapeHtml(inputId)}">${escapeHtml(field.label)}</label>
        <select id="${escapeHtml(inputId)}" data-public-record-field="${escapeHtml(question.id)}" data-public-record-index="${recordIndex}" data-public-record-id="${escapeHtml(field.id)}">
          <option value="">请选择</option>
          ${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice.value)}" ${choice.value === value ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}
        </select>
      </div>
    `;
  }
  if (field.type === "textarea") {
    return `
      <div class="public-form-row full">
        <label for="${escapeHtml(inputId)}">${escapeHtml(field.label)}</label>
        <textarea id="${escapeHtml(inputId)}" data-public-record-field="${escapeHtml(question.id)}" data-public-record-index="${recordIndex}" data-public-record-id="${escapeHtml(field.id)}" placeholder="请按实际情况填写">${escapeHtml(value)}</textarea>
      </div>
    `;
  }
  const type = field.type === "email" ? "email" : "text";
  return `
    <div class="public-form-row">
      <label for="${escapeHtml(inputId)}">${escapeHtml(field.label)}</label>
      <input id="${escapeHtml(inputId)}" type="${type}" ${field.type === "date" ? 'inputmode="numeric"' : ""} data-public-record-field="${escapeHtml(question.id)}" data-public-record-index="${recordIndex}" data-public-record-id="${escapeHtml(field.id)}" value="${escapeHtml(value)}" placeholder="${field.type === "date" ? "YYYY-MM-DD" : "请填写"}">
    </div>
  `;
}

function renderPublicRecordEditor(question, values) {
  seedEducationRecordFromCurrentSchool(question, values);
  const records = ensurePublicRecordRows(question, values);
  const minimum = Math.max(0, Number(question.minRecords ?? 1));
  return `
    <div class="public-record-editor">
      <div class="public-record-editor-heading">
        <div><strong>${escapeHtml(question.recordLabel)}</strong><span>${minimum > 1 ? `至少填写 ${minimum} 条` : minimum === 0 ? "可选，按实际情况添加" : "可按实际情况添加多条"}</span></div>
        <button class="icon-text-btn" type="button" data-public-add-record="${escapeHtml(question.id)}">+ 添加一条</button>
      </div>
      <div class="public-record-list">
        ${records.map((record, recordIndex) => `
          <section class="public-record-item" data-public-record-row="${escapeHtml(question.id)}" data-public-record-index="${recordIndex}">
            <header>
              <strong>${escapeHtml(question.recordLabel)} ${recordIndex + 1}</strong>
              <button class="icon-btn" type="button" data-public-remove-record="${escapeHtml(question.id)}" data-public-remove-index="${recordIndex}" aria-label="删除第 ${recordIndex + 1} 条记录" ${records.length <= minimum ? "disabled" : ""}>${iconClose()}</button>
            </header>
            <div class="public-detail-grid">
              ${publicActiveRecordFields(question, record).map((field) => renderPublicRecordField(question, field, record, recordIndex)).join("")}
            </div>
          </section>
        `).join("")}
      </div>
    </div>
  `;
}

function inferPublicEducationLevel(schoolName) {
  const value = String(schoolName || "").toLowerCase();
  if (/小学|初中|高中|中学|middle school|high school|secondary school/.test(value)) return "secondary";
  if (/中专|职校|职业|技校|vocational/.test(value)) return "vocational";
  if (/研究生|研究院|graduate|postgraduate|博士|硕士/.test(value)) return "postgraduate";
  if (/大学|学院|大专|university|college/.test(value)) return "college";
  return "";
}

function seedEducationRecordFromCurrentSchool(question, values) {
  if (question.id !== "work.education_secondary_or_above" || publicQuestionAnswer(question) !== "yes") return;
  if ((values.records || []).some(publicRecordHasValue)) return;
  const occupation = publicIntakeState.values.questions["work.primary_occupation"] || {};
  if (occupation.answer !== "student") return;
  const details = occupation.details || {};
  const school = details.organization || "";
  if (!school) return;
  const level = details.schoolLevel || inferPublicEducationLevel(school);
  values.records = [{
    level,
    school,
    address: details.address || "",
    course: level === "secondary" ? "" : (details.courseOfStudy || ""),
    startDate: details.startDate || "",
    endDate: ""
  }];
}

function applyPublicAnswerSuggestions(questionId) {
  const question = (publicIntakeState.data?.questions || []).find((item) => item.id === questionId);
  const values = publicIntakeState.values.questions[questionId];
  if (!question || !values) return;
  const suggestions = question.answerSuggestions?.[values.answer] || {};
  Object.entries(suggestions).forEach(([fieldId, value]) => {
    if (!String(values.details?.[fieldId] || "").trim() && String(value || "").trim()) {
      values.details[fieldId] = value;
    }
  });
  if (questionId === "work.primary_occupation" && values.answer === "student") {
    const inferred = values.details.schoolLevel || inferPublicEducationLevel(values.details.organization);
    if (inferred) values.details.schoolLevel = inferred;
    if (inferred === "secondary") delete values.details.courseOfStudy;
  }
}

function renderPublicQuestion(question, questionsById) {
  if (!publicQuestionVisible(question, questionsById)) return "";
  applyPublicAnswerSuggestions(question.id);
  const values = publicIntakeState.values.questions[question.id] || { answer: "", details: {}, clientResponse: "" };
  const answer = publicQuestionAnswer(question);
  const activeDetails = publicActiveDetailFields(question);
  const showRecordResponse = (question.recordFields || []).length && (
    question.answerType === "records" || (question.triggerValues || []).includes(answer)
  );
  const recordResponse = question.id === "contact.social_media"
    ? renderPublicSocialMediaRecords(question, values)
    : renderPublicRecordEditor(question, values);
  let answerControl = "";
  if (question.lockAnswer) {
    const choice = (question.choices || []).find((item) => item.value === answer);
    answerControl = `<div class="public-prefilled-answer"><span>材料已提供</span><strong>${escapeHtml(choice?.label || answer || "已读取")}</strong></div>`;
  } else if (question.answerType === "yes_no") {
    answerControl = `
      <div class="public-choice-grid" role="radiogroup" aria-label="${escapeHtml(question.prompt)}">
        ${(question.choices || []).map((choice) => `
          <label class="public-choice ${answer === choice.value ? "selected" : ""}">
            <input type="radio" name="public-answer-${question.id}" data-public-answer="${question.id}" value="${escapeHtml(choice.value)}" ${answer === choice.value ? "checked" : ""}>
            <span>${escapeHtml(publicChoiceLabel(question, choice))}</span>
          </label>
        `).join("")}
      </div>
    `;
  } else if (question.answerType === "select") {
    answerControl = `
      <div class="public-form-row full">
        <select data-public-answer-select="${question.id}" aria-label="${escapeHtml(question.prompt)}">
          <option value="">请选择</option>
          ${(question.choices || []).map((choice) => `<option value="${escapeHtml(choice.value)}" ${answer === choice.value ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}
        </select>
      </div>
    `;
  }
  return `
    <article class="public-question ${question.sensitive ? "sensitive" : ""}" data-public-question="${escapeHtml(question.id)}">
      <header>
        <div>
          <h3>${escapeHtml(question.prompt)}</h3>
          ${question.englishPrompt ? `<p lang="en">${escapeHtml(question.englishPrompt)}</p>` : ""}
        </div>
      </header>
      ${question.guidance ? `<p class="public-question-guidance">${escapeHtml(question.guidance)}</p>` : ""}
      ${question.id === "companions.has_companions" ? '<p class="public-question-guidance">没有同行人时，直接选择“没有同行人”，后续同行人资料将自动跳过。</p>' : ""}
      ${question.id === "contact.social_media" ? renderPublicSocialMediaOverview(question) : ""}
      ${question.clientOptional ? '<p class="public-question-guidance optional-note">当前没有资料可先跳过，顾问会在最终填写前处理。</p>' : ""}
      ${answerControl}
      ${activeDetails.length ? `
        <div class="public-detail-grid">
          ${activeDetails.map((field) => {
            const value = values.details?.[field.id] || "";
            return `
              <div class="public-form-row ${field.type === "textarea" ? "full" : ""}">
                <label>${escapeHtml(field.label)}</label>
                ${(field.choices || []).length
                  ? `<select data-public-detail="${question.id}" data-public-detail-id="${field.id}"><option value="">请选择</option>${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice.value)}" ${choice.value === value ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}</select>`
                  : field.type === "textarea"
                  ? `<textarea data-public-detail="${question.id}" data-public-detail-id="${field.id}" placeholder="${escapeHtml(field.placeholder || "请按实际情况说明")}">${escapeHtml(value)}</textarea>`
                  : `<input type="${field.type === "email" ? "email" : "text"}" ${field.type === "date" ? 'inputmode="numeric"' : ""} data-public-detail="${question.id}" data-public-detail-id="${field.id}" value="${escapeHtml(value)}" placeholder="${escapeHtml(field.placeholder || (field.type === "date" ? "YYYY-MM-DD" : "请填写"))}">`
                }
              </div>
            `;
          }).join("")}
        </div>
      ` : ""}
      ${showRecordResponse ? recordResponse : ""}
    </article>
  `;
}

function capturePublicIntakeValues({ finalize = false } = {}) {
  const respondentName = document.querySelector("#publicRespondentName");
  if (respondentName) {
    publicIntakeState.respondentName = finalize
      ? respondentName.value.trim() : respondentName.value;
  }
  document.querySelectorAll("[data-public-field]").forEach((input) => {
    const normalized = normalizePublicDs160Value(input.value, finalize);
    if (normalized !== input.value && input.tagName !== "SELECT") input.value = normalized;
    publicIntakeState.values.fields[input.dataset.publicField] = finalize
      ? canonicalBrowserWorkflowFieldValue(input.dataset.publicField, normalized)
      : normalized;
  });
  document.querySelectorAll("[data-public-answer]:checked").forEach((input) => {
    publicIntakeState.values.questions[input.dataset.publicAnswer].answer = input.value;
  });
  document.querySelectorAll("[data-public-answer-select]").forEach((select) => {
    publicIntakeState.values.questions[select.dataset.publicAnswerSelect].answer = select.value;
  });
  document.querySelectorAll("[data-public-detail]").forEach((input) => {
    const values = publicIntakeState.values.questions[input.dataset.publicDetail];
    const normalized = input.tagName === "SELECT"
      ? input.value : normalizePublicDs160Value(input.value, finalize);
    if (normalized !== input.value && input.tagName !== "SELECT") input.value = normalized;
    if (values) values.details[input.dataset.publicDetailId] = normalized;
  });
  document.querySelectorAll("[data-public-response]").forEach((input) => {
    const values = publicIntakeState.values.questions[input.dataset.publicResponse];
    if (values) values.clientResponse = normalizePublicDs160Value(input.value, finalize);
  });
  const structuredRecords = {};
  document.querySelectorAll("[data-public-record-field]").forEach((input) => {
    const questionId = input.dataset.publicRecordField;
    const recordIndex = Number(input.dataset.publicRecordIndex || 0);
    const fieldId = input.dataset.publicRecordId;
    structuredRecords[questionId] = structuredRecords[questionId] || [];
    structuredRecords[questionId][recordIndex] = structuredRecords[questionId][recordIndex] || {};
    const normalized = input.tagName === "SELECT"
      ? input.value : normalizePublicDs160Value(input.value, finalize);
    if (normalized !== input.value && input.tagName !== "SELECT") input.value = normalized;
    structuredRecords[questionId][recordIndex][fieldId] = normalized;
  });
  Object.entries(structuredRecords).forEach(([questionId, records]) => {
    if (publicIntakeState.values.questions[questionId]) {
      publicIntakeState.values.questions[questionId].records = records;
    }
  });
  const currentEducation = publicIntakeState.values.questions["work.primary_occupation"];
  if (currentEducation?.details?.schoolLevel === "secondary") {
    delete currentEducation.details.courseOfStudy;
  }
  const educationHistory = publicIntakeState.values.questions["work.education_secondary_or_above"];
  (educationHistory?.records || []).forEach((record) => {
    if (record.level === "secondary") delete record.course;
  });
  const socialRecords = {};
  const renderedSocialQuestions = new Set();
  document.querySelectorAll("[data-public-social-platform]").forEach((checkbox) => {
    const questionId = checkbox.dataset.publicSocialPlatform;
    renderedSocialQuestions.add(questionId);
    if (!checkbox.checked) return;
    const row = checkbox.closest(".public-social-platform");
    const handle = row?.querySelector("[data-public-social-handle]")?.value.trim() || "";
    socialRecords[questionId] = socialRecords[questionId] || [];
    socialRecords[questionId].push({ platform: checkbox.value, handle });
  });
  renderedSocialQuestions.forEach((questionId) => {
    if (publicIntakeState.values.questions[questionId]) {
      publicIntakeState.values.questions[questionId].records = socialRecords[questionId] || [];
    }
  });
}

function publicIntakeSubmissionPayload() {
  const questionsById = new Map((publicIntakeState.data.questions || []).map((question) => [question.id, question]));
  return {
    respondentName: publicIntakeState.respondentName,
    fields: Object.fromEntries(Object.entries(publicIntakeState.values.fields).filter(([, value]) => String(value).trim())),
    questions: Object.fromEntries(Object.entries(publicIntakeState.values.questions).filter(([questionId]) => {
      const question = questionsById.get(questionId);
      return question && publicQuestionVisible(question, questionsById);
    }).map(([questionId, value]) => {
      const question = questionsById.get(questionId);
      const records = (value.records || []).filter((record) => (
        publicRecordHasValue(record)
        && (question?.id !== "contact.social_media" || publicRecordComplete(question, record))
      ));
      return [questionId, {
        answer: question?.lockAnswer ? "" : value.answer,
        details: Object.fromEntries(Object.entries(value.details || {}).filter(([, detail]) => String(detail).trim())),
        records,
        clientResponse: value.clientResponse || ""
      }];
    }).filter(([, value]) => value.answer || value.clientResponse || value.records.length || Object.keys(value.details).length)),
    sectionIndex: publicIntakeState.sectionIndex
  };
}

async function savePublicIntakeDraft({ silent = true } = {}) {
  if (publicIntakeState.data?.status !== "pending" || publicIntakeState.submitting || publicIntakeState.draftSaving) return false;
  window.clearTimeout(publicIntakeState.draftSaveTimer);
  publicIntakeState.draftSaveTimer = null;
  capturePublicIntakeValues();
  publicIntakeState.draftSaving = true;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/intake`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-DocFlow-Intake": publicIntakeState.token
      },
      body: JSON.stringify(publicIntakeSubmissionPayload())
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "草稿保存失败");
    return true;
  } catch (error) {
    if (!silent) {
      const notice = document.querySelector("#publicIntakeNotice");
      if (notice) {
        notice.textContent = error.message || "草稿保存失败，请稍后重试。";
        notice.className = "inline-notice visible error";
      }
    }
    return false;
  } finally {
    publicIntakeState.draftSaving = false;
  }
}

function schedulePublicIntakeDraftSave() {
  window.clearTimeout(publicIntakeState.draftSaveTimer);
  publicIntakeState.draftSaveTimer = window.setTimeout(() => savePublicIntakeDraft(), 700);
}

function renderPublicIntakeForm() {
  const app = document.querySelector("#app");
  const data = publicIntakeState.data;
  if (data.status === "submitted") {
    app.innerHTML = `
      <main class="public-intake-shell completion">
        <section class="public-completion">
          <div class="public-completion-icon">${iconCheck()}</div>
          <span class="page-kicker">已提交</span>
          <h1>资料已发送给顾问</h1>
          <p>你的回答已经写入客户档案。文案老师或签证顾问会继续核对关键字段；无需再次提交。</p>
          <small>本页面不会提交真实 DS-160，也不会处理费用或法律声明。</small>
        </section>
      </main>
    `;
    return;
  }
  const sections = publicIntakeSections(data);
  publicIntakeState.sectionIndex = Math.max(0, Math.min(publicIntakeState.sectionIndex, sections.length - 1));
  const section = sections[publicIntakeState.sectionIndex] || "待补充信息";
  const fields = (data.fields || []).filter((item) => item.section === section);
  const questions = (data.questions || []).filter((item) => item.section === section);
  const questionsById = new Map((data.questions || []).map((item) => [item.id, item]));
  const progress = sections.length ? Math.round(((publicIntakeState.sectionIndex + 1) / sections.length) * 100) : 100;
  app.innerHTML = `
    <main class="public-intake-shell">
      <header class="public-intake-header">
        <div class="brand-line"><span class="brand-dot"></span><span lang="en">WestoryVisa</span></div>
        <span>${escapeHtml(data.visaType)}</span>
      </header>
      <section class="public-intake-intro">
        <span class="page-kicker">${escapeHtml(data.applicantName)} · 客户资料补充</span>
        <h1>只需补充材料里没有的信息</h1>
        <p>顾问已经上传并整理现有材料。这里不会重复询问材料中已识别的内容；请按真实情况回答，专业格式与最终核查由顾问完成。不适用的文字字段可直接填写 D，系统会自动转为 DOES NOT APPLY。</p>
      </section>
      <div class="public-intake-progress">
        <div><span>第 ${publicIntakeState.sectionIndex + 1} / ${Math.max(1, sections.length)} 部分</span><strong>${escapeHtml(section)}</strong></div>
        <span>${progress}%</span>
        <div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>
      </div>
      <form id="publicIntakeForm" class="public-intake-form">
        <section class="public-identity-check">
          <div>
            <span class="page-kicker">档案核对</span>
            <h2>请先填写本次申请人的姓名</h2>
            <p>顾问档案中的申请人为“${escapeHtml(data.applicantName)}”。如果不是本人，请停止填写并联系顾问确认链接。</p>
          </div>
          <div class="public-form-row">
            <label for="publicRespondentName">申请人姓名</label>
            <input id="publicRespondentName" type="text" value="${escapeHtml(publicIntakeState.respondentName)}" placeholder="请输入申请人姓名" autocomplete="name" required>
          </div>
        </section>
        ${fields.length ? `<section class="public-field-group"><header><h2>基础资料补充</h2><p>以下内容没有从现有材料中稳定识别到。</p></header><div class="public-detail-grid">${fields.map(renderPublicField).join("")}</div></section>` : ""}
        ${questions.map((question) => renderPublicQuestion(question, questionsById)).join("")}
        ${!fields.length && !questions.some((question) => publicQuestionVisible(question, questionsById)) ? '<div class="public-empty-section"><strong>这一部分目前无需补充</strong><span>可以直接进入下一部分。</span></div>' : ""}
        <div class="inline-notice" id="publicIntakeNotice" role="status"></div>
        <footer class="public-intake-footer">
          <button class="btn secondary" type="button" id="publicPrevious" ${publicIntakeState.sectionIndex === 0 ? "disabled" : ""}>${iconArrowLeft()} 上一步</button>
          ${publicIntakeState.sectionIndex < sections.length - 1
            ? '<button class="btn" type="button" id="publicNext">保存并继续</button>'
            : `<button class="btn" type="submit" id="publicSubmit" ${publicIntakeState.submitting ? "disabled" : ""}>${publicIntakeState.submitting ? "正在提交…" : "提交给顾问"}</button>`}
        </footer>
      </form>
      <footer class="public-intake-safety">资料仅用于当前顾问整理 DS-160 初稿，不提供法律建议，不预测签证结果，不连接或提交至美国政府网站。</footer>
    </main>
  `;

  document.querySelectorAll("[data-public-answer], [data-public-answer-select]").forEach((input) => {
    input.addEventListener("change", () => {
      capturePublicIntakeValues();
      const questionId = input.dataset.publicAnswer || input.dataset.publicAnswerSelect;
      if (questionId) applyPublicAnswerSuggestions(questionId);
      schedulePublicIntakeDraftSave();
      renderPublicIntakeForm();
    });
  });
  document.querySelectorAll("[data-public-social-platform]").forEach((input) => {
    input.addEventListener("change", () => {
      capturePublicIntakeValues();
      schedulePublicIntakeDraftSave();
      renderPublicIntakeForm();
    });
  });
  document.querySelectorAll("[data-public-add-record]").forEach((button) => {
    button.addEventListener("click", () => {
      capturePublicIntakeValues();
      const values = publicIntakeState.values.questions[button.dataset.publicAddRecord];
      if (values) values.records.push({});
      schedulePublicIntakeDraftSave();
      renderPublicIntakeForm();
    });
  });
  document.querySelectorAll("[data-public-remove-record]").forEach((button) => {
    button.addEventListener("click", () => {
      capturePublicIntakeValues();
      const values = publicIntakeState.values.questions[button.dataset.publicRemoveRecord];
      if (values) values.records.splice(Number(button.dataset.publicRemoveIndex || 0), 1);
      schedulePublicIntakeDraftSave();
      renderPublicIntakeForm();
    });
  });
  document.querySelectorAll('select[data-public-detail-id="schoolLevel"], select[data-public-record-id="level"]').forEach((select) => {
    select.addEventListener("change", () => {
      capturePublicIntakeValues();
      schedulePublicIntakeDraftSave();
      renderPublicIntakeForm();
    });
  });
  document.querySelectorAll("#publicIntakeForm input, #publicIntakeForm textarea, #publicIntakeForm select").forEach((input) => {
    input.addEventListener("input", () => {
      capturePublicIntakeValues();
      schedulePublicIntakeDraftSave();
    });
  });
  document.querySelector("#publicPrevious")?.addEventListener("click", async () => {
    capturePublicIntakeValues({ finalize: true });
    publicIntakeState.sectionIndex -= 1;
    await savePublicIntakeDraft({ silent: false });
    renderPublicIntakeForm();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  document.querySelector("#publicNext")?.addEventListener("click", async () => {
    capturePublicIntakeValues({ finalize: true });
    const issue = publicSectionValidationIssue(section);
    if (issue) {
      showPublicIntakeIssue(issue);
      return;
    }
    publicIntakeState.sectionIndex += 1;
    await savePublicIntakeDraft({ silent: false });
    renderPublicIntakeForm();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  document.querySelector("#publicIntakeForm")?.addEventListener("submit", submitPublicIntake);
}

async function submitPublicIntake(event) {
  event.preventDefault();
  capturePublicIntakeValues({ finalize: true });
  if (!publicIntakeState.respondentName) {
    const notice = document.querySelector("#publicIntakeNotice");
    if (notice) {
      notice.textContent = "请先填写申请人姓名，以便顾问核对客户档案。";
      notice.className = "inline-notice visible error";
    }
    document.querySelector("#publicRespondentName")?.focus();
    return;
  }
  const sections = publicIntakeSections(publicIntakeState.data);
  for (let index = 0; index < sections.length; index += 1) {
    const issue = publicSectionValidationIssue(sections[index]);
    if (!issue) continue;
    publicIntakeState.sectionIndex = index;
    renderPublicIntakeForm();
    showPublicIntakeIssue(issue);
    return;
  }
  publicIntakeState.submitting = true;
  renderPublicIntakeForm();
  try {
    const response = await DocFlowApi.request(`${API_BASE}/intake`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-DocFlow-Intake": publicIntakeState.token
      },
      body: JSON.stringify(publicIntakeSubmissionPayload())
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "资料提交失败");
    publicIntakeState.data = {
      status: "submitted",
      submittedAt: new Date().toISOString(),
      identityMatch: data.identityMatch
    };
    renderPublicIntakeForm();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    publicIntakeState.submitting = false;
    renderPublicIntakeForm();
    const notice = document.querySelector("#publicIntakeNotice");
    if (notice) {
      notice.textContent = error.message || "资料提交失败，请稍后重试。";
      notice.className = "inline-notice visible error";
    }
  }
}

async function renderPublicIntake(token) {
  const app = document.querySelector("#app");
  publicIntakeState.token = token;
  app.innerHTML = `
    <main class="public-intake-shell loading">
      <div class="public-intake-loading"><span class="loading-dot"></span><strong>正在读取客户补充表</strong></div>
    </main>
  `;
  try {
    const response = await DocFlowApi.request(`${API_BASE}/intake`, {
      headers: { "X-DocFlow-Intake": token }
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "补充链接无法打开");
    publicIntakeState.data = data;
    initializePublicIntakeValues(data);
    renderPublicIntakeForm();
  } catch (error) {
    app.innerHTML = `
      <main class="public-intake-shell completion">
        <section class="public-completion error">
          <span class="page-kicker">链接不可用</span>
          <h1>请联系顾问重新发送</h1>
          <p>${escapeHtml(error.message || "该补充链接已失效或过期。")}</p>
        </section>
      </main>
    `;
  }
}

async function boot() {
  const intakeToken = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("intake")
    || new URLSearchParams(window.location.search).get("intake");
  if (intakeToken) {
    await renderPublicIntake(intakeToken);
    return;
  }
  await loadState();
  if (state.user && !state.membership?.active && !state.trial?.active && !state.membershipBypass) {
    window.location.replace("/membership?access=required");
    return;
  }
  const savedNavigation = readSavedNavigation();
  const restorableViews = new Set([
    "dashboard", "create", "documents", "processing", "fields", "questions",
    "validation", "preview", "prefill", "report"
  ]);
  if (state.user) {
    await loadApplicationsForCurrentOrganization();
  }
  if (state.user && savedNavigation && restorableViews.has(savedNavigation.view)) {
    const applicationId = savedNavigation.applicationId || "";
    const needsApplication = !["dashboard", "create"].includes(savedNavigation.view);
    if (!needsApplication || state.applications.some((item) => item.id === applicationId)) {
      route(savedNavigation.view, applicationId || undefined);
      return;
    }
    route("dashboard");
    return;
  }
  if (state.user) {
    route("dashboard");
    return;
  }
  render("login");
}

boot();
