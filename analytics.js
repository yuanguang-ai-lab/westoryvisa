const ANALYTICS_API = "/api";
const dashboardState = {
  days: 30,
  summary: null,
  config: null,
  loading: false
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDuration(milliseconds) {
  const totalSeconds = Math.max(0, Math.round(Number(milliseconds || 0) / 1000));
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes} 分 ${seconds} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  }).format(date);
}

function showNotice(message, type = "") {
  const notice = document.querySelector("#analyticsNotice");
  notice.textContent = message;
  notice.className = `analytics-notice ${type}`.trim();
  notice.hidden = !message;
}

async function request(path, options = {}) {
  const response = await fetch(`${ANALYTICS_API}${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    const error = new Error("请先登录机构工作台，再查看落地页数据。");
    error.code = "unauthorized";
    throw error;
  }
  if (!response.ok) throw new Error(data.error || "数据请求失败");
  return data;
}

function renderMetrics(summary) {
  const totals = summary.totals || {};
  document.querySelector("#metricVisitors").textContent = Number(totals.visitors || 0).toLocaleString("zh-CN");
  document.querySelector("#metricPageViews").textContent = Number(totals.pageViews || 0).toLocaleString("zh-CN");
  document.querySelector("#metricDwell").textContent = formatDuration(totals.averageActiveMs);
  document.querySelector("#metricConversion").textContent = `${Number(totals.wjxConversionRate || 0).toFixed(1)}%`;
  document.querySelector("#metricWjxVisitors").textContent = Number(totals.wjxVisitors || 0).toLocaleString("zh-CN");
}

function renderTrend(summary) {
  const chart = document.querySelector("#trendChart");
  const trend = (summary.trend || []).slice(-30);
  if (!trend.length) {
    chart.innerHTML = `<div class="chart-empty">接受匿名统计的访客出现后，这里会显示每日趋势。</div>`;
    document.querySelector("#trendTotal").textContent = "暂无数据";
    return;
  }
  const maximum = Math.max(...trend.map((item) => Number(item.visitors || 0)), 1);
  chart.innerHTML = trend.map((item, index) => {
    const visitors = Number(item.visitors || 0);
    const height = Math.max(3, Math.round(visitors / maximum * 100));
    const date = new Date(`${item.day}T00:00:00`);
    const label = `${date.getMonth() + 1}/${date.getDate()}`;
    const showLabel = trend.length <= 14 || index % Math.ceil(trend.length / 8) === 0 || index === trend.length - 1;
    return `
      <div class="trend-column" title="${escapeHtml(item.day)}：${visitors} 位访客">
        <b>${visitors}</b>
        <i style="height:${height}%"></i>
        <span>${showLabel ? label : ""}</span>
      </div>
    `;
  }).join("");
  document.querySelector("#trendTotal").textContent = `图表显示最近 ${trend.length} 个有访问记录的日期`;
}

function renderDevices(summary) {
  const container = document.querySelector("#deviceList");
  const devices = summary.devices || [];
  const total = devices.reduce((sum, item) => sum + Number(item.visitors || 0), 0) || 1;
  const labels = { desktop: "桌面端", mobile: "移动端", tablet: "平板", unknown: "未知" };
  if (!devices.length) {
    container.innerHTML = `<div class="rank-empty">暂无设备数据</div>`;
    return;
  }
  container.innerHTML = devices.map((item) => {
    const count = Number(item.visitors || 0);
    const percentage = Math.round(count / total * 100);
    return `
      <div class="device-row">
        <span>${escapeHtml(labels[item.device] || item.device)}</span>
        <div class="device-bar"><i style="width:${percentage}%"></i></div>
        <b>${percentage}%</b>
      </div>
    `;
  }).join("");
}

function renderRankList(selector, items, valueKey, emptyText) {
  const container = document.querySelector(selector);
  if (!items?.length) {
    container.innerHTML = `<div class="rank-empty">${escapeHtml(emptyText)}</div>`;
    return;
  }
  container.innerHTML = items.map((item, index) => `
    <div class="rank-row">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <b>${escapeHtml(item[valueKey] || "未命名")}</b>
      <strong>${Number(item.count ?? item.visitors ?? 0).toLocaleString("zh-CN")}</strong>
    </div>
  `).join("");
}

function renderSessions(summary) {
  const rows = document.querySelector("#sessionRows");
  const empty = document.querySelector("#sessionEmpty");
  const sessions = summary.recentSessions || [];
  empty.hidden = sessions.length > 0;
  rows.innerHTML = sessions.map((session) => `
    <tr tabindex="0" data-session-id="${escapeHtml(session.id)}" aria-label="查看 ${escapeHtml(formatDateTime(session.first_seen_at))} 的访问路径">
      <td>${escapeHtml(formatDateTime(session.first_seen_at))}</td>
      <td>${escapeHtml(formatDuration(session.active_ms))}</td>
      <td>${escapeHtml(session.utm_source || session.referrer_host || "direct")}</td>
      <td>${escapeHtml({ desktop: "桌面", mobile: "手机", tablet: "平板" }[session.device_type] || "未知")}</td>
      <td>${Number(session.page_views || 0)} 次</td>
      <td>${session.converted_wjx ? `<span class="converted">已打开</span>` : `<span class="not-converted">未打开</span>`}</td>
    </tr>
  `).join("");

  rows.querySelectorAll("[data-session-id]").forEach((row) => {
    const open = () => openSession(row.dataset.sessionId);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });
}

function renderSummary(summary) {
  renderMetrics(summary);
  renderTrend(summary);
  renderDevices(summary);
  renderRankList("#clickList", summary.clicks, "target", "还没有按钮点击记录");
  renderRankList("#sectionList", summary.sections, "section_name", "还没有模块浏览记录");
  renderSessions(summary);
}

async function loadSummary() {
  if (dashboardState.loading) return;
  dashboardState.loading = true;
  showNotice("");
  try {
    const summary = await request(`/product/analytics?days=${dashboardState.days}`);
    dashboardState.summary = summary;
    renderSummary(summary);
    document.querySelector("#rangeLabel").textContent = `最近 ${dashboardState.days} 天`;
  } catch (error) {
    showNotice(error.message, "error");
    if (error.code === "unauthorized") {
      window.setTimeout(() => { window.location.href = "/index.html"; }, 1800);
    }
  } finally {
    dashboardState.loading = false;
  }
}

async function loadConfig() {
  const status = document.querySelector("#settingsStatus");
  try {
    const response = await fetch(`${ANALYTICS_API}/product/config`, { cache: "no-store" });
    const config = await response.json();
    dashboardState.config = config;
    document.querySelector("#wjxSurveyUrl").value = config.wjxSurveyUrl || "";
    status.textContent = config.wjxConfigured ? "问卷星已连接，产品页预约按钮可以正常打开。" : "尚未配置，粘贴问卷星发布链接后保存。";
    status.className = config.wjxConfigured ? "success" : "";
  } catch (_error) {
    status.textContent = "暂时无法读取问卷星配置。";
    status.className = "error";
  }
}

async function saveConfig(event) {
  event.preventDefault();
  const button = document.querySelector("#saveWjxSettings");
  const status = document.querySelector("#settingsStatus");
  const value = document.querySelector("#wjxSurveyUrl").value.trim();
  button.disabled = true;
  button.textContent = "保存中";
  status.className = "";
  try {
    const config = await request("/product/settings", {
      method: "POST",
      body: JSON.stringify({ wjxSurveyUrl: value })
    });
    dashboardState.config = config;
    status.textContent = value ? "已保存。重新打开产品页即可使用新的问卷。" : "已清除问卷星链接。";
    status.className = "success";
  } catch (error) {
    status.textContent = error.message;
    status.className = "error";
  } finally {
    button.disabled = false;
    button.textContent = "保存链接";
  }
}

const eventLabels = {
  page_view: "进入产品页",
  click: "点击",
  section_view: "看到内容模块",
  dwell: "有效停留",
  wjx_open: "打开问卷星",
  wjx_close: "关闭问卷星"
};

async function openSession(sessionId) {
  const layer = document.querySelector("#sessionDrawer");
  const summary = document.querySelector("#drawerSummary");
  const timeline = document.querySelector("#eventTimeline");
  layer.classList.add("open");
  layer.setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
  summary.innerHTML = `<div><span>正在读取</span><b>—</b></div>`;
  timeline.innerHTML = "";
  try {
    const data = await request(`/product/analytics/sessions/${encodeURIComponent(sessionId)}`);
    const session = data.session || {};
    summary.innerHTML = `
      <div><span>开始时间</span><b>${escapeHtml(formatDateTime(session.first_seen_at))}</b></div>
      <div><span>有效停留</span><b>${escapeHtml(formatDuration(session.active_ms))}</b></div>
      <div><span>访问来源</span><b>${escapeHtml(session.utm_source || session.referrer_host || "direct")}</b></div>
    `;
    timeline.innerHTML = (data.events || []).map((item) => {
      const detail = item.event_type === "dwell"
        ? formatDuration(item.active_ms)
        : (item.target || item.section_name || item.page_path || "");
      return `
        <li>
          <time>${escapeHtml(formatDateTime(item.created_at))}</time>
          <div><strong>${escapeHtml(eventLabels[item.event_type] || item.event_type)}</strong><span>${escapeHtml(detail)}</span></div>
        </li>
      `;
    }).join("") || `<li><div><strong>没有事件明细</strong></div></li>`;
  } catch (error) {
    timeline.innerHTML = `<li><div><strong>读取失败</strong><span>${escapeHtml(error.message)}</span></div></li>`;
  }
}

function closeSession() {
  const layer = document.querySelector("#sessionDrawer");
  layer.classList.remove("open");
  layer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
}

function initializeDashboardEvents() {
  document.querySelectorAll("[data-days]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-days]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      dashboardState.days = Number(button.dataset.days || 30);
      loadSummary();
    });
  });
  document.querySelector("#wjxSettingsForm")?.addEventListener("submit", saveConfig);
  document.querySelectorAll("[data-close-drawer]").forEach((button) => button.addEventListener("click", closeSession));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSession();
  });
}

async function initializeDashboard() {
  initializeDashboardEvents();
  await Promise.all([loadSummary(), loadConfig()]);
}

initializeDashboard();
