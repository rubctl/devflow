// Live-Log: Läufe, die noch aktiv sind, holen ihre letzten Zeilen alle zwei Sekunden nach, ohne die Seite neu zu laden.
// Text kommt nur über textContent in die Seite, nie als HTML.
// Handlungen: Formulare mit data-bestaetigen fragen vorher nach; jedes Formular geht nur einmal ab (Doppelklick).
// App: Service Worker anmelden, „Als App installieren“, „Neue Version – neu laden“, offline: Handlungen sperren.
'use strict';
(() => {
  // Projektwahl in der oberen Leiste: Knopf klappt die Liste auf und zu; Klick daneben oder Escape schließt sie.
  const wahl = document.querySelector('.projektwahl');
  if (wahl) {
    const knopf = wahl.querySelector('button');
    const liste = wahl.querySelector('ul');
    const zu = () => { liste.hidden = true; knopf.setAttribute('aria-expanded', 'false'); };
    knopf.addEventListener('click', () => { liste.hidden = !liste.hidden; knopf.setAttribute('aria-expanded', String(!liste.hidden)); });
    document.addEventListener('click', (ev) => { if (!wahl.contains(ev.target)) zu(); });
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') zu(); });
  }
  const TAKT = 2000;
  // Text und Art je Zustand kommen vom Server (web.py, ZUSTAND); hier nur das Symbol: ● ok, ◐ aktiv, ○ idle, ▲ warn, ✕ error.
  const GLYPH = { ok: '●', aktiv: '◐', idle: '○', warn: '▲', error: '✕' };
  const zustandZeigen = (id, text, art) => {
    for (const el of document.querySelectorAll('[data-zustand]')) {
      if (el.dataset.zustand === id) {
        art = GLYPH[art] ? art : 'idle';
        const glyph = document.createElement('span');
        glyph.className = 'rc-status-glyph';
        glyph.setAttribute('aria-hidden', 'true');
        glyph.textContent = GLYPH[art];
        el.replaceChildren(glyph, document.createTextNode(text));
        el.className = `rc-status rc-status-${art}`;
      }
    }
  };
  const beobachten = (pre) => {
    const id = pre.dataset.lauf;
    const zeilen = pre.dataset.zeilen || '40';
    const holen = async () => {
      let warten = TAKT;
      try {
        const r = await fetch(`/api/lauf/${encodeURIComponent(id)}?zeilen=${encodeURIComponent(zeilen)}`,
          { cache: 'no-store', credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        const unten = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
        const text = d.log.join('\n') || '(noch keine Ausgabe)';
        if (pre.textContent !== text) {
          pre.textContent = text;
          if (unten) pre.scrollTop = pre.scrollHeight;
        }
        zustandZeigen(id, d.text, d.art);
        if (!d.aktiv) return;
      } catch (err) {
        warten = TAKT * 5;
      }
      setTimeout(holen, warten);
    };
    pre.scrollTop = pre.scrollHeight;
    setTimeout(holen, TAKT);
  };
  // Die gespeicherte Fassung (Service Worker) ist ein Stand von früher: kein Nachladen des Logs.
  const gespeichert = document.body.hasAttribute('data-offline');
  if (!gespeichert) document.querySelectorAll('pre[data-lauf]').forEach(beobachten);

  const freigeben = (form) => {
    delete form.dataset.gesendet;
    form.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  };
  document.querySelectorAll('form.aktion').forEach((form) => {
    form.addEventListener('submit', (ev) => {
      if (gespeichert || !navigator.onLine) {  // offline: nichts senden (der Service Worker lehnt es sonst auch ab)
        ev.preventDefault();
        return;
      }
      if (form.dataset.gesendet || (form.dataset.bestaetigen && !window.confirm(form.dataset.bestaetigen))) {
        ev.preventDefault();
        return;
      }
      form.dataset.gesendet = '1';
      setTimeout(() => form.querySelectorAll('button').forEach((b) => { b.disabled = true; }), 0);
    });
  });
  // Zurück-Taste (Seite aus dem Zwischenspeicher): Knöpfe wieder freigeben.
  window.addEventListener('pageshow', (ev) => {
    if (ev.persisted) document.querySelectorAll('form.aktion').forEach(freigeben);
  });
  // Offline: Handlungen (POST-Formulare) sperren. Die gespeicherte Seite trägt Banner und `data-offline` vom Service Worker;
  // wird die Verbindung erst bei offener Seite weg, zeigt dieser Teil das Banner selbst.
  const sperren = (an) => document.querySelectorAll('form[method="post"]').forEach((form) => {
    form.querySelectorAll('button, input, textarea, select').forEach((el) => {
      if (an && !el.disabled) { el.disabled = true; el.dataset.offlineGesperrt = '1'; }
      if (!an && el.dataset.offlineGesperrt) { delete el.dataset.offlineGesperrt; el.disabled = false; }
    });
  });
  const offlineBanner = (an) => {
    let b = document.getElementById('offline-banner');
    if (an && !b && !gespeichert) {
      b = document.createElement('p');
      b.id = 'offline-banner';
      b.className = 'offline-hinweis';
      b.setAttribute('role', 'status');
      b.textContent = '▲ offline';
      document.body.prepend(b);
    }
    if (!an && b) b.remove();
    sperren(an);
  };
  window.addEventListener('offline', () => offlineBanner(true));
  window.addEventListener('online', () => {
    if (gespeichert) location.reload();  // wieder verbunden: frische Fassung statt Stand von früher
    else offlineBanner(false);
  });
  if (gespeichert || !navigator.onLine) offlineBanner(true);

  // Installierbar: Chrome/Android bietet die Installation per Ereignis an; der Knopf im Kopf erscheint erst dann.
  const installKnopf = document.getElementById('app-installieren');
  let angebot = null;
  window.addEventListener('beforeinstallprompt', (ev) => {
    ev.preventDefault();
    angebot = ev;
    if (installKnopf) installKnopf.hidden = false;
  });
  window.addEventListener('appinstalled', () => { angebot = null; if (installKnopf) installKnopf.hidden = true; });
  if (installKnopf) {
    installKnopf.addEventListener('click', async () => {
      const a = angebot;
      if (!a) return;
      angebot = null;  // das Angebot gilt nur einmal
      installKnopf.hidden = true;
      a.prompt();
      await a.userChoice.catch(() => null);
    });
  }

  // Neue Version: ein neuer Service Worker wartet, bis hier „Neue Version – neu laden“ gedrückt wird.
  // Neu geladen wird nur nach diesem Klick (`angefordert`), nicht bei der ersten Übernahme (clients.claim) in einem frischen Profil.
  let angefordert = false;
  const neueVersion = (reg) => {
    if (document.getElementById('neue-version')) return;
    const leiste = document.createElement('div');
    leiste.id = 'neue-version';
    leiste.className = 'app-leiste';
    leiste.setAttribute('role', 'status');
    const knopf = document.createElement('button');
    knopf.type = 'button';
    knopf.className = 'rc-btn rc-btn-primary';
    knopf.textContent = 'Neue Version – neu laden';
    knopf.addEventListener('click', () => {
      knopf.disabled = true;
      angefordert = true;
      if (reg.waiting) reg.waiting.postMessage({ art: 'aktivieren' });
      else location.reload();
    });
    leiste.append(knopf);
    document.body.append(leiste);
  };
  const aktualisierung = (reg) => {
    const wartet = () => { if (reg.waiting && navigator.serviceWorker.controller) neueVersion(reg); };
    wartet();
    reg.addEventListener('updatefound', () => {
      const neu = reg.installing;
      if (neu) neu.addEventListener('statechange', () => { if (neu.state === 'installed') wartet(); });
    });
    // Eine als App geöffnete Seite wird selten neu geladen: beim Zurückkehren nach einer neuen Version fragen.
    document.addEventListener('visibilitychange', () => { if (!document.hidden) reg.update().catch(() => null); });
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (angefordert) { angefordert = false; location.reload(); }
    });
  };
  // Service Worker anmelden (App-Hülle, Hinweise); auf /hinweise Hinweise für dieses Gerät an- und abschalten.
  const sw = 'serviceWorker' in navigator ? navigator.serviceWorker.register('/sw.js', { scope: '/' })
    .then((reg) => { aktualisierung(reg); return reg; }).catch(() => null) : null;
  const bereich = document.getElementById('hinweise');
  if (bereich) {
    const status = document.getElementById('hinweis-status');
    const knopf = (id) => document.getElementById(id);
    const sagen = (text) => { status.textContent = text; };
    const schluessel = (b64) => {
      const roh = atob(b64.replace(/-/g, '+').replace(/_/g, '/'));
      return Uint8Array.from(roh, (c) => c.charCodeAt(0));
    };
    const senden = async (pfad, daten) => {
      const r = await fetch(pfad, { method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(daten) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    };
    const anzeigen = async () => {
      for (const id of ['hinweis-an', 'hinweis-aus', 'hinweis-test']) knopf(id).hidden = true;
      if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
        sagen('Dieser Browser kann hier keine Hinweise empfangen. Auf dem iPhone: Seite zum Home-Bildschirm hinzufügen und von dort öffnen.');
        return;
      }
      if (Notification.permission === 'denied') {
        sagen('Hinweise sind im Browser für diese Seite blockiert. Das lässt sich nur in den Browser-Einstellungen ändern.');
        return;
      }
      const reg = await sw && await navigator.serviceWorker.ready;
      const abo = reg && await reg.pushManager.getSubscription();
      // Das Browser-Abo kann den Server überdauern (Gerät in der Liste entfernt): beide Seiten abgleichen.
      if (abo && !(await senden('/hinweise/status', { endpoint: abo.endpoint })).angemeldet) {
        sagen('Dieses Gerät ist beim Dienst nicht mehr angemeldet (z. B. aus der Liste entfernt). Mit „Hinweise erlauben“ meldest du es wieder an.');
        knopf('hinweis-an').hidden = false;
      } else if (abo) {
        sagen('Hinweise sind auf diesem Gerät an.');
        knopf('hinweis-aus').hidden = false;
        knopf('hinweis-test').hidden = false;
      } else {
        sagen('Hinweise sind auf diesem Gerät aus.');
        knopf('hinweis-an').hidden = false;
      }
    };
    const tun = (id, fn) => knopf(id).addEventListener('click', async () => {
      knopf(id).disabled = true;
      try { await fn(); } catch (err) { sagen(`Das hat nicht geklappt (${err.message}).`); }
      knopf(id).disabled = false;
    });
    tun('hinweis-an', async () => {
      if (await Notification.requestPermission() !== 'granted') { sagen('Ohne Erlaubnis gibt es keine Hinweise.'); return; }
      const reg = await navigator.serviceWorker.ready;
      const abo = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: schluessel(bereich.dataset.schluessel) });
      try {
        await senden('/hinweise/abo', abo.toJSON());
      } catch (err) {
        await abo.unsubscribe();
        throw err;
      }
      location.reload();
    });
    tun('hinweis-aus', async () => {
      const reg = await navigator.serviceWorker.ready;
      const abo = await reg.pushManager.getSubscription();
      if (abo) {
        await senden('/hinweise/abmelden', { endpoint: abo.endpoint });
        await abo.unsubscribe();
      }
      location.reload();
    });
    tun('hinweis-test', async () => {
      const reg = await navigator.serviceWorker.ready;
      const abo = await reg.pushManager.getSubscription();
      const d = await senden('/hinweise/test', { endpoint: abo.endpoint });
      sagen(d.ok ? 'Probehinweis ist unterwegs.' : `Der Push-Dienst hat den Probehinweis nicht angenommen (Status ${d.status}).`);
    });
    anzeigen().catch((err) => sagen(`Das hat nicht geklappt (${err.message}).`));
  }
})();
