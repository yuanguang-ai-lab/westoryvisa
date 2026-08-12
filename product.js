const PRODUCT_API = "/api";
const CONSENT_KEY = "docflowProductAnalyticsConsent";
const SESSION_KEY = "docflowProductAnalyticsSession";

const productState = {
  config: {
    wjxSurveyUrl: "",
    wjxConfigured: false,
    analyticsConsentVersion: "anonymous-product-analytics-v1"
  },
  analyticsEnabled: false,
  viewedSections: new Set(),
  visibleSince: null,
  lastFocusedElement: null
};

function randomId(prefix = "evt") {
  const raw = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
    || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}_${raw}`;
}

function analyticsSessionId() {
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = randomId("visit");
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }
  return sessionId;
}

function referrerHost() {
  if (!document.referrer) return "direct";
  try {
    return new URL(document.referrer).hostname || "direct";
  } catch (_error) {
    return "unknown";
  }
}

function deviceType() {
  if (window.innerWidth < 720) return "mobile";
  if (window.innerWidth < 1100) return "tablet";
  return "desktop";
}

function eventPayload(eventType, details = {}) {
  const query = new URLSearchParams(window.location.search);
  return {
    sessionId: analyticsSessionId(),
    eventId: randomId("event"),
    eventType,
    pagePath: window.location.pathname.slice(0, 300),
    target: String(details.target || "").slice(0, 240),
    section: String(details.section || "").slice(0, 160),
    activeMs: Number(details.activeMs || 0),
    referrerHost: referrerHost(),
    utmSource: query.get("utm_source") || "",
    utmMedium: query.get("utm_medium") || "",
    utmCampaign: query.get("utm_campaign") || "",
    deviceType: deviceType(),
    locale: navigator.language || "",
    consentVersion: productState.config.analyticsConsentVersion
  };
}

function sendAnalytics(eventType, details = {}, useBeacon = false) {
  if (!productState.analyticsEnabled) return;
  const payload = eventPayload(eventType, details);
  const body = JSON.stringify(payload);
  if (useBeacon && navigator.sendBeacon) {
    navigator.sendBeacon(
      `${PRODUCT_API}/product/analytics/events`,
      new Blob([body], { type: "application/json" })
    );
    return;
  }
  fetch(`${PRODUCT_API}/product/analytics/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    keepalive: true
  }).catch(() => {});
}

function flushActiveTime(useBeacon = false) {
  if (!productState.analyticsEnabled || productState.visibleSince === null) return;
  const activeMs = Math.max(0, Math.round(performance.now() - productState.visibleSince));
  productState.visibleSince = document.visibilityState === "visible" ? performance.now() : null;
  if (activeMs >= 500) sendAnalytics("dwell", { activeMs }, useBeacon);
}

function initializeAnalytics() {
  if (productState.analyticsEnabled) return;
  productState.analyticsEnabled = true;
  productState.visibleSince = document.visibilityState === "visible" ? performance.now() : null;
  sendAnalytics("page_view", { target: document.title });
  initializeSectionTracking();
}

function initializeConsent() {
  const banner = document.querySelector("#consentBanner");
  const stored = localStorage.getItem(CONSENT_KEY);
  const doNotTrack = navigator.doNotTrack === "1" || window.doNotTrack === "1";
  if (stored === "accepted" && !doNotTrack) {
    initializeAnalytics();
    return;
  }
  if (stored === "declined" || doNotTrack) return;
  window.setTimeout(() => {
    banner.hidden = false;
  }, 700);

  document.querySelector("#acceptAnalytics")?.addEventListener("click", () => {
    localStorage.setItem(CONSENT_KEY, "accepted");
    banner.hidden = true;
    initializeAnalytics();
  });
  document.querySelector("#declineAnalytics")?.addEventListener("click", () => {
    localStorage.setItem(CONSENT_KEY, "declined");
    banner.hidden = true;
  });
}

function initializeSectionTracking() {
  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting || entry.intersectionRatio < 0.32) return;
      const sectionName = entry.target.dataset.section;
      if (!sectionName || productState.viewedSections.has(sectionName)) return;
      productState.viewedSections.add(sectionName);
      sendAnalytics("section_view", { section: sectionName, target: sectionName });
      observer.unobserve(entry.target);
    });
  }, { threshold: [0.32, 0.6] });
  document.querySelectorAll("[data-section]").forEach((section) => observer.observe(section));
}

async function loadProductConfig() {
  try {
    const response = await fetch(`${PRODUCT_API}/product/config`, { cache: "no-store" });
    if (!response.ok) throw new Error("配置读取失败");
    productState.config = { ...productState.config, ...(await response.json()) };
  } catch (_error) {
    productState.config.wjxSurveyUrl = "";
    productState.config.wjxConfigured = false;
  }
}

function surveyUrlForSession() {
  if (!productState.config.wjxSurveyUrl) return "";
  try {
    const url = new URL(productState.config.wjxSurveyUrl);
    if (productState.analyticsEnabled) {
      url.searchParams.set("sojumpparm", analyticsSessionId().slice(0, 80));
    }
    return url.toString();
  } catch (_error) {
    return "";
  }
}

function renderMissingSurvey() {
  const surveyState = document.querySelector("#surveyState");
  surveyState.hidden = false;
  surveyState.innerHTML = `
    <div class="survey-message">
      <h3>问卷星链接尚未配置</h3>
      <p>登录数据后台后粘贴问卷星的 HTTPS 发布链接。配置完成后，这里会直接载入预约表单。</p>
      <a class="button button-primary" href="/analytics.html">前往配置</a>
    </div>
  `;
}

function openSurvey(trigger) {
  const modal = document.querySelector("#wjxModal");
  const frame = document.querySelector("#wjxFrame");
  const surveyState = document.querySelector("#surveyState");
  const externalLink = document.querySelector("#wjxExternalLink");
  productState.lastFocusedElement = trigger || document.activeElement;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  sendAnalytics("wjx_open", { target: trigger?.dataset.track || "预约产品演示" });

  const url = surveyUrlForSession();
  if (!url) {
    externalLink.hidden = true;
    frame.hidden = true;
    renderMissingSurvey();
  } else {
    externalLink.href = url;
    externalLink.hidden = false;
    if (frame.dataset.surveyUrl === url) {
      surveyState.hidden = true;
      frame.hidden = false;
      window.setTimeout(() => modal.querySelector(".close-button")?.focus(), 80);
      return;
    }
    surveyState.hidden = false;
    surveyState.innerHTML = `<div class="survey-loading"><span></span><p>正在载入问卷…</p></div>`;
    frame.hidden = true;
    frame.onload = () => {
      surveyState.hidden = true;
      frame.hidden = false;
    };
    frame.dataset.surveyUrl = url;
    frame.src = url;
  }
  window.setTimeout(() => modal.querySelector(".close-button")?.focus(), 80);
}

function closeSurvey() {
  const modal = document.querySelector("#wjxModal");
  if (!modal.classList.contains("open")) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  sendAnalytics("wjx_close", { target: "关闭预约表单" });
  productState.lastFocusedElement?.focus?.();
}

function initializeNavigation() {
  const header = document.querySelector("#siteHeader");
  const menuButton = document.querySelector("#menuButton");
  const mobileNav = document.querySelector("#mobileNav");
  const updateHeader = () => header.classList.toggle("scrolled", window.scrollY > 24);
  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });

  menuButton?.addEventListener("click", () => {
    const open = menuButton.getAttribute("aria-expanded") !== "true";
    menuButton.setAttribute("aria-expanded", String(open));
    mobileNav.hidden = false;
    requestAnimationFrame(() => mobileNav.classList.toggle("open", open));
    if (!open) window.setTimeout(() => { mobileNav.hidden = true; }, 230);
  });
  mobileNav?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      menuButton.setAttribute("aria-expanded", "false");
      mobileNav.classList.remove("open");
      window.setTimeout(() => { mobileNav.hidden = true; }, 230);
    });
  });
}

function initializeInteractions() {
  document.querySelectorAll("[data-wjx-open]").forEach((button) => {
    button.addEventListener("click", () => openSurvey(button));
  });
  document.querySelectorAll("[data-wjx-close]").forEach((button) => {
    button.addEventListener("click", closeSurvey);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSurvey();
      return;
    }
    if (event.key !== "Tab") return;
    const modal = document.querySelector("#wjxModal");
    if (!modal.classList.contains("open")) return;
    const focusable = [...modal.querySelectorAll(".survey-sheet a[href]:not([hidden]), .survey-sheet button:not([disabled])")]
      .filter((element) => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  document.addEventListener("click", (event) => {
    const tracked = event.target.closest("[data-track]");
    if (tracked) sendAnalytics("click", { target: tracked.dataset.track });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      flushActiveTime(true);
    } else if (productState.analyticsEnabled) {
      productState.visibleSince = performance.now();
    }
  });
  window.addEventListener("pagehide", () => flushActiveTime(true));
}

async function initializeProductPage() {
  initializeNavigation();
  initializeInteractions();
  await loadProductConfig();
  initializeConsent();
}

initializeProductPage();
