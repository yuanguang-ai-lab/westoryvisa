(function initializeWestoryLegal(global) {
  "use strict";

  const api = global.DocFlowApi;
  const fallbackProfile = Object.freeze({
    productName: "Westory Visa",
    legalNameEn: "",
    legalNameZh: "",
    businessRegistrationNumber: "",
    registeredAddress: "",
    supportEmail: "",
    supportPhone: "",
    supportHours: "",
    website: "https://westoryvisa.com",
    termsVersion: "",
    privacyVersion: "",
    refundPolicyVersion: "",
    refundWindowDays: 7,
    configured: false,
    missingFields: [],
  });

  function endpoint(path) {
    return `${api?.apiBaseUrl || "/api"}${path}`;
  }

  function setText(selector, value, fallback = "待运营主体配置") {
    document.querySelectorAll(selector).forEach((element) => {
      element.textContent = value || fallback;
      element.classList.toggle("legal-value-missing", !value);
    });
  }

  function setOptional(selector, value) {
    document.querySelectorAll(selector).forEach((element) => {
      element.hidden = !value;
      if (value) element.textContent = value;
    });
  }

  function renderProfile(profile) {
    const legalName = profile.legalNameZh
      ? `${profile.legalNameEn}（${profile.legalNameZh}）`
      : profile.legalNameEn;
    setText('[data-merchant-field="legalName"]', legalName);
    setText('[data-merchant-field="legalNameEn"]', profile.legalNameEn);
    setOptional('[data-merchant-field="legalNameZh"]', profile.legalNameZh);
    setText('[data-merchant-field="brn"]', profile.businessRegistrationNumber);
    setText('[data-merchant-field="address"]', profile.registeredAddress);
    setText('[data-merchant-field="supportEmail"]', profile.supportEmail);
    setText('[data-merchant-field="supportPhone"]', profile.supportPhone, "未配置");
    setText('[data-merchant-field="supportHours"]', profile.supportHours, "未配置");
    setText('[data-merchant-field="termsVersion"]', profile.termsVersion, "未配置");
    setText('[data-merchant-field="privacyVersion"]', profile.privacyVersion, "未配置");
    setText('[data-merchant-field="refundPolicyVersion"]', profile.refundPolicyVersion, "未配置");
    setText('[data-merchant-field="refundWindowDays"]', String(profile.refundWindowDays || 7), "7");

    document.querySelectorAll("[data-merchant-email-link]").forEach((link) => {
      link.textContent = profile.supportEmail || "客服邮箱待配置";
      link.href = profile.supportEmail ? `mailto:${profile.supportEmail}` : "/contact";
    });
    document.querySelectorAll("[data-merchant-summary]").forEach((element) => {
      element.textContent = profile.configured
        ? `${legalName} · BRN ${profile.businessRegistrationNumber}`
        : "香港运营主体资料尚未配置完整";
    });
    document.querySelectorAll("[data-profile-warning]").forEach((element) => {
      element.hidden = profile.configured;
    });
    document.querySelectorAll("[data-legal-acceptance]").forEach((checkbox) => {
      checkbox.disabled = !profile.configured;
      checkbox.dataset.termsVersion = profile.termsVersion || "";
    });

    global.WESTORY_LEGAL = Object.freeze({ ...profile });
    document.dispatchEvent(new CustomEvent("westory:merchant-profile", { detail: profile }));
  }

  async function loadProfile() {
    if (!api || global.location.protocol === "file:") {
      renderProfile(fallbackProfile);
      return;
    }
    try {
      const response = await api.request(endpoint("/merchant-profile"), { cache: "no-store" });
      if (!response.ok) throw new Error("Unable to load merchant profile");
      renderProfile({ ...fallbackProfile, ...(await response.json()) });
    } catch (_error) {
      renderProfile(fallbackProfile);
    }
  }

  loadProfile();
})(window);
