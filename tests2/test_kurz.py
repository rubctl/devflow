"""Kurzfassung überall (S14.1): Regel, Planentwurf, Review-JSON, Aufträge, Vorlagen und Anzeige samt Rückfall (nur synthetische Daten)."""
import logging
import os
os.environ.setdefault('DEVFLOW2_INSTANZ', os.devnull)  # nie die echte instanz.toml lesen
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import Fehler, ablauf, auftraege, kurz, laeufe, plaene, web  # noqa: E402
from devflow2.web_md import kurz_kasten  # noqa: E402
from hilfen import Fall, git  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
logging.getLogger('httpx').setLevel(logging.WARNING)
WURZEL = Path(__file__).resolve().parent.parent

ENTWURF = """# Vorhaben Neu · Claude · 30.10.2026

**Stand:** Entwurf

Nach der Abnahme durch den Inhaber wird diese Zeile genau so ersetzt: `**Stand:** abgenommen vom Inhaber am TT.MM.JJJJ`.

## Kurz

- **Erste Zeile:** Entscheidung zuerst.
- Zweite Zeile.

## Ziel

Ein Ziel.

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| N1 | Erste | claude | `a.py` | K1: Test grün | |
"""
ALTER_BERICHT = """# A9 · Alte Aufgabe · Claude

**Branch/Commit:** `claude-a9` · **Status:** Review

## Was

Die Kachel zeigt jetzt die Temperatur an. Dazu kam ein Test für den Randfall. Der dritte Satz gehört nicht mehr dazu.

## Tests

ok
"""


class RegelTest(unittest.TestCase):
    def test_entwurf_ohne_kurz_oder_zu_lang_wird_abgelehnt(self):
        self.assertIsNone(kurz.pruefen(ENTWURF))
        self.assertIn('## Kurz', kurz.pruefen(ENTWURF.replace('## Kurz', '## Kurzum')))
        leer = ENTWURF.replace('- **Erste Zeile:** Entscheidung zuerst.\n- Zweite Zeile.\n', '')
        self.assertIn('## Kurz', kurz.pruefen(leer))
        lang = ENTWURF.replace('- Zweite Zeile.', '- Zwei.\n- Drei.\n- Vier.')
        self.assertIn('4 Zeilen', kurz.pruefen(lang))

    def test_ausdrueckliche_kurzfassung_hat_vorrang(self):
        self.assertEqual(kurz.aus_text(ENTWURF), '- **Erste Zeile:** Entscheidung zuerst.\n- Zweite Zeile.')
        self.assertEqual(kurz.aus_text('# T\n\n**Kurz:** Bestanden. Zwei P3.\nZweite Zeile.\n\n**Branch:** x\n\n## Was\n\nText. Mehr.'),
                         'Bestanden. Zwei P3.\nZweite Zeile.')
        # eine Marke weiter unten im Text (hinter einem Abschnitt oder im Codeblock) zählt nicht
        self.assertEqual(kurz.aus_text('# T\n\n```\n**Kurz:** im Code\n```\n\nEcht. Zweiter.\n\n## X\n\n**Kurz:** spät'), 'Echt. Zweiter.')

    def test_rueckfall_ueberspringt_unformatierte_stand_zeile(self):
        text = '# Pflege\n\nStand: Commit `da75f92`, geprüft in einer Kopie.\n\n## Zusammenfassung\n\nDie Lücken sind null. Fünf Pakete sind veraltet.\n'
        self.assertEqual(kurz.aus_text(text), 'Die Lücken sind null. Fünf Pakete sind veraltet.')
        self.assertEqual(kurz.aus_text('# T\n\nStand: Commit `x`.\n\nEin Satz im Text. Und noch einer.'), 'Ein Satz im Text. Und noch einer.')

    def test_rueckfall_geht_ueber_absatzende_hinaus(self):
        self.assertEqual(kurz.aus_text('# T\n\nDie Anzeige ist repariert.\n\nDer Regressionstest ist grün.\n\nDritter Absatz.'),
                         'Die Anzeige ist repariert. Der Regressionstest ist grün.')
        # kein Sprung über Überschrift, Liste oder Code hinweg
        self.assertEqual(kurz.aus_text('# T\n\nNur ein Satz.\n\n## Was\n\nAnderer Text.'), 'Nur ein Satz.')
        self.assertEqual(kurz.aus_text('# T\n\nNur ein Satz.\n\n- Punkt.\n'), 'Nur ein Satz.')

    def test_kurz_im_codebeispiel_zaehlt_nicht(self):
        text = '# B\n\n**Kurz:** Gebaut. Tests grün.\n\n## Was\n\n```markdown\n## Kurz\n\n- Beispiel\n```\n\nEnde.'
        self.assertEqual(kurz.aus_text(text), 'Gebaut. Tests grün.')
        self.assertIsNone(kurz.abschnitt('# P\n\n```\n## Kurz\n- x\n```\n'))
        self.assertIn('## Kurz', kurz.pruefen('# P\n\n```\n## Kurz\n- x\n```\n'))
        # ein Codeblock im echten Abschnitt bleibt, eine Überschrift darin beendet ihn nicht
        self.assertEqual(kurz.abschnitt('## Kurz\n\n```\n# kein Kopf\n```\n\n## Weiter'), ['```', '# kein Kopf', '```'])

    def test_rueckfall_erste_zwei_saetze_ohne_ueberschrift_und_metadaten(self):
        self.assertFalse(kurz.ausdruecklich(ALTER_BERICHT))
        self.assertEqual(kurz.aus_text(ALTER_BERICHT), 'Die Kachel zeigt jetzt die Temperatur an. Dazu kam ein Test für den Randfall.')
        alter_plan = ENTWURF.replace('## Kurz\n\n- **Erste Zeile:** Entscheidung zuerst.\n- Zweite Zeile.\n\n', '')
        self.assertEqual(kurz.aus_text(alter_plan), 'Ein Ziel.')
        self.assertEqual(kurz.aus_text('# Nur Überschrift\n\n**Stand:** x\n'), '')
        self.assertEqual(kurz.aus_text(''), '')
        self.assertEqual(kurz.aus_text('# T\n\n- Nur ein Punkt.\n'), 'Nur ein Punkt.')


class AuftragTest(unittest.TestCase):
    def k(self):
        return dict(projekt='demo', id='X1', aufgabe='A', worktree='/w', branch='b', basis='main', test='t', vorgaben=[], cwd='/c', commit='c1', basis_commit='c0')

    def test_alle_berichtsauftraege_verlangen_kurz(self):
        k = self.k()
        texte = {'entwickeln': auftraege.entwickeln(k), 'nacharbeit': auftraege.nacharbeit(k, 'g', 'd'),
                 'review': auftraege.review(k, 1, [], 'Sol'), 'meinung': auftraege.meinung('demo', 'plan/x.md', '', 'Sol'),
                 'rueckblick': auftraege.traum_rueckblick('f'), 'code': auftraege.traum_code('demo'),
                 'vorschlag': auftraege.traum_vorschlag(dict(k, titel='T', auftrag='A')), 'planen': auftraege.planen('demo', [])}
        for name, text in texte.items():
            with self.subTest(name):
                self.assertIn('Kurz', text)
        for name in ('entwickeln', 'meinung', 'rueckblick', 'code', 'vorschlag'):
            self.assertIn('**Kurz:**', texte[name], name)
        self.assertIn('höchstens drei Zeilen', texte['entwickeln'])
        self.assertIn('Urteil', texte['review'])
        self.assertIn('## Kurz', texte['planen'])

    def test_review_schema_hat_kurz(self):
        self.assertIn('kurz', auftraege.REVIEW_SCHEMA['properties'])
        self.assertIn('kurz', auftraege.REVIEW_SCHEMA['required'])

    def test_urteil_liest_kurz_und_toleriert_altes_format(self):
        u = ablauf.urteil('{"urteil": "bestanden", "kurz": "Bestanden.\\nKeine Befunde.", "bericht": "x", "befunde": []}')
        self.assertEqual(u['kurz'], 'Bestanden.\nKeine Befunde.')
        # Gliederung bleibt erhalten (Zeilen und Punkte), nur Leerraum wird geglättet
        u = ablauf.urteil('{"urteil": "nacharbeit", "kurz": "Nacharbeit.\\n- M1  beheben.\\n\\n- Tests ergänzen.", "bericht": "x", "befunde": []}')
        self.assertEqual(u['kurz'], 'Nacharbeit.\n- M1 beheben.\n- Tests ergänzen.')
        self.assertEqual(ablauf.urteil('{"urteil": "bestanden", "bericht": "x", "befunde": []}')['kurz'], '')

    @unittest.skipUnless((WURZEL / 'plan/_vorlage.md').exists(), 'plan/ und berichte/ gehören nicht zum Release-Export')
    def test_vorlagen(self):
        self.assertIn('\n## Kurz\n', (WURZEL / 'plan/_vorlage.md').read_text())
        self.assertIsNone(kurz.pruefen((WURZEL / 'plan/_vorlage.md').read_text()))
        for datei in ('_vorlage.md', '_review-vorlage.md'):
            self.assertIn('**Kurz:**', (WURZEL / 'berichte' / datei).read_text())
        for skill in ('planungssitzung', 'neues-projekt'):
            self.assertIn('Kurz', (WURZEL / 'skills' / skill / 'SKILL.md').read_text())


class PlanTest(Fall):
    def test_entwurf_ohne_kurz_ist_fehler_und_schreibt_nichts(self):
        with self.assertRaisesRegex(Fehler, '## Kurz'):
            plaene.entwurf(self.p, 'neu', ENTWURF.replace('## Kurz', '## Anders'))
        self.assertFalse((self.repo / 'plan/neu.md').exists())
        self.assertEqual(plaene.entwurf(self.p, 'neu', ENTWURF), 'plan/neu.md')

    def test_plan_seite_zeigt_kurz_oben_hervorgehoben(self):
        plaene.entwurf(self.p, 'neu', ENTWURF)
        s = TestClient(web.anwendung(), headers=LOGIN).get('/p/demo/plan/neu').text
        i = s.index('<aside class="kurz"')
        self.assertIn('<strong>Erste Zeile:</strong> Entscheidung zuerst.', s[i:s.index('</aside>', i)])
        self.assertLess(i, s.index('Stufe je Aufgabe'))


class AlterPlanTest(Fall):
    plan_text = ENTWURF.replace('## Kurz\n\n- **Erste Zeile:** Entscheidung zuerst.\n- Zweite Zeile.\n\n', '').replace('**Stand:** Entwurf', '**Stand:** abgenommen vom Inhaber am 01.11.2026')

    def test_alter_plan_bleibt_gueltig_und_zeigt_rueckfall(self):
        info = plaene.info(self.p, 'wetter')
        self.assertNotIn('## Kurz', info['text'])
        self.assertEqual(info['kurz'], 'Ein Ziel.')
        s = TestClient(web.anwendung(), headers=LOGIN).get('/p/demo/plan/wetter').text
        i = s.index('<aside class="kurz"')
        self.assertIn('Ein Ziel.', s[i:s.index('</aside>', i)])
        self.assertLess(i, s.index('<article class="md">'))


class ReviewTest(Fall):
    def lauf_mit_review(self, kurz_text):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'app/w.py': 'X = 1\n', 'berichte/A1.md': ALTER_BERICHT}, 'ergebnis': '**Kurz:** Gebaut. Test grün.\n\nMehr Text.'}],
                      codex=[self.bestanden('# Review\n\nBestanden.', kurz=kurz_text)])
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        e = ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.warten(lambda: laeufe.laden(e['id'])['zustand'] == 'fertig', text='A1 entwickelt')
        r = self.review_fertig(automatisch=False)
        return e['id'], r['id']

    def test_review_kurz_wird_am_lauf_gespeichert_und_oben_angezeigt(self):
        entw, rev = self.lauf_mit_review('Bestanden.\nKeine Befunde.')
        self.assertEqual(laeufe.laden(rev)['kurz'], 'Bestanden.\nKeine Befunde.')
        c = TestClient(web.anwendung(), headers=LOGIN)
        s = c.get(f'/lauf/{rev}').text
        i = s.index('<aside class="kurz"')
        self.assertIn('Bestanden. Keine Befunde.', s[i:s.index('</aside>', i)])
        self.assertLess(i, s.index('class="laufkopf"'))
        # dieselbe Kurzfassung auf der Aufgabenseite und im MCP-/Lagebild
        self.assertIn('Bestanden. Keine Befunde.', c.get('/p/demo/a/A1').text)
        self.assertEqual(ablauf.kurz(laeufe.laden(rev))['kurz'], 'Bestanden.\nKeine Befunde.')
        # Entwicklungslauf: Kurzfassung aus der ersten Zeile `**Kurz:**` der Antwort
        s = c.get(f'/lauf/{entw}').text
        self.assertIn('Gebaut. Test grün.', s[s.index('<aside class="kurz"'):])
        self.assertNotIn('Mehr Text', s[s.index('<aside class="kurz"'):s.index('</aside>', s.index('<aside class="kurz"'))])

    def test_review_ohne_kurz_faellt_auf_den_bericht_zurueck(self):
        _, rev = self.lauf_mit_review(None)
        self.assertEqual(laeufe.laden(rev)['kurz'], '')
        s = TestClient(web.anwendung(), headers=LOGIN).get(f'/lauf/{rev}').text
        i = s.index('<aside class="kurz"')
        self.assertIn('Bestanden.', s[i:s.index('</aside>', i)])

    def test_berichtsseite_alter_bericht_zeigt_erste_saetze_ohne_ueberschrift(self):
        self.drehbuch()
        (self.repo / 'berichte').mkdir()
        (self.repo / 'berichte/A9.md').write_text(ALTER_BERICHT)
        (self.repo / 'berichte/A8.md').write_text('# A8\n\n**Kurz:** Bestanden. Alles grün.\n\n## Was\n\nLanger Text. Zweiter. Dritter.\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'berichte')
        c = TestClient(web.anwendung(), headers=LOGIN)
        s = c.get('/p/demo/md/berichte/A9.md').text
        box = s[s.index('<aside class="kurz"'):]
        box = box[:box.index('</aside>')]
        self.assertIn('Die Kachel zeigt jetzt die Temperatur an. Dazu kam ein Test für den Randfall.', box)
        self.assertNotIn('A9', box)
        self.assertNotIn('Branch', box)
        self.assertNotIn('dritte Satz', box)
        s = c.get('/p/demo/md/berichte/A8.md').text
        self.assertIn('Bestanden. Alles grün.', s[s.index('<aside class="kurz"'):s.index('</aside>', s.index('<aside class="kurz"'))])
        self.assertNotIn('<aside class="kurz"', c.get('/p/demo/md/ROADMAP.md').text)


class KastenTest(unittest.TestCase):
    def test_leer_gibt_nichts_und_html_wird_maskiert(self):
        self.assertIsNone(kurz_kasten(''))
        self.assertIsNone(kurz_kasten(None))
        s = str(kurz_kasten('<script>x</script> **fett**'))
        self.assertNotIn('<script>', s)
        self.assertIn('<strong>fett</strong>', s)
