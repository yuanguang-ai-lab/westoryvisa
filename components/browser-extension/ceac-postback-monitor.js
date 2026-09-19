(function initializeCeacPostbackMonitor(global) {
  "use strict";

  const MONITOR_VERSION = "1.0.5";
  if (global.__docflowCeacPostbackMonitorVersion === MONITOR_VERSION) return;
  global.__docflowCeacPostbackMonitorVersion = MONITOR_VERSION;

  const PENDING_ATTRIBUTE = "data-docflow-ceac-request-pending";
  const PENDING_SINCE_ATTRIBUTE = "data-docflow-ceac-request-since";
  const PENDING_REASON_ATTRIBUTE = "data-docflow-ceac-request-reason";
  const NEXT_REQUEST_ATTRIBUTE = "data-docflow-ceac-next-request";
  const NEXT_ACK_ATTRIBUTE = "data-docflow-ceac-next-ack";
  const NEXT_EVENT = "docflow-ceac-native-next";
  let pageRequestManager = null;
  let clearTimer = 0;
  let staleTimer = 0;
  let nativeNextGuardUntil = 0;

  function root() {
    return document.documentElement;
  }

  function markPending(reason) {
    const element = root();
    if (!element) return;
    global.clearTimeout(clearTimer);
    clearTimer = 0;
    global.clearTimeout(staleTimer);
    element.setAttribute(PENDING_ATTRIBUTE, "true");
    element.setAttribute(PENDING_SINCE_ATTRIBUTE, String(Date.now()));
    element.setAttribute(PENDING_REASON_ATTRIBUTE, String(reason || "request").slice(0, 40));
    // A missed ASP.NET endRequest event must not leave the Agent blocked forever.
    // A real full-page navigation destroys this timer together with the document.
    // CEAC occasionally leaves PageRequestManager in a stale "busy" state
    // even though the selected value is already visible and the DOM is quiet.
    // A 22-second fallback multiplied across every branch question made a
    // normal application take tens of minutes. Final field reconciliation
    // still catches any late redraw, so release the stale marker promptly.
    staleTimer = global.setTimeout(() => clearPending(), 5000);
  }

  function clearPending() {
    global.clearTimeout(staleTimer);
    // Health checks run every 250 ms. Re-arming this 280 ms timer on each
    // idle check starved it forever and forced core's 6-second fallback.
    // Keep the first completion deadline; only a new request cancels it.
    if (clearTimer || root()?.getAttribute(PENDING_ATTRIBUTE) !== "true") return;
    clearTimer = global.setTimeout(() => {
      clearTimer = 0;
      const element = root();
      if (!element) return;
      element.removeAttribute(PENDING_ATTRIBUTE);
      element.removeAttribute(PENDING_SINCE_ATTRIBUTE);
      element.removeAttribute(PENDING_REASON_ATTRIBUTE);
    }, 280);
  }

  document.addEventListener("submit", () => markPending("form-submit"), true);
  global.addEventListener("beforeunload", (event) => {
    markPending("beforeunload");
    if (Date.now() > nativeNextGuardUntil) return;

    // This listener is installed at document_start, before CEAC registers its
    // dirty-form warning.  Once this exact page has passed DocFlow's audit and
    // the native Next control has been invoked, stop the later CEAC warning
    // listeners for this one navigation only.  Do not call preventDefault and
    // do not set returnValue: either would itself request a browser prompt.
    event.stopImmediatePropagation();
  }, true);
  global.addEventListener("pagehide", () => markPending("pagehide"), true);

  // Run the actual CEAC Next click in the page's MAIN world. CEAC's inline
  // handler sets `needToConfirm = false` before its ASP.NET postback. Calling
  // click() only from an extension isolated world can submit the form without
  // reliably updating that page-global flag, which causes Chrome's recurring
  // "Leave this site?" beforeunload prompt.
  document.addEventListener(NEXT_EVENT, () => {
    const element = root();
    const raw = element?.getAttribute(NEXT_REQUEST_ATTRIBUTE) || "";
    const separator = raw.indexOf("|");
    const token = separator > 0 ? raw.slice(0, separator) : "";
    const controlId = separator > 0 ? raw.slice(separator + 1) : "";
    const button = controlId ? document.getElementById(controlId) : null;
    if (!token || !button || !/^(?:INPUT|BUTTON|A)$/.test(button.tagName)) return;
    try {
      nativeNextGuardUntil = Date.now() + 5000;
      global.needToConfirm = false;
      // CEAC versions have used both a page-global flag and a DOM0 handler for
      // the same dirty-form warning.  The document_start capture guard above
      // is authoritative; clearing DOM0 here covers older page variants too.
      global.onbeforeunload = null;
      element.setAttribute(NEXT_ACK_ATTRIBUTE, token);
      markPending("native-next");
      button.click();
    } catch (_error) {
      nativeNextGuardUntil = 0;
      element.removeAttribute(NEXT_ACK_ATTRIBUTE);
    }
  }, true);

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
  const healthTimer = global.setInterval(() => {
    attachAspNetAjaxMonitor();
    const element = root();
    const reason = element?.getAttribute(PENDING_REASON_ATTRIBUTE) || "";
    if (!reason.startsWith("async-") || !pageRequestManager) return;
    try {
      if (!pageRequestManager.get_isInAsyncPostBack?.()) clearPending();
    } catch (_error) {
      // Keep the timeout fail-safe active when ASP.NET's state cannot be read.
    }
  }, 250);
  global.addEventListener("pagehide", () => global.clearInterval(healthTimer), true);
})(globalThis);
