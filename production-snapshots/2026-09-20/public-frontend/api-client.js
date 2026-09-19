(function initializeDocFlowApi(global) {
  "use strict";

  const runtimeConfig = global.DOCFLOW_CONFIG || {};
  const configuredApiBase = String(runtimeConfig.apiBaseUrl || "/api").trim();
  const apiBaseUrl = configuredApiBase === "/"
    ? ""
    : configuredApiBase.replace(/\/+$/, "");
  const transport = global.fetch.bind(global);

  const localPageRoutes = Object.freeze({
    "/": "product.html",
    "/landing-page": "product.html",
    "/landingpage": "product.html",
    "/workspace": "workspace.html",
    "/membership": "membership.html",
    "/terms": "terms.html",
    "/privacy": "privacy.html",
    "/refund-policy": "refund-policy.html",
    "/contact": "contact.html",
    "/admin/payments": "admin-payments.html",
  });

  function localFileHref(rawHref) {
    if (global.location.protocol !== "file:" || !rawHref?.startsWith("/")) return rawHref;
    const parsed = new URL(rawHref, "https://local.westoryvisa.invalid");
    const pathname = parsed.pathname.length > 1 ? parsed.pathname.replace(/\/+$/, "") : parsed.pathname;
    const filename = localPageRoutes[pathname];
    return filename ? `${filename}${parsed.search}${parsed.hash}` : rawHref;
  }

  function rewriteLocalFileLinks(root) {
    if (global.location.protocol !== "file:") return;
    (root || global.document).querySelectorAll("a[href]").forEach((link) => {
      const rawHref = link.getAttribute("href");
      const rewritten = localFileHref(rawHref);
      if (rewritten !== rawHref) link.setAttribute("href", rewritten);
    });
  }

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", () => rewriteLocalFileLinks(), { once: true });
  } else {
    rewriteLocalFileLinks();
  }

  async function request(resource, options) {
    const requestOptions = Object.assign({ credentials: "include" }, options || {});
    const headers = new Headers(requestOptions.headers || {});
    const serviceCountry = String(global.WestoryCountry?.code || "").toUpperCase();
    if (["CN", "MX", "BR", "IN"].includes(serviceCountry)) {
      headers.set("X-Westory-Service-Country", serviceCountry);
    }
    requestOptions.headers = headers;
    const response = await transport(resource, requestOptions);
    // Callers display access errors in context; transport must not navigate.
    return response;
  }

  Object.defineProperty(global, "DocFlowApi", {
    configurable: false,
    enumerable: true,
    writable: false,
    value: Object.freeze({ apiBaseUrl, request, localFileHref, rewriteLocalFileLinks }),
  });
})(window);
