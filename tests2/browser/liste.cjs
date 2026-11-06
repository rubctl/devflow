// Browserprobe Entscheidungsliste (S14.3) bei 390 px: keine waagrechte Scrollleiste, Knopf neben der Überschrift,
// Aufklappen per Tastatur und per Tippen, „Antworten“ öffnet die Zeile, Klick auf „Empfehlung annehmen“ wirkt sofort und nur einmal.
// Aufruf: node liste.cjs <playwright-core> <basis-url> <login>
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
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
    extraHTTPHeaders: { 'Tailscale-User-Login': login }, serviceWorkers: 'block',
  });
  const page = await context.newPage();
  const fehler = [];
  page.on('pageerror', (e) => fehler.push(String(e)));
  await page.goto(`${base}/`);

  const zeilen = page.locator('#braucht-dich li.eintrag');
  assert.ok((await zeilen.count()) >= 2, 'Einträge fehlen');

  // 1. Nichts ragt über den Rand, auch nicht aufgeklappt.
  const breite = () => page.evaluate(() => ({ innen: window.innerWidth, doc: document.documentElement.scrollWidth }));
  let b = await breite();
  assert.ok(b.doc <= b.innen, `Seite ${b.doc} px breit bei ${b.innen}`);
  for (const d of await page.locator('#braucht-dich li.eintrag > details').all()) await d.evaluate((el) => { el.open = true; });
  b = await breite();
  assert.ok(b.doc <= b.innen, `aufgeklappt: Seite ${b.doc} px breit bei ${b.innen}`);
  for (const d of await page.locator('#braucht-dich li.eintrag > details').all()) await d.evaluate((el) => { el.open = false; });

  // 2. Der Knopf steht neben der Überschrift (auf Höhe der Zusammenfassung, rechts davon), mit Tippfläche von 44 px.
  const mitKnopf = page.locator('#braucht-dich li.eintrag:has(.knopf)');
  assert.ok((await mitKnopf.count()) >= 1, 'kein Eintrag mit Knopf');
  for (const z of await mitKnopf.all()) {
    const s = await z.locator('summary').boundingBox();
    const k = await z.locator('.knopf .rc-btn').boundingBox();
    assert.ok(k.x >= s.x + s.width - 1, `Knopf (${k.x}) steht nicht rechts der Überschrift (${s.x + s.width})`);
    assert.ok(k.y < s.y + s.height && k.y + k.height > s.y, 'Knopf nicht auf Höhe der Überschrift');
    assert.ok(k.height >= 43.5, `Tippfläche nur ${k.height} px`);
    assert.ok(k.x + k.width <= 390.5, 'Knopf ragt über den Rand');
  }

  // 3. Aufklappen per Tastatur (Enter und Leertaste) und per Tippen.
  const erste = zeilen.first();
  const details = erste.locator('details');
  await erste.locator('summary').focus();
  await page.keyboard.press('Enter');
  assert.equal(await details.evaluate((d) => d.open), true, 'Enter klappt nicht auf');
  await page.keyboard.press('Space');
  assert.equal(await details.evaluate((d) => d.open), false, 'Leertaste klappt nicht zu');
  await erste.locator('summary').tap();
  assert.equal(await details.evaluate((d) => d.open), true, 'Tippen klappt nicht auf');
  await erste.locator('summary').tap();

  // 4. „Antworten“ (Frage ohne eindeutige Empfehlung) öffnet die Zeile und springt ins Antwortfeld.
  const antworten = page.locator('#braucht-dich a[data-antworten]').first();
  if (await antworten.count()) {
    await antworten.tap();
    assert.equal(await antworten.locator('xpath=ancestor::li[1]//details').evaluate((d) => d.open), true, '„Antworten“ öffnet nicht');
    assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.tagName), 'TEXTAREA');
  }

  // 5. Ein Klick wirkt sofort (keine Rückfrage) und nur einmal, auch beim Doppeltippen.
  let dialoge = 0;
  page.on('dialog', (d) => { dialoge += 1; d.dismiss(); });
  const empf = page.locator('#braucht-dich form[action$="/empfehlung"] button').first();
  assert.ok(await empf.count(), 'kein Knopf „Empfehlung annehmen“');
  assert.equal((await empf.textContent()).trim(), 'Empfehlung annehmen');
  await Promise.all([page.waitForURL(/meldung=empfehlung/), empf.evaluate((b) => { b.click(); b.click(); })]);  // Doppelklick
  assert.equal(dialoge, 0, 'der Klick fragte nach');
  assert.match(await page.textContent('body'), /Empfehlung angenommen/);
  const b2 = await breite();
  assert.ok(b2.doc <= b2.innen, 'nach dem Klick: waagrechtes Scrollen');

  assert.deepEqual(fehler, []);
  await browser.close();
  console.log('Listenprobe bestanden');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
