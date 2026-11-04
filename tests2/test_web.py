"""Dashboard (S2.1): Zugang, jede Seite am Testprojekt, Maskierung, md-Darstellung."""
import logging
import os
os.environ.setdefault('DEVFLOW2_INSTANZ', os.devnull)  # nie die echte instanz.toml lesen
import re
import shutil
import time
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import laeufe, web  # noqa: E402
from devflow2.web_md import md, url_ok  # noqa: E402
from web_daten import SKRIPT, aufbauen, zeile_anhaengen  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
logging.getLogger('httpx').setLevel(logging.WARNING)


class SeitenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fall, cls.ids = aufbauen()
        cls.addClassCleanup(cls.fall.tearDown)
        cls.client = TestClient(web.anwendung(), headers=LOGIN)

    def holen(self, pfad, code=200):
        r = self.client.get(pfad)
        self.assertEqual(r.status_code, code, f'{pfad}: {r.text[:500]}')
        return r.text

    def sicher(self, text):
        """Kein ausführbares Skript aus Inhalten: nur das eigene <script src=…> im Kopf."""
        self.assertEqual(re.findall(r'<script\b[^>]*>', text), ['<script src="/static/app.js" defer>'])
        self.assertNotIn('javascript:', re.sub(r'&[^;]+;', '', ' '.join(re.findall(r'href="[^"]*"', text))))

    # --- Zugang ---------------------------------------------------------------------------------

    def test_ohne_oder_mit_falscher_kopfzeile_403(self):
        pfade = ['/health', '/', '/p/demo', '/p/demo/a/A1', f'/lauf/{self.ids["fertig"]}', f'/api/lauf/{self.ids["fertig"]}',
                 '/p/demo/md/plan/wetter.md', '/static/app.js', '/gibtsnicht']
        roh = TestClient(web.anwendung())
        for pfad in pfade:
            for kopf in ({}, {'Tailscale-User-Login': 'fremd@github'}, {'Tailscale-User-Login': ''},
                         {'Tailscale-User-Login': 'INHABER@github'}):
                with self.subTest(pfad=pfad, kopf=kopf):
                    r = roh.get(pfad, headers=kopf)
                    self.assertEqual(r.status_code, 403)
                    self.assertNotIn('demo', r.text)
        doppelt = roh.get('/', headers=[('Tailscale-User-Login', 'fremd@github'), ('Tailscale-User-Login', 'inhaber@github')])
        self.assertEqual(doppelt.status_code, 403)
        self.assertEqual(roh.get('/', headers=LOGIN).status_code, 200)

    def test_login_einstellbar(self):
        alt = os.environ.get('DEVFLOW2_WEB_LOGIN')
        os.environ['DEVFLOW2_WEB_LOGIN'] = 'jemand@github'
        try:
            c = TestClient(web.anwendung())
        finally:
            os.environ.pop('DEVFLOW2_WEB_LOGIN') if alt is None else os.environ.update(DEVFLOW2_WEB_LOGIN=alt)
        self.assertEqual(c.get('/', headers=LOGIN).status_code, 403)
        self.assertEqual(c.get('/', headers={'Tailscale-User-Login': 'jemand@github'}).status_code, 200)

    def test_health_nur_mit_login(self):
        self.assertEqual(TestClient(web.anwendung()).get('/health').status_code, 403)
        r = self.client.get('/health')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True})

    def test_seiten_nur_per_get(self):
        """Handlungen stehen in test_web_aktion.py; die Seiten selbst nehmen kein POST an."""
        mit = {'Origin': 'http://testserver'}
        self.assertEqual(self.client.post('/', data={'x': 1}, headers=mit).status_code, 405)
        self.assertEqual(self.client.post(f'/api/lauf/{self.ids["laufend"]}', headers=mit).status_code, 405)
        self.assertEqual(self.client.post('/', data={'x': 1}).status_code, 403)  # ohne passende Origin gar nicht erst

    def test_sicherheitskopfzeilen(self):
        r = self.client.get('/')
        self.assertIn("script-src 'self'", r.headers['content-security-policy'])
        self.assertIn("frame-ancestors 'none'", r.headers['content-security-policy'])
        self.assertEqual(r.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(r.headers['referrer-policy'], 'same-origin')

    # --- Seiten ---------------------------------------------------------------------------------

    def test_lage(self):
        s = self.holen('/')
        self.sicher(s)
        bei_dir = s.split('</span>Braucht dich')[1].split('<h2 ')[0]
        self.assertIn('href="/p/demo/a/A1#fragen"', bei_dir)  # Sprung zur Frage (A1 steht ihretwegen auf „Braucht dich“)
        self.assertIn('F1 · A1', bei_dir)  # offene Frage
        self.assertIn(f'href="/lauf/{self.ids["kaputt"]}"', bei_dir)  # fehlgeschlagener Lauf
        self.assertIn('blockiert', bei_dir)
        self.assertNotIn(self.ids['fertig'], bei_dir)
        laufend = s.split('</span>Läuft gerade')[1].split('<h2 ')[0]
        self.assertIn(f'href="/lauf/{self.ids["laufend"]}"', laufend)
        self.assertIn('href="/p/demo/a/A2"', laufend)
        self.assertNotIn(self.ids['fertig'], laufend)
        self.assertIn('href="/p/demo"', s)
        self.assertNotIn('angehalten:', s)

    def test_lage_zeigt_halt_und_kaputtes_projekt(self):
        from devflow2 import projekte, schreiben
        (projekte.zustand() / 'angehalten').write_text('x')
        daten = projekte.alle()
        schreiben(projekte.zustand() / 'projekte.json', '{"demo": "%s", "weg": "/gibt/es/nicht"}' % daten['demo'])
        try:
            s = self.holen('/')
        finally:
            (projekte.zustand() / 'angehalten').unlink()
            schreiben(projekte.zustand() / 'projekte.json', '{"demo": "%s"}' % daten['demo'])
        self.assertIn('devflow ist angehalten', s)
        self.assertIn('<h3>weg</h3>', s)
        self.assertIn('Fehler:', s)

    def test_projekt(self):
        s = self.holen('/p/demo')
        self.sicher(s)
        vorhaben = s.split('</span>Aktuelles Vorhaben')[1].split('<h2 ')[0]
        for aid in ('A1', 'A2', 'A3'):
            self.assertIn(f'href="/p/demo/a/{aid}"', vorhaben)
        self.assertNotIn('href="/p/demo/a/0.1"', vorhaben)  # erledigt
        fragen = s.split('</span>Offene Fragen')[1].split('<h2 ')[0]
        self.assertIn('F1 · ', fragen)
        self.assertIn('Celsius oder Fahrenheit? &lt;script&gt;alert(1)&lt;/script&gt;', fragen)
        tabelle = s.split('<details id="erledigt">')[1].split('</details>')[0]
        self.assertIn('href="/p/demo/a/0.1"', tabelle)
        self.assertNotIn('href="/p/demo/a/A1"', tabelle)
        self.assertIn('Plan plan/wetter.md', s.split('<details id="protokoll">')[1])
        self.assertIn(f'href="/lauf/{self.ids["laufend"]}"', s.split('</span>Läuft gerade')[1].split('<h2 ')[0])
        self.holen('/p/gibtsnicht', 404)
        self.holen('/p/%2e%2e', 404)

    def test_projekt_reihenfolge_eingeklappt_und_fuenf_laeufe(self):
        import time
        for i in range(8):
            laeufe.speichern({'id': f'alt{i}', 'projekt': 'demo', 'art': 'entwickeln', 'modell': 'claude', 'aufgabe': 'A1', 'zustand': 'fertig',
                              'angelegt': time.time() - 100 + i})
        s = self.holen('/p/demo')
        folge = [s.index(x) for x in ('id="vorhaben"', 'id="fragen"', 'id="laeuft"', 'id="laeufe"', 'id="ideen"', '<details id="erledigt"', '<details id="protokoll"')]
        self.assertEqual(folge, sorted(folge))
        abschnitt = s.split('id="laeufe"')[1].split('id="ideen"')[0]
        letzte, alle = abschnitt.split('<details id="alle-laeufe">')
        self.assertEqual(letzte.count('<li>'), 5)
        self.assertIn('/lauf/alt7', letzte)
        self.assertNotIn('/lauf/alt0', letzte)
        self.assertIn('Alle Läufe (', alle)  # geschlossen, mit allen abgeschlossenen Läufen
        self.assertIn('/lauf/alt0', alle)
        self.assertEqual(len(re.findall(r'<details id="(?:erledigt|protokoll)">', s)), 2)  # geschlossen: kein `open`
        self.assertNotIn('<details open', s)

    def test_aufgabe_mit_abgeschlossenen_laeufen(self):
        s = self.holen('/p/demo/a/A1')
        self.sicher(s)
        zeile = s.split('</span>Roadmap')[1].split('<h2 ')[0]
        self.assertIn('Wetter-Kachel', zeile)
        self.assertIn('Braucht dich', zeile)
        self.assertIn('Claude, Review Sol', zeile)
        self.assertIn('href="/p/demo/md/plan/wetter.md"', zeile)
        self.assertIn('Kachel zeigt Temperatur', zeile)
        self.assertIn('Celsius oder Fahrenheit?', s.split('</span>Fragen')[1])
        ls = s.split('</span>Läufe')[1].split('<h2 ')[0]
        self.assertIn('rc-kicker-num">(2)', s)
        self.assertIn(f'href="/lauf/{self.ids["fertig"]}"', ls)
        self.assertIn('Urteil: Nacharbeit nötig', ls)
        self.assertIn('M1 (P2)', ls)
        self.assertIn('Grenzfall &lt;script&gt;alert(1)&lt;/script&gt;', ls)
        self.assertIn('Ergebnis: Gebaut &lt;script&gt;alert(1)&lt;/script&gt;', ls)  # Log
        self.assertIn('href="/p/demo/md/berichte/A1-review-sol.md?zweig=claude-a1"', ls)
        berichte = s.split('</span>Bericht und Reviews')[1].split('<h2 ')[0]
        self.assertIn('href="/p/demo/md/berichte/A1.md?zweig=claude-a1"', berichte)
        self.assertIn('href="/p/demo/md/berichte/A1-review-sol.md?zweig=claude-a1"', berichte)
        diff = s.split('</span>Diff des Branches')[1]
        self.assertIn('git diff main...claude-a1', diff)
        self.assertIn('<span class="plus">+TEMP = 12</span>', diff)
        self.assertIn('+Gebaut mit &lt;script&gt;alert(1)&lt;/script&gt;', diff)
        self.assertIn('app/wetter.py', diff.split('class="stat"')[1])

    def test_aufgabe_laufend_und_fehlgeschlagen(self):
        s = self.holen('/p/demo/a/A2')
        self.sicher(s)
        self.assertIn(f'data-lauf="{self.ids["laufend"]}"', s)
        self.assertIn('<details open>', s)
        self.assertIn('rc-status-aktiv" data-zustand', s)
        self.assertIn('Branch sol-a2 hat noch keine Änderungen', s)
        s = self.holen('/p/demo/a/A3')
        self.assertIn('rc-status-error" data-zustand', s)
        self.assertIn('Exit 3', s)
        self.assertNotIn('data-lauf=', s)
        self.assertIn('Noch keine.', s.split('</span>Bericht und Reviews')[1])
        s = self.holen('/p/demo/a/0.1')
        self.assertIn('Noch kein Branch.', s)
        self.holen('/p/demo/a/X9', 404)
        self.holen('/p/demo/a/A1%2F..%2F..', 404)

    def test_lauf_seite_und_api(self):
        s = self.holen(f'/lauf/{self.ids["fertig"]}')
        self.sicher(s)
        self.assertIn('rc-status-ok" data-zustand', s)
        self.assertIn('Ergebnis: Gebaut &lt;script&gt;alert(1)&lt;/script&gt;', s)
        self.assertIn('claude-a1', s)
        self.assertNotIn('data-lauf=', s)
        s = self.holen(f'/lauf/{self.ids["review"]}')
        self.assertIn('Urteil: Nacharbeit nötig', s)
        self.assertIn('Befunde', s)
        s = self.holen(f'/lauf/{self.ids["laufend"]}')
        self.assertIn(f'data-lauf="{self.ids["laufend"]}" data-zeilen="200"', s)
        a = self.client.get(f'/api/lauf/{self.ids["fertig"]}').json()
        self.assertEqual((a['zustand'], a['aktiv']), ('fertig', False))
        self.assertIn(f'Ergebnis: Gebaut {SKRIPT}', a['log'])
        zeile_anhaengen(self.ids['laufend'], 'Neue Zeile <b>fett</b>')
        a = self.client.get(f'/api/lauf/{self.ids["laufend"]}?zeilen=5').json()
        self.assertEqual((a['zustand'], a['aktiv']), ('laeuft', True))
        self.assertEqual(a['log'][-1], 'Neue Zeile <b>fett</b>')
        self.assertIn('Neue Zeile &lt;b&gt;fett&lt;/b&gt;', self.holen(f'/lauf/{self.ids["laufend"]}'))
        self.assertEqual(self.client.get('/api/lauf/x?zeilen=abc').status_code, 404)
        self.holen('/lauf/gibtsnicht', 404)
        self.holen('/lauf/%2e%2e', 404)
        self.assertEqual(self.client.get('/api/lauf/..%2Fprojekte.json').status_code, 404)

    def test_md_ansicht(self):
        s = self.holen('/p/demo/md/plan/wetter.md')
        self.sicher(s)
        self.assertIn('<h1 id="vorhaben-wetter--claude--29102026">Vorhaben Wetter · Claude · 29.10.2026</h1>', s)
        self.assertIn('<td><code>app/wetter.py</code></td>', s)
        s = self.holen('/p/demo/md/berichte/A1.md?zweig=claude-a1')
        self.sicher(s)
        self.assertIn('Gebaut mit &lt;script&gt;alert(1)&lt;/script&gt; im Text', s)
        self.assertIn('[böser Link](javascript:alert(1)) neben <a href="https://example.org">gutem Link</a>', s)
        self.assertIn('<td><code>&lt;b&gt;</code></td>', s)
        self.holen('/p/demo/md/berichte/A1.md', 404)  # nur auf dem Branch
        self.assertIn('Nacharbeit', self.holen('/p/demo/md/berichte/A1-review-sol.md?zweig=claude-a1'))
        liste = self.holen('/p/demo/md/')
        self.assertIn('href="/p/demo/md/plan/wetter.md"', liste)
        self.assertIn('href="/p/demo/md/ROADMAP.md"', liste)
        self.assertIn('href="/p/demo/md/berichte/A1.md?zweig=claude-a1"', self.holen('/p/demo/md/?zweig=claude-a1'))
        self.assertIn('wetter.md', self.holen('/p/demo/md/plan/'))
        for pfad in ('/p/demo/md/%2e%2e/ROADMAP.md', '/p/demo/md/plan/%2e%2e/ROADMAP.md', '/p/demo/md/.git/config.md',
                     '/p/demo/md/devflow.toml', '/p/demo/md/plan/fehlt.md', '/p/demo/md/plan/wetter.md?zweig=..',
                     '/p/demo/md/plan/wetter.md?zweig=gibtsnicht', '/p/demo/md/plan/wetter.md?zweig=-x',
                     '/p/demo/md/gibtsnicht/', '/p/demo/md/%2e%2e/x.md'):
            with self.subTest(pfad=pfad):
                self.holen(pfad, 404)

    def test_relative_links_behalten_den_branch(self):
        s = self.holen('/p/demo/md/berichte/A1.md?zweig=claude-a1')
        ziel = '/p/demo/md/berichte/A1-review-sol.md?zweig=claude-a1'
        self.assertIn(f'<a href="{ziel}">Review</a>', s)
        self.assertIn('<a href="/p/demo/md/plan/wetter.md?zweig=claude-a1#aufgaben">Plan</a>', s)
        self.assertIn('<a href="/p/demo/md/docs/ziel.md?zweig=claude-a1">Wiki</a>', s)
        self.assertIn(' und raus.', s)  # führt aus dem Repo: nur Text
        self.assertIn('Nacharbeit', self.holen(ziel))  # gibt es nur auf dem Branch
        self.holen('/p/demo/md/plan/wetter.md?zweig=claude-a1')
        self.holen('/p/demo/md/docs/ziel.md?zweig=claude-a1')

    def test_relative_links_aus_der_roadmap(self):
        ziel = '<a href="/p/demo/md/docs/ziel.md">Wiki</a>'
        for seite in ('/p/demo', '/p/demo/a/A3', '/'):
            with self.subTest(seite=seite):
                self.assertIn(ziel, self.holen(seite))
        self.assertIn('Nur auf main.', self.holen('/p/demo/md/docs/ziel.md'))
        a1 = self.holen('/p/demo/a/A1')
        self.assertIn('<a href="/p/demo/md/plan/wetter.md">plan/wetter.md</a>', a1)

    def test_verwaister_lauf_endet_auch_im_live_log(self):
        lid = 'demo-verwaist-test'
        lauf = dict(id=lid, projekt='demo', art='meinung', modell='sol', cwd=str(self.fall.repo), auftrag='',
                    zustand='laeuft', angelegt=time.time(), pid=os.getpid())
        laeufe.verzeichnis(lid).mkdir(parents=True)
        self.addCleanup(shutil.rmtree, laeufe.verzeichnis(lid))
        laeufe.speichern(lauf)
        a = self.client.get(f'/api/lauf/{lid}').json()
        self.assertEqual((a['zustand'], a['aktiv']), ('laeuft', True))  # Anlauffrist von 30 s
        laeufe.speichern({**lauf, 'angelegt': time.time() - 31})
        a = self.client.get(f'/api/lauf/{lid}').json()
        self.assertEqual((a['zustand'], a['aktiv']), ('fehler', False))
        self.assertEqual(laeufe.laden(lid)['fehler'], 'Prozess unerwartet beendet')
        laeufe.speichern({**lauf, 'angelegt': time.time() - 31})
        s = self.holen(f'/lauf/{lid}')
        self.assertIn('rc-status-error" data-zustand', s)
        self.assertNotIn('data-lauf=', s)
        self.assertEqual(self.client.get(f'/api/lauf/{self.ids["laufend"]}').json()['zustand'], 'laeuft')

    def test_statische_dateien(self):
        self.assertIn('textContent', self.holen('/static/app.js'))
        self.assertIn('prefers-color-scheme', self.holen('/static/cd/cd.css'))
        self.assertIn('var(--panel)', self.holen('/static/style.css'))


class VerweiseTest(unittest.TestCase):
    def test_aufloesen(self):
        v = web.verweise('demo', 'docs/README.md')
        self.assertEqual(v('architektur.md'), '/p/demo/md/docs/architektur.md')
        self.assertEqual(v('../ROADMAP.md#protokoll'), '/p/demo/md/ROADMAP.md#protokoll')
        self.assertEqual(v('entscheidungen/'), '/p/demo/md/docs/entscheidungen/')
        self.assertEqual(v('/plan/x.md'), '/p/demo/md/plan/x.md')
        self.assertEqual(v('../'), '/p/demo/md/')
        self.assertEqual(v('?a=1'), '/p/demo/md/docs/README.md')
        self.assertEqual(v('#anker'), '#anker')
        self.assertEqual(v('https://example.org/x'), 'https://example.org/x')
        self.assertIsNone(v('../../x.md'))
        self.assertIsNone(v('/../x.md'))
        z = web.verweise('demo', 'berichte/A1.md', 'claude-a1')
        self.assertEqual(z('A1-review-sol.md#befunde'), '/p/demo/md/berichte/A1-review-sol.md?zweig=claude-a1#befunde')
        self.assertEqual(z('ä b.md'), '/p/demo/md/berichte/%C3%A4%20b.md?zweig=claude-a1')
        self.assertEqual(web.verweise('demo', 'ROADMAP.md')('docs/ziel.md'), '/p/demo/md/docs/ziel.md')


class MdTest(unittest.TestCase):
    def test_bloecke(self):
        h = md('# Titel\n\nText **fett** und _kursiv_ mit `code <x>`.\n\n- a\n  - b\n- c\n\n1. eins\n2. zwei\n\n'
               '```\n<script>x</script>\n```\n\n> zitat\n\n---\n\n| A | B |\n|---|---|\n| 1 \\| 2 | **3** |')
        self.assertIn('<h1 id="titel">Titel</h1>', h)
        self.assertIn('<p>Text <strong>fett</strong> und <em>kursiv</em> mit <code>code &lt;x&gt;</code>.</p>', h)
        self.assertIn('<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>', h)
        self.assertIn('<ol><li>eins</li><li>zwei</li></ol>', h)
        self.assertIn('<pre class="code"><code>&lt;script&gt;x&lt;/script&gt;</code></pre>', h)
        self.assertIn('<blockquote><p>zitat</p></blockquote>', h)
        self.assertIn('<hr>', h)
        self.assertIn('<td>1 | 2</td><td><strong>3</strong></td>', h)

    def test_html_wird_maskiert(self):
        for roh in ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '<a href="javascript:x">y</a>',
                    '**<script>x</script>**', '[<script>x</script>](https://a.b)', '| <i>x</i> |\n|---|\n| <svg onload=x> |'):
            with self.subTest(roh=roh):
                h = md(roh)
                self.assertNotRegex(h, r'<(script|img|svg|i)\b')
                self.assertNotRegex(h, r'<a href="javascript')

    def test_links(self):
        self.assertIn('<a href="https://a.b/c?d=1&amp;e=2">x</a>', md('[x](https://a.b/c?d=1&e=2)'))
        self.assertIn('<a href="../ROADMAP.md">Roadmap</a>', md('[Roadmap](../ROADMAP.md)'))
        self.assertIn('<a href="#abschnitt">hier</a>', md('[hier](#abschnitt)'))
        self.assertIn('<a href="x.md"><strong>fett</strong></a>', md('[**fett**](x.md)'))
        for boese in ('javascript:void0', 'javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,x', 'vbscript:x', 'java\tscript:x',
                      'file:///etc/passwd'):
            with self.subTest(url=boese):
                self.assertFalse(url_ok(boese))
                self.assertNotIn('<a', md(f'[x]({boese})'))
        self.assertIn('&quot;', md('[x](a"onmouseover=alert(1))'))
        self.assertNotIn('<a href="a"', md('[x](a"onmouseover=alert(1))'))

    def test_unterstriche_in_woertern_bleiben(self):
        self.assertIn('snake_case_name', md('snake_case_name und https://x/a_b_c'))


if __name__ == '__main__':
    unittest.main()
