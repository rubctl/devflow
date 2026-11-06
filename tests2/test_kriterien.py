"""Nummerierte Kriterien (S15.1): Zerlegen, Prüfung neuer Entwürfe, Altbestand, Anzeige auf der Planseite (nur synthetische Daten)."""
from pathlib import Path
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import Fehler, auftraege, kriterien, plaene, roadmap, web  # noqa: E402
from hilfen import Fall  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
KOPF = """# Vorhaben Neu · Claude · 06.11.2026

**Stand:** Entwurf

## Kurz

- Entscheidung zuerst.

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
"""
PLAN = KOPF + """| N1 | Erste | claude | `a.py` | K1: Liste <b>erscheint</b>.<br>K2: Zweites Kriterium. | |
| N2 | Zweite | sol | `b.py` | K1: Nur eins. | |
"""
ALT = KOPF + '| A1 | Alt | claude | `a.py` | Test grün | |\n'


def mit(kriterium):
    return KOPF + f'| N1 | Erste | claude | `a.py` | {kriterium} | |\n'


def aufgaben(text):
    return roadmap.plan(text)['aufgaben']


class ZerlegenTest(unittest.TestCase):
    def test_nummerierte_kriterien(self):
        self.assertEqual(kriterien.zerlegen('K1: a<br>K2: b'), ('', [(1, 'a'), (2, 'b')]))
        self.assertEqual(kriterien.zerlegen('K1: a<BR/>K10:b')[1], [(1, 'a'), (10, 'b')])

    def test_abschnittskopf_ist_kein_kriterium_und_aufzaehlung_gehoert_zum_vorigen(self):
        ein, liste = kriterien.zerlegen('**Auftrag:** Allgemein.<br>**K1 Belege:** Text<br>• Punkt eins;<br>• Punkt zwei<br>K2: Ende')
        self.assertEqual(ein, '**Auftrag:** Allgemein.')
        self.assertEqual(liste, [(1, 'Belege: Text • Punkt eins; • Punkt zwei'), (2, 'Ende')])

    def test_br_in_code_spanne_trennt_nicht(self):
        text = 'K1: Vorlage verlangt `K1: …<br>K2: …`, Köpfe zählen nicht.<br>K2: Zweites<br>K3: Drittes `a<br>b` Ende'
        liste = kriterien.zerlegen(text)[1]
        self.assertEqual([n for n, _ in liste], [1, 2, 3])
        self.assertIn('`K1: …<br>K2: …`', liste[0][1])
        self.assertIn('`a<br>b` Ende', liste[2][1])
        self.assertEqual(kriterien.doppelte(liste), [])
        self.assertEqual(kriterien.zerlegen('K1: ein ` Backtick<br>K2: b')[1], [(1, 'ein ` Backtick'), (2, 'b')])  # ungepaart trennt weiter

    def test_ohne_nummer(self):
        self.assertEqual(kriterien.zerlegen('Test grün'), ('Test grün', []))
        self.assertEqual(kriterien.zerlegen('Kernzusage „X“: Test grün'), ('Kernzusage „X“: Test grün', []))  # „Kernzusage“ ist kein K
        self.assertEqual(kriterien.zerlegen(''), ('', []))


class PruefenTest(Fall):
    def test_korrekter_plan_wird_gespeichert_und_kernzusage_als_k_ist_erlaubt(self):
        self.assertEqual(plaene.entwurf(self.p, 'neu', PLAN), 'plan/neu.md')
        plaene.entwurf(self.p, 'neu2', mit('K1: Kernzusage „Daten“: Test T.'))

    def test_aufgabe_ohne_k_ist_fehler_und_schreibt_nichts(self):
        for kriterium in ('Test grün', '', 'Kernzusage „X“: Test', 'K: ohne Zahl', 'K1 ohne Doppelpunkt'):
            with self.assertRaisesRegex(Fehler, 'Aufgabe N1: .*nummerierte Kriterien'):
                plaene.entwurf(self.p, 'neu', mit(kriterium))
        self.assertFalse((self.repo / 'plan/neu.md').exists())

    def test_eine_aufgabe_ohne_k_unter_mehreren(self):
        with self.assertRaisesRegex(Fehler, 'Aufgabe N2'):
            plaene.entwurf(self.p, 'neu', PLAN.replace('K1: Nur eins.', 'Nur eins.'))

    def test_doppelte_nummer_ist_fehler(self):
        with self.assertRaisesRegex(Fehler, 'Aufgabe N1: doppelte Kriteriennummer K2'):
            plaene.entwurf(self.p, 'neu', mit('K1: a<br>K2: b<br>K2: c'))
        with self.assertRaisesRegex(Fehler, 'doppelte Kriteriennummer K1'):
            plaene.entwurf(self.p, 'neu', mit('K1: a<br>K01: b'))
        self.assertFalse((self.repo / 'plan/neu.md').exists())

    def test_formatbeispiel_in_backticks_ist_kein_doppeltes_k(self):
        k = 'K1: Die Vorlage verlangt `K1: …<br>K2: …`.<br>K2: `plan_entwurf` meldet Fehler.<br>K3: Seite zeigt Liste.'
        self.assertEqual(plaene.entwurf(self.p, 'neu', mit(k)), 'plan/neu.md')
        self.assertEqual([n for n, _ in plaene.info(self.p, 'neu')['aufgaben'][0]['kriterien']], [1, 2, 3])

    def test_gleiche_nummern_in_verschiedenen_aufgaben_sind_erlaubt(self):
        self.assertIsNone(kriterien.pruefen(aufgaben(PLAN)))

    def test_pflegeplan_bleibt_ausgenommen(self):
        text = '# Pflege\n\n**Stand:** Entwurf\n'
        self.assertEqual(plaene.entwurf(self.p, 'pflege', text), 'plan/pflege.md')


class AnzeigeTest(Fall):
    def seite(self, name):
        return TestClient(web.anwendung(), headers=LOGIN).get(f'/p/demo/plan/{name}')

    def test_planseite_zeigt_kriterien_als_liste_schon_vor_der_abnahme(self):
        plaene.entwurf(self.p, 'neu', PLAN)
        info = plaene.info(self.p, 'neu')
        self.assertFalse(info['altbestand'])
        self.assertEqual(info['aufgaben'][0]['kriterien'], [(1, 'Liste <b>erscheint</b>.'), (2, 'Zweites Kriterium.')])
        r = self.seite('neu')
        self.assertEqual(r.status_code, 200)
        i = r.text.index('id="kriterien"')
        ab = r.text[i:r.text.index('</section>', i)]
        self.assertIn('<strong>K1</strong> Liste &lt;b&gt;erscheint&lt;/b&gt;.', ab)  # maskiert
        self.assertIn('<strong>K2</strong> Zweites Kriterium.', ab)
        self.assertNotIn('Altbestand', ab)
        self.assertLess(r.text.index('Stufe je Aufgabe'), i)

    def test_altbestand_ist_gueltig_und_wird_so_angezeigt(self):
        (self.repo / 'plan/alt.md').write_text(ALT, encoding='utf-8')
        info = plaene.info(self.p, 'alt')
        self.assertTrue(info['altbestand'])
        self.assertEqual(info['aufgaben'][0]['kriterien'], [])
        self.assertEqual(plaene.liste(self.p)[0]['name'], 'alt')
        r = self.seite('alt')
        self.assertEqual(r.status_code, 200)
        i = r.text.index('id="kriterien"')
        ab = r.text[i:r.text.index('</section>', i)]
        self.assertIn('Altbestand', ab)
        self.assertIn('Test grün', ab)

    def test_aufgabe_ohne_k_in_plan_mit_k_wird_gekennzeichnet(self):
        (self.repo / 'plan/misch.md').write_text(PLAN.replace('K1: Nur eins.', 'Nur eins.'), encoding='utf-8')
        info = plaene.info(self.p, 'misch')
        self.assertFalse(info['altbestand'])
        ab = self.seite('misch').text
        self.assertIn('keine nummerierten Kriterien', ab)
        self.assertNotIn('vor S15.1', ab)

    def test_vorlage_und_skill_verlangen_nummerierte_kriterien(self):
        wurzel = __import__('pathlib').Path(__file__).resolve().parent.parent
        vorlage = wurzel / 'plan/_vorlage.md'
        if vorlage.exists():
            self.assertIn('K1: …<br>K2: …', vorlage.read_text())
        self.assertIn('K1: …<br>K2: …', (wurzel / 'skills/planungssitzung/SKILL.md').read_text())


if __name__ == '__main__':
    unittest.main()


class AuftragKriterienTest(unittest.TestCase):
    """S15.2: Entwicklungs- und Nacharbeitsauftrag nennen K wörtlich und verlangen Belege und Selbstprüfung."""
    PLAN = dict(datei='plan/x.md', bereich='a.py', kriterium='**Auftrag:** Einleitung.<br>**K1 Belege:** Erstes `a<br>b`.<br>K2: Zweites<br>• Punkt')

    def k(self, plan):
        return dict(projekt='demo', id='X1', aufgabe='A', worktree='/w', branch='b', basis='main', test='t', vorgaben=[], plan=plan, karte_auslassen=())

    def alle(self, k):
        return {'entwickeln': auftraege.entwickeln(k), 'nacharbeit': auftraege.nacharbeit(k, 'g', 'd'),
                'frisch': auftraege.nacharbeit(k, 'g', 'd', frisch=True)}

    def test_neu_nennt_kriterien_belege_und_checkliste(self):
        for name, t in self.alle(self.k(self.PLAN)).items():
            with self.subTest(name):
                self.assertIn('- K1: Belege: Erstes `a<br>b`.', t)
                self.assertIn('- K2: Zweites • Punkt', t)
                self.assertIn('Abschnitt „Kriterien“: je K1, K2', t)
                self.assertIn('Abschnitt „Selbstprüfung“', t)
                for punkt in auftraege.SELBSTPRUEFUNG:
                    self.assertIn(punkt, t)
                self.assertIn('Handybreite', t)
                self.assertIn('tatsächliche Verhalten', t)
                self.assertNotIn('<br>K2', t)
        self.assertEqual(self.alle(self.k(self.PLAN))['nacharbeit'].count('- K1:'), 1)
        self.assertEqual(self.alle(self.k(self.PLAN))['frisch'].count('- K1:'), 1)

    def test_altbestand_bisheriger_auftrag_plus_checkliste(self):
        for plan in (dict(self.PLAN, kriterium='Alles läuft.'), dict(self.PLAN, kriterium=''), None):
            for name, t in self.alle(self.k(plan)).items():
                with self.subTest(name, plan=plan):
                    self.assertNotIn('Abschnitt „Kriterien“', t)
                    self.assertNotIn('- K1:', t)
                    self.assertIn('Abschnitt „Selbstprüfung“', t)
                    self.assertIn('alte Daten und Konfigurationen', t)
        t = auftraege.entwickeln(self.k(dict(self.PLAN, kriterium='Alles läuft.')))
        self.assertIn('Abnahmekriterium: Alles läuft.', t)

    def test_review_auftrag_nennt_kriterien(self):
        """S15.3: Der Review-Auftrag nennt die Kriterien wörtlich und verlangt die Bewertung je ID; Belege sind nur Orientierung."""
        t = auftraege.review(dict(self.k(self.PLAN), cwd='/c', commit='c1', basis_commit='c0'), 1, [], 'Sol')
        self.assertIn('- K1: Belege: Erstes `a<br>b`.', t)
        self.assertIn('Bewerte jedes Kriterium (K1, K2) einzeln', t)
        self.assertIn('genau einem Eintrag je Kriterium K1, K2', t)
        self.assertIn('Orientierung, kein Nachweis', t)
        self.assertIn('kategorie (kriterium:', t)
        self.assertNotIn('Altbestand', t)
        alt = auftraege.review(dict(self.k(dict(self.PLAN, kriterium='Alles läuft.')), cwd='/c', commit='c1', basis_commit='c0'), 1, [], 'Sol')
        self.assertIn('Abnahmekriterium: Alles läuft.', alt)
        self.assertIn('kriterien: [] (leere Liste)', alt)
        self.assertNotIn('Bewerte jedes Kriterium', alt)

    def test_nachreview_vorrang_fuer_kriterien(self):
        vorher = [dict(nr='M1', schwere='P2', ort='a:1', was='x', kategorie='kriterium', kriterium='K2')]
        k = dict(self.k(self.PLAN), cwd='/c', commit='c2', basis_commit='c0', vorher_commit='c1')
        t = auftraege.review(k, 2, vorher, 'Sol')
        self.assertIn('- M1 (P2, kriterium, K2) a:1: x', t)
        self.assertIn('ein Kriterium mit „nein“ oder „teilweise“ ist auch hier mindestens P2', t)
        alt = auftraege.review(dict(k, plan=None), 2, [dict(nr='M1', schwere='P2', ort='a:1', was='x')], 'Sol')
        self.assertIn('- M1 (P2) a:1: x', alt)
        self.assertNotIn('ist auch hier mindestens P2', alt)

    def test_vorlage_hat_alle_punkte(self):
        datei = Path(__file__).resolve().parents[1] / 'berichte' / '_vorlage.md'
        if not datei.exists():  # berichte/ wird nicht in den Release exportiert
            self.skipTest('berichte/ fehlt im Export')
        vorlage = datei.read_text(encoding='utf-8')
        for punkt in ('## Kriterien', '## Selbstprüfung') + auftraege.SELBSTPRUEFUNG:
            self.assertIn(punkt, vorlage)
