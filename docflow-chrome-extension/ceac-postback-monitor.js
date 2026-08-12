(function initializeCeacPostbackMonitor(global) {
  "use strict";

  const MONITOR_VERSION = "0.9.3";
  if (global.__docflowCeacPostbackMonitorVersion === MONITOR_VERSION) return;
  global.__docflowCeacPostbackMonitorVersion = MONITOR_VERSION;

  const PENDING_ATTRIBUTE = "data-docflow-ceac-request-pending";
  const PENDING_SINCE_ATTRIBUTE = "data-docflow-ceac-request-since";
  const PENDING_REASON_ATTRIBUTE = "data-docflow-ceac-request-reason";
  let pageRequestManager = null;
  let clearTimer = 0;

  function root() {
    return document.documentElement;
  }

  function markPending(reason) {
    const element = root();
    if (!element) return;
    global.clearTimeout(clearTimer);
    element.setAttribute(PENDING_ATTRIBUTE, "true");
    element.setAttribute(PENDING_SINCE_ATTRIBUTE, String(Date.now()));
    element.setAttribute(PENDING_REASON_ATTRIBUTE, String(reason || "request").slice(0, 40));
  }

  function clearPending() {
    global.clearTimeout(clearTimer);
    clearTimer = global.setTimeout(() => {
      const element = root();
      if (!element) return;
      element.removeAttribute(PENDING_ATTRIBUTE);
      element.removeAttribute(PENDING_SINCE_ATTRIBUTE);
      element.removeAttribute(PENDING_REASON_ATTRIBUTE);
    }, 280);
  }

  document.addEventListener("submit", () => markPending("form-submit"), true);
  global.addEventListener("beforeunload", () => markPending("beforeunload"), true);
  global.addEventListener("pagehide", () => markPending("pagehide"), true);

  const nativeSubmit = global.HTMLFormElement?.prototype?.submit;
  if (nativeSubmit && !nativeSubmit.__docflowWrapped) {
    const wrappedSubmit = function docflowObservedSubmit(...args) {
      markPending("native-submit");
      return nativeSubmit.apply(this, args);
    };
    Object.defineProperty(wrappedSubmit, "__docflowWrapped", { value: true });
    global.HTMLFormElement.prototype.submit = wrappedSubmit;
  }

  function attachAspNetAjaxMonitor() {
    let manager;
    try {
      manager = global.Sys?.WebForms?.PageRequestManager?.getInstance?.();
    } catch (_error) {
      manager = null;
    }
    if (!manager || manager === pageRequestManager) return Boolean(manager);
    pageRequestManager = manager;
    manager.add_initializeRequest?.(() => markPending("async-initialize"));
    manager.add_beginRequest?.(() => markPending("async-begin"));
    manager.add_endRequest?.(() => clearPending());
    manager.add_pageLoaded?.(() => clearPending());
    try {
      if (!manager.get_isInAsyncPostBack?.()) clearPending();
    } catch (_error) {
      // The next PageRequestManager event will update the shared DOM marker.
    }
    return true;
  }

  attachAspNetAjaxMonitor();
  const attachTimer = global.setInterval(() => {
    if (attachAspNetAjaxMonitor()) global.clearInterval(attachTimer);
  }, 250);
  global.setTimeout(() => global.clearInterval(attachTimer), 20000);
})(globalThis);
