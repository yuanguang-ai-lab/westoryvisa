const DEMO_STAGES = [
  {
    title: "客户档案", eyebrow: "01 · 档案", description: "建立客户档案后，签证类型、负责人、时间节点和后续材料始终沿同一案件流转。", status: "3 个进行中案件",
    rows: [["客户 A-1024 · F-1", "近期建立", "资料整理中", "进行中"], ["客户 B-2086 · B1/B2", "等待补充", "等待客户补充", "待确认"], ["客户 C-3168 · J-1", "字段处理中", "字段核查", "进行中"]],
    side: [["负责人", "顾问 01"], ["签证路径", "F-1 学生签证"], ["目标日期", "2027-06-18"], ["当前进度", "72%"]]
  },
  {
    title: "客户资料", eyebrow: "02 · 资料", description: "护照、I-20、身份证和行程资料进入同一案件，系统保留文件、页码和原文证据。", status: "3 / 3 已读取",
    rows: [["Passport_A1024_masked.pdf", "护照 · 12 个字段", "DeepSeek 结构化", "完成"], ["I-20_A1024_masked.pdf", "学校与 SEVIS · 17 个字段", "版面识别", "完成"], ["National_ID_A1024_masked.jpg", "身份与地址 · 8 个字段", "图像识别", "完成"]],
    side: [["已提取字段", "42"], ["识别失败", "0"], ["重复材料", "0"], ["最新处理", "刚刚"]]
  },
  {
    title: "材料整理", eyebrow: "03 · 整理", description: "DeepSeek 理解并翻译材料内容，RPA 把结果合并到统一的 DS-160 字段体系。", status: "自动整理中",
    rows: [["身份与护照", "12 个字段", "来源证据已绑定", "完成"], ["学校与 SEVIS", "17 个字段", "英文原文已保留", "完成"], ["旅行与联系方式", "13 个字段", "正在合并", "处理中"]],
    side: [["DeepSeek", "材料理解"], ["RPA", "重复操作"], ["Gemini", "页面观察"], ["异常处理", "自动暂停"]]
  },
  {
    title: "字段核查", eyebrow: "04 · 字段核查", description: "只把有冲突、低置信度或缺少来源的字段交给顾问处理，已确认内容保持安静。", status: "3 项需要处理",
    rows: [["预计抵达日期", "行程单 / 客户补充", "18 JUL / 20 JUL", "信息冲突", "danger"], ["曾用名拼写", "护照第 1 页", "需要核对", "待确认", "warn"], ["美国联系人", "I-20 第 1 页", "学校联系人", "已确认"]],
    side: [["整理字段", "42"], ["无需处理", "39"], ["待核查", "2"], ["信息冲突", "1"]]
  },
  {
    title: "待确认项", eyebrow: "05 · 待确认项", description: "系统只生成真正缺失或需要澄清的问题，客户已经提供过的信息不会重复询问。", status: "2 项等待回答",
    rows: [["过去五年出境记录", "材料未提供", "等待客户回答", "待补充", "warn"], ["美国联系人电话", "I-20 缺少号码", "已生成问题", "待补充", "warn"], ["预计停留时间", "行程信息", "120 天", "已完成"]],
    side: [["需要补充", "6"], ["客户已回答", "4"], ["无需再问", "42"], ["链接有效期", "48 小时"]]
  },
  {
    title: "风险复核", eyebrow: "06 · 风险复核", description: "冲突、敏感背景和异常字段提前归集；需要专业判断的内容始终留给顾问。", status: "顾问复核",
    rows: [["旅行日期不一致", "行程单 / 补充答案", "相差 2 天", "需要判断", "danger"], ["安全背景问题", "客户本人陈述", "必须逐项确认", "敏感内容", "warn"], ["曾用名英文拼写", "护照 / 旧材料", "差异已解释", "已解决"]],
    side: [["复核项目", "3"], ["日期冲突", "1"], ["敏感问题", "1"], ["稳定字段", "39"]]
  },
  {
    title: "DS-160 初稿", eyebrow: "07 · DS-160 初稿", description: "按 DS-160 模块形成可复核初稿，并从同一案件进入 Computer Use 逐页填写执行台。", status: "初稿已生成"
  },
  {
    title: "核查清单", eyebrow: "08 · 核查清单", description: "在离开系统前集中核对来源、未决问题与人工操作边界，形成可导出的审计清单。", status: "24 / 28 已通过",
    rows: [["身份与护照一致性", "12 项检查", "全部匹配", "通过"], ["旅行信息完整性", "8 项检查", "2 项待确认", "核查中", "warn"], ["安全背景逐项确认", "顾问负责", "尚未最终确认", "人工确认", "danger"]],
    side: [["检查项目", "28"], ["已经通过", "24"], ["需要确认", "3"], ["人工操作", "1"]]
  }
];

let demoStageMode = "preview";

function escapeDemo(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[character]);
}

function renderModeSwitch(active) {
  return `<div class="demo-mode-switch" aria-label="DS-160 视图切换">
    <button type="button" class="${active === "preview" ? "active" : ""}" data-demo-mode="preview">初稿预览</button>
    <button type="button" class="${active === "computer" ? "active" : ""}" data-demo-mode="computer">Computer Use 执行台</button>
  </div>`;
}

function renderDraftPreview() {
  return `
    <div class="real-screen-toolbar"><div><span>⌂</span> 工作台</div><i>/</i><div>← 上一步</div>${renderModeSwitch("preview")}</div>
    <div class="draft-hero">
      <span>B1/B2 访问签证</span>
      <h3>DS-160 初稿预览</h3>
      <p>按 DS-160 模块展示可复核的填写初稿。敏感背景问题仅显示提醒，不自动代填。</p>
      <div>客户 A-1024　负责人：顾问 01　初稿已生成　B1/B2 访问签证　更新于 2026年8月16日 14:30</div>
    </div>
    <div class="draft-boundary">初稿仅供中介人员核查。Computer Use 可以在可见 Chrome 中辅助写入 CEAC，但验证码、敏感背景判断、电子签名和最终提交必须由人工完成。</div>
    <div class="draft-grid">
      <section class="draft-card draft-card-wide"><header><h4>申请信息</h4><span>8 个模块</span></header><div class="draft-field"><span>计划申请的使领馆国家 / 地区</span><strong>CHINA</strong><em>待人工核查</em></div><div class="draft-field"><span>签证类型 / 访问目的</span><strong>B1/B2 访问签证</strong><em class="ok">已确认</em></div><div class="draft-field"><span>预计抵达美国日期</span><strong>2027-06-18</strong><em class="ok">已确认</em></div></section>
      <section class="draft-card"><header><h4>基础信息</h4><span>来源可追溯</span></header><div class="draft-field stacked"><span>姓（Surname）</span><strong>W***</strong></div><div class="draft-field stacked"><span>名（Given Names）</span><strong>A***</strong></div><div class="draft-field stacked"><span>出生日期</span><strong>2001-08-19</strong></div><div class="draft-field stacked"><span>性别</span><strong>MALE</strong></div></section>
    </div>
    <button class="demo-primary-action" type="button" data-demo-mode="computer">查看 Computer Use 逐页填写界面 →</button>`;
}

function renderComputerUse() {
  const fields = [["基础信息 · 姓（Surname）", "W***"], ["基础信息 · 名（Given Names）", "A***"], ["基础信息 · 出生日期", "2001-08-19"], ["护照信息 · 护照号码", "P•••••••1"], ["护照信息 · 护照有效期至", "2031-05-26"], ["旅行信息 · 签证类型 / 访问目的", "B1/B2 访问签证"], ["旅行信息 · 预计抵达日期", "2027-06-18"], ["美国联系人 · 地址", "Seattle, WA 98***"]];
  return `
    <div class="real-screen-toolbar"><div><span>⌂</span> 工作台</div><i>/</i><div>← DS-160 初稿</div>${renderModeSwitch("computer")}</div>
    <section class="computer-banner"><div><i></i><strong>Codex Computer Use</strong><span>系统级可见操作 · 无需 Chrome 扩展</span></div><div><em>任务已准备</em><span>0%</span></div></section>
    <section class="computer-ready"><strong>Computer Use 执行通道已就绪</strong><span>WestoryVisa 只准备短时字段任务并打开 CEAC；实际点击、输入、下拉选择与页面复读由 Codex Desktop 的 Computer Use 完成。</span></section>
    <div class="computer-workspace">
      <main class="computer-main">
        <header><div><span>CURRENT SCOPE</span><h3>当前客户的逐页字段计划</h3></div><em>60 分钟本机任务</em></header>
        <div class="computer-flow">${[["整理字段", "生成当前档案白名单"], ["准备任务", "打开官方起始页"], ["人工进入", "验证码与初始步骤"], ["可见填写", "逐项复读并受控 Next"], ["人工核查", "敏感或未映射页暂停"]].map(([title, copy], index) => `<div class="${index === 0 ? "active" : ""}"><i>${index + 1}</i><strong>${title}</strong><span>${copy}</span></div>`).join("")}</div>
        <section class="computer-field-plan"><header><div><span>FIELD PLAN</span><h3>可交接信息预览</h3></div><strong>24 项预览</strong></header>${fields.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("")}</section>
      </main>
      <aside class="computer-console">
        <header><div><span>LOCAL COMPUTER</span><h3>当前执行状态</h3></div><em>WORKFLOW V4</em></header>
        <div class="console-progress"><div><span>字段进度</span><strong>0%</strong></div><i><b></b></i><p>准备好后从这里打开 CEAC</p></div>
        <div class="console-grid"><div><span>Agent</span><strong>Computer Use</strong></div><div><span>Browser</span><strong>当前 Chrome</strong></div><div><span>节奏</span><strong>0.9–1.5s</strong></div><div><span>Next</span><strong>核验后</strong></div></div>
        <div class="console-line"><span>目标网站</span><strong>ceac.state.gov/GenNIV</strong></div>
        <div class="console-line console-route"><span>已记录页面路径</span><strong>0</strong><small>已映射 0 · 当前尚未捕获表格路径</small></div>
        <div class="console-note"><strong>尚未准备本机任务</strong><span>一次性令牌只保存在当前页面内存中，任务关闭后服务器会擦除字段值。</span></div>
        <div class="console-toggle"><span><strong>普通页面连续填写</strong><small>全部复读无误后才点击 Next</small></span><i></i></div>
        <button type="button" disabled aria-disabled="true">准备任务并打开 CEAC</button>
      </aside>
    </div>`;
}

function renderStandardStage(stage, index) {
  return `
    <div class="real-screen-toolbar"><div><span>⌂</span> 工作台</div><i>/</i><div>← 上一步</div></div>
    <div class="screen-top real-screen-top"><div class="screen-title"><span>${escapeDemo(stage.eyebrow)}</span><h3>${escapeDemo(stage.title)}</h3><p>${escapeDemo(stage.description)}</p></div><div class="screen-meta"><span>客户 A-1024 · B1/B2</span><strong>${escapeDemo(stage.status)}</strong><small>自动保存 · 刚刚</small></div></div>
    <div class="screen-grid real-screen-grid">
      <section class="screen-card real-data-card"><header class="card-head"><strong>${index === 7 ? "提交前核查" : "当前工作区"}</strong><span>${escapeDemo(stage.status)}</span></header><div class="data-rows">${stage.rows.map((row) => `<div class="data-row"><div><strong>${escapeDemo(row[0])}</strong><small>${escapeDemo(row[1])}</small></div><span>${escapeDemo(row[2])}</span><em class="${escapeDemo(row[4] || "")}">${escapeDemo(row[3])}</em></div>`).join("")}</div></section>
      <section class="screen-card real-summary-card"><header class="card-head"><strong>案件摘要</strong><span>${index + 1} / ${DEMO_STAGES.length}</span></header><div class="summary-list">${stage.side.map(([label, value]) => `<div><span>${escapeDemo(label)}</span><strong>${escapeDemo(value)}</strong></div>`).join("")}</div></section>
    </div>`;
}

function renderStage(stage, index) {
  const screen = document.querySelector("#demoScreen");
  if (!screen) return;
  screen.setAttribute("aria-labelledby", `flowTab${index}`);
  screen.dataset.demoStage = String(index);
  if (index !== 6) demoStageMode = "preview";
  screen.innerHTML = index === 6
    ? (demoStageMode === "computer" ? renderComputerUse() : renderDraftPreview())
    : renderStandardStage(stage, index);
}

function initializeDemo() {
  const player = document.querySelector("[data-demo-player]");
  if (!player) return;
  const tabs = [...player.querySelectorAll("[data-demo-chapter]")];
  const toggle = player.querySelector("#demoToggle");
  const progress = player.querySelector("#demoProgress");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const duration = 6200;
  let current = 0;
  let elapsed = 0;
  let previousTime = 0;
  let visible = false;
  let wantsPlay = !reducedMotion.matches;

  player.addEventListener("click", (event) => {
    const allowedControl = event.target.closest("[data-demo-chapter], [data-demo-mode], #demoToggle");
    if (allowedControl) return;
    if (event.target.closest("a, button")) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  const isPlaying = () => wantsPlay && visible && !reducedMotion.matches;
  const wireModeButtons = () => {
    player.querySelectorAll("[data-demo-mode]").forEach((button) => button.addEventListener("click", () => {
      demoStageMode = button.dataset.demoMode;
      wantsPlay = false;
      elapsed = 0;
      renderStage(DEMO_STAGES[6], 6);
      wireModeButtons();
      updatePlayback();
    }));
  };
  const updatePlayback = () => {
    progress.style.width = `${Math.min(100, ((current * duration + elapsed) / (DEMO_STAGES.length * duration)) * 100)}%`;
    toggle.classList.toggle("paused", !isPlaying());
    toggle.querySelector("span").textContent = isPlaying() ? "暂停自动播放" : "继续自动播放";
    toggle.setAttribute("aria-label", isPlaying() ? "暂停自动播放" : "继续自动播放");
  };
  const update = () => {
    tabs.forEach((tab, index) => {
      const active = index === current;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    renderStage(DEMO_STAGES[current], current);
    wireModeButtons();
    updatePlayback();
  };
  const select = (index, focus = false) => {
    current = (index + DEMO_STAGES.length) % DEMO_STAGES.length;
    elapsed = 0;
    update();
    tabs[current].scrollIntoView({ block: "nearest", inline: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
    if (focus) tabs[current].focus();
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => { wantsPlay = false; select(index); });
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      wantsPlay = false;
      if (event.key === "Home") select(0, true);
      else if (event.key === "End") select(tabs.length - 1, true);
      else select(index + (["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1), true);
    });
  });
  toggle.addEventListener("click", () => { wantsPlay = !wantsPlay; updatePlayback(); });
  reducedMotion.addEventListener?.("change", (event) => { if (event.matches) wantsPlay = false; updatePlayback(); });
  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting && entry.intersectionRatio > .15; updatePlayback(); }, { threshold: [0, .15, .5] }).observe(player);
  const tick = (time) => {
    if (!previousTime) previousTime = time;
    if (isPlaying()) {
      elapsed += time - previousTime;
      if (elapsed >= duration) select(current + 1);
      else updatePlayback();
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

function initializeFeatureCarousel() {
  const track = document.querySelector("#featureCarousel");
  const previous = document.querySelector("#detailPrev");
  const next = document.querySelector("#detailNext");
  if (!track || !previous || !next) return;

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const card = track.querySelector(".feature-card");
  const step = () => Math.max(280, Math.min(track.clientWidth * .82, (card?.getBoundingClientRect().width || 360) + 22));
  const update = () => {
    previous.disabled = track.scrollLeft < 8;
    next.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 8;
  };
  const move = (direction) => track.scrollBy({ left: direction * step(), behavior: reducedMotion.matches ? "auto" : "smooth" });

  previous.addEventListener("click", () => move(-1));
  next.addEventListener("click", () => move(1));
  track.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update, { passive: true });

  let dragging = false;
  let startX = 0;
  let startScroll = 0;
  track.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "touch") return;
    dragging = true;
    startX = event.clientX;
    startScroll = track.scrollLeft;
    track.classList.add("dragging");
    track.setPointerCapture(event.pointerId);
  });
  track.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    track.scrollLeft = startScroll - (event.clientX - startX);
  });
  const stopDragging = () => {
    dragging = false;
    track.classList.remove("dragging");
  };
  track.addEventListener("pointerup", stopDragging);
  track.addEventListener("pointercancel", stopDragging);
  update();
}

initializeNavigation();
initializeDemo();
initializeFeatureCarousel();
