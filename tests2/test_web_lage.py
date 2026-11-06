"""Übersicht (S4.3): Leitstand mit vier Blöcken, Kontingentbalken, Navigation, Zuordnung Rohwert → Anzeige, Grün nur für Abgeschlossenes."""
import logging
import os
import re
import time
import unittest
import unittest.mock
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import inhaber, laeufe, roadmap, vorschlag, web, web_html  # noqa: E402
from devflow2.projekte import Projekt  # noqa: E402
from devflow2.web_kontingent import balken  # noqa: E402
import test_vorschlag  # noqa: E402
from hilfen import Fall, git  # noqa: E402
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

    def test_braucht_dich_ist_eine_liste_mit_projektname_je_zeile(self):
        s = self.client.get('/').text.split('id="braucht-dich"')[1].split('id="laeuft"')[0]
        self.assertGreaterEqual(s.count('class="eintrag"'), 2)
        self.assertNotIn('class="gruppe"', s)
        self.assertRegex(s, r'<summary>.*?<span class="leise">demo</span>')  # S14.3: flache Liste, das Projekt steht in der Zeile

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
        self.assertIn('action="/p/demo/plan/wetter/abnehmen"', s)
        self.assertIn('href="/p/demo/plan/wetter"', s)  # ganzen Plan lesen
        self.assertIn('neue Idee', s)
        self.assertIn('href="/p/demo#ideen"', s)
        self.assertLess(s.index('<summary>Zur Info (1)</summary>'), s.index('neue Idee'))  # reine Information steht eingeklappt unter „Zur Info“
        self.assertIn('(1)</span></h2>', s)  # die Anzahl zählt die Info nicht
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
        self.assertEqual(s.split('<summary>3 weitere</summary>')[0].count('class="eintrag"'), 5)

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


HERKUNFT = {**LOGIN, 'Origin': 'http://testserver'}


def felder(html, aktion):
    """Die Formularfelder (versteckt) des Formulars mit dieser Aktion, so wie der Browser sie senden würde."""
    form = re.search(r'<form method="post" action="' + re.escape(aktion) + r'"[^>]*>(.*?)</form>', html, re.S)
    assert form, f'kein Formular {aktion}'
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form.group(1)))


class EntscheidungBasis(Fall):
    """Die Übersicht als Entscheidungsliste (S14.3): Knöpfe senden an dieselben Routen wie die Formulare, gebunden an den angezeigten Stand."""

    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)

    def start(self):
        return self.client.get('/').text.split('id="braucht-dich"')[1].split('id="laeuft"')[0]

    def knopf(self, aktion, **mehr):
        """Klick: genau die Felder des angezeigten Formulars."""
        return self.client.post(aktion, data={**felder(self.start(), aktion), **mehr})


class PlanKnopfTest(EntscheidungBasis):
    plan_text = ENTWURF

    def test_abnehmen_per_knopf_ist_dieselbe_planzeile_wie_ueber_die_planseite(self):
        kopie = ENTWURF.replace('Vorhaben Wetter', 'Vorhaben Zwei')
        (self.repo / 'plan' / 'zwei.md').write_text(kopie)
        git(self.repo, 'add', 'plan/zwei.md')
        git(self.repo, 'commit', '-q', '-m', 'plan: zwei')
        s = self.start()
        self.assertEqual(s.count('>Abnehmen</button>'), 2)
        f = felder(s, '/p/demo/plan/wetter/abnehmen')
        self.assertEqual((f['stufe.A1'], f['zurueck']), ('', 'start'))
        r = self.knopf('/p/demo/plan/wetter/abnehmen')
        self.assertEqual((r.status_code, r.headers['location']), (303, '/?meldung=plan-abgenommen#braucht-dich'))
        # dieselbe Aufgabe über das Formular der Planseite (Felder `stufe.<ID>` und `gesehen`, ohne `zurueck`)
        seite = self.client.get('/p/demo/plan/zwei').text
        gesehen = re.search(r'name="gesehen" value="([^"]+)"', seite).group(1)
        r = self.client.post('/p/demo/plan/zwei/abnehmen', data={'stufe.A1': '', 'gesehen': gesehen})
        self.assertEqual(r.headers['location'], '/p/demo/plan/zwei?meldung=plan-abgenommen')
        a, b = [(self.repo / 'plan' / n).read_text() for n in ('wetter.md', 'zwei.md')]
        self.assertEqual(a.replace('Vorhaben Wetter', 'X'), b.replace('Vorhaben Zwei', 'X'))
        self.assertRegex(a, r'\*\*Stand:\*\* abgenommen')
        self.assertNotIn('action="/p/demo/plan/wetter/abnehmen"', self.start())

    def test_stufen_stammen_aus_der_plantabelle(self):
        text = ENTWURF.replace('| A1 | Wetter-Kachel | claude | `app/wetter.py` | Kachel zeigt Temperatur |',
                               '| A1 | Wetter-Kachel | claude | `app/wetter.py` | Kachel zeigt Temperatur | schwer |').replace('Abnahmekriterium |\n|---|---|---|---|---|',
                                                                                                                 'Abnahmekriterium | Stufe |\n|---|---|---|---|---|---|')
        (self.repo / 'plan' / 'wetter.md').write_text(text)
        git(self.repo, 'commit', '-qam', 'plan: Stufe')
        s = self.start()
        self.assertEqual(felder(s, '/p/demo/plan/wetter/abnehmen')['stufe.A1'], 'schwer')
        self.assertIn('A1 schwer', s)  # die Beschriftung nennt die Stufen
        self.knopf('/p/demo/plan/wetter/abnehmen')
        self.assertIn('schwer', (self.repo / 'plan' / 'wetter.md').read_text().split('abgenommen')[1])

    def test_veralteter_plan_wird_abgelehnt(self):
        s = self.start()
        f = felder(s, '/p/demo/plan/wetter/abnehmen')
        (self.repo / 'plan' / 'wetter.md').write_text(ENTWURF + '\nNeuer Absatz.\n')
        git(self.repo, 'commit', '-qam', 'plan: geändert')
        r = self.client.post('/p/demo/plan/wetter/abnehmen', data=f)
        self.assertEqual(r.status_code, 409)
        self.assertIn('hat sich geändert', r.text)
        self.assertNotIn('abgenommen', (self.repo / 'plan' / 'wetter.md').read_text().split('**Stand:**')[1].split('\n')[0])

    def test_herkunft_wird_wie_bei_den_formularen_geprueft(self):
        f = felder(self.start(), '/p/demo/plan/wetter/abnehmen')
        r = TestClient(web.anwendung(), headers=LOGIN, follow_redirects=False).post('/p/demo/plan/wetter/abnehmen', data=f)
        self.assertEqual(r.status_code, 403)

    def test_zurueck_ist_kein_frei_waehlbares_ziel(self):
        f = felder(self.start(), '/p/demo/plan/wetter/abnehmen')
        r = self.client.post('/p/demo/plan/wetter/abnehmen', data={**f, 'zurueck': 'https://boese.example/'})
        self.assertEqual(r.headers['location'], '/p/demo/plan/wetter?meldung=plan-abgenommen')

    def test_aenderungswunsch_steht_in_den_details_und_bleibt_auf_der_uebersicht(self):
        r = self.client.post('/p/demo/plan/wetter/aenderung', data={'wunsch': 'Mehr Tests', 'zurueck': 'start'})
        self.assertEqual(r.headers['location'], '/?meldung=rueckmeldung#braucht-dich')
        s = self.client.get('/').text
        self.assertNotIn('action="/p/demo/plan/wetter/abnehmen"', s)  # mit offenem Wunsch kein Ein-Klick-Abnehmen
        self.assertIn('Plan: Änderung gewünscht', s.split('Zur Info')[1])


class EmpfehlungTest(unittest.TestCase):
    def frage(self, optionen, empfehlung):
        from devflow2 import roadmap as rm
        text, nr = rm.frage(f'## Offene Fragen {inhaber.an_formen()[0]}\n\n_Keine._\n', '06.11.', 'Welche?', optionen, empfehlung, 'A1')
        return rm.fragen(text)[0]

    def test_eindeutig_nur_bei_genau_einer_option(self):
        from devflow2 import entscheidung as e
        o = ['Celsius, weil metrisch', 'Fahrenheit']
        for empf, soll in [('Option 1', 1), ('Option 2', 2), ('2.', 2), ('Option 2: Fahrenheit', 2), ('Option 1: celsius', 1), ('1. Celsius', 1), ('2)', 2),
                           ('Fahrenheit', 2), ('fahrenheit.', 2)]:
            self.assertEqual((e.empfehlung(self.frage(o, empf)) or (None,))[0], soll, empf)
        for empf in ('keine', '', 'Option 3', 'Option 1 oder Option 2', 'Option 1, später 2. Schritt', 'Erst Review, dann Merge', '2 Wochen warten', 'Celsius oder Fahrenheit',
                     'Option 1 und 2 kombinieren', 'Option 1/2', 'Option 1 + 2', '1. und 2.', 'Option 1 und beide', 'Option 1 sowie Fahrenheit', 'Option 1, kombiniert',
                     'Option 1, zusätzlich Dokumentation ergänzen.', 'Option 2, außerdem Review', 'Option 1, dazu eine Doku',
                     'Option 1 (zusätzlich Dokumentation ergänzen)', 'Option 1 (Empfehlung)', 'Option 2: weil gewohnt', 'Option 1, weil Tests fehlen; zusätzlich Doku',
                     'Option 2, denn schneller', 'Option 2 da gewohnt'):
            self.assertIsNone(e.empfehlung(self.frage(o, empf)), empf)
        self.assertIsNone(e.empfehlung(self.frage([], 'Option 1')))  # keine Optionen vorgegeben
        self.assertIsNone(e.empfehlung(self.frage(['a', 'a'], 'a')))  # Text zweimal: nicht eindeutig

    def test_mehrzeilige_empfehlung(self):
        from devflow2 import entscheidung as e
        self.assertEqual(e.empfehlung(self.frage(['A', 'B'], 'Option 2\n'))[0], 2)
        self.assertIsNone(e.empfehlung(self.frage(['A', 'B'], 'Option 2\nweil schneller')))


class FrageKnopfTest(EntscheidungBasis):
    plan_text = ENTWURF

    def setUp(self):
        super().setUp()
        (self.repo / 'plan' / 'wetter.md').unlink()
        git(self.repo, 'commit', '-qam', 'ohne Plan')

    def frage(self, empfehlung):
        from devflow2 import ablauf
        ablauf.frage_stellen(self.p, 'Celsius oder Fahrenheit?', ['Celsius', 'Fahrenheit'], empfehlung)

    def test_eindeutige_empfehlung_hat_einen_knopf_und_traegt_die_option_ein(self):
        self.frage('Option 1')
        s = self.start()
        self.assertIn('Empfehlung annehmen', s)
        r = self.knopf('/p/demo/frage/1/empfehlung')
        self.assertEqual((r.status_code, r.headers['location']), (303, '/?meldung=empfehlung#braucht-dich'))
        self.assertIn('Empfehlung angenommen: Option 1, Celsius', self.roadmap())
        self.assertIn('Empfehlung angenommen', self.client.get(r.headers['location']).text)
        self.assertEqual(self.client.get('/').text.count('Empfehlung annehmen'), 0)

    def test_kombinierte_empfehlung_hat_keinen_knopf_und_traegt_nichts_ein(self):
        self.keine_empfehlung_per_knopf(['Celsius', 'Fahrenheit'], 'Option 1 und 2 kombinieren')

    def keine_empfehlung_per_knopf(self, optionen, empfehlung):
        """Kein Knopf, und selbst ein POST mit der gültigen angezeigten Prüfsumme trägt nichts ein."""
        from devflow2 import ablauf, entscheidung as e, roadmap as rm
        ablauf.frage_stellen(self.p, 'Was fehlt?', optionen, empfehlung)
        self.assertNotIn('Empfehlung annehmen', self.start())
        gesehen = e.frage_stand(rm.fragen(self.roadmap())[0])
        r = self.client.post('/p/demo/frage/1/empfehlung', data={'gesehen': gesehen})
        self.assertEqual(r.status_code, 409)
        self.assertNotIn('Empfehlung angenommen', self.roadmap())

    def test_empfehlung_mit_zusatz_in_klammer_hat_keinen_knopf_und_traegt_nichts_ein(self):
        self.keine_empfehlung_per_knopf(['Tests ergänzen', 'Dokumentation ergänzen'], 'Option 1 (zusätzlich Dokumentation ergänzen)')

    def test_empfehlung_mit_begruendung_und_zusatz_hat_keinen_knopf_und_traegt_nichts_ein(self):
        self.keine_empfehlung_per_knopf(['Tests ergänzen', 'Dokumentation ergänzen'], 'Option 1, weil Tests fehlen; zusätzlich Dokumentation ergänzen.')

    def test_ohne_eindeutige_empfehlung_antworten_in_den_details(self):
        self.frage('Erst klären, dann entscheiden')
        s = self.start()
        self.assertNotIn('Empfehlung annehmen', s)
        self.assertIn('data-antworten', s)
        self.assertIn('>Antworten</a>', s)
        self.assertIn('action="/p/demo/frage/1/antwort"', s)  # das Formular steckt in den Details
        r = self.client.post('/p/demo/frage/1/empfehlung', data={'gesehen': 'x'})
        self.assertEqual(r.status_code, 409)  # auch ein gebastelter Aufruf trägt keine Antwort ein
        self.assertNotIn('Empfehlung angenommen', self.roadmap())

    def test_geaenderte_empfehlung_wird_abgelehnt(self):
        self.frage('Option 1')
        f = felder(self.start(), '/p/demo/frage/1/empfehlung')
        (self.repo / 'ROADMAP.md').write_text(self.roadmap().replace('**Empfehlung:** Option 1', '**Empfehlung:** Option 2'))
        git(self.repo, 'commit', '-qam', 'Empfehlung geändert')
        r = self.client.post('/p/demo/frage/1/empfehlung', data=f)
        self.assertEqual(r.status_code, 409)
        self.assertIn('hat sich geändert', r.text)
        self.assertNotIn('Empfehlung angenommen', self.roadmap())

    def test_doppelklick_antwortet_nur_einmal(self):
        self.frage('Option 2')
        f = felder(self.start(), '/p/demo/frage/1/empfehlung')
        for _ in range(2):
            r = self.client.post('/p/demo/frage/1/empfehlung', data=f)
            self.assertEqual(r.status_code, 303)
        self.assertEqual(self.roadmap().count('Empfehlung angenommen: Option 2, Fahrenheit'), 1)
        self.assertEqual(r.headers['location'], '/?meldung=schon#braucht-dich')

    def test_ohne_origin_abgelehnt(self):
        self.frage('Option 1')
        f = felder(self.start(), '/p/demo/frage/1/empfehlung')
        self.assertEqual(TestClient(web.anwendung(), headers=LOGIN, follow_redirects=False).post('/p/demo/frage/1/empfehlung', data=f).status_code, 403)


class LaufKnopfTest(EntscheidungBasis):
    plan_text = ENTWURF

    def setUp(self):
        super().setUp()
        (self.repo / 'plan' / 'wetter.md').unlink()
        git(self.repo, 'commit', '-qam', 'ohne Plan')
        from devflow2 import ablauf
        ablauf.aufgabe_anlegen(self.p, 'A1', 'Wetter')
        self.lauf = {'id': 'l1', 'projekt': 'demo', 'art': 'entwickeln', 'modell': 'claude', 'aufgabe': 'A1', 'zustand': 'fehler', 'angelegt': time.time(),
                     'ende': time.time(), 'fehler': 'kaputt'}
        laeufe.speichern(self.lauf)

    def test_fortsetzen_per_knopf(self):
        self.assertIn('Lauf fehlgeschlagen', self.start())
        f = felder(self.start(), '/lauf/l1/fortsetzen')
        with unittest.mock.patch('devflow2.ablauf.fortsetzen') as fort:
            r = self.client.post('/lauf/l1/fortsetzen', data=f)
        self.assertEqual((r.status_code, r.headers['location']), (303, '/?meldung=fortgesetzt#braucht-dich'))
        fort.assert_called_once_with('l1', 'dashboard')

    def test_veralteter_lauf_wird_abgelehnt(self):
        f = felder(self.start(), '/lauf/l1/fortsetzen')
        laeufe.speichern({**self.lauf, 'fehler': 'etwas anderes'})
        with unittest.mock.patch('devflow2.ablauf.fortsetzen') as fort:
            r = self.client.post('/lauf/l1/fortsetzen', data=f)
        self.assertEqual(r.status_code, 409)
        self.assertIn('hat sich geändert', r.text)
        fort.assert_not_called()

    def test_ohne_gesehen_gilt_die_alte_seite_weiter(self):
        with unittest.mock.patch('devflow2.ablauf.fortsetzen') as fort:
            self.assertEqual(self.client.post('/lauf/l1/fortsetzen', data={}).headers['location'], '/lauf/l1?meldung=fortgesetzt')
        fort.assert_called_once()

    def test_kein_knopf_bei_offener_frage_traum_und_halt(self):
        from devflow2 import ablauf
        ablauf.frage_stellen(self.p, 'Weiter?', ['ja', 'nein'], 'keine Ahnung', 'A1')
        s = self.start()
        self.assertNotIn('/lauf/l1/fortsetzen', s)
        self.assertIn('Fortsetzen ist gerade nicht möglich: Wartet auf Antwort', s)
        ablauf.antwort_eintragen(self.p, 'F1', 'ja')
        self.assertIn('/lauf/l1/fortsetzen', self.start())
        laeufe.speichern({**self.lauf, 'traum': 'x'})
        self.assertNotIn('/lauf/l1/fortsetzen', self.start())

    def test_halt_ist_ein_klaerungseintrag_oben_und_sperrt_fortsetzen(self):
        from devflow2 import ablauf
        ablauf.anhalten('alles', 'dashboard')
        s = self.start()
        self.assertLess(s.index('devflow ist angehalten'), s.index('Lauf fehlgeschlagen'))
        self.assertIn('action="/alles/fortsetzen"', s)
        self.assertNotIn('/lauf/l1/fortsetzen', s)
        self.assertEqual(self.client.post('/lauf/l1/fortsetzen', data={}).status_code, 409)  # auch der gebastelte Aufruf

    def test_login_problem_und_blockade_stehen_oben(self):
        from devflow2 import ablauf
        ablauf.aufgabe_anlegen(self.p, 'A2', 'Zweite')
        ablauf.status_setzen(self.p, 'A2', 'blockiert', details='Wartet auf Zugang')
        laeufe.speichern({**self.lauf, 'login': 'claude'})
        s = self.start()
        klaeren = s.split('class="liste entscheidungen klaerung"')[1].split('</ul>')[0]
        self.assertIn('Wartet auf Zugang', klaeren)
        self.assertIn('Anmeldung von claude abgelaufen', klaeren)
        self.assertIn('/lauf/l1/fortsetzen', klaeren)


class VorschlagKnopfTest(test_vorschlag.Hilfe):
    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)

    def start(self):
        return self.client.get('/').text.split('id="braucht-dich"')[1].split('id="laeuft"')[0]

    def test_uebernehmen_per_knopf_bindet_an_den_stand(self):
        v = self.bereit()
        s = self.start()
        self.assertIn('>Übernehmen</button>', s)
        f = felder(s, f'/traum/vorschlag/{v["id"]}/uebernehmen')
        self.assertEqual(f['gesehen'], self.kopf())
        r = self.client.post(f'/traum/vorschlag/{v["id"]}/uebernehmen', data=f)
        self.assertEqual((r.status_code, r.headers['location']), (303, '/?meldung=vorschlag-merge#braucht-dich'))
        self.assertEqual(vorschlag.laden(v['id'])['zustand'], 'uebernommen')
        self.assertEqual(vorschlag.laden(v['id'])['freigabe']['commit'], f['gesehen'])
        self.assertNotIn('>Übernehmen</button>', self.start())

    def test_neuer_stand_wird_abgelehnt(self):
        v = self.bereit()
        f = felder(self.start(), f'/traum/vorschlag/{v["id"]}/uebernehmen')
        git(v['worktree'], 'commit', '-q', '--allow-empty', '-m', 'noch eine Änderung')
        r = self.client.post(f'/traum/vorschlag/{v["id"]}/uebernehmen', data=f)
        self.assertEqual(r.status_code, 409)
        self.assertIn('nicht mehr auf dem angezeigten Stand', r.text)
        self.assertEqual(vorschlag.laden(v['id'])['zustand'], 'offen')

    def test_verwerfen_steht_in_den_details_mit_rueckfrage(self):
        v = self.bereit()
        s = self.start()
        self.assertRegex(s, r'<form method="post" action="/traum/vorschlag/' + v['id'] + r'/verwerfen"[^>]*data-bestaetigen=')
        f = felder(s, f'/traum/vorschlag/{v["id"]}/verwerfen')
        self.assertEqual(f['gesehen'], vorschlag.kopf(Projekt('demo'), v))
        r = self.client.post(f'/traum/vorschlag/{v["id"]}/verwerfen', data=f)
        self.assertEqual(r.headers['location'], '/?meldung=vorschlag-verworfen#braucht-dich')
        self.assertEqual(vorschlag.laden(v['id'])['zustand'], 'verworfen')

    def test_verwerfen_mit_neuem_stand_wird_abgelehnt(self):
        v = self.bereit()
        f = felder(self.start(), f'/traum/vorschlag/{v["id"]}/verwerfen')
        git(v['worktree'], 'commit', '-q', '--allow-empty', '-m', 'noch eine Änderung')
        r = self.client.post(f'/traum/vorschlag/{v["id"]}/verwerfen', data=f)
        self.assertEqual(r.status_code, 409)
        self.assertIn('nicht mehr auf dem angezeigten Stand', r.text)
        self.assertEqual(vorschlag.laden(v['id'])['zustand'], 'offen')
        self.assertTrue(os.path.isdir(v['worktree']))
        r = self.client.post(f'/traum/vorschlag/{v["id"]}/verwerfen', data={'zurueck': 'start'})  # altes Formular ohne Stand
        self.assertEqual(r.status_code, 409)
        self.assertEqual(vorschlag.laden(v['id'])['zustand'], 'offen')


if __name__ == '__main__':
    unittest.main()
