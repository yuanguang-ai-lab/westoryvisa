const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '../../components/frontend/www');
const source = (name) => fs.readFileSync(path.join(root, name), 'utf8');
const locales = ['zh-CN', 'es', 'pt-BR', 'en'];
const countries = ['CN', 'MX', 'BR', 'IN'];
const nativeCountries = ['中国', 'México', 'Brasil', 'India'];
const tick = () => new Promise((resolve) => setTimeout(resolve, 25));
let failed = false;

async function check(name, fn) {
  try {
    await fn();
    console.log(JSON.stringify({ name, ok: true, mockedBackend: true }));
  } catch (error) {
    failed = true;
    console.log(JSON.stringify({ name, ok: false, mockedBackend: true, error: error.message }));
  }
}

async function boot(locale) {
  const dom = new JSDOM(source('workspace.html'), {
    url: `https://westoryvisa.test/workspace.html?lang=${locale}`,
    runScripts: 'outside-only', pretendToBeVisual: true
  });
  const w = dom.window;
  w.Headers = global.Headers;
  w.Response = global.Response;
  w.requests = [];
  w.fetch = async (resource, options = {}) => {
    w.requests.push({ resource: String(resource), body: options.body, headers: options.headers });
    if (String(resource).endsWith('/health')) return new Response(JSON.stringify({
      apiVersion: '2026-07-27-inline-intake-v22', apiRevision: 22,
      auth: 'cookie-v1', registrationVerification: { mode: 'none' }
    }), { status: 200 });
    // Exercise the real form request construction without creating an account.
    return new Response(JSON.stringify({ error: 'Synthetic audit: no account created' }), { status: 401 });
  };
  w.scrollTo = () => {};
  w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  w.IntersectionObserver = class { observe() {} disconnect() {} };
  for (const name of ['runtime-config.js', 'api-client.js', 'intake-i18n.js', 'site-language.js', 'site-translations.js']) w.eval(source(name));
  w.eval(`${source('mockData.js')}\n${source('app.js')}\nwindow.auditState = state;`);
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  await tick();
  return dom;
}

function fillForm(w, mode) {
  const values = {
    authEmail: 'synthetic@example.test', authPassword: 'SyntheticOnly2026!',
    authConfirmPassword: 'SyntheticOnly2026!', authOrganization: 'Synthetic Audit Agency',
    authName: 'Synthetic Tester', authPhone: '1234567890'
  };
  for (const [id, value] of Object.entries(values)) {
    const element = w.document.getElementById(id);
    if (element) element.value = value;
  }
  assert.equal(w.auditState.authMode, mode);
}

(async () => {
  for (const locale of locales) {
    const dom = await boot(locale);
    const w = dom.window;
    try {
      for (const mode of ['register', 'login']) {
        w.auditState.authMode = mode;
        w.render('login');
        for (const country of countries) {
          await check(`${mode}-${country}-independent-of-${locale}`, async () => {
            fillForm(w, mode);
            const select = w.document.getElementById('authServiceCountry');
            assert.deepEqual([...select.options].map((option) => option.value), countries);
            assert.deepEqual([...select.options].map((option) => option.textContent), nativeCountries);
            select.value = country;
            select.dispatchEvent(new w.Event('change', { bubbles: true }));
            await tick();
            assert.equal(w.WestoryCountry.code, country);
            assert.equal(w.WestoryLanguage.locale, locale);
            assert.equal(w.document.getElementById('authEmail').value, 'synthetic@example.test');
            assert.equal(w.document.getElementById('authPassword').value, 'SyntheticOnly2026!');
            const alternate = locale === 'en' ? 'zh-CN' : 'en';
            w.WestoryLanguage.activate(alternate, { reload: false });
            assert.equal(w.WestoryCountry.code, country);
            assert.equal(w.document.getElementById('authServiceCountry').value, country);
            w.WestoryLanguage.activate(locale, { reload: false });
            const form = w.document.getElementById('authForm');
            await w.submitAuthForm({ preventDefault() {}, currentTarget: form });
            const request = w.requests.filter((item) => item.resource.endsWith(`/${mode}`)).at(-1);
            assert.ok(request, 'form must reach the mocked backend');
            assert.equal(JSON.parse(request.body).serviceCountry, country);
            assert.equal(request.headers.get('X-Westory-Service-Country'), country);
          });
        }
      }

      await check(`MX-case-display-uses-${locale}`, () => {
        const application = { applicationCountry: 'MX', caseMeta: { applicationCountry: 'MX' }, branchQuestionnaire: [] };
        const expected = {
          'zh-CN': ['客户确认', '仍有 2 项敏感历史问题等待客户逐题回答。系统不会默认选择 No。', '当前档案还有 1 项资料未收齐，暂不能开始逐页填写'],
          es: ['Confirmado por el cliente', 'Aún faltan respuestas del cliente para 2 preguntas sensibles de antecedentes. El sistema nunca selecciona No por defecto.', 'Faltan 1 datos; aún no se puede iniciar el llenado página por página'],
          'pt-BR': ['Confirmado pelo cliente', 'Ainda faltam respostas do cliente para 2 perguntas sensíveis de antecedentes. O sistema nunca seleciona Não por padrão.', 'Faltam 1 dados; o preenchimento página a página ainda não pode começar'],
          en: ['Client confirmation', "2 sensitive background questions still need the client's answers. The system never selects No by default.", '1 details are still missing; page-by-page entry cannot start yet']
        }[locale];
        assert.equal(w.consultantQuestionSource(application, '客户确认'), expected[0]);
        assert.equal(w.consultantValidationMessage(application, '仍有 2 项敏感历史问题等待客户逐题回答。系统不会默认选择 No。'), expected[1]);
        assert.equal(w.consultantPrefillMessages(application, [{ id: 'personal.surname', label: '姓氏' }]).title, expected[2]);
        assert.equal(application.applicationCountry, 'MX');
      });

      await check(`account-country-lock-and-filter-survive-${locale}`, () => {
        const user = { serviceCountry: 'MX', organizationId: 'org-a', identity: 'Agency A', platformAdmin: false };
        w.auditState.user = user;
        w.applyAccountCountryScope(user);
        w.auditState.applications = ['MX', 'CN'].map((country) => ({
          id: country, applicationCountry: country,
          caseMeta: { applicationCountry: country, organizationId: 'org-a', organizationName: 'Agency A' }
        }));
        for (const displayLocale of locales) {
          w.WestoryLanguage.activate(displayLocale, { reload: false });
          assert.equal(w.WestoryCountry.code, 'MX');
          assert.equal(w.WestoryCountry.activate('CN', { reload: false }), false);
          assert.deepEqual(Array.from(w.visibleApplications(), (application) => application.id), ['MX']);
        }
      });

      await check(`platform-admin-country-selection-survives-${locale}`, () => {
        assert.ok(!w.renderWorkspacePortalHeader().includes('adminServiceCountry'), 'ordinary accounts do not receive a country switch');
        const user = { ...w.auditState.user, platformAdmin: true };
        w.auditState.user = user;
        w.applyAccountCountryScope(user);
        w.WestoryLanguage.activate(locale, { reload: false });
        const header = JSDOM.fragment(w.renderWorkspacePortalHeader());
        const select = header.querySelector('#adminServiceCountry');
        assert.ok(select, 'retain the existing platform-admin country-switch entry');
        assert.deepEqual([...select.options].map((option) => option.value), countries);
        for (const country of countries) {
          assert.equal(w.WestoryCountry.activate(country, { reload: false }), true);
          assert.equal(w.WestoryCountry.code, country);
          assert.equal(w.WestoryLanguage.locale, locale);
        }
      });
    } finally { w.close(); }

    await check(`extension-CN-only-boundary-${locale}`, async () => {
      const page = new JSDOM(source('extension.html'), {
        url: `https://westoryvisa.test/extension.html?lang=${locale}`, runScripts: 'outside-only'
      });
      try {
        page.window.eval(source('site-language.js'));
        page.window.eval(source('site-translations.js'));
        page.window.document.dispatchEvent(new page.window.Event('DOMContentLoaded'));
        await tick();
        const expected = { 'zh-CN': '中国版客户档案', es: 'versión para China', 'pt-BR': 'versão para a China', en: 'China-version case' }[locale];
        assert.ok(page.window.document.body.textContent.includes(expected));
        assert.equal(new URL(page.window.document.querySelector('a[download]').href).pathname, '/downloads/WestoryVisa-Chrome-1.0.5.zip');
      } finally { page.window.close(); }
    });
  }
  if (failed) process.exitCode = 1;
})().catch((error) => { console.error(error); process.exitCode = 1; });
