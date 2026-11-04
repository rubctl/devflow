"""Übersicht (S4.3): Leitstand mit vier Blöcken, Kontingentbalken, Navigation, Zuordnung Rohwert → Anzeige, Grün nur für Abgeschlossenes."""
import logging
import re
import time
import unittest
import unittest.mock
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import laeufe, roadmap, web, web_html  # noqa: E402
from devflow2.web_kontingent import balken  # noqa: E402
from hilfen import Fall  # noqa: E402
from web_daten import aufbauen  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
logging.getLogger('httpx').setLevel(logging.WARNING)
JETZT = 1_800_000_000
# Rohwerte aus Lauf-Metadaten, die nie als sichtbarer Text erscheinen dürfen (Anzeige: Zuordnung in web.py)
ROH = ('laeuft', 'abschluss', 'kontingent', 'fehler', 'entwickeln', 'nacharbeit', 'meinung', 'mergen', 'steuern', 'codex')
# Texte, die grün (`rc-status-ok`) sein dürfen: abgeschlossen oder in Ordnung
GRUEN = re.compile(r'erledigt( \d+)?|fertig|Urteil: bestanden|abgenommen am [\d.]+|\d+ %|Selbstlauf an')


def sichtbar(html):
    """Text einer Seite ohne Logs, Code, Skripte und Tags."""
    roh = re.sub(r'<(pre|code|script|style)\b.*?</\1>', ' ', html, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', roh))


def messung(prozent, zustand='aktuell', frei=JETZT + 3600, hinweis=''):
    return {'zustand': zustand, 'prozent': prozent, 'frei': frei, 'stand': JETZT - 60, 'hinweis': hinweis}


class ZuordnungTest(unittest.TestCase):
    def test_alle_lauf_zustaende_sind_zugeordnet(self):
        erwartet = {'wartet': ('wartet', 'idle'), 'laeuft': ('läuft', 'aktiv'), 'abschluss': ('schließt ab', 'aktiv'), 'fertig': ('fertig', 'ok'),
                    'kontingent': ('wartet auf Kontingent', 'warn'), 'angehalten': ('angehalten', 'warn'), 'fehler': ('Fehler', 'error'),
                    'traum-beendet': ('Traum beendet', 'idle')}
        self.assertEqual(web.ZUSTAND, erwartet)
        for roh, (text, art) in erwartet.items():
            html = str(web.zustand({'id': 'x1', 'zustand': roh}))
            self.assertIn(f'rc-status-{art}"', html)
            self.assertTrue(html.endswith(f'{text}</span>'), html)
            if roh != text:
                self.assertNotIn(f'>{roh}<', html)
        self.assertEqual(set(erwartet), {'wartet', 'laeuft', 'kontingent', 'abschluss', 'fertig', 'angehalten', 'fehler', laeufe.TRAUM_ENDE})

    def test_alle_zustaende_decken_laeufe_ab(self):
        """Neue Lauf-Zustände im Code müssen hier eine Anzeige bekommen."""
        quelle = (web.Path(laeufe.__file__)).read_text(encoding='utf-8')
        im_code = set(re.findall(r"zustand='([a-z]+)'", quelle)) | set(laeufe.AKTIV)
        self.assertLessEqual(im_code, set(web.ZUSTAND))

    def test_arten_modelle_und_urteile(self):
        self.assertEqual(web.ART_TEXT['entwickeln'], 'Entwicklung')
        for art in ('entwickeln', 'nacharbeit', 'review', 'meinung', 'mergen', 'deploy', 'test', 'steuern'):
            self.assertNotEqual(web.art_text({'art': art}), art)
        self.assertEqual([web.modell_text({'modell': m}) for m in ('claude', 'sol', 'codex', 'shell', 'intern')], ['Claude', 'Sol', 'Sol', 'Shell', 'intern'])
        self.assertIn('Nacharbeit nötig', str(web.urteil({'urteil': 'nacharbeit'})))
        self.assertIsNone(web.urteil({}))

    def test_gruen_nur_fuer_erledigt_und_ok(self):
        gruen = {s for s, a in web.STATUS_ART.items() if a == 'ok'}
        self.assertEqual(gruen, {'erledigt'})
        self.assertEqual({z for z, (_, a) in web.ZUSTAND.items() if a == 'ok'}, {'fertig'})
        self.assertEqual({u for u, (_, a) in web.URTEIL.items() if a == 'ok'}, {'bestanden'})
        for s in ('Review', 'in Arbeit'):
            self.assertEqual(web.STATUS_ART[s], 'aktiv')
            self.assertNotIn('rc-status-ok', str(web.status(s)))
        self.assertEqual({s for s, (_, a) in web.ROADMAP_SYMBOL.items() if a == 'ok'}, {'erledigt'})
        self.assertEqual(web_html.GLYPHEN['aktiv'], '◐')
        for s in roadmap.STATUS:  # jeder Roadmap-Status hat eine Art, die es gibt
            self.assertIn(web.STATUS_ART.get(s, 'idle'), web_html.GLYPHEN)


class BalkenTest(unittest.TestCase):
    def html(self, f, schmal=False):
        return str(balken('claude', '5h', f, JETZT, schmal))

    def test_prozent_stufen(self):
        for p, glyph, hoch in ((0, '●', False), (50, '●', False), (79, '●', False), (80, '▲', True), (100, '▲', True)):
            with self.subTest(prozent=p):
                h = self.html(messung(p))
                self.assertIn(f'<progress value="{p}" max="100" role="progressbar"', h)
                self.assertIn(f'aria-valuenow="{p}"', h)
                self.assertIn('aria-label="Claude 5 h"', h)
                self.assertIn(f'aria-valuetext="Claude 5 h: {p} % verbraucht, frei ', h)
                self.assertIn(f'<span class="rc-status-glyph" aria-hidden="true">{glyph}</span>{p} %', h)
                self.assertEqual('k-hoch' in h, hoch)
                self.assertIn('frei ', h)
                self.assertIn('data-kontingent="claude-5h"', h)

    def test_unbekannt_hat_keinen_wert(self):
        h = self.html(messung(None, 'unbekannt', frei=None, hinweis='keine Messung'))
        self.assertIn('role="progressbar"', h)
        self.assertNotIn('<progress', h)
        self.assertNotIn('aria-valuenow', h)
        self.assertIn('aria-valuetext="Claude 5 h: unbekannt (keine Messung)"', h)
        self.assertIn('○</span>unbekannt', h)
        self.assertIn('keine Messung', h)
        self.assertIn('k-unbekannt', h)

    def test_veraltet_ist_blass_mit_hinweis(self):
        h = self.html(messung(40, 'veraltet', hinweis='Messung älter als 2 h'))
        self.assertIn('k-veraltet', h)
        self.assertIn('40 % (veraltet)', h)
        self.assertIn('Messung älter als 2 h', h)
        self.assertIn('○</span>40 %', h)
        self.assertIn('▲</span>85 %', self.html(messung(85, 'veraltet', hinweis='Messung älter als 2 h')))  # ▲ gilt auch hier

    def test_schmal_ohne_stand_und_hinweis(self):
        h = self.html(messung(50, 'veraltet', hinweis='Messung älter als 2 h'), schmal=True)
        self.assertIn('k-schmal', h)
        self.assertNotIn('Stand ', h)
        self.assertNotIn('k-hinweis', h)
        self.assertIn('Messung älter als 2 h', h)  # steht im Text für Screenreader
        self.assertIn('Stand ', self.html(messung(50)))

    def test_keine_inline_stile(self):
        self.assertNotIn('style=', self.html(messung(50)))


class LeitstandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fall, cls.ids = aufbauen()
        cls.addClassCleanup(cls.fall.tearDown)
        cls.client = TestClient(web.anwendung(), headers=LOGIN)

    def holen(self, pfad):
        r = self.client.get(pfad)
        self.assertEqual(r.status_code, 200, pfad)
        return r.text

    def test_vier_bloecke_in_reihenfolge(self):
        s = self.holen('/')
        h2 = re.findall(r'<h2 class="rc-kicker"[^>]*><span class="rc-kicker-bar"></span>([^<]+)', s)
        self.assertEqual(h2, ['Braucht dich', 'Läuft gerade', 'Projekte', 'Selbstlauf', 'Traum'])
        self.assertIn('<h1>Übersicht</h1>', s)

    def test_braucht_dich_springt_zur_handlung(self):
        s = self.holen('/').split('id="laeuft"')[0]
        self.assertIn('href="/p/demo/a/A1#fragen"', s)  # offene Frage F1
        self.assertIn('Celsius oder Fahrenheit?', s)
        self.assertNotIn('F1 · 31', s)
        self.assertIn('href="/p/demo/a/A3"', s)  # blockiert
        self.assertIn('Lauf fehlgeschlagen', s)
        self.assertIn('action="/lauf/', s)  # Fortsetzen direkt aus der Übersicht
        self.assertNotIn('<script>alert', s)
        self.assertEqual(s.count('href="/p/demo/a/A1"'), 0)  # A1 steht wegen der Frage da, nicht doppelt

    def test_laeuft_gerade_zeigt_aufgabe_art_modell_seit_kosten(self):
        s = self.holen('/').split('id="laeuft"')[1].split('id="projekte"')[0]
        self.assertIn('href="/p/demo/a/A2"', s)
        for text in ('Art', 'Entwicklung', 'Modell', 'Sol', 'seit', 'Aufgabenkosten bisher'):
            self.assertIn(f'>{text}<', s)
        self.assertIn('läuft', s)
        self.assertIn('Anhalten', s)
        self.assertRegex(s, r'seit</dt><dd>\d\d\.\d\d\. \d\d:\d\d \(\d+ min')

    def test_projekte_karten(self):
        s = self.holen('/').split('id="projekte"')[1].split('id="selbstlauf"')[0]
        self.assertRegex(s, r'<progress value="\d+" max="\d+" class="fortschritt"')
        self.assertIn('Nächste Aufgabe:', s)
        self.assertIn('Letzter Deploy: noch keiner', s)
        self.assertIn('href="/p/demo/roadmap"', s)

    def test_kontingent_und_selbstlauf(self):
        s = self.holen('/')
        self.assertNotIn('id="kontingent"', s)  # S11.3: Balken stehen nur in der Leiste oben
        self.assertEqual(len(re.findall(r'data-kontingent="', s)), 4)
        selbst = s.split('id="selbstlauf"')[1]
        self.assertIn('Selbstlauf aus', selbst)
        self.assertRegex(selbst, r'Budget heute: 0 von \d+ Steuerläufen')
        for abschn in (selbst.split('id="traum"')[0], s.split('id="traum"')[1]):  # Zustandszeile, Knöpfe eingeklappt
            self.assertRegex(abschn, r'<details><summary><span class="rc-status')
            self.assertNotIn('<details open', abschn)

    def test_obere_leiste_ist_projektuebergreifend_schlank(self):
        for pfad in ('/', '/p/demo', '/p/demo/a/A1', '/p/demo/roadmap', '/p/demo/eingang', '/p/demo/pflege', '/kontingent', '/plaene', '/traum', '/hinweise',
                     f'/lauf/{self.ids["fertig"]}'):
            with self.subTest(pfad=pfad):
                nav = re.search(r'<nav class="haupt".*?</nav>', self.holen(pfad), re.S).group(0)
                self.assertEqual(re.findall(r'<a href="([^"]+)"', nav), ['/', '/p/demo', '/traum', '/kontingent'])
                self.assertIn('aria-controls="projektwahl-liste"', nav)
                for alt in ('/roadmap', '/eingang', '/pflege'):
                    self.assertNotIn(f'href="{alt}"', nav)
        self.assertRegex(self.holen('/'), r'<a href="/" aria-current="page">Übersicht')
        self.assertRegex(self.holen('/kontingent'), r'<a href="/kontingent" aria-current="page">Kontingent')

    def test_jede_projektseite_zeigt_die_unterleiste(self):
        erwartet = ['/p/demo', '/p/demo/roadmap', '/p/demo/eingang', '/p/demo/pflege']
        seiten = {'/p/demo': 'Projekt', '/p/demo/roadmap': 'Roadmap', '/p/demo/eingang': 'Eingang', '/p/demo/pflege': 'Pflege',
                  '/p/demo/a/A1': 'Projekt', '/p/demo/md/ROADMAP.md': 'Projekt', f'/lauf/{self.ids["fertig"]}': 'Projekt'}
        for pfad, aktuell in seiten.items():
            with self.subTest(pfad=pfad):
                unter = re.search(r'<nav class="unter".*?</nav>', self.holen(pfad), re.S).group(0)
                self.assertEqual(re.findall(r'<a href="([^"]+)"', unter), erwartet)
                self.assertEqual(re.findall(r'<a href="[^"]+" aria-current="page">([^<]+)', unter), [aktuell])
        for pfad in ('/', '/kontingent', '/plaene', '/hinweise'):
            self.assertNotIn('class="unter"', self.holen(pfad), pfad)

    def test_alte_sammelpfade_leiten_um(self):
        for alt, unter in (('/roadmap', 'roadmap'), ('/eingang', 'eingang'), ('/pflege', 'pflege')):
            r = self.client.get(alt, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers['location']), (303, f'/p/demo/{unter}'), alt)

    def test_alte_sammelpfade_ohne_eindeutiges_projekt_gehen_zur_uebersicht(self):
        with unittest.mock.patch('devflow2.projekte.alle', return_value=['a', 'b']):
            for alt in ('/roadmap', '/eingang', '/pflege'):
                r = self.client.get(alt, follow_redirects=False)
                self.assertEqual((r.status_code, r.headers['location']), (303, '/#projekte'), alt)

    def test_projektunterseiten(self):
        self.assertIn('Roadmap · demo', self.holen('/p/demo/roadmap'))
        e = self.holen('/p/demo/eingang')
        self.assertIn('action="/p/demo/idee"', e)
        self.assertIn('id="ideen"', e)
        self.assertEqual(self.client.get('/p/gibtsnicht/eingang').status_code, 404)
        self.assertEqual(self.client.get('/p/gibtsnicht/pflege').status_code, 404)

    def test_kontingentseite_hat_dieselben_balken(self):
        s = self.holen('/kontingent').split('<main>')[1]
        self.assertEqual(len(re.findall(r'<progress|class="k-spur"', s)), 4)
        self.assertEqual(len(re.findall(r'data-kontingent="', s)), 4)

    def test_keine_rohwerte_in_der_oberflaeche(self):
        seiten = ['/', '/p/demo', '/p/demo/a/A1', '/p/demo/a/A2', '/p/demo/a/A3', '/kontingent', '/plaene'] + [f'/lauf/{i}' for i in self.ids.values()]
        for pfad in seiten:
            text = sichtbar(self.holen(pfad))
            for roh in ROH:
                with self.subTest(pfad=pfad, roh=roh):
                    self.assertIsNone(re.search(rf'(?<![\w-]){roh}(?![\w-])', text), f'{roh} in {pfad}')
        self.assertIn('Entwicklung · Sol', sichtbar(self.holen('/kontingent')))

    def test_gruen_nur_an_erledigt_und_ok(self):
        seiten = ['/', '/p/demo', '/p/demo/a/A1', '/p/demo/a/A2', '/p/demo/a/A3', '/kontingent', '/plaene', '/p/demo/roadmap'] + [f'/lauf/{i}' for i in self.ids.values()]
        gesehen = set()
        for pfad in seiten:
            s = self.holen(pfad)
            for text in re.findall(r'class="rc-status rc-status-ok"[^>]*><span class="rc-status-glyph"[^>]*>[^<]*</span>([^<]*)</span>', s):
                gesehen.add(text)
                self.assertRegex(text.strip(), GRUEN, f'{pfad}: „{text}“ ist grün')
        self.assertIn('fertig', gesehen)
        for pfad in ('/', '/p/demo', '/p/demo/roadmap'):  # „Review“ und „in Arbeit“ nie grün
            self.assertNotRegex(self.holen(pfad), r'rc-status-ok"[^>]*><span class="rc-status-glyph"[^>]*>[^<]*</span>(Review|in Arbeit|läuft)')

    def test_symbole_in_lesbarer_groesse(self):
        css = self.holen('/static/style.css')
        m = re.search(r'\.rc-status-glyph\s*\{[^}]*font-size:\s*(\d+)px', css)
        self.assertGreaterEqual(int(m.group(1)), 16)

    def test_live_status_kommt_vom_server(self):
        a = self.client.get(f'/api/lauf/{self.ids["laufend"]}').json()
        self.assertEqual((a['zustand'], a['text'], a['art']), ('laeuft', 'läuft', 'aktiv'))
        a = self.client.get(f'/api/lauf/{self.ids["kaputt"]}').json()
        self.assertEqual((a['text'], a['art']), ('Fehler', 'error'))
        js = self.holen('/static/app.js')
        self.assertIn('d.text, d.art', js)
        self.assertNotIn('laeuft:', js)

    def test_braucht_dich_je_projekt_gruppiert(self):
        s = self.client.get('/').text.split('id="braucht-dich"')[1].split('id="laeuft"')[0]
        self.assertEqual(re.findall(r'<li class="gruppe"><h3>([^<]+)</h3>', s), ['demo'])
        self.assertGreaterEqual(s.count('class="eintrag"'), 2)
        self.assertNotIn('<span class="leise">demo</span>', s)  # Projektname steht nur in der Zwischenzeile

    def test_uebersicht_ohne_erledigtes_und_alte_laeufe(self):
        s = self.client.get('/').text.split('<main>')[1]
        self.assertNotIn('Projekt angelegt', s)  # erledigte Aufgabe 0.1
        self.assertNotIn(f'/lauf/{self.ids["fertig"]}', s)  # abgeschlossener Lauf
        self.assertNotIn(f'/lauf/{self.ids["kaputt"]}', s.split('id="laeuft"')[1].split('id="projekte"')[0])
        self.assertIn(f'/lauf/{self.ids["laufend"]}', s)


ENTWURF = """# Vorhaben Wetter · Claude · 29.10.2026

**Stand:** Entwurf

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium |
|---|---|---|---|---|
| A1 | Wetter-Kachel | claude | `app/wetter.py` | Kachel zeigt Temperatur |
"""


class LeerTest(Fall):
    plan_text = ENTWURF

    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=LOGIN)

    def test_leere_bloecke_zeigen_einen_satz(self):
        (self.repo / 'plan' / 'wetter.md').unlink()
        s = self.client.get('/').text
        self.assertIn('Nichts offen: Du musst gerade nichts tun.', s)
        self.assertIn('Gerade läuft nichts.', s)
        self.assertIn('Kein offenes Vorhaben.', s)
        self.assertNotIn('<ul class="liste"></ul>', s)

    def test_plan_zur_abnahme_und_neue_idee(self):
        from devflow2 import eingang
        eingang.anlegen(self.p, 'Dunkles Thema <b>prüfen</b>')
        s = self.client.get('/').text.split('id="laeuft"')[0]
        self.assertIn('Plan zur Abnahme', s)
        self.assertIn('href="/p/demo/plan/wetter#abnehmen"', s)
        self.assertIn('neue Idee', s)
        self.assertIn('href="/p/demo#ideen"', s)
        self.assertIn('Dunkles Thema &lt;b&gt;prüfen&lt;/b&gt;', s)
        self.assertNotIn('<b>prüfen', s)
        self.assertNotIn('Nichts offen', s)

    def test_viele_eintraege_klappen_ab_dem_sechsten_ein(self):
        from devflow2 import ablauf
        (self.repo / 'plan' / 'wetter.md').unlink()
        for i in range(8):
            ablauf.frage_stellen(self.p, f'Frage {i}?', ['ja', 'nein'], 'ja')
        s = self.client.get('/').text.split('id="laeuft"')[0]
        self.assertEqual(s.count('class="eintrag"'), 8)
        self.assertIn('<summary>3 weitere</summary>', s)
        self.assertEqual(s.split('<details>')[0].count('class="eintrag"'), 5)

    def test_defektes_projekt_bricht_die_seite_nicht(self):
        (self.repo / 'ROADMAP.md').write_text('kaputt\n| ID | x |\n|---|---|\n| 1 | 2 |\n')
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Fehler:', r.text)
        self.assertEqual(self.client.get('/p/demo/roadmap').status_code, 200)
        self.assertEqual(self.client.get('/eingang').status_code, 200)

    def test_letzter_deploy(self):
        laeufe.speichern({'id': 'd1', 'projekt': 'demo', 'art': 'deploy', 'modell': 'shell', 'aufgabe': 'A1', 'zustand': 'fertig', 'angelegt': time.time(), 'ende': time.time()})
        s = self.client.get('/').text.split('id="projekte"')[1].split('id="selbstlauf"')[0]
        self.assertRegex(s, r'Letzter Deploy: <span class="rc-status rc-status-ok"[^>]*><span[^>]*>●</span>fertig')


if __name__ == '__main__':
    unittest.main()
