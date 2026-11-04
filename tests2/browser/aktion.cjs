// Browserprobe Formulare: Antwort senden und „Alles anhalten“ per echtem Klick (Origin kommt vom Browser).
// Aufruf: node aktion.cjs <playwright-core> <basis-url> <login>
// Exit 77 = kein Browser startbar.
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require(process.argv[2]);
const [base, login] = process.argv.slice(3, 5);

(async () => {
  let browser;
  try {
    browser = await chromium.launch();
  } catch (err) {
    console.error(`Browser nicht startbar: ${err.message.split('\n')[0]}`);
    process.exit(77);
  }
  const context = await browser.newContext({ extraHTTPHeaders: { 'Tailscale-User-Login': login } });
  const page = await context.newPage();
  page.on('dialog', (d) => d.accept());

  await page.goto(`${base}/p/demo/a/A1`);
  await page.fill('textarea[name="antwort"]', 'Celsius, bitte');
  await Promise.all([page.waitForURL(/meldung=antwort/), page.click('form[action$="/antwort"] button')]);
  assert.match(await page.textContent('body'), /Antwort ist in der Roadmap eingetragen/);

  await page.goto(`${base}/`);
  await page.click('#selbstlauf summary'); // Knöpfe sind eingeklappt
  await Promise.all([page.waitForURL(/meldung=alles-angehalten/), page.click('form[action="/alles/anhalten"] button')]);
  assert.match(await page.textContent('body'), /Alles angehalten/);

  await browser.close();
  console.log('Aktionsprobe bestanden');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
