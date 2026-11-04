"""S7.2: Halt beim Laufstart. Start und Anhalten ändern `lauf.json` unter derselben Sperre."""
import threading
import time
from unittest import mock

from devflow2 import ablauf, laeufe
from hilfen import Fall


class HaltStartTest(Fall):
    def setUp(self):
        super().setUp()
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        with mock.patch.object(laeufe, '_spawn'):  # Der Lauf wird in diesen Tests im Testprozess ausgeführt
            self.lauf = laeufe.starten(self.p, 'entwickeln', 'claude', self.repo, 'Tu etwas', aufgabe='A1')
        self.lid = self.lauf['id']
        self.addCleanup(laeufe.HAKEN.clear)

    def zustand(self):
        return laeufe.laden(self.lid)['zustand']

    def keine_arbeit(self):
        self.assertEqual(self.aufrufe(), [])
        self.assertFalse((laeufe.verzeichnis(self.lid) / 'log.jsonl').exists())
        self.assertIsNone(laeufe.laden(self.lid).get('pid'))

    def test_angehalten_vor_dem_start_bleibt_angehalten(self):
        ablauf.anhalten(self.lid, 'dashboard')
        laeufe.ausfuehren(self.lid)
        self.assertEqual(self.zustand(), 'angehalten')
        self.assertEqual(laeufe.laden(self.lid)['halt_herkunft'], 'dashboard')
        self.keine_arbeit()

    def test_anhalten_zwischen_haken_und_start(self):
        laeufe.HAKEN['vor_start'] = lambda: ablauf.anhalten(self.lid)
        laeufe.ausfuehren(self.lid)
        self.assertEqual(self.zustand(), 'angehalten')
        self.keine_arbeit()

    def test_globaler_halt_beim_start(self):
        ablauf.anhalten('alles', 'dashboard')
        laeufe.speichern(dict(laeufe.laden(self.lid), zustand='wartet', halt_herkunft=None))  # Lauf entstand nach dem Halt
        laeufe.ausfuehren(self.lid)
        lauf = laeufe.laden(self.lid)
        self.assertEqual((lauf['zustand'], lauf['halt_herkunft']), ('angehalten', 'dashboard'))
        self.keine_arbeit()

    def test_halt_zwischen_laden_und_speichern(self):
        """Der Halt wird genau zwischen Laden und Speichern im Start angefordert: Der Lauf bleibt angehalten und beginnt
        keine Modellarbeit, auch nicht für einen Augenblick."""
        halter = []

        def haken():
            halter.append(threading.Thread(target=lambda: ablauf.anhalten(self.lid, 'dashboard')))
            halter[0].start()
            ende = time.monotonic() + 10
            while not laeufe._halt_anfrage(self.lid).exists() and time.monotonic() < ende:  # Anfrage ist da, die Sperre noch belegt
                time.sleep(0.01)
            self.assertTrue(laeufe._halt_anfrage(self.lid).exists())

        laeufe.HAKEN['start_geladen'] = haken
        laeufe.ausfuehren(self.lid)
        halter[0].join(30)
        self.assertFalse(halter[0].is_alive())
        lauf = laeufe.laden(self.lid)
        self.assertEqual((lauf['zustand'], lauf['halt_herkunft']), ('angehalten', 'dashboard'))
        self.keine_arbeit()
        self.assertFalse(laeufe._halt_anfrage(self.lid).exists())

    def test_anfrage_nach_dem_eintragen_haelt_den_start_auf(self):
        """Kommt die Anfrage nach dem Laden, aber vor dem Ende des Eintragens, beginnt der Start keine Arbeit."""
        laeufe.HAKEN['start_eingetragen'] = lambda: laeufe._halt_anfrage(self.lid).write_text('steuerung')
        self.assertIsNone(laeufe._start_eintragen(self.lid))
        lauf = laeufe.laden(self.lid)
        self.assertEqual((lauf['zustand'], lauf['halt_herkunft'], lauf['pid']), ('angehalten', 'steuerung', None))

    def test_start_waehrend_des_anhaltens_beginnt_nichts(self):
        """Der Start kommt nach dem Beenden, aber vor dem Setzen des Halts: Er sieht die Anfrage und beginnt nichts."""
        ergebnis = []

        def haken():
            laeufe.HAKEN.clear()
            ergebnis.append(laeufe._start_eintragen(self.lid))

        laeufe.HAKEN['anhalten_beendet'] = haken
        ablauf.anhalten(self.lid, 'steuerung')
        self.assertEqual(ergebnis, [None])
        self.assertEqual(self.zustand(), 'angehalten')
        self.keine_arbeit()

    def test_traumlauf_endet_statt_anzuhalten(self):
        laeufe.speichern(dict(laeufe.laden(self.lid), traum='t1'))
        ablauf.anhalten('alles', 'dashboard')
        laeufe.speichern(dict(laeufe.laden(self.lid), zustand='wartet'))
        laeufe.ausfuehren(self.lid)
        self.assertEqual(self.zustand(), laeufe.TRAUM_ENDE)
        self.keine_arbeit()
