// Browserprobe App (PWA): Manifest gültig und installierbar, Service Worker aktiv, offline gespeicherte Seite mit Hinweis statt
// Browserfehler, Handlungen offline gesperrt, Installationsknopf, „Neue Version – neu laden“ (auch im frischen Profil).
// Aufruf: node app.cjs <playwright-core> <server-url> <login>
// Exit 77 = kein Browser startbar.
// Den Service Worker holt der Browser ohne Zusatz-Kopfzeilen (im Betrieb setzt `tailscale serve` die Anmeldung). Deshalb geht der
// Browser über einen kleinen Vorschalter, der die Kopfzeile ergänzt; „offline“ heißt: der Vorschalter wird gestoppt.
'use strict';
const assert = require('node:assert/strict');
const http = require('node:http');
const { chromium } = require(process.argv[2]);
const [ziel, login] = process.argv.slice(3, 5);
const zielUrl = new URL(ziel);

let version2 = false;  // liefert /sw.js mit geändertem Inhalt: „neue Version“
const vorschalter = () => http.createServer((req, res) => {
  const an = http.request({ host: zielUrl.hostname, port: zielUrl.port, method: req.method, path: req.url,
    headers: { ...req.headers, 'tailscale-user-login': login } }, (r) => {
    if (version2 && req.url === '/sw.js') {
      const stuecke = [];
      r.on('data', (d) => stuecke.push(d));
      r.on('end', () => {
        const kopf = { ...r.headers };
        delete kopf['content-length'];
        res.writeHead(r.statusCode, kopf);
        res.end(Buffer.concat([...stuecke, Buffer.from('\n// version 2\n')]));
      });
      return;
    }
    res.writeHead(r.statusCode, r.headers);
    r.pipe(res);
  });
  an.on('error', () => { res.statusCode = 502; res.end(); });
  req.pipe(an);
});
const starten = (server, port) => new Promise((ok) => server.listen(port, '127.0.0.1', ok));
// `waitForFunction` wartet nicht auf ein Promise aus der Seite (ein async-Prädikat gilt sofort als wahr); deshalb hier abfragen,
// bis der Zustand eindeutig erreicht ist.
const warten = async (page, pruefung, arg, timeout = 15000) => {
  const ende = Date.now() + timeout;
  while (!(await page.evaluate(pruefung, arg))) {
    if (Date.now() > ende) throw new Error(`Zustand nicht erreicht: ${pruefung}`);
    await page.waitForTimeout(100);
  }
};
const stoppen = (server) => new Promise((ok) => { server.close(ok); server.closeAllConnections(); });

(async () => {
  let browser;
  try {
    browser = await chromium.launch();
  } catch (err) {
    console.error(`Browser nicht startbar: ${err.message.split('\n')[0]}`);
    process.exit(77);
  }
  let server = vorschalter();
  await starten(server, 0);
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const fehler = [];
  page.on('pageerror', (err) => fehler.push(err.message));
  page.on('dialog', (d) => d.accept());

  // 1. Manifest gültig und installierbar, Service Worker aktiv.
  await page.goto(`${base}/`);
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null, null, { timeout: 15000 });
  const cdp = await context.newCDPSession(page);
  const m = await cdp.send('Page.getAppManifest');
  assert.deepEqual(m.errors, [], `Manifest-Fehler: ${JSON.stringify(m.errors)}`);
  const man = JSON.parse(m.data);
  assert.equal(man.id, '/');
  assert.ok(man.description && man.display === 'standalone');
  assert.deepEqual(man.shortcuts.map((s) => s.name), ['Braucht dich', 'Roadmap', 'Eingang', 'Kontingent']);
  for (const s of man.shortcuts) {
    const r = await page.request.get(new URL(s.url, base).href);
    assert.equal(r.status(), 200, s.url);
  }
  assert.deepEqual(man.icons.map((i) => `${i.sizes}/${i.purpose}`).sort(), ['192x192/any', '192x192/maskable', '512x512/any', '512x512/maskable']);
  const inst = await cdp.send('Page.getInstallabilityErrors');
  assert.deepEqual(inst.installabilityErrors, [], `nicht installierbar: ${JSON.stringify(inst.installabilityErrors)}`);
  assert.equal(await page.locator('#app-installieren').isVisible(), false, 'Installationsknopf ohne Angebot des Browsers');

  // 2. Seiten laden (werden gemerkt), dann Server weg: Fassung von eben mit Hinweis statt Browserfehler.
  const seiten = ['/', '/p/demo/a/A1', '/kontingent'];
  for (const s of seiten) await page.goto(`${base}${s}`);
  await warten(page, async (n) => (await (await caches.open('devflow-seiten-1')).keys()).filter((k) => !k.url.includes('/static/')).length >= n, seiten.length);
  await page.goto(`${base}/p/demo/a/A1`);
  assert.equal(await page.locator('body[data-offline]').count(), 0);
  const ueberschrift = (await page.locator('h1').first().textContent()).trim();
  await stoppen(server);

  const offline = await page.goto(`${base}/p/demo/a/A1`);
  assert.equal(offline.status(), 200);
  assert.match(await page.locator('.offline-hinweis').first().textContent(), /^▲ offline, Stand \d\d:\d\d$/);
  assert.match(await page.locator('body').getAttribute('data-offline'), /^\d\d:\d\d$/);
  assert.equal((await page.locator('h1').first().textContent()).trim(), ueberschrift, 'gespeicherte Fassung zeigt den Inhalt');
  const knoepfe = page.locator('form[method="post"] button, form[method="post"] textarea');
  assert.ok(await knoepfe.count() > 0);
  for (let i = 0; i < await knoepfe.count(); i++) assert.equal(await knoepfe.nth(i).isDisabled(), true, 'Handlung offline gesperrt');

  // 3. POST offline gesperrt: per Skript und als Formular-Navigation; nichts wird vorgemerkt.
  const antwort = await page.evaluate(async () => {
    const r = await fetch('/alles/anhalten', { method: 'POST', credentials: 'same-origin' });
    return [r.status, await r.json()];
  });
  assert.deepEqual(antwort, [503, { fehler: 'offline' }]);
  await page.evaluate(() => {
    const f = document.createElement('form');
    f.method = 'post';
    f.action = '/alles/anhalten';
    document.body.append(f);
    f.submit();
  });
  await page.waitForFunction(() => document.body.textContent.includes('Handlung wurde nicht ausgeführt'));

  // 4. Nie geladene Seite offline: Hinweis statt Fehler.
  const fremd = await page.goto(`${base}/plaene`);
  assert.equal(fremd.status(), 503);
  assert.match(await page.locator('body').textContent(), /noch nicht gespeichert/);

  // 5. Wieder online: frische Fassung ohne Hinweis. Neuer Vorschalter auf demselben Port.
  server = vorschalter();
  await starten(server, port);
  await page.goto(`${base}/p/demo/a/A1`);
  assert.equal(await page.locator('body[data-offline]').count(), 0);
  assert.equal(await page.locator('.offline-hinweis').count(), 0);

  // 5b. Neues Profil: erste Öffnung gar nicht über den Worker gelaufen, trotzdem offline mit Stand, sobald der Worker aktiv ist.
  const frisch = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const erste = await frisch.newPage();
  await erste.goto(`${base}/p/demo/a/A1`);
  await warten(erste, async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    return Boolean(reg && reg.active && reg.active.state === 'activated' && navigator.serviceWorker.controller
      && (await (await caches.open('devflow-seiten-1')).keys()).some((k) => k.url.endsWith('/p/demo/a/A1')));
  });
  await stoppen(server);
  const nochmal = await erste.goto(`${base}/p/demo/a/A1`);
  assert.equal(nochmal.status(), 200);
  assert.match(await erste.locator('.offline-hinweis').first().textContent(), /^▲ offline, Stand \d\d:\d\d$/);
  await frisch.close();
  server = vorschalter();
  await starten(server, port);

  // 6. Installationsknopf erscheint erst mit dem Angebot des Browsers und ruft es genau einmal auf.
  await page.evaluate(() => {
    window.__angebote = 0;
    const ev = new Event('beforeinstallprompt', { cancelable: true });
    ev.prompt = () => { window.__angebote += 1; };
    ev.userChoice = Promise.resolve({ outcome: 'accepted' });
    window.dispatchEvent(ev);
  });
  await page.locator('#app-installieren').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#app-installieren').textContent(), 'Als App installieren');
  await page.click('#app-installieren');
  assert.equal(await page.evaluate(() => window.__angebote), 1);
  assert.equal(await page.locator('#app-installieren').isVisible(), false);

  // 7. Neue Version: geänderter Service Worker wartet, Hinweis erscheint, Klick lädt neu und aktiviert ihn.
  await page.evaluate(() => { window.__alt = true; });
  assert.equal(await page.locator('#neue-version').count(), 0);
  version2 = true;
  await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistration()).update(); });
  const hinweis = page.locator('#neue-version button');
  await hinweis.waitFor({ state: 'visible', timeout: 15000 });
  assert.equal(await hinweis.textContent(), 'Neue Version – neu laden');
  await Promise.all([page.waitForNavigation(), hinweis.click()]);
  assert.equal(await page.evaluate(() => window.__alt === undefined), true, 'Seite wurde neu geladen');
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
  assert.equal(await page.locator('#neue-version').count(), 0);

  // 8. Frisches Profil: erste Übernahme durch den Worker lädt nicht neu; die Seite bleibt offen, eine neue Version wird angeboten,
  // der Klick lädt genau dann neu und der Hinweis verschwindet.
  version2 = false;
  const neuProfil = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const offen = await neuProfil.newPage();
  offen.on('pageerror', (err) => fehler.push(err.message));
  let ladungen = 0;
  offen.on('load', () => { ladungen += 1; });
  await offen.goto(`${base}/p/demo/a/A1`);
  await warten(offen, async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    return Boolean(reg && reg.active && reg.active.state === 'activated' && navigator.serviceWorker.controller);
  });
  await offen.evaluate(() => { window.__alt = true; });
  assert.equal(ladungen, 1, 'erste Übernahme lädt nicht neu');
  assert.equal(await offen.evaluate(() => window.__alt === true), true);
  version2 = true;
  await offen.evaluate(async () => { await (await navigator.serviceWorker.getRegistration()).update(); });
  const hinweis2 = offen.locator('#neue-version button');
  await hinweis2.waitFor({ state: 'visible', timeout: 15000 });
  await Promise.all([offen.waitForNavigation(), hinweis2.click()]);
  assert.equal(await offen.evaluate(() => window.__alt === undefined), true, 'Seite wurde neu geladen');
  await warten(offen, () => navigator.serviceWorker.controller !== null);
  assert.equal(await offen.locator('#neue-version').count(), 0);
  assert.equal(ladungen, 2, 'genau ein Neuladen');
  await neuProfil.close();

  assert.deepEqual(fehler, []);
  await browser.close();
  await stoppen(server);
  console.log('App-Probe bestanden');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
