const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../../frontend');
const cjk = /[\u3400-\u9fff]/;
const locales = ['zh-CN', 'es', 'pt-BR', 'en'];
const languageNames = ['中文', 'Español', 'Português', 'English'];

function source(name) {
  return fs.readFileSync(path.join(root, name), 'utf8');
}

function compact(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function installBrowserStubs(window, { authenticated = false, sampleCase = null, publicIntake = null, healthRevision = 22 } = {}) {
  window.Headers = global.Headers;
  window.Request = global.Request;
  window.Response = global.Response;
  window.auditRequests = [];
  window.fetch = async (resource) => {
    const url = String(resource || '');
    window.auditRequests.push(url);
    if (publicIntake && url.endsWith('/intake')) {
      return new Response(JSON.stringify(publicIntake), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    if (url.endsWith('/health')) {
      return new Response(JSON.stringify(authenticated ? {
        apiVersion: `2026-07-27-inline-intake-v${healthRevision}`, apiRevision: healthRevision, auth: 'cookie-v1', membershipBypass: true
      } : { apiVersion: 'test', apiRevision: 0 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    if (authenticated && url.endsWith('/session')) {
      return new Response(JSON.stringify({ user: {
        id: 'user-1', name: 'Alex Consultant', email: 'alex@example.test', identity: 'Example Visa Agency',
        organizationName: 'Example Visa Agency', organizationId: 'org-1', serviceCountry: 'MX', platformAdmin: false
      } }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (authenticated && url.endsWith('/billing')) {
      return new Response(JSON.stringify({ membership: { active: true }, trial: null }), {
        status: 200, headers: { 'Content-Type': 'application/json' }
      });
    }
    if (authenticated && url.endsWith('/cases')) {
      return new Response(JSON.stringify({ cases: sampleCase ? [sampleCase] : [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' }
      });
    }
    return new Response(JSON.stringify({ error: 'Not available in language audit' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' }
    });
  };
  window.scrollTo = () => {};
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  window.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

async function publicIntake(locale) {
  const data = {
    status: 'pending',
    applicantName: 'Sample Applicant',
    visaType: 'F1 Student Visa',
    applicationCountry: 'MX',
    sourceLocale: 'es-MX',
    fields: [{
      id: 'personal.surname',
      section: '基础信息',
      label: '姓氏',
      inputType: 'text',
      required: true,
      placeholder: '请输入护照上的姓氏'
    }],
    questions: [],
    draft: {}
  };
  const dom = new JSDOM(source('workspace.html'), {
    url: `https://westoryvisa.test/workspace.html?intake=test-token&lang=${encodeURIComponent(locale)}`,
    runScripts: 'outside-only',
    pretendToBeVisual: true
  });
  const { window } = dom;
  installBrowserStubs(window, { publicIntake: data });
  for (const script of ['runtime-config.js', 'api-client.js', 'intake-i18n.js', 'site-language.js', 'site-translations.js']) {
    window.eval(source(script));
  }
  window.eval(`${source('mockData.js')}\n${source('app.js')}`);
  window.eval(source('extension-account.js'));
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise((resolve) => setTimeout(resolve, 160));
  const result = {
    page: 'public-intake',
    locale,
    htmlLang: window.document.documentElement.lang,
    intakeLocale: window.DocFlowIntakeI18n.locale(data),
    controls: [...window.document.querySelectorAll('[data-language-select]')].map((item) => compact(item.textContent)),
    currentLanguage: compact(window.document.querySelector('[data-country-current]')?.textContent),
    leftovers: leftovers(window.document, window.Node)
  };
  window.close();
  return result;
}

function leftovers(document, Node) {
  const values = new Set();
  for (const element of document.querySelectorAll('body *')) {
    if (element.closest('script, style, template, noscript, [data-language-select]')) continue;
    for (const child of element.childNodes) {
      if (child.nodeType !== Node.TEXT_NODE) continue;
      const value = compact(child.nodeValue);
      if (value && cjk.test(value)) values.add(`text: ${value}`);
    }
    for (const attribute of ['placeholder', 'aria-label', 'title', 'alt', 'value']) {
      if (attribute === 'value' && !element.matches('input[type="button"], input[type="submit"], input[readonly]')) continue;
      const value = compact(element.getAttribute(attribute));
      if (value && cjk.test(value)) values.add(`${attribute}: ${value}`);
    }
  }
  return [...values];
}

async function workspace(locale) {
  const dom = new JSDOM(source('workspace.html'), {
    url: `https://westoryvisa.test/workspace.html?lang=${encodeURIComponent(locale)}`,
    runScripts: 'outside-only',
    pretendToBeVisual: true
  });
  const { window } = dom;
  installBrowserStubs(window);
  for (const script of ['runtime-config.js', 'api-client.js', 'intake-i18n.js', 'site-language.js', 'site-translations.js']) {
    window.eval(source(script));
  }
  window.eval(`${source('mockData.js')}\n${source('app.js')}`);
  window.eval(source('extension-account.js'));
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise((resolve) => setTimeout(resolve, 120));
  const result = {
    page: 'workspace-login',
    locale,
    htmlLang: window.document.documentElement.lang,
    controls: [...window.document.querySelectorAll('[data-language-select]')].map((item) => compact(item.textContent)),
    leftovers: leftovers(window.document, window.Node)
  };
  window.close();
  return result;
}

function sampleCase() {
  return {
    id: 'case-1',
    applicantName: 'Sample Applicant',
    email: 'consultant@example.test',
    visaType: 'F1 学生签证',
    applicationCountry: 'MX',
    sourceLocale: 'es-MX',
    currentStep: 6,
    caseMeta: {
      organizationName: 'Example Visa Agency', organizationId: 'org-1', owner: 'Alex Consultant',
      applicationCountry: 'MX', sourceLocale: 'es-MX', status: '初稿已生成', notes: ''
    },
    documents: [],
    extractedFields: [],
    missingQuestions: [],
    branchQuestionnaire: [],
    validationResults: [],
    agentTimeline: [],
    auditReport: {},
    prefillLog: [],
    createdAt: '2026-09-20T00:00:00Z',
    lastUpdated: '2026-09-20T00:00:00Z'
  };
}

async function workspaceView(locale, view) {
  const dom = new JSDOM(source('workspace.html'), {
    url: `https://westoryvisa.test/workspace.html?lang=${encodeURIComponent(locale)}`,
    runScripts: 'outside-only',
    pretendToBeVisual: true
  });
  const { window } = dom;
  const needsCase = !['dashboard', 'create'].includes(view);
  const application = needsCase ? sampleCase() : null;
  installBrowserStubs(window, { authenticated: true, sampleCase: application });
  window.sessionStorage.setItem('docflowDs160Navigation', JSON.stringify({
    view,
    applicationId: application?.id || ''
  }));
  for (const script of ['runtime-config.js', 'api-client.js', 'intake-i18n.js', 'site-language.js', 'site-translations.js']) {
    window.eval(source(script));
  }
  window.eval(`${source('mockData.js')}\n${source('app.js')}`);
  window.eval(source('extension-account.js'));
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise((resolve) => setTimeout(resolve, 150));
  const result = {
    page: `workspace-${view}`,
    locale,
    htmlLang: window.document.documentElement.lang,
    controls: [...window.document.querySelectorAll('[data-language-select]')].map((item) => compact(item.textContent)),
    leftovers: leftovers(window.document, window.Node)
  };
  window.close();
  return result;
}

async function product(locale) {
  const dom = new JSDOM(source('product.html'), {
    url: `https://westoryvisa.test/product.html?lang=${encodeURIComponent(locale)}`,
    runScripts: 'outside-only',
    pretendToBeVisual: true
  });
  const { window } = dom;
  installBrowserStubs(window);
  for (const script of ['runtime-config.js', 'api-client.js', 'site-language.js', 'site-translations.js', 'product.js']) {
    window.eval(source(script));
  }
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise((resolve) => setTimeout(resolve, 80));
  const result = {
    page: 'product-runtime',
    locale,
    htmlLang: window.document.documentElement.lang,
    controls: [...window.document.querySelectorAll('[data-language-select]')].map((item) => compact(item.textContent)),
    leftovers: leftovers(window.document, window.Node)
  };
  window.close();
  return result;
}

async function apiCompatibility(test) {
  const dom = new JSDOM(source('workspace.html'), {
    url: `${test.origin}/workspace.html?lang=zh-CN`,
    runScripts: 'outside-only',
    pretendToBeVisual: true
  });
  const { window } = dom;
  installBrowserStubs(window, { authenticated: true, healthRevision: test.revision });
  window.eval(source('runtime-config.js'));
  // A stale local-preview marker must never bypass the release's API revision
  // requirement, including when this release is served on loopback.
  if (test.marker) window.DOCFLOW_CONFIG = { ...window.DOCFLOW_CONFIG, localPreviewApiRevision: 20 };
  for (const script of ['api-client.js', 'intake-i18n.js', 'site-language.js', 'site-translations.js']) {
    window.eval(source(script));
  }
  window.eval(`${source('mockData.js')}\n${source('app.js')}`);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise((resolve) => setTimeout(resolve, 150));
  const sessionRequested = window.auditRequests.some((url) => url.endsWith('/session'));
  const dashboardRendered = Boolean(window.document.querySelector('.workspace-root'));
  const compatibilityAlert = compact(window.document.querySelector('.service-alert')?.textContent);
  const actual = sessionRequested && dashboardRendered;
  const ok = actual === test.accepted && (test.accepted || compatibilityAlert.includes('当前地址连接的是后端'));
  const result = { name: test.name, mockedBackend: true, expectedAccepted: test.accepted, actualAccepted: actual,
    sessionRequested, dashboardRendered, compatibilityAlert, ok };
  window.close();
  return result;
}

(async () => {
  let failed = false;
  // Network responses are deliberate fixtures: these checks exercise rendering,
  // not backend health, authentication, payments, or live case persistence.
  for (const locale of process.argv.includes('--compat-only') ? [] : locales) {
    const checks = [() => product(locale), () => workspace(locale), () => publicIntake(locale)];
    for (const view of ['dashboard', 'create', 'documents', 'processing', 'fields', 'questions', 'validation', 'preview', 'prefill', 'report']) {
      checks.push(() => workspaceView(locale, view));
    }
    for (const check of checks) {
      const result = await check();
      const chineseContent = result.leftovers.length;
      if (locale === 'zh-CN') result.leftovers = [];
      const expectedIntakeLocale = { 'zh-CN': 'zh-CN', es: 'es-MX', 'pt-BR': 'pt-BR', en: 'en-IN' }[locale];
      const ok = result.htmlLang === locale
        && (locale === 'zh-CN' ? chineseContent > 0 : result.leftovers.length === 0)
        && result.controls.length >= 4
        && result.controls.length % 4 === 0
        && result.controls.every((item, index) => item === languageNames[index % 4])
        && (!result.intakeLocale || result.intakeLocale === expectedIntakeLocale);
      if (!ok) failed = true;
      console.log(JSON.stringify({ ok, mockedBackend: true, chineseContent, ...result }));
    }
  }
  for (const test of [
    { name: 'production-rejects-revision20-even-with-marker', origin: 'https://westoryvisa.com', revision: 20, marker: true, accepted: false },
    { name: 'loopback-rejects-revision20-even-with-marker', origin: 'http://127.0.0.1:8766', revision: 20, marker: true, accepted: false },
    { name: 'loopback-rejects-revision20-without-marker', origin: 'http://127.0.0.1:8766', revision: 20, marker: false, accepted: false },
    { name: 'production-accepts-revision22', origin: 'https://westoryvisa.com', revision: 22, marker: false, accepted: true }
  ]) {
    const result = await apiCompatibility(test);
    if (!result.ok) failed = true;
    console.log(JSON.stringify(result));
  }
  if (failed) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
