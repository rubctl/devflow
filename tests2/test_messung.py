"""Frische Messung vor dem Träumen (S5.5): Messaufruf bei veralteter oder fehlender Messung, Fehlergrund, kein Aufruf bei frischer Messung."""
import datetime
import json
import os
from pathlib import Path
import time

from devflow2 import Fehler, ablauf, kontingent, laeufe, traum
from test_traum import TEXT, Basis


def limit(prozent=20, woche=30):
    return {**TEXT, 'limit': {'5h': prozent, 'woche': woche, 'frei': int(time.time() + 3600)}}


class MessungTest(Basis):
    def arten(self):
        return sorted((l['modell'], l['art']) for l in laeufe.alle())

    def test_frische_messung_kein_aufruf(self):
        self.assertTrue(traum.starten('dashboard'))
        self.assertEqual(self.aufrufe(), [])
        self.assertEqual(laeufe.alle(), [])

    def test_veraltet_misst_beide_modelle_dann_startet_der_traum(self):
        self.messen(alter=3 * 3600)
        self.sol_alt()
        self.drehbuch(claude=[limit(20)], codex=[limit(10)])
        t = traum.starten('dashboard')
        self.assertTrue(t)
        self.assertEqual(sorted(a['werkzeug'] for a in self.aufrufe()), ['claude', 'codex'])
        self.assertEqual(self.arten(), [('claude', 'messung'), ('sol', 'messung')])
        self.assertEqual(kontingent.traum_pruefen('claude', **traum.grenzen()['claude'])[0], '')
        self.assertEqual(kontingent.traum_pruefen('sol', **traum.grenzen()['sol'])[0], '')

    def sol_alt(self):
        """Sol-Messung von vor 3 Stunden (maßgeblich ist der Zeitstempel im Ereignis)."""
        d = Path(os.environ['DEVFLOW2_CODEX_SITZUNGEN'])
        zeile = json.loads((d / 'a.jsonl').read_text())
        zeile['timestamp'] = datetime.datetime.fromtimestamp(time.time() - 3 * 3600, datetime.timezone.utc).isoformat()
        (d / 'a.jsonl').write_text(json.dumps(zeile) + '\n')

    def test_nur_das_veraltete_modell_wird_gemessen(self):
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[limit()])
        traum.starten('dashboard')
        self.assertEqual([a['werkzeug'] for a in self.aufrufe()], ['claude'])

    def test_messaufruf_ist_minimal(self):
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[limit()])
        traum.starten('dashboard')
        a = self.aufrufe('claude')[0]['argv']
        self.assertIn('--strict-mcp-config', a)
        self.assertEqual(a[a.index('--tools') + 1], '')
        self.assertEqual(a[a.index('--model') + 1], 'haiku')
        self.assertNotEqual(self.aufrufe('claude')[0]['cwd'], str(self.repo))

    def test_messaufruf_scheitert_ablehnung_mit_grund(self):
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[{'tue': 'kontingent'}])
        with self.assertRaisesRegex(Fehler, r'Claude: keine frische Messung, der Messaufruf ist gescheitert.*Kontingent'):
            traum.starten('dashboard')
        self.assertEqual(traum.alle(), [])

    def test_aufruf_ohne_grenzwerte_lehnt_ab(self):
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[TEXT])  # läuft durch, meldet aber nichts
        with self.assertRaisesRegex(Fehler, 'lieferte keine Grenzwerte'):
            traum.starten('dashboard')
        self.assertEqual(traum.alle(), [])

    def test_sol_scheitert_ablehnung_mit_grund(self):
        self.sol_alt()  # Claude frisch, Sol veraltet
        self.drehbuch(codex=[{'tue': 'kontingent'}])
        with self.assertRaisesRegex(Fehler, r'Sol: keine frische Messung, der Messaufruf ist gescheitert'):
            traum.starten('dashboard')
        self.assertEqual(traum.alle(), [])

    def test_grenze_nach_der_messung_erreicht(self):
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[limit(prozent=91)])
        with self.assertRaisesRegex(Fehler, 'Traumgrenze'):
            traum.starten('dashboard')
        self.assertEqual(len(self.aufrufe()), 1)

    def test_reserve_nach_der_messung_erreicht(self):
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[limit(woche=90)])
        with self.assertRaisesRegex(Fehler, 'Wochenreserve'):
            traum.starten('dashboard')

    def test_messlauf_zaehlt_nicht_als_normale_arbeit(self):
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[limit()])
        traum.starten('dashboard')
        self.assertEqual(traum._normale_arbeit(traum.aktiv()), '')
        self.assertEqual(traum._normale_arbeit(), '')
        self.assertEqual(traum.aktiv()['zaehler'], {'claude': 0, 'sol': 0})

    def test_laufender_messlauf_sperrt_den_traum_nicht(self):
        l = dict(id='m1', projekt='demo', art='messung', modell='claude', zustand='laeuft', angelegt=time.time(), cwd='/tmp', auftrag='')
        laeufe.verzeichnis('m1').mkdir(parents=True)
        laeufe.speichern(l)
        self.assertEqual(traum._normale_arbeit(), '')
        self.assertEqual(traum.startgrund(), '')

    def test_anzeige_veraltet_zeigt_den_knopf_ohne_aufruf(self):
        self.messen(alter=3 * 3600)
        self.assertEqual(traum.startgrund(), '')
        self.assertEqual(self.aufrufe(), [])

    def test_vorpruefung_lehnt_ohne_messaufruf_ab(self):
        self.messen(alter=3 * 3600)
        ablauf.anhalten('alles', 'dashboard')
        with self.assertRaisesRegex(Fehler, 'angehalten'):
            traum.starten('dashboard')
        self.assertEqual(self.aufrufe(), [])

    def test_veraltet_im_traum_misst_erst(self):
        traum.starten('dashboard')
        self.messen(alter=3 * 3600)
        self.drehbuch(claude=[limit(), TEXT])
        traum.takt()
        t = traum.aktiv()
        self.assertIsNone(t['stopp'] if t else None)
        self.assertEqual(self.aufrufe('claude')[0]['schritt'], 'text')
        self.assertEqual(laeufe.alle()[0]['art'], 'messung')

    def test_gescheiterte_messung_im_traum_wird_nicht_wiederholt(self):
        t = traum.starten('dashboard')
        self.sol_alt()
        self.drehbuch(codex=[{'tue': 'kontingent'}])
        g1 = traum._modell_grund(t, 'sol')
        g2 = traum._modell_grund(t, 'sol')
        self.assertIn('Messaufruf', g1)
        self.assertEqual(g1, g2)
        self.assertEqual(len(self.aufrufe('codex')), 1)

    def test_aufruf_ohne_grenzwerte_im_traum_wird_nicht_wiederholt(self):
        t = traum.starten('dashboard')
        self.sol_alt()
        self.drehbuch(codex=[TEXT])  # fertig, aber ohne Grenzwerte
        g1 = traum._modell_grund(t, 'sol')
        g2 = traum._modell_grund(t, 'sol')
        self.assertIn('keine Grenzwerte', g1)
        self.assertEqual(g1, g2)
        self.assertEqual(len(self.aufrufe('codex')), 1)
