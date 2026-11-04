"""App (S4.2): Manifest mit Kurzbefehlen, Installationsknopf im Kopf, Service Worker und app.js tragen die Offline- und Versionslogik.

Das Verhalten im Browser (offline, POST gesperrt, Installieren, neue Version) prüft `tests2/browser/app.cjs` über
`test_web_browser.py`.
"""
from pathlib import Path
import re
import shutil
import tempfile
from unittest import mock

from starlette.testclient import TestClient

from devflow2 import projekte, web, web_html, web_push
from hilfen import Fall

HERKUNFT = {'Tailscale-User-Login': 'inhaber@github', 'Origin': 'http://testserver'}
STATIC = Path(web_push.__file__).with_name('static')


class ManifestTest(Fall):
    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)

    def manifest(self):
        r = self.client.get('/manifest.webmanifest')
        self.assertEqual(r.headers['content-type'], 'application/manifest+json')
        return r.json()

    def test_pflichtfelder_und_farben_aus_den_tokens(self):
        m = self.manifest()
        self.assertEqual((m['id'], m['start_url'], m['scope'], m['display']), ('/', '/', '/', 'standalone'))
        self.assertTrue(m['description'].strip())
        self.assertEqual((m['name'], m['short_name'], m['lang']), ('devflow', 'devflow', 'de'))
        dunkel = web_html.grund()[0]
        self.assertEqual((m['background_color'], m['theme_color']), (dunkel, dunkel))

    def test_icons_je_zweck_ein_eintrag(self):
        zwecke = [i['purpose'] for i in self.manifest()['icons']]
        self.assertEqual(sorted(zwecke), ['any', 'any', 'maskable', 'maskable'])  # nie „any maskable“ in einem Eintrag

    def test_kurzbefehle_zeigen_auf_erreichbare_seiten_im_geltungsbereich(self):
        kurz = self.manifest()['shortcuts']
        self.assertEqual([k['name'] for k in kurz], ['Braucht dich', 'Roadmap', 'Eingang', 'Kontingent'])
        name = next(iter(projekte.alle()))
        self.assertEqual([k['url'] for k in kurz], ['/', f'/p/{name}/roadmap', f'/p/{name}#ideen', '/kontingent'])
        for k in kurz:
            self.assertTrue(k['url'].startswith('/'), k)
            self.assertEqual(self.client.get(k['url'].split('#')[0]).status_code, 200, k['url'])
            for i in k['icons']:
                self.assertEqual(self.client.get(i['src']).status_code, 200)

    def test_ohne_projekt_fallen_roadmap_und_eingang_auf_die_uebersicht_zurueck(self):
        (projekte.zustand() / 'projekte.json').write_text('{}', encoding='utf-8')
        urls = {k['name']: k['url'] for k in self.manifest()['shortcuts']}
        self.assertEqual((urls['Roadmap'], urls['Eingang']), ('/', '/'))
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_installationsknopf_ist_im_kopf_und_zunaechst_verborgen(self):
        s = self.client.get('/').text
        self.assertRegex(s, r'<button[^>]*id="app-installieren"[^>]*hidden[^>]*>Als App installieren</button>')
        self.assertLess(s.index('id="app-installieren"'), s.index('<main>'))


class SkriptTest(Fall):
    def test_service_worker_ist_netz_zuerst_und_lehnt_handlungen_offline_ab(self):
        sw = (STATIC / 'sw.js').read_text(encoding='utf-8')
        self.assertIn('▲ offline, Stand ${zeit}', sw)
        self.assertIn("req.method !== 'GET'", sw)
        self.assertIn('sperre(req)', sw)
        # Gemerkt wird nur nach einer Netzantwort und nur über `speichern`; Handlungen erreichen es nicht.
        self.assertEqual(len(re.findall(r'\bcache\.put\(', sw)), 1)
        self.assertLess(sw.index('speichern(cache'), sw.index("'fetch'"))
        # Die Push-Hinweise bleiben.
        self.assertIn("addEventListener('push'", sw)
        self.assertIn("addEventListener('notificationclick'", sw)

    def test_app_js_meldet_neue_version_und_bietet_installation_an(self):
        js = (STATIC / 'app.js').read_text(encoding='utf-8')
        for text in ('Neue Version – neu laden', 'beforeinstallprompt', 'appinstalled', "art: 'aktivieren'", 'controllerchange'):
            self.assertIn(text, js)
        sw = (STATIC / 'sw.js').read_text(encoding='utf-8')
        self.assertIn("ev.data.art === 'aktivieren'", sw)  # Gegenstück zur Nachricht aus app.js
        self.assertIn('self.skipWaiting()', sw)

    def test_service_worker_holt_die_erste_seite_beim_aktivieren_nach(self):
        sw = (STATIC / 'sw.js').read_text(encoding='utf-8')
        aktiv = sw[sw.index("addEventListener('activate'"):sw.index('// Neue Version')]
        self.assertIn('includeUncontrolled: true', aktiv)
        self.assertIn('speichern(cache, url.href', aktiv)


class StandTest(Fall):
    def test_neue_app_js_ergibt_anderen_service_worker(self):
        client = TestClient(web.anwendung(), headers=HERKUNFT)
        vorher = client.get('/sw.js').text
        with tempfile.TemporaryDirectory() as tmp:
            kopie = Path(tmp) / 'static'
            shutil.copytree(STATIC, kopie, ignore=shutil.ignore_patterns('fonts'))
            with mock.patch.object(web_push, 'STATIC', kopie):
                gleich = client.get('/sw.js').text
                (kopie / 'app.js').write_text((kopie / 'app.js').read_text(encoding='utf-8') + '\n// neu\n', encoding='utf-8')
                neu = client.get('/sw.js').text
        self.assertEqual(gleich, vorher)  # Schriften zählen nicht, sonst nur die Dateien selbst
        self.assertNotEqual(neu, gleich)
