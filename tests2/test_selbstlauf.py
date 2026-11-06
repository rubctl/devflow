"""Selbstlauf (S3.5): Ereignisse, Steuerlauf mit `claude`-Attrappe, Budget, Schalter, Halt, Auftrag, Dashboard."""
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import unittest
from unittest import mock
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import Fehler, ablauf, auftraege, ereignis, laeufe, plaene, projekte, push, selbstlauf, web, inhaber  # noqa: E402
from devflow2.projekte import Projekt  # noqa: E402
from hilfen import Fall, git  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
HERKUNFT = {**LOGIN, 'Origin': 'http://testserver'}
logging.getLogger('httpx').setLevel(logging.WARNING)
FERTIG = {'tue': 'text', 'text': 'Nichts zu tun.'}
ENTWURF = """# Vorhaben Neu · Claude · 30.10.2026

**Stand:** Entwurf

Nach der Abnahme durch den Inhaber wird diese Zeile genau so ersetzt: `**Stand:** abgenommen vom Inhaber am TT.MM.JJJJ`.

## Kurz

- Test.

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| N1 | Erste | claude | `a.py` | Test grün | |
"""


class Basis(Fall):
    deploy = 'true'

    def setUp(self):
        super().setUp()
        selbstlauf.schalten(True)

    def steuerlaeufe(self):
        return [l for l in laeufe.alle() if l['art'] == 'steuern']

    def melden(self, n=1, art='neue-idee'):
        for i in range(n):
            ereignis.melden(art, 'demo', f'bezug-{i}')

    def laufende(self):
        """Ein echter Lauf endet (Deploy-Attrappe) und meldet sein Ereignis."""
        l = ablauf.deployen(self.p)
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fertig' and ereignis.lesen(), text='bis Laufende')
        return l


class SteuerlaufTest(Basis):
    def test_laufende_startet_genau_einen_steuerlauf(self):
        self.drehbuch(claude=[FERTIG])
        l = self.laufende()
        self.assertEqual([e['art'] for e in ereignis.lesen()], ['lauf-ende'])
        lauf = selbstlauf.pruefen()
        self.assertEqual((lauf['art'], lauf['modell'], lauf['cwd']), ('steuern', 'claude', str(self.repo)))
        self.assertEqual(ereignis.lesen(), [])
        self.assertIsNone(selbstlauf.pruefen())
        self.ruhig()
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual(len(self.aufrufe('claude')), 1)
        self.assertEqual(laeufe.laden(lauf['id'])['zustand'], 'fertig')
        self.assertIn(l['id'], laeufe.laden(lauf['id'])['auftrag'])

    def test_drei_ereignisse_waehrenddessen_ergeben_genau_einen_weiteren(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 1.5}, FERTIG, FERTIG])
        self.melden()
        erster = selbstlauf.pruefen()
        self.melden(3)
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual(len(ereignis.lesen()), 3)
        self.ruhig()
        zweiter = selbstlauf.pruefen()
        self.assertNotEqual(zweiter['id'], erster['id'])
        self.assertEqual(len(zweiter['ereignisse']), 3)
        self.assertIsNone(selbstlauf.pruefen())
        self.ruhig()
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual(len(self.steuerlaeufe()), 2)
        self.assertEqual(len(self.aufrufe('claude')), 2)

    def test_steuerlauf_weckt_sich_nicht_selbst(self):
        self.drehbuch(claude=[FERTIG])
        self.melden()
        selbstlauf.pruefen()
        self.ruhig()
        self.assertEqual(ereignis.lesen(), [])

    def test_fehlgeschlagener_steuerlauf_zaehlt_und_weckt_nicht(self):
        self.drehbuch(claude=[])  # Attrappe endet mit Exit 3
        self.melden()
        lauf = selbstlauf.pruefen()
        self.ruhig()
        self.assertEqual(laeufe.laden(lauf['id'])['zustand'], 'fehler')
        self.assertEqual(ereignis.lesen(), [])
        self.assertEqual(selbstlauf.stand()['heute'], 1)

    def test_aufruf_mit_mcp_und_begrenzten_werkzeugen(self):
        self.drehbuch(claude=[FERTIG])
        self.melden(art='lauf-ende')
        lauf = selbstlauf.pruefen()
        self.ruhig()
        argv = self.aufrufe('claude')[0]['argv']
        wert = lambda name: argv[argv.index(name) + 1]  # noqa: E731
        self.assertEqual((wert('--model'), wert('--effort')), ('sonnet', 'medium'))
        self.assertIn('--strict-mcp-config', argv)
        self.assertEqual(wert('--tools'), 'Read,Grep,Glob')
        erlaubt = wert('--allowedTools').split(',')
        self.assertIn('mcp__devflow__entwickeln', erlaubt)
        for verboten in ('antwort_eintragen', 'status_setzen', 'projekt_anmelden', 'anhalten'):
            self.assertNotIn(f'mcp__devflow__{verboten}', erlaubt)
        self.assertEqual(argv[-1], lauf['auftrag'])
        self.assertGreater(argv.index('--output-format'), argv.index('--allowedTools'))  # Listenoption nimmt den Prompt nicht mit
        server = json.loads(Path(wert('--mcp-config')).read_text())['mcpServers']['devflow']
        self.assertEqual(server['args'], ['-B', '-m', 'devflow2', 'mcp', 'steuerung'])
        self.assertEqual(server['env']['DEVFLOW2_STATE'], os.environ['DEVFLOW2_STATE'])

    def test_steuerlauf_wartet_nicht_auf_einen_modellplatz(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 30}, FERTIG])
        with mock.patch.dict(os.environ, DEVFLOW2_MAX='1'):
            arbeit = laeufe.starten(self.p, 'meinung', 'claude', self.repo, 'lange Arbeit', review_worktree='', datei='plan/wetter.md', reviewer='claude')
            self.warten(lambda: laeufe.laden(arbeit['id'])['zustand'] == 'laeuft', text='bis Lauf läuft')
            self.melden()
            lauf = selbstlauf.pruefen()
            self.warten(lambda: laeufe.laden(lauf['id'])['zustand'] == 'fertig', text='bis Steuerlauf fertig')
        self.assertEqual(laeufe.laden(arbeit['id'])['zustand'], 'laeuft')

    def test_stufe_steuern_aus_devflow_toml_ist_im_plan_nicht_waehlbar(self):
        (self.repo / 'devflow.toml').write_text('[stufen.steuern]\nclaude_modell = "opus"\n')
        p = Projekt('demo')
        self.assertEqual(p.steuer_cli(), dict(stufe='steuern', cli_modell='opus', cli_effort='medium'))
        self.assertNotIn('steuern', p.cfg['stufen'])
        with self.assertRaises(Fehler):
            plaene.stufen_pruefen(p, {'A1': 'steuern'})

    def test_ereignis_eines_abgemeldeten_projekts_faellt_weg(self):
        ereignis.melden('neue-idee', 'weg')
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual(ereignis.lesen(), [])

    def test_zwei_projekte_nacheinander_einer_zur_zeit(self):
        zweites = self.tmp / 'zweites'
        git(self.tmp, 'clone', '-q', str(self.repo), str(zweites))
        projekte.anmelden(zweites, 'zweites')
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 1}, FERTIG])
        self.melden()
        ereignis.melden('neue-idee', 'zweites')
        self.melden()
        erster = selbstlauf.pruefen()
        self.assertEqual((erster['projekt'], len(erster['ereignisse'])), ('demo', 2))
        self.assertIsNone(selbstlauf.pruefen())
        self.ruhig()
        zweiter = selbstlauf.pruefen()
        self.assertEqual((zweiter['projekt'], zweiter['cwd']), ('zweites', str(zweites)))

    def test_schleife_startet_steuerlauf(self):
        self.drehbuch(claude=[FERTIG])
        self.melden()

        async def kurz():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(selbstlauf.schleife(), 0.5)
        with mock.patch.dict(os.environ, DEVFLOW2_SELBSTLAUF_TAKT='0.05'):
            asyncio.run(kurz())
        self.assertEqual(len(self.steuerlaeufe()), 1)
        self.ruhig()


class SperrenTest(Basis):
    weitere_toml = 'selbstlauf_budget = 1\n'

    def test_budget_erschoepft_kein_start_ein_hinweis(self):
        self.drehbuch(claude=[FERTIG, FERTIG])
        push.pruefen()  # erster Lauf merkt sich nur den Stand
        self.melden()
        self.assertIsNotNone(selbstlauf.pruefen())
        self.ruhig()
        self.melden(2)
        self.assertIsNone(selbstlauf.pruefen())
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual(len(self.steuerlaeufe()), 1)
        self.assertEqual(len(ereignis.lesen()), 2)
        self.assertEqual(push.pruefen(), [('budget', 1)])
        self.melden()
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual(push.pruefen(), [])

    def test_ohne_blockierten_start_kein_hinweis(self):
        self.drehbuch(claude=[FERTIG])
        push.pruefen()
        self.melden()
        selbstlauf.pruefen()
        self.ruhig()
        self.assertEqual(selbstlauf.pruefen(), None)
        self.assertEqual(push.pruefen(), [])

    def test_neuer_tag_gibt_neues_budget(self):
        self.drehbuch(claude=[FERTIG, FERTIG])
        self.melden()
        lauf = selbstlauf.pruefen()
        self.ruhig()
        l = laeufe.laden(lauf['id'])
        l['angelegt'] -= 86400
        laeufe.speichern(l)
        self.melden()
        self.assertIsNotNone(selbstlauf.pruefen())

    def test_kleinstes_budget_gilt_ueber_alle_projekte(self):
        zweites = self.tmp / 'zweites'
        git(self.tmp, 'clone', '-q', str(self.repo), str(zweites))
        (zweites / 'devflow.toml').write_text('selbstlauf_budget = 50\n')
        projekte.anmelden(zweites, 'zweites')
        self.assertEqual(selbstlauf.budget(), 1)

    def test_ungueltiges_budget_ist_ein_fehler(self):
        (self.repo / 'devflow.toml').write_text('selbstlauf_budget = "viel"\n')
        with self.assertRaises(Fehler):
            Projekt('demo')

    def test_schalter_aus_kein_start(self):
        self.drehbuch(claude=[FERTIG])
        selbstlauf.schalten(False)
        self.melden()
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual((self.steuerlaeufe(), len(ereignis.lesen())), ([], 1))
        selbstlauf.schalten(True)
        self.assertIsNotNone(selbstlauf.pruefen())

    def test_standard_ist_aus(self):
        (projekte.zustand() / 'selbstlauf.json').unlink()
        self.melden()
        self.assertFalse(selbstlauf.an())
        self.assertIsNone(selbstlauf.pruefen())

    def test_angehalten_kein_start(self):
        self.drehbuch(claude=[FERTIG])
        ablauf.anhalten('alles')
        self.melden()
        self.assertIsNone(selbstlauf.pruefen())
        self.assertEqual(self.steuerlaeufe(), [])
        ablauf.fortsetzen('alles', 'dashboard')
        self.assertIsNotNone(selbstlauf.pruefen())

    def test_anhalten_alles_stoppt_den_steuerlauf(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 30}])
        self.melden()
        lauf = selbstlauf.pruefen()
        self.warten(lambda: laeufe.laden(lauf['id'])['zustand'] == 'laeuft', text='bis Steuerlauf läuft')
        ablauf.anhalten('alles')
        self.assertEqual(laeufe.laden(lauf['id'])['zustand'], 'angehalten')
        self.melden()
        self.assertIsNone(selbstlauf.pruefen())

    def test_kein_zweiter_steuerlauf_durch_fortsetzen(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 30}])
        self.melden()
        erster = selbstlauf.pruefen()
        self.warten(lambda: laeufe.laden(erster['id'])['zustand'] == 'laeuft', text='bis Steuerlauf läuft')
        ablauf.anhalten(erster['id'])
        l = laeufe.laden(erster['id'])
        anderer = {**l, 'id': erster['id'] + '-2', 'zustand': 'laeuft', 'pid': os.getpid(), 'angelegt': time.time()}
        laeufe.verzeichnis(anderer['id']).mkdir()
        with mock.patch.object(laeufe, 'lebt', lambda lauf: lauf['id'] == anderer['id']):
            laeufe.speichern(anderer)
            with self.assertRaises(Fehler):
                ablauf.fortsetzen(erster['id'])
            anderer['zustand'] = 'fertig'
            laeufe.speichern(anderer)


class EreignisseTest(Basis):
    test_befehl = 'true'

    def test_kette_bis_merge_meldet_ein_laufende(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        (self.repo / 'devflow.toml').write_text('test = "true"\n')
        git(self.repo, 'commit', '-qam', 'ohne Deploy')
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        ablauf.entwickeln(Projekt('demo'), 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt', text='bis erledigt')
        self.ruhig()
        self.warten(lambda: ereignis.lesen(), text='bis Ereignis')
        arten = [(e['art'], e['bezug'].split('-')[-3]) for e in ereignis.lesen()]
        self.assertEqual(arten, [('lauf-ende', 'mergen')])

    def test_frage_aus_entwicklung_endet_mit_laufende(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'frage'}])
        ablauf.entwickeln(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich', text='bis Frage')
        self.ruhig()
        self.warten(lambda: ereignis.lesen(), text='bis Ereignis')
        self.assertEqual([e['art'] for e in ereignis.lesen()], ['lauf-ende'])

    def test_angehaltener_lauf_meldet_kein_ende(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 30}])
        l = laeufe.starten(self.p, 'meinung', 'claude', self.repo, 'x', review_worktree='', datei='plan/wetter.md', reviewer='claude')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'laeuft', text='bis läuft')
        laeufe.anhalten(l['id'])
        self.assertEqual(ereignis.lesen(), [])

    def test_kontingent_frei_und_ende(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'kontingent', 'in': 1}, {'tue': 'commit'}])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        self.warten(lambda: len(ereignis.lesen()) >= 2, text='bis Ereignisse')
        self.assertEqual([e['art'] for e in ereignis.lesen()], ['kontingent-frei', 'lauf-ende'])

    def test_antwort_meldet_einmal(self):
        nr = ablauf.frage_stellen(self.p, 'Celsius?', ['ja'], 'ja')['frage']
        ablauf.antwort_eintragen(self.p, nr, 'ja')
        with self.assertRaises(Fehler):
            ablauf.antwort_eintragen(self.p, nr, 'ja')
        self.assertEqual([(e['art'], e['bezug'], e['projekt']) for e in ereignis.lesen()], [('antwort', nr, 'demo')])

    def test_antwort_bleibt_wenn_ereignis_nicht_geht(self):
        nr = ablauf.frage_stellen(self.p, 'Celsius?', ['ja'], 'ja')['frage']
        with mock.patch('devflow2.ereignis.os.open', side_effect=OSError('voll')):
            ablauf.antwort_eintragen(self.p, nr, 'ja')
        self.assertIn('**Antwort (', self.roadmap())

    def test_kaputte_zeile_stoert_nicht_und_entfernen_laesst_neue_stehen(self):
        self.melden()
        with open(ereignis.datei(), 'a') as f:
            f.write('kaputt\n"text"\n')
        alt = ereignis.lesen()
        self.assertEqual(len(alt), 1)
        self.melden(1, 'antwort')
        ereignis.entfernen(alt)
        self.assertEqual([e['art'] for e in ereignis.lesen()], ['antwort'])
        self.assertEqual(ereignis.datei().stat().st_mode & 0o777, 0o600)


class AuftragTest(unittest.TestCase):
    def text(self):
        return auftraege.steuern('demo', [dict(art='plan-abgenommen', bezug='plan/x.md'), dict(art='lauf-ende', bezug='')])

    def test_regeln(self):
        text = self.text()
        self.assertIn(f'Nicht ohne {inhaber.akk()}', text)
        self.assertIn(f'Abnehmen darf nur {inhaber.nom()}', text)
        self.assertIn('nie abnehmen', text)
        self.assertIn('Lage prüfen, nächsten Schritt tun, bei Bedarf Frage stellen', text)
        self.assertIn('- plan-abgenommen · plan/x.md', text)

    @unittest.skipUnless((Path(__file__).parents[1] / 'AGENTS.md').exists(), 'AGENTS.md liegt nur im Entwicklungsrepo, nicht im Release')
    def test_regeln_entsprechen_agents_md(self):
        zeile = next(z for z in (Path(__file__).parents[1] / 'AGENTS.md').read_text(encoding='utf-8').split('\n')
                     if z.startswith(f'- **Nicht ohne {inhaber.akk()}:**'))
        self.assertIn(auftraege.nicht_ohne_inhaber(), zeile)
        self.assertIn(auftraege.nicht_ohne_inhaber(), self.text())


class EntwurfTest(Fall):
    def test_entwurf_ist_ein_commit_und_laesst_sich_abnehmen(self):
        vorher = git(self.repo, 'rev-list', '--count', 'HEAD')
        self.assertEqual(plaene.entwurf(self.p, 'neu', ENTWURF), 'plan/neu.md')
        self.assertEqual(int(git(self.repo, 'rev-list', '--count', 'HEAD')), int(vorher) + 1)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        plaene.entwurf(self.p, 'neu', ENTWURF.replace('Erste', 'Überarbeitet'))
        i = plaene.info(self.p, 'neu')
        self.assertEqual(i['aufgaben'][0]['aufgabe'], 'Überarbeitet')
        plaene.abnehmen(self.p, 'neu', {}, i['pruefsumme'])
        self.assertTrue(plaene.info(self.p, 'neu')['abgenommen'])

    def test_abnahmezeile_wird_abgelehnt(self):
        text = ENTWURF.replace('**Stand:** Entwurf', '**Stand:** abgenommen vom Inhaber am 30.10.2026')
        with self.assertRaisesRegex(Fehler, f'nur {inhaber.nom()}'):
            plaene.entwurf(self.p, 'neu', text)
        self.assertFalse((self.repo / 'plan/neu.md').exists())

    def test_abgenommener_plan_wird_nicht_ueberschrieben(self):
        with self.assertRaisesRegex(Fehler, 'abgenommen'):
            plaene.entwurf(self.p, 'wetter', ENTWURF)
        self.assertIn('abgenommen vom Inhaber', (self.repo / 'plan/wetter.md').read_text())

    def test_ungeeignete_eingaben(self):
        for name, text in (('../weg', ENTWURF), ('eingang', ENTWURF), ('_vorlage', ENTWURF), ('neu', '# Ohne Stand\n')):
            with self.assertRaises(Fehler):
                plaene.entwurf(self.p, name, text)


class DashboardTest(Basis):
    def client(self, kopf=HERKUNFT):
        return TestClient(web.anwendung(login='inhaber@github'), headers=kopf, follow_redirects=False)

    def test_schalter(self):
        selbstlauf.schalten(False)
        with self.client() as c:
            self.assertIn('Selbstlauf aus', c.get('/').text)
            r = c.post('/selbstlauf/an')
            self.assertEqual((r.status_code, r.headers['location']), (303, '/?meldung=selbstlauf-an'))
            self.assertTrue(selbstlauf.an())
            seite = c.get('/?meldung=selbstlauf-an').text
            self.assertIn('Selbstlauf an', seite)
            self.assertIn('Budget heute: 0 von 30 Steuerläufen', seite)
            c.post('/selbstlauf/aus')
            self.assertFalse(selbstlauf.an())

    def test_schutz(self):
        selbstlauf.schalten(False)
        with self.client() as c:
            self.assertEqual(c.get('/selbstlauf/an').status_code, 405)
            self.assertEqual(c.post('/selbstlauf/an', headers={'Origin': 'http://boese.example'}).status_code, 403)
        with self.client(kopf={}) as c:
            self.assertEqual(c.post('/selbstlauf/an').status_code, 403)
        self.assertFalse(selbstlauf.an())

    def test_steuerlauf_erscheint_mit_log(self):
        self.drehbuch(claude=[FERTIG])
        self.melden()
        lauf = selbstlauf.pruefen()
        self.ruhig()
        with self.client() as c:
            seite = c.get(f'/lauf/{lauf["id"]}')
        self.assertEqual(seite.status_code, 200)
        self.assertIn('steuern', seite.text)
        self.assertIn('Nichts zu tun.', seite.text)

    def test_lage_nennt_den_selbstlauf(self):
        self.assertEqual(ablauf.lage()['selbstlauf']['an'], True)


class SpawnTest(Fall):
    def test_im_dienst_eigene_scope_einheit(self):
        (laeufe.verzeichnis('x')).mkdir(parents=True)
        aufrufe = []

        def run(argv, **kw):
            aufrufe.append((argv, kw['env']['PATH']))
        with mock.patch.dict(os.environ, INVOCATION_ID='abc', PATH='/usr/bin'), mock.patch.object(laeufe.subprocess, 'run', run), \
                mock.patch.object(laeufe.shutil, 'which', return_value='/usr/bin/systemd-run'):
            laeufe._spawn('x')
        self.assertEqual(aufrufe[0][0][:3], ['systemd-run', '--user', '--scope'])
        self.assertTrue(aufrufe[0][1].startswith(str(Path.home() / '.local/bin') + os.pathsep))

    def test_ohne_systemd_run_geht_es_trotzdem(self):
        (laeufe.verzeichnis('x')).mkdir(parents=True)
        aufrufe = []

        def run(argv, **kw):
            aufrufe.append(argv)
            if argv[0] == 'systemd-run':
                raise subprocess.CalledProcessError(1, argv)
        with mock.patch.dict(os.environ, INVOCATION_ID='abc'), mock.patch.object(laeufe.subprocess, 'run', run), \
                mock.patch.object(laeufe.shutil, 'which', return_value='/usr/bin/systemd-run'):
            laeufe._spawn('x')
        self.assertEqual([a[0] for a in aufrufe], ['systemd-run', 'setsid'])


if __name__ == '__main__':
    unittest.main()
