const PRODUCT_API = DocFlowApi.apiBaseUrl;
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
  DocFlowApi.request(`${PRODUCT_API}/product/analytics/events`, {
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
    const response = await DocFlowApi.request(`${PRODUCT_API}/product/config`, { cache: "no-store" });
    if (!response.ok) throw new Error("Unable to load configuration");
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

function syncSurveyActions() {
  const surveyAvailable = Boolean(surveyUrlForSession());
  document.querySelectorAll("[data-wjx-open]").forEach((button) => {
    button.hidden = !surveyAvailable;
  });
}

function openSurvey(trigger) {
  const url = surveyUrlForSession();
  if (!url) return;
  const modal = document.querySelector("#wjxModal");
  const frame = document.querySelector("#wjxFrame");
  const surveyState = document.querySelector("#surveyState");
  const externalLink = document.querySelector("#wjxExternalLink");
  productState.lastFocusedElement = trigger || document.activeElement;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  sendAnalytics("wjx_open", { target: trigger?.dataset.track || "Book a product demo" });

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
  window.setTimeout(() => modal.querySelector(".close-button")?.focus(), 80);
}

function closeSurvey() {
  const modal = document.querySelector("#wjxModal");
  if (!modal.classList.contains("open")) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  sendAnalytics("wjx_close", { target: "Close demo request form" });
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

function initializeProductDemo() {
  const player = document.querySelector("[data-demo-player]");
  if (!player) return;
  const stages = [...player.querySelectorAll("[data-demo-stage]")];
  const chapters = [...document.querySelectorAll("[data-demo-chapter]")];
  const toggle = document.querySelector("#demoToggle");
  const progress = document.querySelector("#demoProgress");
  const time = document.querySelector("#demoTime");
  const stageDuration = 7000;
  const totalDuration = stageDuration * stages.length;
  let currentStage = 0;
  let stageElapsed = 0;
  let lastFrame = 0;
  let inView = false;
  let userWantsPlay = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let playing = false;

  const renderStage = () => {
    stages.forEach((stage, index) => {
      const active = index === currentStage;
      stage.classList.toggle("is-active", active);
      stage.setAttribute("aria-hidden", String(!active));
    });
    chapters.forEach((chapter, index) => {
      const active = index === currentStage;
      chapter.classList.toggle("is-active", active);
      chapter.setAttribute("aria-selected", String(active));
    });
  };

  const renderPlayback = () => {
    const totalElapsed = currentStage * stageDuration + stageElapsed;
    progress.style.width = `${Math.min(100, totalElapsed / totalDuration * 100)}%`;
    time.textContent = `00:${String(Math.min(28, Math.floor(totalElapsed / 1000))).padStart(2, "0")}`;
    toggle.classList.toggle("is-paused", !playing);
    toggle.querySelector("b").textContent = playing ? "暂停" : "播放";
    toggle.setAttribute("aria-label", playing ? "暂停连续演示" : "播放连续演示");
  };

  const tick = (now) => {
    if (!lastFrame) lastFrame = now;
    if (playing) {
      stageElapsed += now - lastFrame;
      if (stageElapsed >= stageDuration) {
        stageElapsed %= stageDuration;
        currentStage = (currentStage + 1) % stages.length;
        renderStage();
      }
      renderPlayback();
    }
    lastFrame = now;
    window.requestAnimationFrame(tick);
  };

  toggle?.addEventListener("click", () => {
    userWantsPlay = !userWantsPlay;
    playing = userWantsPlay && inView;
    renderPlayback();
  });
  chapters.forEach((chapter, index) => {
    chapter.addEventListener("click", () => {
      currentStage = index;
      stageElapsed = 0;
      userWantsPlay = true;
      playing = true;
      renderStage();
      renderPlayback();
    });
  });

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting && entry.intersectionRatio >= 0.35;
      playing = userWantsPlay && inView;
      renderPlayback();
    }, { threshold: [0, 0.35, 0.7] });
    observer.observe(player);
  } else {
    inView = true;
    playing = userWantsPlay;
  }

  renderStage();
  renderPlayback();
  window.requestAnimationFrame(tick);
}

function initializePageReveals() {
  if (!("IntersectionObserver" in window)) return;
  const elements = [...document.querySelectorAll(".editorial-section, .module-section, .workflow-list li, .security-points > div")];
  if (!elements.length) return;
  document.body.classList.add("reveal-ready");
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.16, rootMargin: "0px 0px -8%" });
  elements.forEach((element) => observer.observe(element));
}

function initializeSurfaceTilt() {
  if (!window.matchMedia("(pointer: fine)").matches) return;
  document.querySelectorAll(".product-surface").forEach((surface) => {
    surface.addEventListener("pointermove", (event) => {
      const bounds = surface.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width - 0.5;
      const y = (event.clientY - bounds.top) / bounds.height - 0.5;
      surface.style.setProperty("--tilt-x", `${(-y * 3).toFixed(2)}deg`);
      surface.style.setProperty("--tilt-y", `${(x * 4).toFixed(2)}deg`);
    });
    surface.addEventListener("pointerleave", () => {
      surface.style.setProperty("--tilt-x", "0deg");
      surface.style.setProperty("--tilt-y", "0deg");
    });
  });
}

function initializePricing() {
  const range = document.querySelector("#caseVolumeRange");
  if (!range) return;
  const volumeLabel = document.querySelector("#pricingVolume");
  const billingButtons = [...document.querySelectorAll("[data-billing]")];
  const levels = [
    { cases: "20", starter: 199, growth: 399, business: 799 },
    { cases: "50", starter: 299, growth: 599, business: 1199 },
    { cases: "100", starter: 499, growth: 999, business: 1999 },
    { cases: "250", starter: 899, growth: 1799, business: 3599 },
    { cases: "500+", starter: 1499, growth: 2999, business: 5999 }
  ];
  let billing = "yearly";

  const render = () => {
    const level = levels[Number(range.value)];
    volumeLabel.textContent = level.cases;
    range.setAttribute("aria-valuetext", `每月最多 ${level.cases} 份客户档案`);
    ["starter", "growth", "business"].forEach((plan) => {
      const monthlyPrice = level[plan];
      const displayedPrice = billing === "yearly" ? Math.round(monthlyPrice * 10 / 12) : monthlyPrice;
      document.querySelector(`[data-plan-price="${plan}"]`).textContent = displayedPrice.toLocaleString("zh-CN");
      document.querySelector(`[data-plan-note="${plan}"]`).textContent = billing === "yearly"
        ? `按年支付 ¥${(monthlyPrice * 10).toLocaleString("zh-CN")}`
        : "按月支付，可随时调整案件额度";
    });
  };

  billingButtons.forEach((button) => {
    button.addEventListener("click", () => {
      billing = button.dataset.billing;
      billingButtons.forEach((item) => {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", String(active));
      });
      render();
    });
  });
  range.addEventListener("input", render);
  render();
}

async function initializeProductPage() {
  initializeNavigation();
  initializeInteractions();
  initializeProductDemo();
  initializePageReveals();
  initializePricing();
  await loadProductConfig();
  syncSurveyActions();
  initializeConsent();
}

initializeProductPage();
