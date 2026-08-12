const STEP_LABELS = [
  "档案",
  "资料",
  "整理",
  "字段核查",
  "待确认项",
  "风险复核",
  "DS-160 初稿",
  "核查清单",
  "预约开户",
  "预约资料"
];

const UPLOAD_SLOTS = [
  "护照",
  "身份证 / National ID",
  "签证照片",
  "旅行行程单",
  "酒店预订单",
  "邀请函",
  "在职证明",
  "银行流水 / 资金支持材料",
  "I-20 / 录取或在读证明",
  "DS-2019 / 交流项目材料",
  "DS-7002 / 培训实习计划",
  "过往美国签证",
  "其他支持材料"
];

const AGENT_STEPS = [
  "OCR / 文档解析 Agent",
  "Personal Information Agent",
  "Passport Information Agent",
  "Travel Information Agent",
  "Previous U.S. Travel Agent",
  "Family / Work Education Agent",
  "SEVIS / Student Info Agent",
  "Consistency Validation Agent",
  "Missing Info Question Agent",
  "DS-160 Draft Mapping Agent",
  "Review Checklist Agent"
];

const BASE_EXTRACTED_FIELDS = [
  {
    id: "personal.surname",
    label: "姓（Surname）",
    section: "基础信息",
    value: "ZHANG",
    sourceDocument: "passport_mock.pdf",
    confidence: 0.97,
    riskLevel: "high",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "personal.givenNames",
    label: "名（Given Names）",
    section: "基础信息",
    value: "WEI",
    sourceDocument: "passport_mock.pdf",
    confidence: 0.96,
    riskLevel: "high",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "personal.dateOfBirth",
    label: "出生日期",
    section: "基础信息",
    value: "1998-04-16",
    sourceDocument: "passport_mock.pdf",
    confidence: 0.92,
    riskLevel: "high",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "passport.number",
    label: "护照号码",
    section: "护照信息",
    value: "E12345678",
    sourceDocument: "passport_mock.pdf",
    confidence: 0.94,
    riskLevel: "high",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "passport.expiration",
    label: "护照有效期至",
    section: "护照信息",
    value: "2031-08-20",
    sourceDocument: "passport_mock.pdf",
    confidence: 0.9,
    riskLevel: "medium",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "travel.visaType",
    label: "签证类型 / 访问目的",
    section: "旅行信息",
    value: "F1 Student",
    sourceDocument: "i20_mock.pdf",
    confidence: 0.88,
    riskLevel: "medium",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "travel.arrivalDate",
    label: "预计抵达美国日期",
    section: "旅行信息",
    value: "2026-08-11",
    sourceDocument: "travel_itinerary_mock.pdf",
    confidence: 0.81,
    riskLevel: "medium",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "contact.usAddress",
    label: "美国停留地址",
    section: "美国联系人",
    value: "1200 College Ave, Seattle, WA",
    sourceDocument: "i20_mock.pdf",
    confidence: 0.74,
    riskLevel: "medium",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "education.schoolName",
    label: "学校名称",
    section: "SEVIS / 学生信息",
    value: "Northwest State University",
    sourceDocument: "i20_mock.pdf",
    confidence: 0.89,
    riskLevel: "low",
    requiresUserConfirmation: false,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "education.sevisId",
    label: "SEVIS ID",
    section: "SEVIS / 学生信息",
    value: "N0034567891",
    sourceDocument: "i20_mock.pdf",
    confidence: 0.86,
    riskLevel: "high",
    requiresUserConfirmation: true,
    confirmed: false,
    editedByUser: false
  },
  {
    id: "work.employerName",
    label: "雇主名称",
    section: "工作 / 教育 / 培训",
    value: "Horizon Analytics Ltd.",
    sourceDocument: "employment_letter_mock.pdf",
    confidence: 0.69,
    riskLevel: "low",
    requiresUserConfirmation: false,
    confirmed: false,
    editedByUser: false
  }
];

const BASE_MISSING_QUESTIONS = [
  {
    id: "contact.phone",
    label: "客户的美国联系人电话是多少？",
    answer: ""
  },
  {
    id: "travel.previousUsTravel",
    label: "客户是否曾经去过美国？",
    answer: ""
  },
  {
    id: "work.schoolAddress",
    label: "客户当前雇主或学校的地址是什么？",
    answer: ""
  }
];

const BASE_VALIDATION_RESULTS = [
  {
    id: "conflict.nameOrder",
    type: "conflict",
    severity: "medium",
    category: "跨材料冲突",
    message: "护照显示 ZHANG WEI，但 I-20 显示 WEI ZHANG。请确认 DS-160 中应填写的姓名顺序。",
    requiresUserResolution: true,
    resolved: false
  },
  {
    id: "missing.usPhone",
    type: "missing",
    severity: "medium",
    category: "缺失信息",
    message: "上传材料中没有找到美国联系人电话。",
    requiresUserResolution: false,
    resolved: false
  },
  {
    id: "low.usAddress",
    type: "low-confidence",
    severity: "low",
    category: "低置信度信息",
    message: "美国停留地址的识别置信度低于复核阈值，建议人工确认。",
    requiresUserResolution: false,
    resolved: false
  },
  {
    id: "sensitive.refusal",
    type: "sensitive",
    severity: "high",
    category: "安全与背景问题",
    message: "过往拒签、拒绝入境、移民申请等敏感问题必须由顾问根据客户真实情况逐项确认，系统不会自动代填。",
    requiresUserResolution: false,
    resolved: false
  }
];

const SAFETY_BOUNDARIES = [
  "客户护照、家庭、工作教育和背景信息应严格保密，并通过权限管理、访问控制和操作记录保护。",
  "本工具仅辅助资料整理、DS-160 初稿生成和核查清单整理，不提供法律建议，不生成获签预测。",
  "系统会先完成格式、来源和跨材料一致性校验；文案老师或签证顾问重点复核关键、冲突和低置信度字段，并负责最终确认。",
  "本演示不连接真实政府网站、不提交 DS-160、不支付费用、不确认法律声明。",
  "安全与背景问题、拒签记录、移民违规等敏感字段必须人工逐项确认，系统不会自动代答。"
];
