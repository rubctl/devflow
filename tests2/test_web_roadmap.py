"""Roadmap-Ansicht (S3.6): Vorhaben mit Fortschritt, Zuordnung, „Sonstiges“, Maskierung."""
import html
import logging
import os
import re
import shutil
import time
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import Fehler, kontingent, laeufe, projekte, roadmap, web, web_roadmap  # noqa: E402
from hilfen import Fall, git  # noqa: E402

logging.getLogger('httpx').setLevel(logging.WARNING)
KOPF = '| ID | Aufgabe | Wer | Status | Datum | Details |\n|---|---|---|---|---|---|\n'
TEXT = f"""# demo · Roadmap

## Offene Fragen an den Inhaber

_Keine._

## Aufgaben

{KOPF}| 0.1 | Projekt <b>angelegt</b> | Claude | erledigt | 29.10. | |
| 0.2 | Lose Aufgabe | | offen | 29.10. | |

### Vorhaben Eins · Claude · 29.10.2026 ([Plan](plan/eins.md))

{KOPF}| E1 | Erste | Claude | erledigt | 29.10. | |
| E2 | Zweite <script>alert(1)</script> | Claude | in Arbeit | 30.10. | Stand: läuft |
| E3 | Dritte | Sol | Braucht dich | 30.10. | |
| E4 | Vierte | Sol | entfällt | 30.10. | |

### Vorhaben Zwei · Claude · 30.10.2026 ([Plan](plan/zwei.md))

{KOPF}| Z1 | Eins | Claude | erledigt | 30.10. | |
| Z2 | Zwei | Claude | erledigt | 30.10. | |

## Protokoll

- 29.10.2026 · Projekt angelegt.
"""


class RoadmapAnsichtTest(Fall):
    def setUp(self):
        super().setUp()
        (self.repo / 'ROADMAP.md').write_text(TEXT)
        laeufe.speichern({'id': 'lose', 'projekt': 'demo', 'art': 'entwickeln', 'modell': 'claude', 'aufgabe': '0.2', 'zustand': 'fertig', 'angelegt': 1, 'start': 1, 'ende': 2})
        self.client = TestClient(web.anwendung(), headers={'Tailscale-User-Login': 'inhaber@github'})

    def test_zuordnung_und_fortschritt(self):
        g = roadmap.vorhaben(TEXT)
        self.assertEqual([x['titel'] for x in g], ['Vorhaben Eins · Claude · 29.10.2026', 'Vorhaben Zwei · Claude · 30.10.2026', 'Sonstiges'])
        self.assertEqual([x['plan'] for x in g], ['plan/eins.md', 'plan/zwei.md', None])
        self.assertEqual([[a['id'] for a in x['aufgaben']] for x in g], [['E1', 'E2', 'E3', 'E4'], ['Z1', 'Z2'], ['0.1', '0.2']])
        # entfällt zählt nicht mit
        self.assertEqual([(x['erledigt'], x['gesamt']) for x in g], [(1, 3), (2, 2), (1, 2)])
        self.assertEqual(g[0]['verteilung'], {'Braucht dich': 1, 'in Arbeit': 1, 'erledigt': 1, 'entfällt': 1})

    def test_tabelle_ohne_planlink_unter_sonstiges(self):
        text = TEXT.replace('## Protokoll', f'### Wartung\n\n{KOPF}| W1 | Pflege | | offen | 30.10. | |\n\n## Protokoll')
        g = roadmap.vorhaben(text)
        self.assertEqual([x['titel'] for x in g], ['Vorhaben Eins · Claude · 29.10.2026', 'Vorhaben Zwei · Claude · 30.10.2026', 'Sonstiges'])
        self.assertEqual([a['id'] for a in g[2]['aufgaben']], ['0.1', '0.2', 'W1'])
        self.assertEqual((g[2]['erledigt'], g[2]['gesamt']), (1, 3))

    def test_kein_sonstiges_ohne_lose_aufgaben(self):
        self.assertNotIn('Sonstiges', [x['titel'] for x in roadmap.vorhaben(TEXT.replace('| 0.1 ', '| X ').split('## Aufgaben')[0] + '## Protokoll\n')])

    def test_defekte_tabelle(self):
        with self.assertRaises(Fehler):
            roadmap.vorhaben(KOPF + '| A | b |\n')

    def test_seite(self):
        r = self.client.get('/p/demo/roadmap')
        self.assertEqual(r.status_code, 200, r.text[:300])
        h = r.text
        self.assertIn('1 von 3 erledigt', h)
        self.assertIn('2 von 2 erledigt', h)
        self.assertIn('1 von 2 erledigt', h)
        self.assertIn('Sonstiges', h)
        self.assertIn('href="/p/demo/md/plan/eins.md"', h)
        self.assertIn('<progress', h)
        # Symbole
        self.assertRegex(h, r'●</span>erledigt')
        self.assertRegex(h, r'◐</span>in Arbeit')
        self.assertRegex(h, r'▲</span>Braucht dich')
        self.assertRegex(h, r'○</span>offen')
        # erledigtes Vorhaben eingeklappt, die anderen offen
        details = re.findall(r'<details\b[^>]*>', h)
        self.assertEqual(['open' in d for d in details], [True, True, False])  # offene zuerst, erledigtes Vorhaben zuletzt und zu
        # Maskierung
        self.assertNotIn('<script>alert', h)
        self.assertNotIn('<b>angelegt', h)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', h)
        self.assertEqual(re.findall(r'<script\b[^>]*>', h), ['<script src="/static/app.js" defer>'])

    def test_einstiege_und_fehler(self):
        self.assertIn('href="/p/demo/roadmap"', self.client.get('/').text)
        self.assertIn('href="/p/demo/roadmap"', self.client.get('/p/demo').text)
        self.assertEqual(self.client.get('/p/gibtsnicht/roadmap').status_code, 404)
        self.assertEqual(TestClient(web.anwendung()).get('/p/demo/roadmap').status_code, 403)
        (self.repo / 'ROADMAP.md').write_text(KOPF + '| A | b |\n')
        r = self.client.get('/p/demo/roadmap')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Spalten', r.text)


PLAN_EINS = """# Eins

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| E3 | Dritte | sol | x | y | schwer |
"""
KOSTEN = '{"type":"result","total_cost_usd":%s}'


def zeilen(h):
    """{ID: Zellentexte} der Roadmap-Tabellen."""
    out = {}
    for tr in re.findall(r'<tr>(.*?)</tr>', h, re.S):
        zellen = [' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', z)).split()) for z in re.findall(r'<t[hd]\b[^>]*>(.*?)</t[hd]>', tr, re.S)]
        if len(zellen) == 6 and not zellen[0].startswith('ID'):
            out[zellen[0]] = zellen
    return out


class RoadmapTabelleTest(Fall):
    """Zwei Vorhaben mit erfundenen Läufen (S4.4)."""

    def lauf(self, lid, aufgabe, art='entwickeln', modell='claude', start=None, dauer=None, kosten=None, **mehr):
        laeufe.speichern({'id': lid, 'projekt': 'demo', 'art': art, 'modell': modell, 'aufgabe': aufgabe, 'zustand': 'fertig',
                          'angelegt': start or 1, 'start': start, 'ende': start + dauer if start and dauer else None, **mehr})
        if kosten is not None:
            (laeufe.verzeichnis(lid) / 'log.jsonl').write_text(KOSTEN % kosten + '\n')

    def setUp(self):
        super().setUp()
        (self.repo / 'ROADMAP.md').write_text(TEXT)
        (self.repo / 'plan' / 'eins.md').write_text(PLAN_EINS)
        self.lauf('lose', '0.2', start=10, dauer=5)
        self.lauf('e1-ent', 'E1', start=1000, dauer=600, kosten=0.5, cli_modell='sonnet', cli_effort='high', stufe='normal')
        self.lauf('e1-rev', 'E1', art='review', modell='sol', start=2000, dauer=300, cli_effort='high')
        self.lauf('e1-na1', 'E1', art='nacharbeit', start=3000, dauer=60, kosten=0.25, cli_modell='sonnet', cli_effort='high', zaehlt=True)
        self.lauf('e1-na2', 'E1', art='nacharbeit', start=4000, dauer=30, cli_modell='sonnet', cli_effort='high', zaehlt=False)  # Nachhaken, kein Log
        self.lauf('z1-sol', 'Z1', modell='sol', start=5000, dauer=3700, cli_effort='medium', stufe='leicht')
        self.lauf('z2-ent', 'Z2', start=6000, dauer=120, kosten=1.5, cli_modell='opus', cli_effort='high', stufe='schwer')
        self.lauf('ohne', None, start=7000, dauer=999, kosten=9)  # gehört zu keiner Aufgabe
        self.client = TestClient(web.anwendung(), headers={'Tailscale-User-Login': 'inhaber@github'})

    def test_spalten_je_aufgabe(self):
        h = self.client.get('/p/demo/roadmap').text
        self.assertEqual(re.findall(r'<th scope="col"[^>]*><a [^>]*>([^<▲▼]*)', h)[:6], ['ID', 'Kurztitel', 'Stand', 'Läuft / Wartet', 'Stufe', 'Kosten'])
        z = zeilen(h)
        self.assertEqual(z['E1'], ['E1', 'Erste', '● erledigt 29.10.', '–', 'normal', '0,75 $ 2 von 3 Claude-Läufen'])
        self.assertEqual(z['Z1'][2:], ['● erledigt 30.10.', '–', 'leicht', '–'])
        self.assertEqual(z['Z2'][2:], ['● erledigt 30.10.', '–', 'schwer', '1,50 $'])
        self.assertEqual(z['E3'][2:], ['▲ Braucht dich 30.10.', '–', 'schwer', '–'])  # Stufe aus dem Plan
        self.assertEqual(z['0.2'][3:], ['–'] * 3)

    def test_reihenfolge_offen_vor_erledigt(self):
        h = self.client.get('/p/demo/roadmap').text
        titel = re.findall(r'<span class="v-titel">([^<]*)', h)
        self.assertEqual(titel, ['Vorhaben Eins · Claude · 29.10.2026', 'Sonstiges', 'Vorhaben Zwei · Claude · 30.10.2026'])
        self.assertLess(h.index('>E1<'), h.index('>0.1<'))
        self.assertLess(h.index('>0.1<'), h.index('>Z1<'))
        self.assertIn('2 von 2 erledigt', h[h.index('Vorhaben Zwei'):])
        self.assertRegex(h, r'<details id="demo-v2" class="rc-card vorhaben" data-fertig="1">')  # geschlossen: kein open

    def test_vorhaben_neueste_zuerst(self):
        neu = TEXT.replace('## Protokoll', f'### Vorhaben Drei · Claude · 31.10.2026 ([Plan](plan/drei.md))\n\n{KOPF}| D1 | Neu | Claude | offen | 31.10. | |\n\n## Protokoll')
        (self.repo / 'ROADMAP.md').write_text(neu)
        git(self.repo, 'commit', '-q', '-am', 'drei')
        titel = re.findall(r'<span class="v-titel">([^<]*)', self.client.get('/p/demo/roadmap').text)
        self.assertEqual(titel, ['Vorhaben Drei · Claude · 31.10.2026', 'Vorhaben Eins · Claude · 29.10.2026', 'Sonstiges', 'Vorhaben Zwei · Claude · 30.10.2026'])
        g = [{'titel': t, 'plan': 'p', 'gesamt': 1, 'erledigt': 0} for t in ('A', 'B · 01.01.2026', 'C')]
        self.assertEqual([x['titel'] for x in web_roadmap.neueste_zuerst(g)], ['B · 01.01.2026', 'C', 'A'])  # ohne Datum: später in der Datei zuerst

    def test_sonstiges_entfaellt_ohne_laeufe_auch_bei_offener_aufgabe(self):
        shutil.rmtree(laeufe.verzeichnis('lose'))  # kein Lauf zu Sonstiges: Karte entfällt, obwohl 0.2 offen ist
        titel = re.findall(r'<span class="v-titel">([^<]*)', self.client.get('/p/demo/roadmap').text)
        self.assertNotIn('Sonstiges', titel)
        self.assertEqual(len(titel), 2)

    def test_laufender_lauf_in_seiner_zeile(self):
        laeufe.speichern({'id': 'lauf', 'projekt': 'demo', 'art': 'nacharbeit', 'modell': 'claude', 'aufgabe': 'E2', 'zustand': 'laeuft', 'angelegt': laeufe.jetzt(), 'start': 100, 'pid': os.getpid()})
        laeufe.speichern({'id': 'warte', 'projekt': 'demo', 'art': 'review', 'modell': 'sol', 'aufgabe': 'E3', 'zustand': 'kontingent', 'angelegt': laeufe.jetzt()})
        h = self.client.get('/p/demo/roadmap').text
        z = zeilen(h)
        self.assertEqual(z['E2'][3], 'läuft Nacharbeit · Claude')
        self.assertIn('<a href="/lauf/lauf">Nacharbeit</a> · Claude', h)
        self.assertEqual(z['E3'][3], 'wartet Review (Kontingent) · Sol')
        self.assertIn('<a href="/lauf/warte">Review (Kontingent)</a> · Sol', h)
        self.assertEqual(z['E1'][3], '–')

    def test_kurztitel(self):
        k = web_roadmap.kurztitel
        self.assertEqual(k('Roadmap als Tabelle: Kurztitel, Stand, Kosten'), 'Roadmap als Tabelle')
        self.assertEqual(k('**Navigation:** schlank'), 'Navigation')
        self.assertEqual(k('Um 12:30 Uhr läuft es'), 'Um 12:30 Uhr läuft es')  # Doppelpunkt ohne Leerraum trennt nicht
        lang = 'Ein sehr langer Text ohne jeden Doppelpunkt, der weit über die Grenze der Anzeige hinausgeht und gekürzt werden muss'
        self.assertTrue(k(lang).endswith(' …'))
        self.assertLessEqual(len(k(lang)), 62)
        self.assertTrue(lang.startswith(k(lang)[:-2]))
        self.assertEqual(k(': nur'), ': nur')

    def test_kopf_mit_fortschritt_und_summen(self):
        h = self.client.get('/p/demo/roadmap').text
        self.assertIn('1 von 3 erledigt', h)
        self.assertIn('4 Läufe · Nacharbeit 1 · 0,75 $ Claude · Dauer 16 min', h)
        self.assertIn('2 Läufe · Nacharbeit 0 · 1,50 $ Claude · Dauer 1 h 03 min', h)
        self.assertIn('1 Lauf · Nacharbeit 0 · Kosten – · Dauer 5 s', h)  # Sonstiges mit dem Lauf zu 0.2

    def test_kopf_einzahl(self):
        self.assertIn('1 Lauf · Nacharbeit 0 · Kosten – · Dauer 5 s', self.client.get('/p/demo/roadmap').text)

    def test_filter_nach_status(self):
        h = self.client.get('/p/demo/roadmap?status=erledigt').text
        self.assertEqual(sorted(zeilen(h)), ['0.1', 'E1', 'Z1', 'Z2'])
        self.assertIn('Alle 8', h)
        self.assertIn('href="/p/demo/roadmap?status=Braucht+dich"', h)
        self.assertRegex(h, r'<a href="/p/demo/roadmap\?status=erledigt" aria-current="true">')
        self.assertIn('Keine Aufgaben mit diesem Status.', self.client.get('/p/demo/roadmap?status=Review').text)
        # erledigtes Vorhaben ist mit Treffern aufgeklappt
        self.assertEqual(['open' in d for d in re.findall(r'<details\b[^>]*>', h)], [True, True, True])

    def test_unbekannter_filter_und_sortierung_werden_ignoriert(self):
        h = self.client.get('/p/demo/roadmap?status=<script>x&sort=<b>&richtung=ab').text
        self.assertEqual(len(zeilen(h)), 8)
        self.assertNotIn('<script>x', h)
        self.assertNotIn('&lt;script&gt;x', h)

    def test_sortieren_per_spaltenkopf(self):
        def reihenfolge(abfrage, ids):
            h = self.client.get('/p/demo/roadmap' + abfrage).text
            return [i for i in re.findall(r'<th scope="row" class="id"><a href="/p/demo/a/([^"]+)"', h) if i in ids]
        eins = ['E1', 'E2', 'E3', 'E4']
        self.assertEqual(reihenfolge('', eins), eins)
        self.assertEqual(reihenfolge('?sort=stufe', eins), ['E1', 'E3', 'E2', 'E4'])  # leere am Ende
        self.assertEqual(reihenfolge('?sort=id&richtung=ab', eins), ['E4', 'E3', 'E2', 'E1'])
        self.assertEqual(reihenfolge('?sort=kosten&richtung=ab', eins), ['E1', 'E2', 'E3', 'E4'])  # leere am Ende, in Ausgangsreihenfolge
        self.assertEqual(reihenfolge('?sort=kosten&richtung=ab', ['Z1', 'Z2']), ['Z2', 'Z1'])  # Sol ohne Kosten nach Claude mit Kosten
        self.assertEqual(reihenfolge('?sort=kosten', ['Z1', 'Z2']), ['Z2', 'Z1'])  # auch aufsteigend am Ende
        self.assertEqual(reihenfolge('?sort=status', eins), ['E3', 'E2', 'E1', 'E4'])
        h = self.client.get('/p/demo/roadmap?sort=kosten&status=erledigt').text
        self.assertIn('aria-sort="ascending"', h)
        self.assertIn('href="/p/demo/roadmap?status=erledigt&amp;sort=kosten&amp;richtung=ab"', h)  # zweiter Klick: absteigend
        self.assertIn('href="/p/demo/roadmap?status=erledigt&amp;sort=stufe"', h)

    def test_maskiert(self):
        h = self.client.get('/p/demo/roadmap').text
        self.assertNotIn('<script>alert', h)
        self.assertNotIn('<b>angelegt', h)
        self.assertEqual(zeilen(h)['E2'][1], 'Zweite <script>alert(1)</script>')
        self.assertEqual(re.findall(r'<script\b[^>]*>', h), ['<script src="/static/app.js" defer>'])

    def test_bilanz_laufender_lauf_und_leere_felder(self):
        laeufe.speichern({'id': 'lauf', 'projekt': 'demo', 'art': 'entwickeln', 'modell': 'claude', 'aufgabe': 'E2', 'zustand': 'laeuft',
                          'angelegt': 1, 'start': 100})
        laeufe.speichern({'id': 'start-fehlt', 'projekt': 'demo', 'art': 'test', 'modell': 'shell', 'aufgabe': 'E2', 'zustand': 'fertig', 'angelegt': 2})
        b = kontingent.aufgaben_bilanz('demo', jetzt=160)
        self.assertEqual((b['E2']['laeufe'], b['E2']['dauer'], b['E2']['claude'], b['E2']['bekannt']), (2, 60, 1, 0))
        self.assertEqual((b['Z1']['claude'], b['Z1']['kosten'], b['Z1']['entwickler']['modell']), (0, 0.0, 'sol'))
        self.assertNotIn('', b)
        self.assertNotIn(None, b)
        self.assertEqual(kontingent.aufgaben_bilanz('gibtsnicht'), {})
        h = self.client.get('/p/demo/roadmap').text  # die Seite rechnet laufende Läufe bis jetzt
        self.assertIn('Dauer', h)

    def test_bilanz_fortgesetzter_lauf_zaehlt_bis_jetzt(self):
        laeufe.speichern({'id': 'fort', 'projekt': 'demo', 'art': 'entwickeln', 'modell': 'claude', 'aufgabe': 'E2', 'zustand': 'laeuft',
                          'angelegt': laeufe.jetzt(), 'start': 100, 'ende': 110})  # angehalten und fortgesetzt: altes ende bleibt stehen
        self.assertEqual(kontingent.aufgaben_bilanz('demo', jetzt=160)['E2']['dauer'], 60)
        self.assertEqual(kontingent.aufgaben_bilanz('demo', jetzt=280)['E2']['dauer'], 180)
        laeufe.speichern({'id': 'fort', 'projekt': 'demo', 'art': 'entwickeln', 'modell': 'claude', 'aufgabe': 'E2', 'zustand': 'angehalten',
                          'angelegt': 1, 'start': 100, 'ende': 110})
        self.assertEqual(kontingent.aufgaben_bilanz('demo', jetzt=280)['E2']['dauer'], 10)  # beendet: ende gilt

    def test_alle_projekte(self):
        zweit = self.tmp / 'zweit'
        zweit.mkdir()
        git(zweit, 'init', '-q', '-b', 'main')
        (zweit / 'ROADMAP.md').write_text(TEXT.replace('Vorhaben Eins', 'Vorhaben Drei').replace('| E', '| X').replace('| Z', '| Y'))
        projekte.anmelden(zweit, 'zweit')
        h = self.client.get('/p/zweit/roadmap?status=erledigt&sort=id').text
        self.assertIn('Vorhaben Drei', h)
        self.assertNotIn('Vorhaben Eins', h)  # nur dieses Projekt, nichts Projektübergreifendes
        self.assertIn('href="/p/zweit/a/X1"', h)
        self.assertEqual(sorted(zeilen(h)), ['X1', 'Y1', 'Y2'])
        self.assertEqual(self.client.get('/roadmap').status_code, 200)  # Umleitung wird gefolgt; mehrere Projekte → Übersicht
        (zweit / 'ROADMAP.md').write_text(KOPF + '| A | b |\n')
        self.assertIn('Spalten', self.client.get('/p/zweit/roadmap').text)
        self.assertIn('Vorhaben Eins', self.client.get('/p/demo/roadmap').text)  # das andere Projekt bleibt lesbar


if __name__ == '__main__':
    unittest.main()
