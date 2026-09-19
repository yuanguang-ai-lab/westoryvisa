(function initializeDocFlowRuntimeConfig(global) {
  "use strict";

  const existing = global.DOCFLOW_CONFIG || {};
  global.DOCFLOW_CONFIG = Object.freeze({
    apiBaseUrl: typeof existing.apiBaseUrl === "string" ? existing.apiBaseUrl : "/api",
  });
})(window);
