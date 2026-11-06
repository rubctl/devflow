// Browserprobe Pläne-Seite (S14.3b) bei 390 px: keine waagrechte Scrollleiste (zu und aufgeklappt), „Erledigt“ eingeklappt unten,
// Knopf „Abnehmen“ neben der Überschrift.
// Aufruf: node plaene.cjs <playwright-core> <basis-url> <login> <pfad>...
// Exit 77 = kein Browser startbar.
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require(process.argv[2]);
const [base, login] = process.argv.slice(3, 5);
const pfade = process.argv.slice(5);

(async () => {
  let browser;
  try {
    browser = await chromium.launch();
  } catch (err) {
    console.error(`Browser nicht startbar: ${err.message.split('\n')[0]}`);
    process.exit(77);
  }
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
    extraHTTPHeaders: { 'Tailscale-User-Login': login }, serviceWorkers: 'block',
  });
  const page = await context.newPage();
  const fehler = [];
  page.on('pageerror', (e) => fehler.push(String(e)));
  const breite = () => page.evaluate(() => ({ innen: window.innerWidth, doc: document.documentElement.scrollWidth }));
  for (const pfad of pfade) {
    await page.goto(`${base}${pfad}`);
    let b = await breite();
    assert.ok(b.doc <= b.innen, `${pfad}: Seite ${b.doc} px breit bei ${b.innen}`);
    const erledigt = page.locator('details.erledigt');
    assert.equal(await erledigt.count(), 1, `${pfad}: „Erledigt“ fehlt`);
    assert.equal(await erledigt.evaluate((d) => d.open), false, `${pfad}: „Erledigt“ ist nicht eingeklappt`);
    const unten = await page.evaluate(() => {
      const e = document.querySelector('details.erledigt').getBoundingClientRect().top;
      return [...document.querySelectorAll('li.eintrag')].filter((li) => !li.closest('details.erledigt')).every((li) => li.getBoundingClientRect().bottom <= e);
    });
    assert.ok(unten, `${pfad}: „Erledigt“ steht nicht ganz unten`);
    for (const d of await page.locator('li.eintrag > details, details.erledigt').all()) await d.evaluate((el) => { el.open = true; });
    b = await breite();
    assert.ok(b.doc <= b.innen, `${pfad} aufgeklappt: Seite ${b.doc} px breit bei ${b.innen}`);
    for (const z of await page.locator('li.eintrag:has(.knopf)').all()) {
      const s = await z.locator('summary').boundingBox();
      const k = await z.locator('.knopf .rc-btn').boundingBox();
      assert.ok(k.x >= s.x + s.width - 1 && k.x + k.width <= 390.5, `${pfad}: Knopf nicht neben der Überschrift`);
    }
  }
  await browser.close();
  assert.deepEqual(fehler, [], 'Skriptfehler');
  console.log('Pläneprobe bestanden');
})().catch((e) => { console.error(e.stack || e.message); process.exit(1); });
