"""Kennzahlen zur Nacharbeit (S15.4): synthetische Läufe, Ursachen, Auftragsart, Altbefunde, Seite."""
import time
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import kennzahlen, laeufe, web  # noqa: E402
from hilfen import Fall  # noqa: E402

T0 = 1_000_000


def lauf(i, aufgabe, art, t, dauer=600, **kw):
    return {'id': f'l{i}', 'projekt': 'demo', 'aufgabe': aufgabe, 'art': art, 'modell': 'claude', 'zustand': 'fertig',
            'angelegt': T0 + t, 'start': T0 + t, 'ende': T0 + t + dauer, **kw}


# Neue Aufgabe N1: Entwicklung, Tests rot → Nacharbeit, Review verlangt Nacharbeit (2 Befunde, 1 ohne Kategorie), Nacharbeit, Review bestanden.
# Neue Aufgabe N2: gleich beim ersten Review bestanden. Altbestand A1: Review mit Nacharbeit (Altbefund ohne Kategorie).
LAEUFE = [
    lauf(1, 'N1', 'entwickeln', 0, kriterien_soll=['K1']),
    lauf(2, 'N1', 'test', 1000, 60, rc=1),
    lauf(3, 'N1', 'nacharbeit', 2000, zaehlt=True),
    lauf(4, 'N1', 'review', 3000, urteil='nacharbeit', kriterien_soll=['K1'],
         befunde=[{'kategorie': 'kriterium'}, {'prioritaet': 'P3'}]),
    lauf(5, 'N1', 'nacharbeit', 4000, zaehlt=True),
    lauf(6, 'N1', 'nacharbeit', 5000, 30, zaehlt=False),  # Nachhaken zählt nicht
    lauf(7, 'N1', 'review', 6000, urteil='bestanden', kriterien_soll=['K1'], befunde=[{'kategorie': 'rand'}]),
    lauf(8, 'N2', 'entwickeln', 0, kriterien_soll=['K1']),
    lauf(9, 'N2', 'review', 1000, urteil='bestanden', kriterien_soll=['K1'], befunde=[]),
    lauf(10, 'A1', 'entwickeln', 0),
    lauf(11, 'A1', 'review', 1000, urteil='nacharbeit', befunde=[{'prioritaet': 'P2'}]),
    lauf(12, 'A1', 'nacharbeit', 2000, zaehlt=True, hinweise='x'),
    lauf(13, '', 'steuern', 0),
]


class Kennzahlen(unittest.TestCase):
    def test_erwartete_zahlen(self):
        g = kennzahlen.auswerten(LAEUFE)
        k, a = g['kriterien'], g['altbestand']
        self.assertEqual((k['aufgaben'], k['bewertet'], k['mit_nacharbeit'], k['mit_tests'], k['runden']), (2, 2, 1, 1, 2))
        self.assertEqual(k['ursachen'], {'review': 1, 'tests': 1, 'konflikt': 0, 'hinweise': 0})
        self.assertEqual(k['befunde'], {'kriterium': 1, 'rand': 1, kennzahlen.NICHT_ZUGEORDNET: 1})
        # N1: 5 × 600 + 60 + 30 = 3090 s, N2: 1200 s
        self.assertEqual((k['zeit'], k['mit_zeit']), (4290, 2))
        self.assertEqual((a['aufgaben'], a['mit_nacharbeit'], a['runden'], a['mit_tests']), (1, 1, 1, 0))
        self.assertEqual(a['ursachen']['review'], 1)  # Altbestand: Review-Nacharbeit zählt hier als review
        self.assertEqual(a['befunde'], {kennzahlen.NICHT_ZUGEORDNET: 1})

    def test_leer_und_offene_laeufe(self):
        g = kennzahlen.auswerten([])
        self.assertEqual(g['kriterien']['aufgaben'], 0)
        offen = lauf(1, 'X', 'entwickeln', 0)
        del offen['ende']
        self.assertEqual(kennzahlen.auswerten([offen])['altbestand']['mit_zeit'], 0)

    def test_laufart_test_und_pause(self):
        # M1: ablauf.testen erzeugt art='test'; M2: Pausen zählen nicht zur aktiven Zeit
        ls = [lauf(1, 'X', 'entwickeln', 0, 60), lauf(2, 'X', 'test', 100, 120, rc=1), lauf(3, 'X', 'nacharbeit', 300, 60, zaehlt=True),
              lauf(4, 'Y', 'entwickeln', 0, 3720, pause=3600)]
        g = kennzahlen.auswerten(ls)['altbestand']
        self.assertEqual((g['mit_tests'], g['ursachen']['tests']), (1, 1))
        self.assertEqual(g['zeit'], 60 + 120 + 60 + 120)

    def test_angehalten_in_kontingentwartezeit(self):
        # M2 Runde 3: 60 s Entwicklung, 3600 s Kontingent, Anhalten -> 60 s; nach Fortsetzen nicht doppelt abgezogen
        angehalten = lauf(1, 'X', 'entwickeln', 0, 3660, wartet_seit=T0 + 60)
        self.assertEqual(kennzahlen.auswerten([angehalten])['altbestand']['zeit'], 60)
        fortgesetzt = lauf(1, 'X', 'entwickeln', 0, 3780, pause=3600)  # wartet_seit entfernt: 180 s aktiv
        self.assertEqual(kennzahlen.auswerten([fortgesetzt])['altbestand']['zeit'], 180)

    def test_zuordnung_vor_dem_review(self):
        # M3: Entwicklungsauftrag mit K-Liste genügt, noch kein Review
        ls = [lauf(1, 'N', 'entwickeln', 0, kriterien_auftrag=True), lauf(2, 'N', 'test', 100, rc=1),
              lauf(3, 'N', 'nacharbeit', 200, zaehlt=True), lauf(4, 'A', 'entwickeln', 0, kriterien_auftrag=False)]
        g = kennzahlen.auswerten(ls)
        self.assertEqual((g['kriterien']['aufgaben'], g['kriterien']['mit_tests'], g['altbestand']['aufgaben']), (1, 1, 1))

    def test_ursache_konflikt_und_vorbereitung(self):
        ls = [lauf(1, 'X', 'vorbereiten', 0, konflikt=['a']), lauf(2, 'X', 'nacharbeit', 10, zaehlt=True),
              lauf(3, 'X', 'vorbereiten', 20, konflikt=None, test_rc=1), lauf(4, 'X', 'nacharbeit', 30, zaehlt=True)]
        u = kennzahlen.auswerten(ls)['altbestand']['ursachen']
        self.assertEqual((u['konflikt'], u['tests']), (1, 1))


class Pausenerfassung(Fall):
    def fortsetzen(self, **kw):
        from unittest import mock
        l = lauf(1, 'X', 'entwickeln', 0, 3660, zustand='angehalten', modell='shell', **kw)
        l['ende'] = T0 + 3660  # Anhalten nach 60 s Arbeit + 3600 s Kontingentwartezeit (Wartebeginn T0+60)
        laeufe.speichern(l)
        with mock.patch.object(laeufe, '_spawn'), mock.patch.object(laeufe, 'jetzt', return_value=T0 + 3660 + 600):
            laeufe.fortsetzen('l1')
        return laeufe.laden('l1')

    def test_anhalten_waehrend_kontingentwartezeit(self):
        # M2 Runde 2: 60 s Arbeit, 3600 s Warten, 600 s angehalten → Pause 4200 s (ab Wartebeginn), nicht nur 600 s
        r = self.fortsetzen(wartet_seit=T0 + 60)
        self.assertEqual(r['pause'], 4200)
        self.assertNotIn('wartet_seit', r)
        r['ende'] = T0 + 3660 + 600 + 60
        self.assertEqual(r['ende'] - r['start'] - r['pause'], 120)

    def test_anhalten_ohne_kontingent_und_alter_lauf(self):
        self.assertEqual(self.fortsetzen()['pause'], 600)
        self.assertEqual(self.fortsetzen(pause=5)['pause'], 605)


class Seite(Fall):
    def test_abschnitt_nacharbeit(self):
        for l in LAEUFE:
            laeufe.speichern(l)
        r = TestClient(web.anwendung(), headers={'Tailscale-User-Login': 'inhaber@github'}).get('/kontingent')
        self.assertEqual(r.status_code, 200)
        s = r.text.split('<main>')[1]
        self.assertIn('Nacharbeit', s)
        self.assertIn('50 % (1 von 2)', s)
        self.assertIn('nicht zugeordnet 1', s)
        self.assertIn('Altbestand', s)


if __name__ == '__main__':
    unittest.main()
