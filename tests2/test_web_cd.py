"""Dashboard im CD: Farben nur als Tokens aus `static/cd/`, Bausteine, Kopf, Primär-Button einmal pro Ansicht."""
import logging
import os
import re
import unittest
from pathlib import Path

from starlette.testclient import TestClient


os.environ.setdefault('DEVFLOW2_INSTANZ', os.devnull)  # nie die echte instanz.toml lesen

from devflow2 import web, web_html
from web_daten import aufbauen

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
PAKET = Path(web.__file__).parent
FARBE = re.compile(r'#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch|oklab|hwb|color-mix)\(', re.I)
BENANNT = re.compile(r'(?:color|background|border[\w-]*|fill|stroke|outline)\s*:[^;{}]*\b(?:red|green|blue|black|white|gr[ae]y|orange|yellow)\b', re.I)
LOGGER = logging.getLogger('httpx')


class FarbwerteTest(unittest.TestCase):
    def quellen(self):
        for p in sorted(PAKET.rglob('*')):
            if p.suffix in ('.py', '.css', '.js', '.html', '.json', '.svg', '.webmanifest') and not {PAKET / 'static' / 'cd', PAKET / 'static' / 'logo'} & set(p.parents):
                yield p

    def test_kein_farbwert_ausserhalb_von_static_cd(self):
        gefunden = []
        for p in self.quellen():
            for nr, zeile in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
                if FARBE.search(zeile) or BENANNT.search(zeile):
                    gefunden.append(f'{p.relative_to(PAKET)}:{nr}: {zeile.strip()[:80]}')
        self.assertEqual(gefunden, [])

    def test_pruefung_erkennt_farbwerte(self):
        for roh in ('color: #fff;', 'a { background: #1d232b }', 'x: rgb(1,2,3)', 'fill: red;', 'c = "#121417"'):
            self.assertTrue(FARBE.search(roh) or BENANNT.search(roh), roh)
        for ok in ('color: var(--text);', 'href="#fragen"', 'f"&#39;"', 'border: 1px solid var(--line);'):
            self.assertFalse(FARBE.search(ok) or BENANNT.search(ok), ok)

    def test_paket_ist_vollstaendig(self):
        cd = PAKET / 'static' / 'cd'
        for datei in ('cd.css', 'logo/cd-signet.svg', 'fonts/inter-latin-400.woff2', 'fonts/inter-latin-700.woff2',
                      'fonts/jetbrains-mono-latin-400.woff2', 'fonts/jetbrains-mono-latin-700.woff2', 'fonts/LICENSE-Inter.txt'):
            self.assertTrue((cd / datei).is_file(), datei)
        css = (cd / 'cd.css').read_text(encoding='utf-8')
        self.assertIn('prefers-color-scheme: light', css)
        self.assertNotRegex(css, r'https?://|@import')

    def test_grund_kommt_aus_den_tokens(self):
        dunkel, hell = web_html.grund()
        self.assertNotEqual(dunkel, hell)
        css = (PAKET / 'static' / 'cd' / 'cd.css').read_text(encoding='utf-8')
        self.assertIn(f'--ground: {dunkel};', css)
        self.assertIn(f'--ground: {hell};', css)


class BausteineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fall, cls.ids = aufbauen()
        cls.addClassCleanup(cls.fall.tearDown)
        cls.client = TestClient(web.anwendung(), headers=LOGIN)

    def seite(self, pfad):
        r = self.client.get(pfad)
        self.assertEqual(r.status_code, 200, pfad)
        return r.text

    def alle(self):
        return ['/', '/p/demo', '/p/demo/a/A1', '/p/demo/a/A2', '/p/demo/a/A3', f'/lauf/{self.ids["fertig"]}',
                f'/lauf/{self.ids["laufend"]}', '/p/demo/md/', '/p/demo/md/plan/wetter.md', '/hinweise', '/kontingent', '/traum']

    def test_kopf_mit_signet_und_wortmarke(self):
        for pfad in self.alle():
            s = self.seite(pfad)
            kopf = s.split('</header>')[0]
            self.assertIn('src="/static/logo/devflow-signet.svg"', kopf, pfad)
            self.assertIn('<span class="rc-wordmark">devflow<span class="rc-cursor"></span></span>', kopf, pfad)
            self.assertIn('href="/static/cd/cd.css"', s)
            self.assertIn('rel="icon"', s)

    def test_primaer_button_hoechstens_einmal_je_ansicht(self):
        for pfad in self.alle():
            self.assertLessEqual(self.seite(pfad).count('rc-btn-primary'), 1, pfad)
        self.assertEqual(self.seite('/p/demo/a/A1').count('rc-btn-primary'), 1)
        self.assertEqual(self.seite('/p/demo').count('rc-btn-primary'), 1)

    def test_status_mit_symbol_und_wort(self):
        s = self.seite('/p/demo')
        self.assertRegex(s, r'rc-status-warn"><span class="rc-status-glyph" aria-hidden="true">▲</span>Braucht dich')
        for art, glyph in (('ok', '●'), ('idle', '○'), ('warn', '▲'), ('error', '✕')):
            self.assertEqual(web_html.GLYPHEN[art], glyph)
        for art in ('ok', 'idle', 'warn', 'error'):
            self.assertIn(f'rc-status rc-status-{art}"', str(web_html.status('x', art)))

    def test_fehlerstatus_hat_kreuz(self):
        self.assertRegex(self.seite('/p/demo/a/A3'), r'rc-status-error[^>]*><span class="rc-status-glyph" aria-hidden="true">✕</span>Fehler')

    def test_abschnitte_als_kicker_und_karten(self):
        s = self.seite('/p/demo/a/A1')
        self.assertIn('<h2 class="rc-kicker"><span class="rc-kicker-bar"></span>Fragen</h2>', s)
        self.assertIn('class="rc-card frage"', s)

    def test_keine_fremden_hosts_im_html(self):
        for pfad in self.alle():
            s = self.seite(pfad).split('<main>')[0]
            self.assertNotRegex(s, r'(?:src|href)="(?:https?:)?//', pfad)

    def test_manifest_und_theme_color_aus_tokens(self):
        dunkel, hell = web_html.grund()
        m = self.client.get('/manifest.webmanifest').json()
        self.assertEqual((m['background_color'], m['theme_color']), (dunkel, dunkel))
        s = self.seite('/')
        self.assertIn(f'content="{dunkel}" media="(prefers-color-scheme: dark)"', s)
        self.assertIn(f'content="{hell}" media="(prefers-color-scheme: light)"', s)

    def test_statisch_erreichbar(self):
        for pfad, typ in (('/static/cd/cd.css', 'css'), ('/static/cd/fonts/inter-latin-400.woff2', None), ('/static/cd/logo/cd-signet.svg', 'svg')):
            r = self.client.get(pfad)
            self.assertEqual(r.status_code, 200, pfad)
            if typ:
                self.assertIn(typ, r.headers['content-type'])
