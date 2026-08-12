import assert from "node:assert/strict";

export async function runPracticeFlow(page, baseURL) {
  const externalRequests = [];
  page.on("request", (request) => {
    if (!request.url().startsWith(baseURL)) externalRequests.push(request.url());
  });

  await page.addInitScript(() => {
    if (!sessionStorage.getItem("vfpl_e2e_initialized")) {
      localStorage.clear();
      localStorage.setItem("vfpl_safety_guide_v1", "seen");
      localStorage.setItem("vfpl_locale", "zh");
      sessionStorage.setItem("vfpl_e2e_initialized", "yes");
    }
  });

  await page.goto(`${baseURL}/`, { waitUntil: "domcontentloaded" });
  assert.match(await page.locator(".unofficial-banner").innerText(), /非官方网站/);
  await page.locator('a[href="#/start"]').first().click();
  await page.waitForURL(/#\/start/);

  const createButtons = page.locator('[data-action="create-draft"]');
  assert.equal(await createButtons.count(), 2);
  assert.equal(await createButtons.first().isDisabled(), true);
  await page.locator("#practiceConsent").check();
  assert.equal(await createButtons.first().isEnabled(), true);
  await createButtons.first().click();
  await page.waitForURL(/#\/application\//);

  await page.locator('[data-action="continue-step"]').click();
  await page.waitForURL(/\/1$/);
  await page.locator("#field-surname").fill("Example");
  await page.locator("#field-givenName").fill("Alex");
  await page.locator("#field-nativeName").fill("示例人物");
  await page.locator('[data-field="usedOtherNames"][value="no"]').check();
  await page.waitForTimeout(950);
  await page.reload({ waitUntil: "domcontentloaded" });
  assert.equal(await page.locator("#field-surname").inputValue(), "Example");

  await page.goto(`${baseURL}/#/drafts`, { waitUntil: "domcontentloaded" });
  assert.equal(await page.locator(".draft-card").count(), 1);

  await page.goto(`${baseURL}/#/start?mode=example`, { waitUntil: "domcontentloaded" });
  await page.locator("#practiceConsent").check();
  await page.locator('[data-action="create-draft"][data-mode="example"]').click();
  await page.waitForURL(/#\/application\//);
  const currentUrl = new URL(page.url());
  const applicationBase = currentUrl.hash.replace(/\/0$/, "");

  await page.goto(`${baseURL}/${applicationBase}/3`, { waitUntil: "domcontentloaded" });
  await page.locator("#field-practiceEmail").fill("alex@gmail.com");
  await page.locator('[data-action="continue-step"]').click();
  assert.match(await page.locator("#error-practiceEmail").innerText(), /example\.com/);
  await page.locator("#field-practiceEmail").fill("alex@example.com");

  await page.goto(`${baseURL}/${applicationBase}/13`, { waitUntil: "domcontentloaded" });
  assert.match(await page.locator("#stepTitle").innerText(), /复核/);
  await page.locator('[data-action="continue-step"]').click();
  await page.waitForURL(/\/14$/);
  assert.match(await page.locator(".print-sheet").innerText(), /PRACTICE COPY/);
  assert.match(await page.locator(".print-sheet").innerText(), /NOT A VISA APPLICATION/);

  await page.goto(`${baseURL}/#/drafts`, { waitUntil: "domcontentloaded" });
  const downloadPromise = page.waitForEvent("download");
  await page.locator('[data-action="export-draft"]').last().click();
  await page.locator('[data-dialog="confirm"]').click();
  const download = await downloadPromise;
  const downloadPath = await download.path();
  assert.ok(downloadPath);
  const beforeImport = await page.locator(".draft-card").count();
  await page.locator("#importDraftFile").setInputFiles(downloadPath);
  await page.waitForFunction((count) => document.querySelectorAll(".draft-card").length > count, beforeImport);

  await page.keyboard.press("Tab");
  assert.notEqual(await page.evaluate(() => document.activeElement?.tagName), "BODY");
  assert.deepEqual(externalRequests, []);
}
