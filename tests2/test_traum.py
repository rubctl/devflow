"""Traum (S5.3): Knöpfe, gemeinsame Startsperre, Fensterende, frische Messung, Grenzen je Modell, Vorrang, Halt, Reihenfolge, Bericht."""
import datetime
import io
import json
import logging
import os
from pathlib import Path
import threading
import time
from unittest import mock
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import Fehler, ablauf, eingang, ereignis, kontingent, laeufe, mcp_server, pflege, projekte, push, push_protokoll, selbstlauf, traum, web, inhaber  # noqa: E402
from devflow2.projekte import Projekt  # noqa: E402
from hilfen import Fall, git  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
HERKUNFT = {**LOGIN, 'Origin': 'http://testserver'}
logging.getLogger('httpx').setLevel(logging.WARNING)
TEXT = {'tue': 'text', 'text': 'Bericht aus dem Traum.'}
ENTWURF = """# Vorhaben Neu · Claude · 31.10.2026

**Stand:** Entwurf

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| N1 | Erste | claude | `a.py` | Test grün | |
"""


class Basis(Fall):
    def setUp(self):
        super().setUp()
        self.messen()
        self.sol()

    def messen(self, prozent=20, woche=30, frei=None, alter=0, ohne_frei=False):
        """Claude-Messung, wie sie in einem Lauflog stünde."""
        frei = frei or time.time() + 3600
        fuenf = {'utilization': prozent / 100}
        if not ohne_frei:
            fuenf['resetsAt'] = frei
        zeile = {'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {
            'five_hour': fuenf, 'seven_day': {'utilization': woche / 100, 'resetsAt': frei + 86400}}}}
        d = laeufe.verzeichnis('messung')
        d.mkdir(parents=True, exist_ok=True)
        (d / 'log.jsonl').write_text(json.dumps(zeile) + '\n')
        if alter:
            os.utime(d / 'log.jsonl', (time.time() - alter, time.time() - alter))

    def sol(self, prozent=10, woche=20):
        """Sol-Messung, wie sie in einer codex-Sitzungsdatei stünde."""
        zeile = {'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'payload': {'rate_limits': {
            'primary': {'used_percent': prozent, 'resets_at': time.time() + 7200, 'window_minutes': 300},
            'secondary': {'used_percent': woche, 'resets_at': time.time() + 86400, 'window_minutes': 10080}}}}
        d = Path(os.environ['DEVFLOW2_CODEX_SITZUNGEN'])
        d.mkdir(parents=True, exist_ok=True)
        (d / 'a.jsonl').write_text(json.dumps(zeile) + '\n')

    def traeume(self):
        return traum.alle()

    def schritte(self, t=None):
        return [(s['art'], s['zustand']) for s in (t or self.traeume()[-1])['schritte']]

    def zu_ende(self):
        """Takte wie die Hintergrundschleife, bis der Traum beendet ist."""
        def schritt():
            traum.takt()
            return not traum.aktiv()
        self.warten(schritt, text='bis Traumende')
        traum.takt()
        return self.traeume()[-1]

    def bis_lauf(self):
        """Takten, bis der Traum einen Lauf gestartet hat und dessen Modell tatsächlich läuft."""
        def da():
            traum.takt()
            t = self.traeume()[-1]
            s = next((x for x in t['schritte'] if x['zustand'] == 'laeuft'), None)
            return s and self.aufrufe() and laeufe.laden(s['lauf']).get('kind_pid')
        self.warten(da, text='bis Traumlauf')
        t = self.traeume()[-1]
        return next(x for x in t['schritte'] if x['zustand'] == 'laeuft')

    def setze(self, **werte):
        t = self.traeume()[-1]
        t.update(werte)
        traum._sichern(t)


class StartTest(Basis):
    def test_ohne_knopfdruck_nie_ein_traum(self):
        selbstlauf.schalten(True)
        eingang.anlegen(self.p, 'Idee', 'dashboard')
        for _ in range(3):
            traum.takt()
        self.assertEqual(self.traeume(), [])
        self.assertEqual(self.aufrufe(), [])
        self.assertFalse([w for w in mcp_server.ALLE if 'traum' in w or 'traeum' in w])
        self.assertNotIn('traum', json.dumps(selbstlauf.SAETZE))

    def test_starten_nur_aus_dem_dashboard(self):
        for herkunft in ('steuerung', 'nutzer'):
            with self.assertRaisesRegex(Fehler, f'nur {inhaber.nom()}'):
                traum.starten(herkunft)
        self.assertEqual(self.traeume(), [])

    def nicht_moeglich(self, muster):
        with self.assertRaisesRegex(Fehler, muster):
            traum.starten('dashboard')
        self.assertEqual(self.traeume(), [])

    def test_grenze_beim_start_erreicht(self):
        self.messen(prozent=90)
        self.nicht_moeglich('Traumgrenze')

    def test_reserve_beim_start_erreicht(self):
        self.messen(woche=85)  # 15 % verbleiben: genau die Reserve
        self.nicht_moeglich('Wochenreserve')

    def test_knapp_unter_den_grenzen_startet(self):
        self.messen(prozent=89, woche=84)
        self.assertTrue(traum.starten('dashboard'))

    def test_messung_veraltet(self):
        self.messen(alter=3 * 3600)
        self.nicht_moeglich('keine frische Messung')

    def test_messung_fehlt(self):
        (laeufe.verzeichnis('messung') / 'log.jsonl').unlink()
        self.nicht_moeglich('keine frische Messung')

    def test_fensterende_unbekannt(self):
        self.messen(ohne_frei=True)
        self.drehbuch(claude=[TEXT])  # der Messaufruf (S5.5) meldet auch kein Fensterende
        self.nicht_moeglich('lieferte keine Grenzwerte')

    def test_fenster_schon_vorbei(self):
        self.messen(frei=time.time() - 10)
        self.nicht_moeglich('keine frische Messung')

    def test_halt(self):
        ablauf.anhalten('alles', 'dashboard')
        self.nicht_moeglich('angehalten')

    def test_fensterende_wird_gespeichert(self):
        frei = int(time.time() + 1800)
        self.messen(frei=frei)
        t = traum.starten('dashboard')
        self.assertEqual(t['fenster_ende'], frei)
        self.assertEqual(traum.laden(t['id'])['fenster_ende'], frei)

    def test_nur_ein_traum_je_fenster(self):
        traum.starten('dashboard')
        traum.beenden()
        self.zu_ende()
        with self.assertRaisesRegex(Fehler, 'schon einen Traum'):
            traum.starten('dashboard')
        self.assertEqual(len(self.traeume()), 1)

    def test_grenzen_aus_devflow_toml_je_modell_strengster_wert(self):
        (self.repo / 'devflow.toml').write_text('[traum_grenze]\nclaude = 50\n[traum_reserve]\nsol = 40\n')
        andere = self.tmp / 'andere'
        andere.mkdir()
        git(andere, 'init', '-q', '-b', 'main')
        (andere / 'ROADMAP.md').write_text('# x\n')
        (andere / 'devflow.toml').write_text('[traum_grenze]\nclaude = 70\nsol = 60\n')
        projekte.anmelden(andere, 'andere')
        self.assertEqual(traum.grenzen(), {'claude': dict(grenze=50, reserve=15), 'sol': dict(grenze=60, reserve=40)})
        self.messen(prozent=50)
        self.nicht_moeglich('Traumgrenze')

    def test_ungueltige_grenzen(self):
        for zeile in ('traum_grenze = 90\n', '[traum_grenze]\nclaude = 120\n', '[traum_reserve]\ngemini = 10\n', '[traum_grenze]\nclaude = true\n'):
            (self.repo / 'devflow.toml').write_text(zeile)
            with self.assertRaisesRegex(Fehler, 'braucht je Modell'):
                Projekt('demo')


class ReihenfolgeTest(Basis):
    weitere_toml = 'stufe = "schwer"\n'

    def test_vier_traumarten_billig_vor_teuer(self):
        eingang.anlegen(self.p, 'Neue Idee', 'dashboard')
        self.drehbuch(claude=[{'tue': 'text', 'text': 'Rückblick: Vorschlag 1.'}, {'tue': 'commit', 'dateien': {'plan/neu.md': ENTWURF}},
                              {'tue': 'text', 'text': 'Code: Tests fehlen.'}],
                      codex=[{'tue': 'text', 'text': '# Zweitmeinung\n\nTragfähig.'}])
        traum.starten('dashboard')
        t = self.zu_ende()
        self.assertEqual(self.schritte(t), [('rueckblick', 'fertig'), ('pflege', 'fertig'), ('ideen', 'fertig'), ('meinung', 'fertig'), ('code', 'fertig')])
        self.assertEqual(t['grund'], traum.FERTIG)
        self.assertEqual(t['zaehler'], {'claude': 3, 'sol': 1})
        claude = self.aufrufe('claude')
        self.assertIn('Rückblick der Woche', claude[0]['argv'][-1])
        self.assertEqual(claude[0]['argv'][claude[0]['argv'].index('--model') + 1], 'sonnet')
        self.assertEqual(claude[0]['argv'][claude[0]['argv'].index('--tools') + 1], 'Read,Grep,Glob')
        self.assertNotIn('--mcp-config', claude[0]['argv'])
        self.assertEqual(claude[1]['argv'][claude[1]['argv'].index('--model') + 1], 'sonnet')
        self.assertIn('--mcp-config', claude[1]['argv'])
        self.assertIn('mcp__devflow__plan_entwurf', claude[1]['argv'][claude[1]['argv'].index('--allowedTools') + 1])
        self.assertNotIn('mcp__devflow__mergen', claude[1]['argv'][claude[1]['argv'].index('--allowedTools') + 1])
        self.assertEqual(claude[2]['argv'][claude[2]['argv'].index('--model') + 1], 'opus')  # Stufe des Projekts
        self.assertEqual(claude[2]['argv'][claude[2]['argv'].index('--tools') + 1], 'Read,Grep,Glob')
        self.assertEqual(traum.plaene_von(t), ['plan/neu.md'])
        self.assertTrue((self.repo / 'berichte/neu-meinung-sol.md').exists())
        self.assertEqual(ereignis.lesen()[0]['art'], 'neue-idee')
        self.assertEqual(len(ereignis.lesen()), 1)  # kein Lauf des Traums weckt die Steuerung

    def test_ohne_ideen_kein_planungslauf(self):
        self.drehbuch(claude=[TEXT, TEXT])
        traum.starten('dashboard')
        self.assertEqual(self.schritte(self.zu_ende()), [('rueckblick', 'fertig'), ('pflege', 'fertig'), ('code', 'fertig')])

    def test_pflegefunde_im_eingang_sind_keine_ideen(self):
        eingang.repo.auf_main(self.p, lambda x: {eingang.DATEI: eingang.anhaengen('', 1, ['Lücke'], 'pflege')}, 'pflege', pfade=(eingang.DATEI,))
        self.assertFalse(traum._hat_ideen('demo'))

    def test_pflegepruefung_im_traum_loest_keine_update_kette_aus(self):
        for lauf, aufrufe in (({'traum': 'x'}, 0), ({}, 1)):
            with mock.patch('devflow2.pflegeplan.nach_pruefung') as nach:
                pflege.ausfuehren({'id': 'lauf', 'projekt': 'demo', **lauf}, io.StringIO())
            self.assertEqual(nach.call_count, aufrufe)

    def test_ohne_sol_kontingent_entfaellt_nur_die_zweitmeinung(self):
        eingang.anlegen(self.p, 'Neue Idee', 'dashboard')
        self.sol(prozent=95)
        self.drehbuch(claude=[TEXT, {'tue': 'commit', 'dateien': {'plan/neu.md': ENTWURF}}, TEXT])
        traum.starten('dashboard')
        t = self.zu_ende()
        self.assertIn(('meinung', 'ausgelassen'), self.schritte(t))
        self.assertIn(('code', 'fertig'), self.schritte(t))
        self.assertEqual(t['zaehler'], {'claude': 3, 'sol': 0})
        self.assertEqual(self.aufrufe('codex'), [])

    def test_gescheiterter_schritt_blockiert_den_traum_nicht(self):
        self.drehbuch(claude=[{'tue': 'kaputt'}, {'tue': 'kaputt'}, TEXT])
        traum.starten('dashboard')
        t = self.zu_ende()
        self.assertEqual(self.schritte(t), [('rueckblick', 'fehler'), ('pflege', 'fertig'), ('code', 'fertig')])
        self.assertEqual(t['schritte'][0]['versuche'], 2)
        self.assertEqual(t['zaehler']['claude'], 3)


class GrenzenTest(Basis):
    def test_grenze_nach_einem_schritt_erreicht(self):
        frei = time.time() + 3600
        self.drehbuch(claude=[{**TEXT, 'limit': {'5h': 92, 'woche': 30, 'frei': frei}}, TEXT])
        traum.starten('dashboard')
        t = self.zu_ende()
        self.assertEqual(self.schritte(t)[0], ('rueckblick', 'fertig'))
        self.assertNotIn('code', [a for a, _ in self.schritte(t)])
        self.assertIn('Traumgrenze', t['grund'])
        self.assertEqual(len(self.aufrufe('claude')), 1)

    def test_reserve_vor_jedem_start_geprueft(self):
        frei = time.time() + 3600
        self.drehbuch(claude=[{**TEXT, 'limit': {'5h': 30, 'woche': 90, 'frei': frei}}, TEXT])
        traum.starten('dashboard')
        t = self.zu_ende()
        self.assertIn('Wochenreserve', t['grund'])
        self.assertEqual(len(self.aufrufe('claude')), 1)

    def test_reserve_sol_gilt_fuer_sol(self):
        self.sol(woche=90)
        self.assertIn('Wochenreserve', kontingent.traum_pruefen('sol', **traum.grenzen()['sol'])[0])
        self.assertEqual(kontingent.traum_pruefen('claude', **traum.grenzen()['claude'])[0], '')

    def test_messung_veraltet_im_traum(self):
        t = traum.starten('dashboard')
        self.messen(alter=3 * 3600)
        traum.takt()
        t = self.zu_ende()
        self.assertIn('keine frische Messung', t['grund'])
        self.assertEqual([a['schritt'] for a in self.aufrufe('claude')], ['leer'])  # nur der (gescheiterte) Messaufruf, kein Traumlauf
        self.assertEqual({l['art'] for l in laeufe.alle()}, {'messung'})

    def test_tor_im_laufprozess_prueft_und_zaehlt(self):
        t = traum.starten('dashboard')
        lauf = dict(traum=t['id'], modell='claude')
        traum.tor(lauf)
        traum.tor(lauf)
        self.assertEqual(traum.laden(t['id'])['zaehler'], {'claude': 2, 'sol': 0})
        self.messen(prozent=95)
        with self.assertRaisesRegex(laeufe.TraumEnde, 'Traumgrenze'):
            traum.tor(lauf)
        self.assertEqual(traum.laden(t['id'])['zaehler']['claude'], 2)

    def test_wiederholung_passiert_das_tor(self):
        self.drehbuch(claude=[{'tue': 'kaputt'}, TEXT])
        traum.starten('dashboard')
        self.bis_lauf()
        self.warten(lambda: laeufe.alle()[0]['zustand'] == 'fehler', text='bis Fehler')
        self.messen(prozent=95)
        t = self.zu_ende()
        self.assertIn('Traumgrenze', t['grund'])
        self.assertEqual(len(self.aufrufe('claude')), 1)

    def test_wiederholung_zaehlt(self):
        self.drehbuch(claude=[{'tue': 'kaputt'}, TEXT, TEXT])
        traum.starten('dashboard')
        t = self.zu_ende()
        self.assertEqual(self.schritte(t), [('rueckblick', 'fertig'), ('pflege', 'fertig'), ('code', 'fertig')])
        self.assertEqual(t['zaehler']['claude'], 3)
        self.assertEqual(t['schritte'][0]['versuche'], 2)


class FensterTest(Basis):
    def test_fensterwechsel_waehrend_eines_schritts(self):
        self.messen(frei=time.time() + 3)
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}, TEXT])
        t = traum.starten('dashboard')
        s = self.bis_lauf()
        self.assertEqual(laeufe.laden(s['lauf'])['bis'], t['fenster_ende'])
        self.warten(lambda: laeufe.laden(s['lauf'])['zustand'] == laeufe.TRAUM_ENDE, text='bis Laufzeitgrenze')  # ohne Takt: der Lauf selbst
        self.assertLess(laeufe.laden(s['lauf'])['ende'], t['fenster_ende'] + 5)
        t = self.zu_ende()
        self.assertEqual(t['grund'], traum.FENSTER)
        self.assertEqual(self.schritte(t), [('rueckblick', 'abgebrochen')])
        self.assertEqual(len(self.aufrufe('claude')), 1)

    def test_takt_beendet_den_lauf_am_fensterende(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        traum.starten('dashboard')
        s = self.bis_lauf()
        self.setze(fenster_ende=time.time() - 1)
        t = self.zu_ende()
        self.assertEqual(t['grund'], traum.FENSTER)
        self.assertEqual(laeufe.laden(s['lauf'])['zustand'], laeufe.TRAUM_ENDE)

    def test_pflegepruefung_endet_ohne_takt_am_fensterende(self):
        spur, kind = self.tmp / 'scanner.spur', self.tmp / 'scanner.kind'
        scanner = self.tmp / 'scanner'
        scanner.write_text(f'#!/bin/sh\necho los >> {spur}\nsleep 60 &\necho $! > {kind}\nwait\n')
        scanner.chmod(0o755)
        (self.repo / 'uv.lock').write_text('')
        with open(self.repo / 'devflow.toml', 'a') as f:
            f.write('\n[pflege]\npruefungen = ["osv-scanner", "osv-scanner"]\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'pflege')
        self.messen(frei=time.time() + 5)
        t = traum.starten('dashboard')
        with mock.patch.dict(os.environ, DEVFLOW2_PFLEGE_OSV_SCANNER=str(scanner)):
            lid = traum._lauf_starten(t, dict(art='pflege', projekt='demo'))
        self.warten(lambda: laeufe.laden(lid)['zustand'] == laeufe.TRAUM_ENDE, text='bis Laufzeitgrenze')  # ohne Takt: der Lauf selbst
        l = laeufe.laden(lid)
        self.assertEqual(l['grund'], traum.FENSTER)
        self.assertLess(l['ende'], t['fenster_ende'] + 5)
        self.assertEqual(spur.read_text(), 'los\n')  # kein weiterer Prüfschritt
        self.assertFalse(Path(f'/proc/{kind.read_text().strip()}').exists())
        self.assertIsNone(pflege.letzter('demo'))
        self.assertNotIn('pflege: Bericht', git(self.repo, 'log', '--format=%s'))

    def test_pflegerahmen_grenzen(self):
        l = dict(id='demo-traum-pflege', projekt='demo', art='pflege', modell='intern', cwd=str(self.repo), zustand='fertig',
                 angelegt=time.time(), traum='x', bis=time.time() + 60)
        laeufe.verzeichnis(l['id']).mkdir(parents=True)
        laeufe.speichern(l)
        rahmen = pflege.traum_rahmen(l)
        self.assertEqual(rahmen.run(['sh', '-c', 'echo a; echo b >&2; exit 3'], str(self.repo), dict(os.environ)), (3, 'a\n', 'b\n'))
        with mock.patch.object(pflege, 'ZEIT', 0.3), self.assertRaisesRegex(Fehler, 'Zeitlimit'):  # hängt ein Werkzeug, endet nur diese Prüfung
            pflege.traum_rahmen(l).run(['sleep', '30'], str(self.repo), dict(os.environ))
        with self.assertRaisesRegex(Fehler, 'nicht startbar'):
            rahmen.run([str(self.tmp / 'fehlt')], str(self.repo), dict(os.environ))
        l['bis'] = time.time() - 1
        with self.assertRaisesRegex(laeufe.TraumEnde, traum.FENSTER):
            rahmen.run(['true'], str(self.repo), dict(os.environ))
        with self.assertRaisesRegex(laeufe.TraumEnde, traum.FENSTER):  # auch die Schwere fragt nicht weiter
            rahmen.schwere(['CVE-1'])

    def test_kontingentpause_endet_ohne_weiterlaufen(self):
        self.drehbuch(claude=[{'tue': 'kontingent', 'in': 1}, TEXT])
        traum.starten('dashboard')
        t = self.zu_ende()
        s = t['schritte'][0]
        self.assertEqual(s['zustand'], 'abgebrochen')
        self.assertIn('Kontingent', t['grund'])
        self.assertEqual(laeufe.laden(s['lauf'])['zustand'], laeufe.TRAUM_ENDE)
        time.sleep(1.2)
        self.assertEqual(len(self.aufrufe('claude')), 1)
        self.assertEqual(ereignis.lesen(), [])

    def test_gemessenes_neues_fenster_beendet(self):
        t = traum.starten('dashboard')
        self.messen(frei=t['fenster_ende'] + 3 * 3600)
        t = self.zu_ende()
        self.assertEqual(t['grund'], traum.FENSTER)
        self.assertEqual(self.aufrufe('claude'), [])

    def toter_lauf(self, t, zustand='laeuft'):
        """Stand nach einem Neustart: Der Laufprozess ist weg, Zustand und Schritt stehen noch auf der Platte."""
        lauf = dict(id='demo-traum-tot', projekt='demo', art='traum', modell='claude', cwd=str(self.repo), auftrag='', zustand=zustand,
                    angelegt=time.time() - 60, traum=t['id'], bis=t['fenster_ende'], session='sess-12345678')
        laeufe.verzeichnis(lauf['id']).mkdir(parents=True)
        laeufe.speichern(lauf)
        t['schritte'].append(dict(art='rueckblick', projekt='demo', zustand='laeuft', lauf=lauf['id'], laeufe=[lauf['id']], versuche=1))
        t['zaehler']['claude'] = 1
        traum._sichern(t)
        return lauf

    def test_neustart_nach_dem_fensterende(self):
        t = traum.starten('dashboard')
        self.toter_lauf(t)
        self.setze(fenster_ende=time.time() - 1)
        self.drehbuch(claude=[TEXT])
        t = self.zu_ende()
        self.assertEqual(t['grund'], traum.FENSTER)
        self.assertEqual(self.schritte(t), [('rueckblick', 'fehler')])  # keine angekündigte Wiederholung im beendeten Traum
        self.assertEqual(self.aufrufe('claude'), [])

    def test_neustart_im_fenster_zaehler_bleiben(self):
        t = traum.starten('dashboard')
        self.toter_lauf(t)
        self.assertIsNone(traum.starten('dashboard'))
        self.assertEqual(len(self.traeume()), 1)
        self.drehbuch(claude=[TEXT, TEXT])
        t = self.zu_ende()
        self.assertEqual(self.schritte(t), [('rueckblick', 'fertig'), ('pflege', 'fertig'), ('code', 'fertig')])
        self.assertEqual(t['zaehler']['claude'], 3)  # der tote Lauf zählt weiter mit

    def test_traumlauf_laesst_sich_nicht_fortsetzen(self):
        t = traum.starten('dashboard')
        lauf = self.toter_lauf(t, zustand=laeufe.TRAUM_ENDE)
        with self.assertRaisesRegex(Fehler, 'nicht fortgesetzt'):
            ablauf.fortsetzen(lauf['id'], 'dashboard')
        with self.assertRaisesRegex(Exception, 'nicht fortgesetzt'):
            mcp_server.fortsetzen(lauf['id'])
        with self.assertRaisesRegex(Fehler, 'nicht fortgesetzt'):
            laeufe.fortsetzen(lauf['id'])

    def test_tor_nach_dem_fensterende(self):
        t = traum.starten('dashboard')
        self.setze(fenster_ende=time.time() - 1)
        with self.assertRaisesRegex(laeufe.TraumEnde, traum.FENSTER):
            traum.tor(dict(traum=t['id'], modell='claude'))
        with self.assertRaisesRegex(laeufe.TraumEnde, traum.FENSTER):
            traum.tor(dict(traum=t['id'], modell='intern'))


class SteuerungTest(Basis):
    def test_traum_sperrt_den_selbstlauf_bis_zum_ende(self):
        selbstlauf.schalten(True)
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}, {'tue': 'text', 'text': 'Nichts zu tun.'}])
        traum.starten('dashboard')
        self.bis_lauf()
        ereignis.melden('neue-idee', 'demo', 'x')
        self.assertIsNone(selbstlauf.pruefen())
        self.assertTrue(selbstlauf.stand()['traum'])
        ablauf.anhalten('alles', 'dashboard')
        self.zu_ende()
        ablauf.fortsetzen('alles', 'dashboard')
        self.assertEqual(selbstlauf.pruefen()['art'], 'steuern')

    def test_steuerlauf_aktiv_kein_traum(self):
        selbstlauf.schalten(True)
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        ereignis.melden('neue-idee', 'demo', 'x')
        selbstlauf.pruefen()
        with self.assertRaisesRegex(Fehler, 'Vorrang'):
            traum.starten('dashboard')
        self.assertEqual(self.traeume(), [])

    def test_normaler_lauf_aktiv_kein_traum(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        ablauf.entwickeln(self.p, 'A1')
        with self.assertRaisesRegex(Fehler, 'Vorrang'):
            traum.starten('dashboard')

    def test_ereignisse_fuer_den_selbstlauf_gehen_vor(self):
        selbstlauf.schalten(True)
        ereignis.melden('antwort', 'demo', 'F1')
        with self.assertRaisesRegex(Fehler, 'Vorrang'):
            traum.starten('dashboard')

    def test_ereignisse_ohne_selbstlauf_sperren_nicht(self):
        ereignis.melden('antwort', 'demo', 'F1')
        self.assertTrue(traum.starten('dashboard'))
        self.assertEqual(traum._normale_arbeit(self.traeume()[-1]), '')  # älter als der Traum

    def test_verbrauchtes_budget_sperrt_nicht(self):
        selbstlauf.schalten(True)
        (self.repo / 'devflow.toml').write_text('selbstlauf_budget = 0\n')
        ereignis.melden('antwort', 'demo', 'F1')
        self.assertTrue(traum.starten('dashboard'))

    def test_gleichzeitig_nur_eine_steuerung(self):
        selbstlauf.schalten(True)
        for runde in range(5):
            self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
            ereignis.melden('neue-idee', 'demo', str(runde))
            tor, aus = threading.Barrier(2), {}

            def a():
                tor.wait()
                aus['lauf'] = selbstlauf.pruefen()

            def b():
                tor.wait()
                try:
                    aus['traum'] = traum.starten('dashboard')
                except Fehler:
                    aus['traum'] = None
            ts = [threading.Thread(target=f) for f in (a, b)]
            [x.start() for x in ts]
            [x.join() for x in ts]
            self.assertEqual(sum(bool(x) for x in aus.values()), 1, aus)
            ablauf.anhalten('alles', 'dashboard')
            self.ruhig()
            traum.takt()
            ablauf.fortsetzen('alles', 'dashboard')
            ereignis.entfernen(ereignis.lesen())
            for f in projekte.zustand().glob('traeume/*.json'):
                f.unlink()

    def vorrang(self, ausloesen):
        """`ausloesen` passiert während des ersten Schritts: Der läuft zu Ende, danach kommt keiner mehr."""
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 1.5}, TEXT, TEXT])
        traum.starten('dashboard')
        self.bis_lauf()
        ausloesen()
        t = self.zu_ende()
        self.assertEqual(self.schritte(t), [('rueckblick', 'fertig')])
        self.assertIn('Vorrang', t['grund'])

    def test_abnahme_beendet_nach_dem_schritt(self):
        self.vorrang(lambda: ereignis.melden('plan-abgenommen', 'demo', 'plan/wetter.md'))

    def test_antwort_beendet_nach_dem_schritt(self):
        nr = ablauf.frage_stellen(self.p, 'Was nun?')['frage']
        self.vorrang(lambda: ablauf.antwort_eintragen(self.p, nr, 'So.', 'dashboard'))

    def test_nacharbeit_beendet_nach_dem_schritt(self):
        self.vorrang(lambda: laeufe.starten(self.p, 'nacharbeit', 'shell', self.repo, 'true', aufgabe='A1'))

    def test_fortsetzen_beendet_nach_dem_schritt(self):
        l = laeufe.starten(self.p, 'deploy', 'shell', self.repo, 'sleep 30')
        self.warten(lambda: laeufe.laden(l['id']).get('kind_pid'), text='bis Shell')
        ablauf.anhalten(l['id'], 'dashboard')

        def fort():
            ablauf.fortsetzen(l['id'], 'dashboard')
            self.warten(lambda: laeufe.laden(l['id'])['zustand'] in laeufe.AKTIV, text='bis wieder aktiv')
        self.vorrang(fort)

    def test_traum_beenden_laesst_den_schritt_zu_ende_laufen(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 1.5}, TEXT])
        traum.starten('dashboard')
        s = self.bis_lauf()
        self.assertTrue(traum.beenden())
        self.assertFalse(traum.beenden())
        traum.takt()
        self.assertIn(laeufe.laden(s['lauf'])['zustand'], laeufe.AKTIV)
        t = self.zu_ende()
        self.assertEqual(self.schritte(t), [('rueckblick', 'fertig')])
        self.assertEqual(t['grund'], traum.inhaber_beendet())
        self.assertEqual(len(self.aufrufe('claude')), 1)

    def test_traum_beenden_sperrt_auch_einen_schon_gestarteten_lauf(self):
        t = traum.starten('dashboard')
        traum.beenden()
        with self.assertRaises(laeufe.TraumEnde):
            traum.tor(dict(traum=t['id'], modell='claude'))

    def test_alles_anhalten_erfasst_traum_und_lauf(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}, TEXT])
        traum.starten('dashboard')
        s = self.bis_lauf()
        ablauf.anhalten('alles', 'dashboard')
        self.assertEqual(laeufe.laden(s['lauf'])['zustand'], laeufe.TRAUM_ENDE)
        ablauf.fortsetzen('alles', 'dashboard')  # gleich wieder aufgehoben: Der Traum geht trotzdem nicht weiter
        t = self.zu_ende()
        self.assertEqual(t['grund'], traum.HALT)
        self.assertEqual(self.schritte(t), [('rueckblick', 'abgebrochen')])
        self.assertEqual(len(self.aufrufe('claude')), 1)

    def test_steuerung_scheitert_gibt_die_sperre_frei(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        traum.starten('dashboard')
        s = self.bis_lauf()
        with mock.patch.object(traum, '_stoppgrund', side_effect=KeyError('kaputt')), self.assertRaises(KeyError):
            traum.takt()
        self.assertIsNone(traum.aktiv())
        self.assertEqual(laeufe.laden(s['lauf'])['zustand'], laeufe.TRAUM_ENDE)
        self.assertIn('Steuerung gescheitert', self.traeume()[-1]['grund'])


class BerichtTest(Basis):
    def test_bericht_im_repo_und_hinweis_einmal(self):
        push.pruefen()
        eingang.anlegen(self.p, 'Neue Idee', 'dashboard')
        self.sol(prozent=95)
        self.drehbuch(claude=[{'tue': 'text', 'text': 'Vorschlag: weniger Nacharbeit.'}, {'tue': 'commit', 'dateien': {'plan/neu.md': ENTWURF}},
                              {'tue': 'text', 'text': 'Tests fehlen in a.py.'}])
        traum.starten('dashboard')
        self.assertEqual(traum.hinweise(), set())
        t = self.zu_ende()
        self.assertEqual(traum.bilanz(t), 'Traum beendet: 0 Vorschläge, 1 Plan')
        datei = self.repo / t['bericht']['datei']
        self.assertEqual(t['bericht']['datei'], f'berichte/traum-{t["id"]}.md')
        text = datei.read_text()
        for teil in ('Vorschlag: weniger Nacharbeit.', 'Tests fehlen in a.py.', 'Claude 3, Sol 0', 'Alle Schritte erledigt', 'Rückblick der Woche', 'plan/neu.md'):
            self.assertIn(teil, text)
        self.assertEqual(git(self.repo, 'status', '--porcelain', '--', 'berichte'), '')
        with mock.patch.object(push, '_verteilen') as verteilen:
            self.assertEqual(push.pruefen(), [('traum', 1)])
            self.assertEqual(push.pruefen(), [])
        verteilen.assert_called_once_with('traum', 1, None, zahlen=[0, 1])

    def test_commit_scheitert_seite_und_hinweis_bleiben(self):
        self.drehbuch(claude=[TEXT, TEXT])
        traum.starten('dashboard')
        with mock.patch.object(traum.repo, 'auf_main', side_effect=Fehler('main ist nicht sauber')):
            t = self.zu_ende()
        self.assertIn('nicht sauber', t['bericht']['fehler'])
        self.assertEqual(len(traum.hinweise()), 1)

    def test_nutzlast_traegt_nur_zahlen(self):
        wert = push_protokoll.nutzlast(1, 'traum', [3, 1])
        self.assertEqual(wert, {'count': 1, 'kind': 'traum', 'url': '/', 'zahlen': [3, 1]})
        self.assertEqual(push_protokoll.nutzlast(1, 'frage', [3, 1]), {'count': 1, 'kind': 'frage', 'url': '/'})
        with self.assertRaises(push_protokoll.PushFehler):
            push_protokoll.nutzlast(1, 'traum', ['drei', 1])

    def test_traumlauf_fehler_meldet_sich_nicht_einzeln(self):
        self.drehbuch(claude=[{'tue': 'kaputt'}, {'tue': 'kaputt'}, TEXT])
        traum.starten('dashboard')
        self.zu_ende()
        self.assertFalse([k for k in push.push_ereignisse.ereignisse()[0] if k.startswith('fehler:')])

    def test_lage_nennt_den_traum(self):
        self.assertIsNone(ablauf.lage()['traum'])
        t = traum.starten('dashboard')
        self.assertEqual(ablauf.lage()['traum']['id'], t['id'])


class WebTest(Basis):
    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(login='inhaber@github'), raise_server_exceptions=True, follow_redirects=False)

    def get(self, pfad):
        return self.client.get(pfad, headers=LOGIN)

    def post(self, pfad):
        return self.client.post(pfad, headers=HERKUNFT)

    def test_knopf_startet_doppelklick_startet_keinen_zweiten(self):
        self.assertIn('Träumen', self.get('/').text)
        r = self.post('/traum/starten')
        self.assertEqual((r.status_code, r.headers['location']), (303, '/traum?meldung=traum-gestartet'))
        r = self.post('/traum/starten')
        self.assertEqual(r.headers['location'], '/traum?meldung=traum-laeuft')
        self.assertEqual(len(self.traeume()), 1)
        seite = self.get('/traum?meldung=traum-gestartet').text
        self.assertIn('Traum gestartet', seite)
        self.assertIn('Traum beenden', seite)
        self.assertIn('Traum läuft', self.get('/').text)

    def test_ohne_herkunft_kein_traum(self):
        self.assertEqual(self.client.post('/traum/starten', headers=LOGIN).status_code, 403)
        self.assertEqual(self.get('/traum/starten').status_code, 405)
        self.assertEqual(self.traeume(), [])

    def test_kein_start_zeigt_den_grund(self):
        self.messen(prozent=95)
        seite = self.get('/traum').text
        self.assertIn('Traumgrenze', seite)
        self.assertNotIn('action="/traum/starten"', seite)
        self.assertEqual(self.post('/traum/starten').status_code, 409)

    def test_beenden_wiederholbar(self):
        self.assertEqual(self.post('/traum/beenden').headers['location'], '/traum?meldung=traum-schon-beendet')
        self.post('/traum/starten')
        self.assertEqual(self.post('/traum/beenden').headers['location'], '/traum?meldung=traum-beendet')
        self.assertEqual(self.post('/traum/beenden').headers['location'], '/traum?meldung=traum-schon-beendet')
        self.assertIn('Traum endet', self.get('/').text)
        self.assertIsNone(traum.takt())
        self.assertIn(f'{inhaber.von_gross()} beendet', self.get('/traum').text)

    def test_seite_zeigt_schritte_und_bericht(self):
        self.drehbuch(claude=[TEXT, TEXT])
        self.post('/traum/starten')
        t = self.zu_ende()
        seite = self.get('/traum').text
        self.assertIn('Rückblick der Woche', seite)
        self.assertIn('Traum beendet: 0 Vorschläge, 0 Pläne', seite)
        self.assertIn(f'traum-{t["id"]}.md', seite)
        lauf = laeufe.laden(t['schritte'][0]['lauf'])
        self.assertIn(f'/lauf/{lauf["id"]}', seite)

    def test_laufseite_ohne_fortsetzen(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        traum.starten('dashboard')
        s = self.bis_lauf()
        ablauf.anhalten('alles', 'dashboard')
        ablauf.fortsetzen('alles', 'dashboard')
        seite = self.get(f'/lauf/{s["lauf"]}').text
        self.assertIn('Traum beendet', seite)
        self.assertNotIn('/fortsetzen', seite.replace('/alles/fortsetzen', ''))
