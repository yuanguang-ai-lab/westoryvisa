(function initializeDocFlowApi(global) {
  "use strict";

  const runtimeConfig = global.DOCFLOW_CONFIG || {};
  const configuredApiBase = String(runtimeConfig.apiBaseUrl || "/api").trim();
  const apiBaseUrl = configuredApiBase === "/"
    ? ""
    : configuredApiBase.replace(/\/+$/, "");
  const transport = global.fetch.bind(global);

  async function request(resource, options) {
    const requestOptions = Object.assign({ credentials: "include" }, options || {});
    return transport(resource, requestOptions);
  }

  Object.defineProperty(global, "DocFlowApi", {
    configurable: false,
    enumerable: true,
    writable: false,
    value: Object.freeze({ apiBaseUrl, request }),
  });
})(window);
