const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../../frontend');
const runtime = fs.readFileSync(path.join(root, 'site-language.js'), 'utf8');
const translations = fs.readFileSync(path.join(root, 'site-translations.js'), 'utf8');
const pages = [
  'product.html',
  'workspace.html',
  'membership.html',
  'terms.html',
  'privacy.html',
  'refund-policy.html',
  'contact.html',
  'extension.html'
];
const locales = ['zh-CN', 'es', 'pt-BR', 'en'];
const languageNames = ['中文', 'Español', 'Português', 'English'];
const cjk = /[\u3400-\u9fff]/;

function compact(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

async function audit(page, locale) {
  const html = fs.readFileSync(path.join(root, page), 'utf8');
  const dom = new JSDOM(html, {
    url: `https://westoryvisa.test/${page}?lang=${encodeURIComponent(locale)}`,
    runScripts: 'outside-only'
  });
  dom.window.eval(runtime);
  dom.window.eval(translations);
  dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
  await new Promise((resolve) => setTimeout(resolve, 15));
  const document = dom.window.document;
  const leftovers = new Set();

  for (const node of document.querySelectorAll('body *')) {
    if (node.closest('script, style, template, noscript, [data-language-select]')) continue;
    for (const child of node.childNodes) {
      if (child.nodeType !== dom.window.Node.TEXT_NODE) continue;
      const value = compact(child.nodeValue);
      if (value && cjk.test(value)) leftovers.add(`text: ${value}`);
    }
    for (const attribute of ['placeholder', 'aria-label', 'title', 'alt', 'value']) {
      const value = compact(node.getAttribute(attribute));
      if (value && cjk.test(value)) leftovers.add(`${attribute}: ${value}`);
    }
  }

  const controls = [...document.querySelectorAll('[data-language-select]')].map((item) => compact(item.textContent));
  const internalLinks = [...document.querySelectorAll('a[href]:not([data-language-select])')]
    .map((item) => item.getAttribute('href'))
    .filter((href) => href && !href.startsWith('#') && !/^(mailto:|tel:|https?:\/\/)/i.test(href));
  const badLinks = internalLinks.filter((href) => {
    const url = new URL(href, dom.window.location.href);
    return url.searchParams.get('lang') !== locale || url.searchParams.has('country');
  });

  const result = {
    page,
    locale,
    htmlLang: document.documentElement.lang,
    controls,
    chineseContent: leftovers.size,
    leftovers: locale === 'zh-CN' ? [] : [...leftovers],
    badLinks
  };
  dom.window.close();
  return result;
}

async function languagePreferences() {
  const results = [];
  for (const test of [
    { name: 'fresh-visitor-default', query: '', saved: null, expected: 'zh-CN' },
    { name: 'saved-portuguese', query: '', saved: 'pt-BR', expected: 'pt-BR' },
    { name: 'saved-chinese', query: '', saved: 'zh-CN', expected: 'zh-CN' },
    { name: 'explicit-chinese-over-saved-spanish', query: '?lang=zh-CN', saved: 'es', expected: 'zh-CN' },
    { name: 'explicit-english-over-saved-chinese', query: '?lang=en', saved: 'zh-CN', expected: 'en' }
  ]) {
    const dom = new JSDOM(fs.readFileSync(path.join(root, 'product.html'), 'utf8'), {
      url: `https://westoryvisa.test/product.html${test.query}`, runScripts: 'outside-only'
    });
    if (test.saved) dom.window.localStorage.setItem('westoryvisaLanguage', test.saved);
    dom.window.eval(runtime);
    dom.window.eval(translations);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await new Promise((resolve) => setTimeout(resolve, 15));
    const actual = dom.window.WestoryLanguage.locale;
    results.push({ name: test.name, expected: test.expected, actual, ok: actual === test.expected });
    dom.window.close();
  }
  return results;
}

async function languageRoundTrip() {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'product.html'), 'utf8'), {
    url: 'https://westoryvisa.test/product.html?lang=zh-CN', runScripts: 'outside-only'
  });
  const { window } = dom;
  const document = window.document;
  const heading = [...document.querySelectorAll('h1, h2, h3')].find((element) => cjk.test(element.textContent));
  const originalHeading = compact(heading.textContent);
  const input = document.createElement('input');
  const textarea = document.createElement('textarea');
  input.value = '张三 Español Português';
  textarea.value = '客户资料 / 中文 / English';
  document.body.append(input, textarea);
  window.eval(runtime);
  window.eval(translations);
  document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise((resolve) => setTimeout(resolve, 15));
  const translatedHeadings = [];
  for (const locale of ['es', 'pt-BR', 'en']) {
    window.WestoryLanguage.activate(locale, { reload: false });
    await new Promise((resolve) => setTimeout(resolve, 15));
    translatedHeadings.push(compact(heading.textContent));
  }
  window.WestoryLanguage.activate('zh-CN', { reload: false });
  await new Promise((resolve) => setTimeout(resolve, 15));
  const restored = compact(heading.textContent) === originalHeading;
  const enteredValuesPreserved = input.value === '张三 Español Português'
    && textarea.value === '客户资料 / 中文 / English';
  const foreignHeadingsTranslated = translatedHeadings.every((value) => value !== originalHeading && !cjk.test(value));
  const result = { name: 'language-round-trip-and-user-values', restored, enteredValuesPreserved, foreignHeadingsTranslated,
    ok: restored && enteredValuesPreserved && foreignHeadingsTranslated };
  window.close();
  return result;
}

(async () => {
  let failed = false;
  for (const page of pages) {
    for (const locale of locales) {
      const result = await audit(page, locale);
      const ok = result.htmlLang === locale
        && result.controls.length >= 4
        && result.controls.length % 4 === 0
        && result.controls.every((item, index) => item === languageNames[index % 4])
        && (locale !== 'zh-CN' || result.chineseContent > 0)
        && result.leftovers.length === 0
        && result.badLinks.length === 0;
      if (!ok) failed = true;
      console.log(JSON.stringify({ ok, ...result }));
    }
  }
  for (const result of [...await languagePreferences(), await languageRoundTrip()]) {
    if (!result.ok) failed = true;
    console.log(JSON.stringify(result));
  }
  if (failed) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
