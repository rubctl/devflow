// Service Worker: (1) App-Hülle: Seiten und statische Dateien zuerst aus dem Netz, sonst die zuletzt geladene Fassung mit
// Hinweis „▲ offline, Stand HH:MM“; Handlungen (alles außer GET) gehen nie aus dem Speicher und werden offline abgelehnt.
// (2) Web-Push-Hinweise: Der Hinweis trägt nur Anzahl und Art (count, kind); der Text entsteht hier.
'use strict';
const CACHE = 'devflow-seiten-1';  // bei Änderung am Speicherformat hochzählen; alte Speicher räumt `activate` ab
const VORLAUF = ['/static/cd/cd.css', '/static/style.css', '/static/app.js', '/static/logo/devflow-signet.svg'];
const WARTEN = 8000;  // so lange wartet eine Seite mit Kopie auf das Netz, dann gilt sie als offline
const NETZ_AUS = new Set([502, 503, 504]);  // „Dienst nicht erreichbar“ (z. B. tailscale serve ohne Server) zählt wie offline
const MAX_SEITEN = 150;
const STAND = 'x-devflow-stand';
const SICHERHEIT = {
  'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'x-content-type-options': 'nosniff', 'referrer-policy': 'same-origin', 'cache-control': 'no-store',
};
const TEXTE = {
  braucht: ['Eine Aufgabe braucht dich', (n) => `${n} Aufgaben brauchen dich`],
  frage: ['Neue offene Frage', (n) => `${n} neue offene Fragen`],
  fehler: ['Ein Lauf ist fehlgeschlagen', (n) => `${n} Läufe fehlgeschlagen`],
  budget: ['Selbstlauf: Tagesbudget verbraucht', () => 'Selbstlauf: Tagesbudget verbraucht'],
  pflege: ['Pflege: Sicherheitslücke gefunden', (n) => `Pflege: ${n} Sicherheitslücken gefunden`],
  traum: ['Traum beendet', () => 'Traum beendet'],
  test: ['Probehinweis: so meldet sich devflow', () => 'Probehinweis: so meldet sich devflow'],
};
const anzahl = (n, ein, viele) => `${n} ${n === 1 ? ein : viele}`;
const traumText = ([v, p]) => `Traum beendet: ${anzahl(v, 'Vorschlag', 'Vorschläge')}, ${anzahl(p, 'Plan', 'Pläne')}`;

self.addEventListener('push', (ev) => {
  let d = {};
  try { d = ev.data ? ev.data.json() : {}; } catch (err) { d = {}; }
  const art = Object.prototype.hasOwnProperty.call(TEXTE, d.kind) ? d.kind : 'frage';
  const n = Number.isInteger(d.count) && d.count > 0 ? d.count : 1;
  ev.waitUntil(self.registration.showNotification('devflow', {
    body: art === 'traum' && Array.isArray(d.zahlen) ? traumText(d.zahlen) : n === 1 ? TEXTE[art][0] : TEXTE[art][1](n),
    tag: `devflow-${art}`, renotify: true, icon: '/static/icons/icon-192-any.png', badge: '/static/icons/badge-96.png', data: { url: '/' },
  }));
});

self.addEventListener('notificationclick', (ev) => {
  ev.notification.close();
  ev.waitUntil((async () => {
    const offen = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of offen) {
      if (new URL(c.url).origin === self.location.origin && 'focus' in c) return c.focus();
    }
    return self.clients.openWindow('/');
  })());
});

// --- App-Hülle ------------------------------------------------------------------------------------

const uhr = (ms) => new Date(ms).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });

const html = (body, status) => new Response(body, { status, headers: { ...SICHERHEIT, 'content-type': 'text/html; charset=utf-8' } });

const gerueste = (stand, inhalt) => `<!doctype html><html lang="de"><head><meta charset="utf-8">`
  + `<meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline · devflow</title>`
  + `<link rel="stylesheet" href="/static/cd/cd.css"><link rel="stylesheet" href="/static/style.css"></head>`
  + `<body data-offline="${stand}"><p class="offline-hinweis" role="status">▲ offline</p><main>${inhalt}</main></body></html>`;

const keineKopie = () => html(gerueste('', '<h1>Offline</h1><p>Diese Seite ist auf diesem Gerät noch nicht gespeichert. '
  + 'Sie erscheint hier, sobald du sie einmal mit Verbindung geöffnet hast.</p><p><a href="/">Zur Übersicht</a></p>'), 503);

const sperre = (req) => (req.mode === 'navigate'
  ? html(gerueste('', '<h1>Offline</h1><p>Die Handlung wurde nicht ausgeführt: Ohne Verbindung sind Handlungen gesperrt. '
    + 'Versuche es noch einmal, wenn du wieder verbunden bist, und prüfe vorher den Stand.</p><p><a href="/">Zur Übersicht</a></p>'), 503)
  : new Response(JSON.stringify({ fehler: 'offline' }), { status: 503, headers: { 'content-type': 'application/json' } }));

// Kopie mit Sicherheits-Kopfzeilen der Originalantwort und Zeitstempel; Kodierung und Länge entfallen (Body ist entpackt).
const kopieren = async (r) => {
  const kopf = new Headers();
  for (const [k, v] of r.headers) if (!['content-length', 'content-encoding', 'transfer-encoding', 'set-cookie'].includes(k)) kopf.set(k, v);
  kopf.set(STAND, String(Date.now()));
  return new Response(await r.blob(), { status: 200, headers: kopf });
};

const speichern = async (cache, schluessel, r) => {
  await cache.put(schluessel, await kopieren(r));
  const seiten = (await cache.keys()).filter((k) => !new URL(k.url).pathname.startsWith('/static/'));
  for (const k of seiten.slice(0, Math.max(0, seiten.length - MAX_SEITEN))) await cache.delete(k);
};

// Gespeicherte Seite mit Hinweis ausliefern: Banner und `data-offline` (Stand) im Text, damit app.js die Handlungen sperrt.
const alsOffline = async (da) => {
  const zeit = uhr(Number(da.headers.get(STAND)) || Date.now());
  if (!(da.headers.get('content-type') || '').startsWith('text/html')) return da;
  const text = (await da.text()).replace(/<body([^>]*)>/, `<body$1 data-offline="${zeit}"><p class="offline-hinweis" role="status">▲ offline, Stand ${zeit}</p>`);
  const kopf = new Headers(da.headers);
  kopf.delete(STAND);
  return new Response(text, { status: 200, headers: kopf });
};

const netzZuerst = async (ev, req, url) => {
  const cache = await caches.open(CACHE);
  const da = await cache.match(req.url);
  const navigation = req.mode === 'navigate';
  let r = null;
  try {
    r = await fetch(req, da && typeof AbortSignal.timeout === 'function' ? { signal: AbortSignal.timeout(WARTEN) } : undefined);
  } catch (err) {
    r = null;
  }
  if (r && !NETZ_AUS.has(r.status)) {
    const art = r.headers.get('content-type') || '';
    // Weitergeleitetes und Einmal-Meldungen (?meldung=…) nie merken; Seiten nur als HTML.
    if (r.ok && !r.redirected && !url.searchParams.has('meldung') && (!navigation || art.startsWith('text/html'))) {
      ev.waitUntil(speichern(cache, req.url, r.clone()).catch(() => null));
    }
    return r;
  }
  if (da) return navigation ? alsOffline(da) : da;
  if (r) return r;
  return navigation ? keineKopie() : Response.error();
};

self.addEventListener('install', (ev) => {
  // Hülle vorladen, damit eine Seite auch bei der ersten Offline-Öffnung Aussehen hat; scheitert das, bleibt der Worker trotzdem gültig.
  ev.waitUntil(caches.open(CACHE).then((c) => Promise.all(VORLAUF.map((p) => c.add(p).catch(() => null)))));
});

self.addEventListener('activate', (ev) => {
  ev.waitUntil((async () => {
    for (const name of await caches.keys()) if (name.startsWith('devflow-seiten-') && name !== CACHE) await caches.delete(name);
    await self.clients.claim();
    // Die Seite, die den Worker gerade erst angemeldet hat, lief ohne ihn und ist noch nicht gespeichert: jetzt nachholen.
    const cache = await caches.open(CACHE);
    for (const c of await self.clients.matchAll({ type: 'window', includeUncontrolled: true })) {
      const url = new URL(c.url);
      url.hash = '';
      if (url.origin !== self.location.origin || url.searchParams.has('meldung')) continue;
      try {
        const r = await fetch(url.href, { signal: AbortSignal.timeout(WARTEN) });
        if (r.ok && !r.redirected && (r.headers.get('content-type') || '').startsWith('text/html')) await speichern(cache, url.href, r);
      } catch (err) { /* bleibt ungespeichert, die nächste Öffnung holt es nach */ }
    }
  })());
});

// Neue Version: wartet, bis die Seite „Neue Version – neu laden“ bestätigt (app.js).
self.addEventListener('message', (ev) => {
  if (ev.data && ev.data.art === 'aktivieren') self.skipWaiting();
});

self.addEventListener('fetch', (ev) => {
  const req = ev.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || req.method === 'HEAD') return;
  if (req.method !== 'GET') {  // Handlung: nie aus dem Speicher, nie vormerken; ohne Netz klar ablehnen
    ev.respondWith(fetch(req).catch(() => sperre(req)));
    return;
  }
  if (req.mode === 'navigate' || url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    ev.respondWith(netzZuerst(ev, req, url));
  }
});
