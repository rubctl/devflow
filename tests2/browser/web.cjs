// Browserprobe Dashboard: Handybreite (390 px, Dark und Light) ohne waagrechtes Scrollen, Live-Log ohne Neuladen.
// Aufruf: node web.cjs <playwright-core> <basis-url> <login> <log.jsonl> <lauf-id> <seiten-json>
// Exit 77 = kein Browser startbar.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.argv[2]);
const [base, login, logDatei, laufId] = process.argv.slice(3, 7);
const seiten = JSON.parse(process.argv[7]);

const zeile = (text) => fs.appendFileSync(logDatei, JSON.stringify({ type: 'schritt', text }) + '\n');

(async () => {
  let browser;
  try {
    browser = await chromium.launch();
  } catch (err) {
    console.error(`Browser nicht startbar: ${err.message.split('\n')[0]}`);
    process.exit(77);
  }
  const fehler = [];
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
    extraHTTPHeaders: { 'Tailscale-User-Login': login },
    // Den Service Worker holt der Browser ohne diese Kopfzeile (im Betrieb setzt sie `tailscale serve`); hier nicht nötig.
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  // Das Dashboard läuft im Tailnet und lädt nichts von außen: jede Anfrage geht an den eigenen Server.
  const fremd = [];
  page.on('request', (rq) => {
    const u = new URL(rq.url());
    if (!['data:', 'blob:', 'about:'].includes(u.protocol) && u.origin !== new URL(base).origin) fremd.push(rq.url());
  });
  page.on('pageerror', (e) => fehler.push(String(e)));
  page.on('console', (m) => m.type() === 'error' && fehler.push(m.text()));

  // 1. Jede Seite bei 390 px in Dark und Light: nichts ragt über den Rand, die Seite scrollt nicht waagrecht.
  for (const [pfad, schema] of seiten.flatMap((p) => [[p, 'dark'], [p, 'light']])) {
    await page.emulateMedia({ colorScheme: schema });
    const r = await page.goto(base + pfad);
    assert.equal(r.status(), 200, pfad);
    await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true; }));
    const mass = await page.evaluate(() => {
      const breit = [...document.querySelectorAll('body *')]
        .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 0.5 && !el.closest('.breit, .tabellenkasten, pre'))
        .map((el) => `${el.tagName}.${el.className}`);
      window.scrollTo(500, 0);
      return { innen: window.innerWidth, doc: document.documentElement.scrollWidth, x: window.scrollX, breit };
    });
    assert.equal(mass.innen, 390, pfad);
    assert.ok(mass.doc <= 390, `${pfad}: Seite ${mass.doc} px breit`);
    const fontsOk = await page.evaluate(async () => { await document.fonts.ready; return document.fonts.check('600 16px Inter') && document.fonts.check('700 16px "JetBrains Mono"'); });
    assert.ok(fontsOk, `${pfad}: CD-Schriften nicht geladen`);
    const grund = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    assert.equal(grund === 'rgb(14, 15, 16)', schema === 'dark', `${pfad}: Thema ${schema} nicht angewendet (${grund})`);
    assert.ok((await page.$$('.rc-btn-primary')).length <= 1, `${pfad}: mehr als ein Primär-Button`);
    if (process.env.DEVFLOW2_SHOTS) {
      await page.screenshot({ path: `${process.env.DEVFLOW2_SHOTS}/${pfad.replace(/[^a-z0-9]+/gi, '_')}-${schema}.png`, fullPage: true });
    }
    assert.equal(mass.x, 0, `${pfad}: waagrecht gescrollt`);
    assert.deepEqual(mass.breit.slice(0, 5), [], `${pfad}: ragt über den Rand`);
  }

  // 1c. Roadmap-Tabelle (S11.2): sechs Spalten passen auf 390 px ohne seitliches Scrollen, weder Kasten noch Seite.
  for (const schema of ['dark', 'light']) {
    await page.emulateMedia({ colorScheme: schema });
    await page.goto(base + '/p/demo/roadmap');
    await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true; }));
    const mass = await page.evaluate(() => [...document.querySelectorAll('.tabellenkasten')].map((k) => {
      const t = k.querySelector('table').getBoundingClientRect();
      return { breit: k.scrollWidth, sichtbar: k.clientWidth, rechts: k.getBoundingClientRect().right, tabelle: t.width, spalten: k.querySelectorAll('thead th').length };
    }));
    assert.ok(mass.length > 0, `Roadmap ${schema}: keine Tabelle`);
    for (const m of mass) {
      assert.equal(m.spalten, 6, `Roadmap ${schema}: ${m.spalten} statt 6 Spalten`);
      assert.ok(m.breit <= m.sichtbar + 1, `Roadmap ${schema}: Tabelle ${m.breit} px scrollt in ${m.sichtbar} px`);
      assert.ok(m.rechts <= 390.5, `Roadmap ${schema}: Kasten ragt über den Rand`);
    }
    const seite = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, x: window.scrollX }));
    assert.ok(seite.doc <= 390 && seite.x === 0, `Roadmap ${schema}: Seite scrollt waagrecht (${seite.doc} px)`);
    if (process.env.DEVFLOW2_SHOTS) await page.screenshot({ path: `${process.env.DEVFLOW2_SHOTS}/roadmap-tabelle-${schema}.png`, fullPage: true });
  }

  // 1b. Leitstand: bei offener Frage ist Block 2 („Läuft gerade“) auf 390 px ohne Scrollen zu sehen; die Navigation sitzt unten.
  await page.goto(base + '/');
  const lage = await page.evaluate(() => ({
    hoehe: window.innerHeight, block2: document.querySelector('#laeuft h2').getBoundingClientRect(), frage: !!document.querySelector('#braucht-dich a[href*="#fragen"]'),
    nav: document.querySelector('nav.haupt').getBoundingClientRect(), balken: document.querySelectorAll('.kontingent progress, .kontingent [role=progressbar]').length,
  }));
  assert.ok(lage.frage, 'keine offene Frage auf der Übersicht');
  assert.ok(lage.block2.bottom <= lage.nav.top, `„Läuft gerade“ nicht ohne Scrollen sichtbar (${lage.block2.bottom} > ${lage.nav.top})`);
  assert.ok(lage.nav.top > lage.hoehe / 2 && Math.abs(lage.nav.bottom - lage.hoehe) < 2, 'Navigation nicht unten');
  assert.equal(lage.balken, 4, 'Balken in der Kopfzeile');
  if (process.env.DEVFLOW2_SHOTS) await page.screenshot({ path: `${process.env.DEVFLOW2_SHOTS}/leitstand-390.png` });

  // 1c. Veraltete Kontingentwerte (≥ 80 %) passen in die schmale Kopfzeile, ohne sich zu überlappen oder abgeschnitten zu werden.
  const alt = await page.evaluate(() => {
    for (const b of document.querySelectorAll('.kontingent .k-balken')) {
      b.classList.add('k-veraltet');
      b.querySelector('.k-wert').innerHTML = '<span class="rc-status rc-status-warn"><span class="rc-status-glyph">▲</span>85 % (veraltet)</span>';
    }
    const r = [...document.querySelectorAll('.kontingent .k-balken')].map((b) => ({ b: b.getBoundingClientRect(),
      e: b.querySelector('.k-etikett').getBoundingClientRect(), w: b.querySelector('.k-wert').getBoundingClientRect() }));
    return { breite: window.innerWidth, r: r.map((x) => ({ rechts: x.w.right, ueberlappt: x.e.bottom > x.w.top + 1 && x.e.right > x.w.left + 1, rand: x.w.right - x.b.right })) };
  });
  for (const x of alt.r) {
    assert.ok(x.rechts <= alt.breite, `veralteter Wert ragt aus dem Bildschirm (${x.rechts})`);
    assert.ok(x.rand <= 1 && !x.ueberlappt, 'veralteter Wert überlappt oder ragt aus seinem Balken');
  }

  // 1d. Update-Leiste liegt über der Navigation, nicht darauf: alle sechs Ziele bleiben anklickbar.
  const nav = await page.evaluate(() => {
    const l = document.createElement('div'); l.id = 'neue-version'; l.className = 'app-leiste';
    l.innerHTML = '<button type="button" class="rc-btn rc-btn-primary">Neue Version – neu laden</button>'; document.body.append(l);
    const n = document.querySelector('nav.haupt').getBoundingClientRect();
    return { leiste: l.getBoundingClientRect().bottom, nav: n.top, ziele: [...document.querySelectorAll('nav.haupt > a, nav.haupt .projektwahl-knopf')].map((a) => {
      const r = a.getBoundingClientRect(); const o = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return o === a || a.contains(o); }) };
  });
  assert.ok(nav.leiste <= nav.nav + 1, `Update-Leiste verdeckt die Navigation (${nav.leiste} > ${nav.nav})`);
  assert.deepEqual(nav.ziele, [true, true, true, true], 'Navigationsziele verdeckt');
  // 1e. Projektwahl öffnet sich nach oben und bleibt im Bild; die Unterleiste der Projektseite passt in 390 px ohne seitliches Scrollen.
  await page.click('.projektwahl-knopf');
  const wahl = await page.evaluate(() => { const r = document.querySelector('.projektwahl ul').getBoundingClientRect(); return { oben: r.top, rechts: r.right, breite: window.innerWidth }; });
  assert.ok(wahl.oben >= 0 && wahl.rechts <= wahl.breite, `Projektwahl ragt aus dem Bild (${JSON.stringify(wahl)})`);
  await page.goto(base + '/p/demo');
  const unter = await page.evaluate(() => { const n = document.querySelector('nav.unter'); return { namen: [...n.querySelectorAll('a')].map((a) => a.textContent), voll: n.scrollWidth <= n.clientWidth + 1 }; });
  assert.deepEqual(unter.namen, ['Projekt', 'Roadmap', 'Eingang', 'Pflege']);
  assert.ok(unter.voll, 'Unterleiste scrollt seitlich');
  await page.goto(base + '/');

  // 2. Live-Log auf der Laufseite: neue Zeilen erscheinen ohne Neuladen.
  for (const pfad of [`/lauf/${laufId}`]) {
    await page.goto(base + pfad);
    const pre = `pre[data-lauf="${laufId}"]`;
    await page.waitForSelector(pre);
    await page.evaluate(() => { window.probeMarke = 'noch da'; });
    const text = `Probezeile ${Date.now()} <b>roh</b>`;
    assert.ok(!(await page.textContent(pre)).includes(text));
    zeile(text);
    await page.waitForFunction(([sel, t]) => document.querySelector(sel).textContent.includes(t), [pre, text], { timeout: 15000 });
    assert.equal(await page.evaluate(() => window.probeMarke), 'noch da', `${pfad}: Seite wurde neu geladen`);
    assert.equal(await page.$$eval(`${pre} b`, (b) => b.length), 0, 'Logzeile als HTML eingefügt');
  }

  // Screenshots für den Bericht auch in Desktopbreite (nur mit DEVFLOW2_SHOTS).
  if (process.env.DEVFLOW2_SHOTS) {
    for (const schema of ['dark', 'light']) {
      const breit = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: schema,
        extraHTTPHeaders: { 'Tailscale-User-Login': login }, serviceWorkers: 'block' });
      const seite = await breit.newPage();
      for (const pfad of seiten.slice(0, 3)) {
        await seite.goto(base + pfad);
        await seite.screenshot({ path: `${process.env.DEVFLOW2_SHOTS}/1280${pfad.replace(/[^a-z0-9]+/gi, '_')}-${schema}.png`, fullPage: true });
      }
      await breit.close();
    }
  }
  assert.deepEqual(fremd, [], 'Anfragen an fremde Hosts');
  assert.deepEqual(fehler, []);
  await browser.close();
  console.log('Browserprobe bestanden');
})().catch(async (err) => {
  console.error(err);
  process.exit(1);
});
