const PRODUCT_API = globalThis.DocFlowApi?.apiBaseUrl || "/api";

const DEMO_STAGES = [
  {
    title: "客户档案", eyebrow: "01 · 档案", description: "建立客户基本档案，签证类型、时间节点和所有后续材料沿同一案件持续流转。",
    stats: [["3", "进行中案件"], ["72%", "当前案件进度"], ["F-1", "签证类型"], ["10", "完整流程阶段"]],
    mainTitle: "客户案件", badge: "档案已建立",
    rows: [["林然 · F-1", "创建于 7 月 12 日", "资料整理中", "进行中"], ["周文 · B1/B2", "创建于 7 月 10 日", "待确认项", "等待客户"], ["陈屿 · J-1", "创建于 7 月 8 日", "上传资料", "进行中"]],
    sideTitle: "当前案件", side: [["01", "客户身份", "基本信息已建立"], ["02", "签证路径", "F-1 学生签证"], ["03", "目标进度", "DS-160 与预约资料"]]
  },
  {
    title: "客户资料", eyebrow: "02 · 资料", description: "先读取客户已经提供的材料，护照、I-20、身份证和行程资料自动进入案件。",
    stats: [["3", "已上传文件"], ["42", "已提取字段"], ["100%", "识别完成"], ["0", "重复询问"]],
    mainTitle: "材料识别", badge: "全部完成",
    rows: [["Passport_sample.pdf", "护照 · 12 个字段", "DeepSeek 结构化", "完成"], ["I-20_sample.pdf", "学校与 SEVIS · 17 个字段", "版面识别", "完成"], ["National_ID_sample.jpg", "身份与地址 · 8 个字段", "图像识别", "完成"]],
    sideTitle: "自动处理", side: [["AI", "材料理解", "识别文件内容与证据"], ["译", "中英整理", "统一字段表达"], ["源", "保留来源", "可回看文件与页码"]]
  },
  {
    title: "材料整理", eyebrow: "03 · 整理", description: "DeepSeek 自动理解、翻译和结构化材料内容，再把结果合并到统一字段体系。",
    stats: [["42", "已整理字段"], ["3", "来源文件"], ["6", "发现缺失项"], ["1", "处理流程"]],
    mainTitle: "自动整理队列", badge: "RPA 运行中",
    rows: [["身份与护照", "12 个字段", "来源证据已绑定", "完成"], ["学校与 SEVIS", "17 个字段", "英文原文已保留", "完成"], ["旅行与联系方式", "13 个字段", "正在合并", "处理中"]],
    sideTitle: "模型分工", side: [["D", "DeepSeek", "理解与结构化字段"], ["R", "RPA", "推进重复操作"], ["G", "Gemini", "观察可见页面"]]
  },
  {
    title: "字段核查", eyebrow: "04 · 字段核查", description: "字段、来源、置信状态和差异集中展示，已确认内容保持安静。",
    stats: [["42", "整理字段"], ["39", "无需处理"], ["2", "待核查"], ["1", "信息冲突"]],
    mainTitle: "重点字段", badge: "仅 3 项待处理",
    rows: [["预计抵达日期", "行程单 / 客户补充", "18 JUL / 20 JUL", "信息冲突", "danger"], ["曾用名拼写", "护照第 1 页", "需要核对", "待确认", "warn"], ["美国联系人", "I-20 第 1 页", "学校联系人", "置信度高"]],
    sideTitle: "核查依据", side: [["源", "字段来源", "文件、页码与原文"], ["信", "置信状态", "高、中、待确认"], ["史", "修改记录", "保留人工更正"]]
  },
  {
    title: "待确认项", eyebrow: "05 · 待确认项", description: "系统只生成真正缺失或需要澄清的问题，已有信息不再向客户重复询问。",
    stats: [["6", "需要补充"], ["42", "无需再问"], ["4", "客户已回答"], ["2", "等待回答"]],
    mainTitle: "客户补充问题", badge: "只问缺失内容",
    rows: [["过去五年出境记录", "材料未提供", "等待客户回答", "待补充", "warn"], ["美国联系人电话", "I-20 缺少号码", "已生成问题", "待补充", "warn"], ["预计停留时间", "行程信息", "客户已确认 120 天", "已完成"]],
    sideTitle: "自动问卷", side: [["少", "减少问题", "已有字段自动跳过"], ["合", "合并追问", "同类问题集中发送"], ["回", "答案回流", "自动进入字段核查"]]
  },
  {
    title: "风险复核", eyebrow: "06 · 风险复核", description: "冲突、敏感背景和异常字段提前归集，真正需要专业判断的内容留给顾问。",
    stats: [["3", "复核项目"], ["1", "日期冲突"], ["1", "敏感问题"], ["39", "稳定字段"]],
    mainTitle: "风险与冲突", badge: "顾问复核",
    rows: [["旅行日期不一致", "行程单 / 补充答案", "差异 2 天", "需要判断", "danger"], ["安全背景问题", "客户本人陈述", "必须逐项确认", "敏感内容", "warn"], ["曾用名英文拼写", "护照 / 旧材料", "差异已解释", "已解决"]],
    sideTitle: "人工边界", side: [["停", "异常即暂停", "不会带错继续"], ["核", "敏感项确认", "由顾问逐项判断"], ["接", "随时接管", "控制权始终可收回"]]
  },
  {
    title: "DS-160 初稿", eyebrow: "07 · DS-160 初稿", description: "确认后的结果直接进入 DS-160 页面结构，不停留在孤立的 OCR、表格或问卷中。",
    stats: [["42", "已确认字段"], ["8", "页面模块"], ["6", "待确认项"], ["86%", "初稿完成度"]],
    mainTitle: "DS-160 页面结构", badge: "初稿已生成",
    rows: [["Personal Information", "12 / 12 字段", "来源已核对", "完成"], ["Travel Information", "8 / 10 字段", "2 项待确认", "进行中", "warn"], ["Work / Education", "11 / 13 字段", "学校信息已整理", "进行中"]],
    sideTitle: "填写准备", side: [["页", "按页面组织", "对应 DS-160 结构"], ["源", "字段可追溯", "填写前仍可回看"], ["核", "确认后推进", "不跳过未决问题"]]
  },
  {
    title: "核查清单", eyebrow: "08 · 核查清单", description: "在进入页面辅助前形成完整核查清单，让顾问一次看清未决问题和关键操作。",
    stats: [["28", "检查项目"], ["24", "已经通过"], ["3", "需要确认"], ["1", "人工操作"]],
    mainTitle: "提交前核查", badge: "24 / 28 通过",
    rows: [["身份与护照一致性", "12 项检查", "全部匹配", "通过"], ["旅行信息完整性", "8 项检查", "2 项待确认", "核查中", "warn"], ["安全背景逐项确认", "顾问负责", "尚未最终确认", "人工确认", "danger"]],
    sideTitle: "必须人工", side: [["码", "验证码", "不绕过"], ["签", "电子签名", "不替客户完成"], ["交", "最终提交", "由顾问决定"]]
  },
  {
    title: "预约开户", eyebrow: "09 · 预约开户", description: "DS-160 流程完成后，继续准备预约系统所需账户资料，不必重新搬运同一批信息。",
    stats: [["1", "预约档案"], ["9", "可复用字段"], ["2", "待确认项"], ["0", "重复录入"]],
    mainTitle: "预约账户准备", badge: "资料可复用",
    rows: [["账户邮箱", "客户确认", "linran@example.test", "就绪"], ["护照身份", "沿用已核查字段", "姓名与号码一致", "就绪"], ["安全验证方式", "需要顾问选择", "尚未设置", "待确认", "warn"]],
    sideTitle: "连续推进", side: [["复", "复用字段", "不再手工搬运"], ["验", "关键验证", "保留人工完成"], ["停", "页面异常", "识别后自动暂停"]]
  },
  {
    title: "预约资料", eyebrow: "10 · 预约资料", description: "把预约所需信息整理在同一案件中，形成从材料到后续预约准备的完整闭环。",
    stats: [["9", "预约字段"], ["7", "已经就绪"], ["2", "顾问确认"], ["100%", "来源可追溯"]],
    mainTitle: "预约资料包", badge: "最后准备阶段",
    rows: [["申请人与护照信息", "沿用案件档案", "已核查", "就绪"], ["DS-160 确认信息", "初稿流程结果", "等待确认页", "待确认", "warn"], ["预约地点与时间", "顾问选择", "尚未决定", "人工处理", "danger"]],
    sideTitle: "流程结果", side: [["10", "十阶段贯通", "同一案件持续推进"], ["8m", "内部模拟测试", "约 8 分钟跑通"], ["人", "顾问最终掌控", "关键步骤不越过"]]
  }
];

function renderRows(rows) {
  return rows.map((row) => `<div class="data-row"><div><strong>${row[0]}</strong><small>${row[1]}</small></div><span>${row[2]}</span><em class="${row[4] || ""}">${row[3]}</em></div>`).join("");
}

function renderStage(stage, index) {
  const screen = document.querySelector("#demoScreen");
  if (!screen) return;
  screen.setAttribute("aria-labelledby", `flowTab${index}`);
  screen.dataset.demoStage = String(index);
  screen.innerHTML = `
    <div class="screen-top">
      <div class="screen-title"><span>${stage.eyebrow}</span><h3>${stage.title}</h3><p>${stage.description}</p></div>
      <div class="screen-meta"><span>模拟案件</span><strong>林然 · F-1</strong><small>自动保存 · 刚刚</small></div>
    </div>
    <div class="screen-stats">${stage.stats.map((item) => `<div><strong>${item[0]}</strong><span>${item[1]}</span></div>`).join("")}</div>
    <div class="screen-grid">
      <section class="screen-card"><header class="card-head"><strong>${stage.mainTitle}</strong><span>${stage.badge}</span></header><div class="data-rows">${renderRows(stage.rows)}</div></section>
      <section class="screen-card"><header class="card-head"><strong>${stage.sideTitle}</strong><span>自动同步</span></header><div class="side-list">${stage.side.map((item) => `<div class="side-item"><i>${item[0]}</i><div><strong>${item[1]}</strong><small>${item[2]}</small></div></div>`).join("")}</div></section>
      <section class="screen-card wide-card"><header class="card-head"><strong>案件完整进度</strong><span>${index + 1} / ${DEMO_STAGES.length}</span></header><div class="timeline">${DEMO_STAGES.slice(Math.max(0, Math.min(index - 2, 5)), Math.max(0, Math.min(index - 2, 5)) + 5).map((item, visibleIndex) => { const absolute = Math.max(0, Math.min(index - 2, 5)) + visibleIndex; return `<div class="${absolute === index ? "active" : ""}"><strong>${String(absolute + 1).padStart(2, "0")} · ${item.title}</strong><small>${absolute < index ? "已完成" : absolute === index ? "当前阶段" : "等待推进"}</small></div>`; }).join("")}</div></section>
    </div>`;
}

function initializeDemo() {
  const player = document.querySelector("[data-demo-player]");
  if (!player) return;
  const tabs = [...player.querySelectorAll("[data-demo-chapter]")];
  const toggle = player.querySelector("#demoToggle");
  const progress = player.querySelector("#demoProgress");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const duration = 5200;
  let current = 0;
  let elapsed = 0;
  let previousTime = 0;
  let visible = false;
  let wantsPlay = !reducedMotion.matches;

  const isPlaying = () => wantsPlay && visible && !reducedMotion.matches;
  const update = () => {
    tabs.forEach((tab, index) => {
      const active = index === current;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    renderStage(DEMO_STAGES[current], current);
    progress.style.width = `${Math.min(100, ((current * duration + elapsed) / (DEMO_STAGES.length * duration)) * 100)}%`;
    toggle.classList.toggle("paused", !isPlaying());
    toggle.querySelector("span").textContent = isPlaying() ? "暂停自动播放" : "继续自动播放";
    toggle.setAttribute("aria-label", isPlaying() ? "暂停自动播放" : "继续自动播放");
  };
  const select = (index, focus = false) => {
    current = (index + DEMO_STAGES.length) % DEMO_STAGES.length;
    elapsed = 0;
    update();
    tabs[current].scrollIntoView({ block: "nearest", inline: "center", behavior: reducedMotion.matches ? "auto" : "smooth" });
    if (focus) tabs[current].focus();
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(index));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") select(0, true);
      else if (event.key === "End") select(tabs.length - 1, true);
      else select(index + (event.key === "ArrowRight" ? 1 : -1), true);
    });
  });
  toggle.addEventListener("click", () => { wantsPlay = !wantsPlay; update(); });
  reducedMotion.addEventListener?.("change", (event) => { if (event.matches) wantsPlay = false; update(); });
  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting && entry.intersectionRatio > .15; update(); }, { threshold: [0, .15, .5] }).observe(player);
  const tick = (time) => {
    if (!previousTime) previousTime = time;
    if (isPlaying()) {
      elapsed += time - previousTime;
      if (elapsed >= duration) select(current + 1);
      else progress.style.width = `${Math.min(100, ((current * duration + elapsed) / (DEMO_STAGES.length * duration)) * 100)}%`;
    }
    previousTime = time;
    requestAnimationFrame(tick);
  };
  update();
  requestAnimationFrame(tick);
}

function initializeNavigation() {
  const header = document.querySelector("#siteHeader");
  const button = document.querySelector("#menuButton");
  const menu = document.querySelector("#mobileNav");
  const setOpen = (open) => {
    button?.setAttribute("aria-expanded", String(open));
    if (open) { menu.hidden = false; requestAnimationFrame(() => menu.classList.add("open")); }
    else { menu.classList.remove("open"); setTimeout(() => { menu.hidden = true; }, 210); }
  };
  window.addEventListener("scroll", () => header?.classList.toggle("scrolled", window.scrollY > 16), { passive: true });
  button?.addEventListener("click", () => setOpen(button.getAttribute("aria-expanded") !== "true"));
  menu?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => setOpen(false)));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") setOpen(false); });
}

async function loadPublicConfiguration() {
  if (!globalThis.DocFlowApi || window.location.protocol === "file:") return;
  try {
    const response = await DocFlowApi.request(`${PRODUCT_API}/product/config`, { cache: "no-store" });
    if (!response.ok) throw new Error("Public configuration unavailable");
    await response.json();
  } catch (_error) {
    // The public demonstration remains fully usable without server configuration.
  }
}

initializeNavigation();
initializeDemo();
loadPublicConfiguration();
