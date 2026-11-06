"""Traum-Vorschläge (S5.4): Register, Sperre für Merge und Deploy auf allen Wegen, Schreibziele, Übernehmen, Verwerfen, Pflegefunde."""
import io
import json
import os
from pathlib import Path
import subprocess
import time
from unittest import mock

from starlette.testclient import TestClient

from devflow2 import Fehler, ablauf, ereignis, laeufe, mcp_server, pflege, pflegeplan, projekte, roadmap, selbstlauf, traum, vorschlag, web, inhaber
from devflow2.projekte import Projekt
from hilfen import git, ROADMAP
from test_traum import Basis, HERKUNFT, LOGIN, TEXT

TID = '20261031-210000'
VID = f'traum-{TID}-aufraeumen'
BRANCH = f'traum/{TID}-aufraeumen'


def durchsicht(*themen):
    block = json.dumps({'vorschlaege': [dict(thema=x, titel=f'Titel {x}', auftrag=f'Ändere {x}') for x in themen]})
    return {'tue': 'text', 'text': f'Fünf Punkte.\n\n```json\n{block}\n```'}


class Hilfe(Basis):
    def eintrag(self, art, v, **werte):
        lid = f'{art}-{len(laeufe.alle())}'
        laeufe.verzeichnis(lid).mkdir(parents=True)
        l = {**dict(id=lid, projekt=v['projekt'], art=art, modell='claude', cwd=v['worktree'], auftrag='', zustand='fertig', angelegt=time.time(), aufgabe=v['id'],
                    branch=v['branch'], worktree=v['worktree'], entwickler='claude'), **werte}
        laeufe.speichern(l)
        return l

    def bereit(self, review='bestanden', projekt='demo'):
        """Ein fertiger Vorschlag, wie ihn der Traum hinterlässt, ohne den Traum."""
        p = Projekt(projekt)
        vorschlag.registrieren(dict(id=TID), projekt, [dict(thema='aufraeumen', titel='Aufräumen', auftrag='Räume auf')])
        v = next(x for x in vorschlag.von_traum(TID) if x['projekt'] == projekt)
        vorschlag._vorbereiten(p, v)
        (Path(v['worktree']) / 'arbeit.txt').write_text('x\n')
        git(v['worktree'], 'add', '-A')
        git(v['worktree'], 'commit', '-q', '-m', 'traum: aufräumen')
        bau = git(v['worktree'], 'rev-parse', 'HEAD')
        v.update(commit=bau, bau=bau, zustand='offen', bericht='# Vorschlag · Aufräumen\n\nBericht.\n', test='gruen')
        vorschlag._sichern(v)
        self.eintrag('entwickeln', v, zusatzauftrag=v['auftrag'])
        if review:
            self.review(v, review)
        return vorschlag.laden(v['id'])

    def review(self, v, urteil):
        commit = git(v['worktree'], 'rev-parse', 'HEAD')
        (Path(v['worktree']) / 'berichte').mkdir(exist_ok=True)
        (Path(v['worktree']) / 'berichte' / f'{v["id"]}-review-sol.md').write_text('# Review\n')
        git(v['worktree'], 'add', '-A')
        git(v['worktree'], 'commit', '-q', '-m', 'docs: Review')
        kopf = git(v['worktree'], 'rev-parse', 'HEAD')
        self.eintrag('review', v, modell='sol', reviewer='sol', urteil=urteil, befunde=[], commit=commit, kopf=kopf,
                     basis_commit=v['start'], test_commit=commit)  # wie `vorschlag._pruefen` nach grünen Tests
        v['commit'] = kopf
        vorschlag._sichern(v)

    def kopf(self, branch=BRANCH):
        return git(self.repo, 'rev-parse', '-q', '--verify', f'refs/heads/{branch}')

    def hat_branch(self, branch=BRANCH, repo=None):
        return subprocess.run(['git', 'rev-parse', '-q', '--verify', f'refs/heads/{branch}'], cwd=repo or self.repo, capture_output=True).returncode == 0

    def gemergt(self):
        return (self.repo / 'arbeit.txt').exists()

    def merges(self):
        return [l for l in laeufe.alle('demo') if l['art'] == 'mergen']


class TraumTest(Hilfe):
    def traum(self, claude, codex=()):
        self.drehbuch(claude=claude, codex=codex)
        traum.starten('dashboard')
        return self.zu_ende()

    def test_traum_ergibt_vorschlag_unter_braucht_dich(self):
        aufgaben_vorher, main_vorher = roadmap.aufgaben(self.roadmap()), git(self.repo, 'rev-parse', 'HEAD')
        t = self.traum([TEXT, durchsicht('Tests ergänzen'), {'tue': 'schreiben', 'ergebnis': 'Tests ergänzt.'}], [self.bestanden()])
        self.assertEqual(self.schritte(t), [('rueckblick', 'fertig'), ('pflege', 'fertig'), ('code', 'fertig'), ('vorschlag', 'fertig'), ('test', 'fertig'), ('review', 'fertig')])
        v = vorschlag.von_traum(t['id'])[0]
        self.assertEqual((v['zustand'], v['branch'], v['test']), ('offen', f'traum/{t["id"]}-tests-erg-nzen', 'gruen'))
        self.assertEqual(self.kopf(v['branch']), v['commit'])
        self.assertEqual(git(self.repo, 'rev-parse', f'{v["commit"]}~1'), v['bau'])  # der Reviewbericht liegt auf dem Baucommit, die Herkunft bleibt
        p = Projekt('demo')
        self.assertEqual(vorschlag.review_stand(p, v, v['commit'])['urteil'], 'bestanden')
        self.assertIn('Tests ergänzt.', git(self.repo, 'show', f'{v["branch"]}:berichte/{v["id"]}.md'))
        self.assertEqual(git(self.repo, 'show', f'{v["branch"]}:arbeit.txt'), 'x')
        # Keine Aufgabe in der Roadmap, und auf main stehen nur Berichte
        self.assertEqual(roadmap.aufgaben(self.roadmap()), aufgaben_vorher)
        neu = git(self.repo, 'diff', '--name-only', main_vorher, 'HEAD').split('\n')
        self.assertTrue(all(d.startswith('berichte/') or d == 'ROADMAP.md' for d in neu), neu)  # ROADMAP.md: frisches Kanban aus dem Pflegebericht
        self.assertFalse(self.gemergt())
        self.assertEqual(ereignis.lesen(), [])
        self.assertEqual(traum.bilanz(self.traeume()[-1]), 'Traum beendet: 1 Vorschlag, 0 Pläne')
        bericht = (self.repo / f'berichte/traum-{t["id"]}.md').read_text()
        self.assertIn(v['branch'], bericht)
        self.assertNotIn('vorschlaege', bericht)  # der Themenblock gehört nicht in den Bericht
        self.assertIn('Fünf Punkte.', bericht)

    def test_baulauf_hat_keine_shell_und_kein_mcp(self):
        self.traum([TEXT, durchsicht('a'), {'tue': 'schreiben'}], [self.bestanden()])
        argv = self.aufrufe('claude')[2]['argv']
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob,Edit,Write')
        self.assertEqual(argv[argv.index('--allowedTools') + 1], 'Read,Grep,Glob,Edit(./**)')  # Schreiben nur im Worktree, am echten CLI geprüft
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(self.aufrufe('claude')[2]['cwd'], vorschlag.von_traum(self.traeume()[-1]['id'])[0]['worktree'])
        self.assertIn('--strict-mcp-config', argv)
        self.assertNotIn('--mcp-config', argv)

    def test_durchsicht_bleibt_lesend(self):
        self.traum([TEXT, TEXT])
        argv = self.aufrufe('claude')[1]['argv']
        self.assertEqual(argv[argv.index('--tools') + 1], selbstlauf.EINGEBAUT)

    def test_hoechstens_drei_je_projekt(self):
        schreiben = {'tue': 'schreiben'}
        t = self.traum([TEXT, durchsicht('a', 'b', 'c', 'd'), schreiben, schreiben, schreiben], [self.bestanden()] * 3)
        self.assertEqual([v['thema'] for v in vorschlag.von_traum(t['id'])], ['a', 'b', 'c'])
        self.assertEqual(traum.bilanz(t), 'Traum beendet: 3 Vorschläge, 0 Pläne')

    def test_gleiches_thema_gibt_eigene_branches(self):
        schreiben = {'tue': 'schreiben', 'dateien': {'z.txt': 'z\n'}}
        t = self.traum([TEXT, durchsicht('a', 'A'), schreiben, schreiben], [self.bestanden()] * 2)
        self.assertEqual(sorted(v['thema'] for v in vorschlag.von_traum(t['id'])), ['a', 'a-2'])

    def test_sol_ohne_kontingent_vorschlag_ohne_review(self):
        self.sol(prozent=95)
        t = self.traum([TEXT, durchsicht('a'), {'tue': 'schreiben'}])
        self.assertIn(('review', 'ausgelassen'), self.schritte(t))
        v = vorschlag.von_traum(t['id'])[0]
        self.assertEqual(v['zustand'], 'offen')
        self.assertIsNone(vorschlag.review_stand(Projekt('demo'), v, v['commit']))
        self.assertEqual(self.aufrufe('codex'), [])

    def test_rote_tests_kein_review(self):
        t = self.traum([TEXT, durchsicht('a'), {'tue': 'schreiben', 'dateien': {'kaputt': 'x\n'}}])
        self.assertEqual(self.schritte(t)[-2:], [('vorschlag', 'fertig'), ('test', 'fertig')])
        v = vorschlag.von_traum(t['id'])[0]
        self.assertEqual((v['zustand'], v['test']), ('offen', 'rot'))
        self.assertEqual(self.aufrufe('codex'), [])

    def gescheitert(self, schritt, muster, **sonst):
        t = self.traum([TEXT, durchsicht('a'), schritt])
        v = vorschlag.von_traum(t['id'])[0]
        self.assertEqual(v['zustand'], 'gescheitert')
        self.assertRegex(v['grund'], muster)
        self.assertFalse(self.hat_branch(v['branch']))
        self.assertFalse(Path(v['worktree']).exists())
        self.assertEqual(self.aufrufe('codex'), [])
        self.assertIn('0 Vorschläge', traum.bilanz(t))
        return v

    def test_roadmap_ist_kein_schreibziel(self):
        self.gescheitert({'tue': 'schreiben', 'dateien': {'ROADMAP.md': 'weg\n'}}, 'ROADMAP.md')

    def test_haupt_checkout_ist_kein_schreibziel(self):
        ziel = self.repo / 'fremd.txt'
        self.gescheitert({'tue': 'schreiben', 'dateien': {str(ziel): 'x\n'}}, 'Haupt-Checkout von demo')
        self.assertTrue(ziel.exists())  # devflow räumt fremde Dateien nicht weg, der Inhaber sieht den Grund

    def test_selbst_committet_ist_kein_vorschlag(self):
        self.gescheitert({'tue': 'commit'}, 'selbst committet')

    def test_keine_aenderung_kein_vorschlag(self):
        self.gescheitert({'tue': 'text', 'text': 'Nichts zu tun.'}, 'Keine Änderung')

    def test_wiederholung_beginnt_beim_startcommit(self):
        t = self.traum([TEXT, durchsicht('a'), {'tue': 'unbekannt'}, {'tue': 'schreiben'}], [self.bestanden()])
        self.assertEqual(next(s for s in t['schritte'] if s['art'] == 'vorschlag')['versuche'], 2)
        self.assertEqual(vorschlag.von_traum(t['id'])[0]['zustand'], 'offen')

    def test_halt_raeumt_den_halben_vorschlag_weg(self):
        self.drehbuch(claude=[TEXT, durchsicht('a'), {'tue': 'schlafen'}])
        traum.starten('dashboard')

        def baut():
            traum.takt()
            return self.aufrufe('claude')[2:] and next(s for s in self.traeume()[-1]['schritte'] if s['art'] == 'vorschlag')['zustand'] == 'laeuft'
        self.warten(baut, text='bis Baulauf')
        ablauf.anhalten('alles', 'dashboard')
        self.addCleanup(lambda: (projekte.zustand() / 'angehalten').unlink(missing_ok=True))
        t = self.zu_ende()
        v = vorschlag.von_traum(t['id'])[0]
        self.assertEqual(v['zustand'], 'gescheitert')
        self.assertFalse(self.hat_branch(v['branch']))
        self.assertFalse(Path(v['worktree']).exists())

    def test_globaler_halt_beim_start_des_baulaufs(self):
        """S7.2: Der Halt liegt schon, der Baulauf ist aber noch in keiner Liste von „Alles anhalten“: Er beginnt keine Arbeit, der Traum endet, aufgeräumt wird wie sonst."""
        spawn = laeufe._spawn
        self.drehbuch(claude=[TEXT, durchsicht('a'), {'tue': 'schlafen'}])
        with mock.patch.object(laeufe, '_spawn', lambda lid: laeufe.laden(lid).get('vorschlag') or spawn(lid)):
            traum.starten('dashboard')

            def bau():
                traum.takt()
                return next((l for l in laeufe.alle() if l.get('vorschlag')), None)
            self.warten(bau, text='bis Baulauf')
        l = bau()
        self.addCleanup(lambda: (projekte.zustand() / 'angehalten').unlink(missing_ok=True))
        (projekte.zustand() / 'angehalten').write_text('01.11. · dashboard\n')
        self.assertIsNone(laeufe._start_eintragen(l['id']))
        self.assertEqual(laeufe.laden(l['id'])['zustand'], laeufe.TRAUM_ENDE)
        t = self.zu_ende()
        self.assertEqual(len(self.aufrufe('claude')), 2)  # Rückblick und Durchsicht, kein Baulauf
        v = vorschlag.von_traum(t['id'])[0]
        self.assertEqual(v['zustand'], 'gescheitert')
        self.assertFalse(self.hat_branch(v['branch']))
        self.assertFalse(Path(v['worktree']).exists())
        with self.assertRaises(Fehler):
            laeufe.fortsetzen(l['id'])

    def test_ideenlauf_kann_die_roadmap_nicht_aendern(self):
        self.assertNotIn('frage_stellen', selbstlauf.SAETZE['ideen'])
        self.assertTrue(set(selbstlauf.SAETZE['ideen']) <= set(selbstlauf.SAETZE['planung']))
        mcp = json.loads(Path(selbstlauf._argumente('ideen')[1]).read_text())
        self.assertEqual(mcp['mcpServers']['devflow']['args'][-1], 'ideen')


class SperreTest(Hilfe):
    def test_kein_werkzeug_merged_ohne_uebernehmen(self):
        self.bereit()
        with self.assertRaisesRegex(Exception, 'nicht übernommen'):
            mcp_server.mergen('demo', VID)
        with self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            ablauf.mergen(self.p, VID)
        self.assertEqual(self.merges(), [])
        self.assertFalse(self.gemergt())

    def test_kein_werkzeug_entscheidet_einen_vorschlag(self):
        self.assertFalse([w for w in mcp_server.ALLE if 'vorschlag' in w or 'verwerf' in w or w == 'uebernehmen'])
        self.bereit()
        for fn in (lambda: vorschlag.uebernehmen(VID, self.kopf(), 'steuerung'), lambda: vorschlag.verwerfen(VID, 'steuerung')):
            with self.assertRaisesRegex(Fehler, f'nur {inhaber.nom()}'):
                fn()
        self.assertTrue(self.hat_branch())

    def test_kein_merge_auch_wenn_der_lauf_schon_angelegt_ist(self):
        v = self.bereit()
        l = laeufe.starten(self.p, 'mergen', 'intern', self.repo, aufgabe=VID, branch=BRANCH, worktree=v['worktree'], entwickler='claude', automatisch=True)
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler', text='bis Merge abgelehnt')
        self.assertIn('nicht übernommen', laeufe.laden(l['id'])['fehler'])
        self.assertFalse(self.gemergt())

    def test_fortsetzen_nach_neustart_merged_nicht(self):
        v = self.bereit()
        l = self.eintrag('mergen', v, automatisch=True)
        laeufe.speichern({**l, 'zustand': 'fehler', 'modell': 'intern', 'cwd': str(self.repo)})
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler', text='bis Merge abgelehnt')
        self.assertFalse(self.gemergt())

    def test_folgeschritt_nach_bestandenem_review_merged_nicht(self):
        v = self.bereit(review=None)
        commit = git(v['worktree'], 'rev-parse', 'HEAD')
        rwt = self.p.worktree('review-probe-1')
        git(self.repo, 'worktree', 'add', '-q', '--detach', str(rwt), commit)
        l = self.eintrag('review', v, modell='sol', reviewer='sol', commit=commit, runde=1, review_worktree=str(rwt), automatisch=True)
        (laeufe.verzeichnis(l['id']) / 'ergebnis.txt').write_text(json.dumps(self.bestanden()['urteil']))
        vorher = self.roadmap()
        with mock.patch.object(ablauf, '_stand'), self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            ablauf.abschluss(l)  # die normale Kette, wäre ein Review ohne Kennzeichen gestartet worden
        self.assertEqual(self.merges(), [])
        self.assertEqual(self.roadmap(), vorher)

    def test_laufende_ereignis_weckt_keine_steuerung(self):
        v = self.bereit()
        l = self.eintrag('entwickeln', v, traum=TID, vorschlag=VID)
        laeufe._ende_melden(l)
        self.assertEqual(ereignis.lesen(), [])

    def test_unregistrierter_traumbranch_ist_gesperrt(self):
        git(self.repo, 'branch', 'traum/fremd')
        with self.assertRaisesRegex(Fehler, 'nicht registriert'):
            vorschlag.merge_tor(self.p, 'traum/fremd', 'X1')

    def test_gleicher_name_ohne_register_ist_gesperrt(self):
        with self.assertRaisesRegex(Fehler, 'nicht registriert'):
            vorschlag.merge_tor(self.p, 'claude-x', 'traum-20260101-000000-x')

    def test_aufgabe_mit_vorschlagsid_laesst_sich_nicht_anlegen(self):
        with self.assertRaisesRegex(Fehler, 'Übernehmen'):
            ablauf.aufgabe_anlegen(self.p, VID, 'Umweg')

    def test_anderer_branch_bringt_den_vorschlag_nicht_mit(self):
        self.bereit()
        git(self.repo, 'branch', 'claude-a1', BRANCH)
        with self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            vorschlag.merge_tor(self.p, 'claude-a1', 'A1')

    def test_anderer_branch_bringt_den_baucommit_nicht_ohne_reviewbericht_mit(self):
        v = self.bereit()
        self.assertNotEqual(v['bau'], v['commit'])
        git(self.repo, 'branch', 'claude-a1', v['bau'])
        with self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            vorschlag.merge_tor(self.p, 'claude-a1', 'A1')

    def test_normale_kette_mergt_den_baucommit_nicht(self):
        v = self.bereit()
        wt = str(self.p.worktree('claude-a1'))
        git(self.repo, 'worktree', 'add', '-q', '-b', 'claude-a1', wt, v['bau'])
        ablauf.aufgabe_anlegen(self.p, 'A1', 'Umweg')
        self.eintrag('review', dict(id='A1', projekt='demo', branch='claude-a1', worktree=wt), modell='sol', reviewer='sol', urteil='bestanden',
                     befunde=[], commit=v['bau'], kopf=v['bau'])
        l = laeufe.starten(self.p, 'mergen', 'intern', self.repo, aufgabe='A1', branch='claude-a1', worktree=wt, entwickler='claude', automatisch=True)
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler', text='bis Merge abgelehnt')
        self.assertIn('nicht übernommen', laeufe.laden(l['id'])['fehler'])
        self.assertFalse(self.gemergt())
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'offen')

    def test_rebase_stand_bleibt_gesperrt(self):
        v = self.bereit()
        kopf = git(self.repo, 'commit-tree', f'{v["bau"]}^{{tree}}', '-p', 'main', '-m', 'traum: aufräumen, neu aufgesetzt')  # andere Hashes als der Branch
        self.eintrag('mergen', v, modell='intern', rebase_kopf=kopf)
        git(self.repo, 'branch', 'claude-a1', kopf)
        with self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            vorschlag.merge_tor(self.p, 'claude-a1', 'A1')

    def test_normaler_branch_bleibt_frei(self):
        self.bereit()
        git(self.repo, 'branch', 'claude-a1', 'main')
        vorschlag.merge_tor(self.p, 'claude-a1', 'A1')


class DeployTest(Hilfe):
    deploy = 'touch deployt'

    def test_vorschlag_nicht_deploybar(self):
        self.bereit()
        with self.assertRaisesRegex(Fehler, 'kein Deploy'):
            ablauf.deployen(self.p, VID)

    def test_main_mit_vorschlag_nicht_deploybar(self):
        self.bereit()
        git(self.repo, 'merge', '-q', '--ff-only', BRANCH)  # von Hand am Werkzeug vorbei
        with self.assertRaisesRegex(Exception, 'kein Deploy'):
            mcp_server.deployen('demo')

    def test_main_mit_dem_baucommit_nicht_deploybar(self):
        v = self.bereit()
        git(self.repo, 'merge', '-q', '--ff-only', v['bau'])
        with self.assertRaisesRegex(Exception, 'kein Deploy'):
            mcp_server.deployen('demo')

    def test_fortsetzen_eines_deploys_gesperrt(self):
        self.bereit()
        git(self.repo, 'merge', '-q', '--ff-only', BRANCH)
        l = laeufe.starten(self.p, 'deploy', 'shell', self.repo, 'true')
        self.ruhig()
        laeufe.speichern({**laeufe.laden(l['id']), 'zustand': 'fehler'})
        with self.assertRaisesRegex(Fehler, 'kein Deploy'):
            ablauf.fortsetzen(l['id'])

    def test_vorschlag_ohne_freigabe_nach_rebase_auch_deploy_gesperrt(self):
        """S14.2: Der Rebase vor dem Review erzeugt neue Hashes; auch sie bleiben gesperrt, wenn die Freigabe wegfällt."""
        v = self.bereit(review=None)
        (self.repo / 'neu.txt').write_text('von main\n')
        git(self.repo, 'add', 'neu.txt')
        git(self.repo, 'commit', '-q', '-m', 'fachlich auf main')
        self.drehbuch(codex=[{'tue': 'fehler', 'text': 'Review bricht ab'}])
        vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        self.ruhig()
        neu = self.kopf()
        self.assertNotEqual(neu, v['commit'])
        self.assertIn(neu, vorschlag._rebases(self.p, v))
        w = vorschlag.laden(VID)
        vorschlag._zurueck(self.p, w, neu, False)  # die Zustimmung verfällt, wie nach einer Änderung am Branch
        git(self.repo, 'merge', '-q', '--no-edit', neu)  # von Hand am Werkzeug vorbei
        with self.assertRaisesRegex(Exception, 'kein Deploy'):
            mcp_server.deployen('demo')
        git(self.repo, 'branch', 'claude-a1', neu)
        with self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            vorschlag.merge_tor(self.p, 'claude-a1', 'A1')

    def test_ohne_vorschlag_in_main_geht_der_deploy(self):
        self.bereit()
        ablauf.deployen(self.p)
        self.ruhig()
        self.assertTrue((self.repo / 'deployt').exists())

    def test_nach_uebernehmen_gemergt_und_deployt(self):
        v = self.bereit()
        self.assertEqual(vorschlag.uebernehmen(VID, v['commit'], 'dashboard'), 'merge')
        self.warten(lambda: (self.repo / 'deployt').exists(), text='bis Deploy')
        self.ruhig()
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'gemergt')
        self.assertTrue(self.gemergt())
        self.assertEqual(self.zeile(VID)[3], 'erledigt')
        self.assertFalse(self.hat_branch())
        self.assertFalse(Path(v['worktree']).exists())


class UebernehmenTest(Hilfe):
    def test_nur_der_angezeigte_stand(self):
        v = self.bereit()
        for falsch in ('', 'abc', git(self.repo, 'rev-parse', 'HEAD')):
            with self.assertRaisesRegex(Fehler, 'angezeigten Stand'):
                vorschlag.uebernehmen(VID, falsch, 'dashboard')
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'offen')
        self.assertEqual(self.merges(), [])
        self.assertNotIn(VID, self.roadmap())
        self.assertEqual(v['commit'], self.kopf())

    def test_nach_dem_ansehen_veraendert(self):
        v = self.bereit()
        (Path(v['worktree']) / 'mehr.txt').write_text('y\n')
        git(v['worktree'], 'add', '-A')
        git(v['worktree'], 'commit', '-q', '-m', 'später')
        with self.assertRaisesRegex(Fehler, 'angezeigten Stand'):
            vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        self.assertEqual(self.merges(), [])

    def test_ungeprueft_startet_das_review_statt_des_merges(self):
        v = self.bereit(review=None)
        self.drehbuch(codex=[{'tue': 'schlafen', 's': 30}])
        self.assertEqual(vorschlag.uebernehmen(VID, v['commit'], 'dashboard'), 'review')
        self.warten(lambda: [l['art'] for l in laeufe.alle('demo', VID) if l['zustand'] in laeufe.AKTIV] == ['review'], text='bis Review')
        self.assertEqual([l['art'] for l in laeufe.alle('demo', VID)][-2:], ['vorbereiten', 'review'])  # erst Rebase und Tests
        self.assertEqual(self.merges(), [])
        self.assertEqual(self.zeile(VID)[3], 'Review')

    def test_review_besteht_dann_merge(self):
        v = self.bereit(review=None)
        self.drehbuch(codex=[self.bestanden()])
        vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        self.warten(lambda: self.merges() and all(l['zustand'] not in laeufe.AKTIV for l in laeufe.alle()), text='bis Merge')
        self.ruhig()
        self.assertTrue(self.gemergt())
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'gemergt')
        self.assertTrue((self.repo / f'berichte/{VID}-review-sol.md').exists())

    def fachlich_auf_main(self):
        (self.repo / 'neu.txt').write_text('von main\n')
        git(self.repo, 'add', 'neu.txt')
        git(self.repo, 'commit', '-q', '-m', 'fachlich auf main')

    def test_rebase_vor_dem_review_behaelt_die_freigabe(self):
        """S14.2: Die Vorbereitung rebased den übernommenen Stand; devflows Rebase und Reviewbericht gehören zur Herkunft, der Merge geht durch."""
        v = self.bereit(review=None)
        self.fachlich_auf_main()
        self.drehbuch(codex=[self.bestanden()])
        self.assertEqual(vorschlag.uebernehmen(VID, v['commit'], 'dashboard'), 'review')
        self.warten(lambda: vorschlag.laden(VID)['zustand'] == 'gemergt', text='bis Merge')
        self.ruhig()
        vb = next(l for l in laeufe.alle('demo', VID) if l['art'] == 'vorbereiten')
        self.assertEqual(vb['rebases'][0]['ausgang'], v['commit'])
        self.assertTrue(self.gemergt())
        self.assertTrue((self.repo / 'neu.txt').exists())

    def test_bestandenes_traumreview_auf_altem_main_bringt_neues_review(self):
        v = self.bereit()
        self.fachlich_auf_main()
        self.drehbuch(codex=[self.bestanden()])
        self.assertEqual(vorschlag.uebernehmen(VID, v['commit'], 'dashboard'), 'review')
        self.warten(lambda: vorschlag.laden(VID)['zustand'] == 'gemergt', text='bis Merge')
        self.ruhig()
        self.assertEqual([l['art'] for l in laeufe.alle('demo', VID)][-3:], ['vorbereiten', 'review', 'mergen'])
        self.assertTrue(self.gemergt())

    def test_fehlt_das_gegenreview_bleibt_merge_gesperrt(self):
        v = self.bereit(review=None)
        with mock.patch.object(ablauf, 'review_anfordern'):
            vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        with self.assertRaisesRegex(Fehler, 'kein bestandenes Review'):
            ablauf.mergen(self.p, VID)

    def test_selbstreview_zaehlt_nicht(self):
        v = self.bereit(review=None)
        self.review(v, 'bestanden')
        l = next(x for x in laeufe.alle('demo', VID) if x['art'] == 'review')
        laeufe.speichern({**l, 'reviewer': 'claude'})
        with self.assertRaisesRegex(Fehler, 'Selbstreview'):
            vorschlag.uebernehmen(VID, self.kopf(), 'dashboard')
        self.assertFalse(self.gemergt())

    def test_nacharbeit_aendert_den_branch_inhaber_stimmt_neu_zu(self):
        v = self.bereit(review=None)
        self.drehbuch(codex=[self.nacharbeit(), self.bestanden()], claude=[{'tue': 'commit', 'dateien': {'mehr.txt': 'y\n'}}])
        vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        self.warten(lambda: vorschlag.laden(VID)['zustand'] == 'offen', text='bis zurück beim Inhaber')
        self.ruhig()
        neu = vorschlag.laden(VID)
        self.assertFalse(self.gemergt())
        self.assertEqual(self.merges(), [])
        self.assertIsNone(neu['freigabe'])
        self.assertEqual(neu['commit'], self.kopf())
        self.assertEqual(self.zeile(VID)[3], 'Braucht dich')
        self.assertEqual(vorschlag.uebernehmen(VID, neu['commit'], 'dashboard'), 'merge')
        self.ruhig()
        self.assertTrue(self.gemergt())
        self.assertTrue((self.repo / 'mehr.txt').exists())

    def test_branch_aendert_sich_nach_der_zustimmung(self):
        v = self.bereit()
        vorschlag._zeile_anlegen(self.p, v)
        v.update(zustand='uebernommen', freigabe=dict(commit=v['commit'], zeit=1, herkunft='dashboard'))
        vorschlag._sichern(v)
        (Path(v['worktree']) / 'mehr.txt').write_text('y\n')
        git(v['worktree'], 'add', '-A')
        git(v['worktree'], 'commit', '-q', '-m', 'später')
        with self.assertRaisesRegex(Fehler, 'neu zustimmt'):
            ablauf.mergen(self.p, VID)
        neu = vorschlag.laden(VID)
        self.assertEqual((neu['zustand'], neu['freigabe'], neu['commit']), ('offen', None, self.kopf()))
        self.assertEqual(self.zeile(VID)[3], 'Braucht dich')
        self.assertEqual(self.merges(), [])

    def test_aenderung_vor_dem_mergelauf_faellt_auf(self):
        v = self.bereit()
        vorschlag._zeile_anlegen(self.p, v)
        v.update(zustand='uebernommen', freigabe=dict(commit=v['commit'], zeit=1, herkunft='dashboard'))
        vorschlag._sichern(v)
        (Path(v['worktree']) / 'mehr.txt').write_text('y\n')
        git(v['worktree'], 'add', '-A')
        git(v['worktree'], 'commit', '-q', '-m', 'später')
        l = laeufe.starten(self.p, 'mergen', 'intern', self.repo, aufgabe=VID, branch=BRANCH, worktree=v['worktree'], entwickler='claude', automatisch=True)
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler', text='bis Merge abgelehnt')
        self.assertFalse(self.gemergt())
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'offen')
        self.assertEqual(self.zeile(VID)[3], 'Braucht dich')

    def test_halt_und_offene_frage_sperren(self):
        v = self.bereit()
        (projekte.zustand() / 'angehalten').write_text('x')
        self.addCleanup(lambda: (projekte.zustand() / 'angehalten').unlink(missing_ok=True))
        with self.assertRaisesRegex(Fehler, 'angehalten'):
            vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'offen')

    def angehaltener_merge(self):
        """S7.2: Der Mergelauf entsteht, und beim Start gilt schon der globale Halt."""
        v = self.bereit()
        with mock.patch.object(laeufe, '_spawn'):
            self.assertEqual(vorschlag.uebernehmen(VID, v['commit'], 'dashboard'), 'merge')
        l = self.merges()[-1]
        halt = projekte.zustand() / 'angehalten'
        self.addCleanup(lambda: halt.unlink(missing_ok=True))
        halt.write_text('01.11. · dashboard\n')
        self.assertIsNone(laeufe._start_eintragen(l['id']))
        self.assertEqual(laeufe.laden(l['id'])['zustand'], 'angehalten')
        self.assertFalse(self.gemergt())
        halt.unlink()
        return l

    def test_halt_beim_start_des_merges(self):
        l = self.angehaltener_merge()
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'uebernommen')
        ablauf.fortsetzen(l['id'], 'dashboard')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fertig', text='bis Merge')
        self.assertTrue(self.gemergt())
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'gemergt')

    def test_verworfen_nach_halt_beim_start_des_merges(self):
        l = self.angehaltener_merge()
        vorschlag.verwerfen(VID, 'dashboard')
        ablauf.fortsetzen(l['id'], 'dashboard')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler', text='bis Merge abgelehnt')
        self.assertFalse(self.gemergt())
        self.assertEqual(vorschlag.laden(VID)['zustand'], 'verworfen')

    def test_verworfen_laesst_sich_nicht_uebernehmen(self):
        v = self.bereit()
        vorschlag.verwerfen(VID, 'dashboard')
        with self.assertRaisesRegex(Fehler, 'ist verworfen'):
            vorschlag.uebernehmen(VID, v['commit'], 'dashboard')

    def test_verworfen_wird_nicht_gemergt_auch_nicht_mit_alter_freigabe(self):
        v = self.bereit()
        vorschlag.verwerfen(VID, 'dashboard')
        with self.assertRaisesRegex(Fehler, 'ist verworfen'):
            vorschlag.merge_tor(self.p, BRANCH, VID)
        git(self.repo, 'branch', 'claude-a1', v['commit'])
        with self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            vorschlag.merge_tor(self.p, 'claude-a1', 'A1')

    def test_verworfener_baucommit_wird_nicht_gemergt(self):
        v = self.bereit()
        git(self.repo, 'branch', 'claude-a1', v['bau'])  # vor dem Verwerfen abgezweigt, also bleibt der Commit erreichbar
        vorschlag.verwerfen(VID, 'dashboard')
        with self.assertRaisesRegex(Fehler, 'nicht übernommen'):
            vorschlag.merge_tor(self.p, 'claude-a1', 'A1')


class VerwerfenTest(Hilfe):
    def test_entfernt_branch_und_worktree_auch_ungemergt(self):
        v = self.bereit()
        (Path(v['worktree']) / 'lose.txt').write_text('nicht committet\n')
        self.assertTrue(vorschlag.verwerfen(VID, 'dashboard'))
        self.assertFalse(self.hat_branch())
        self.assertFalse(Path(v['worktree']).exists())
        neu = vorschlag.laden(VID)
        self.assertEqual((neu['zustand'], neu['entscheidung']['was'], neu['entscheidung']['herkunft']), ('verworfen', 'verworfen', 'dashboard'))

    def test_bericht_und_entscheidung_bleiben(self):
        self.bereit()
        vorschlag.verwerfen(VID, 'dashboard')
        text = (self.repo / f'berichte/{VID}.md').read_text()
        self.assertIn('Bericht.', text)
        self.assertIn('**Entscheidung:** verworfen', text)
        self.assertIn(f'Vorschlag {VID} verworfen', self.roadmap())
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_uebernommener_entfaellt_in_der_roadmap(self):
        v = self.bereit(review=None)
        with mock.patch.object(ablauf, 'review_anfordern'):
            vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        vorschlag.verwerfen(VID, 'dashboard')
        self.assertEqual(self.zeile(VID)[3], 'entfällt')

    def test_doppelklick_aendert_nichts(self):
        self.bereit()
        vorschlag.verwerfen(VID, 'dashboard')
        kopf = git(self.repo, 'rev-parse', 'HEAD')
        self.assertFalse(vorschlag.verwerfen(VID, 'dashboard'))
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), kopf)

    def test_gleichnamige_branches_bleiben(self):
        v = self.bereit()
        git(self.repo, 'branch', f'{BRANCH}-2', v['commit'])
        git(self.repo, 'branch', f'x/{BRANCH}', v['commit'])
        git(self.repo, 'tag', 'traum-tag', v['commit'])
        vorschlag.verwerfen(VID, 'dashboard')
        self.assertFalse(self.hat_branch())
        self.assertTrue(self.hat_branch(f'{BRANCH}-2'))
        self.assertTrue(self.hat_branch(f'x/{BRANCH}'))

    def test_gleichnamiger_branch_im_anderen_projekt_bleibt(self):
        self.bereit()
        anderes = self.tmp / 'anderes'
        anderes.mkdir()
        git(anderes, 'init', '-q', '-b', 'main')
        (anderes / 'ROADMAP.md').write_text(ROADMAP)
        git(anderes, 'add', '-A')
        git(anderes, 'commit', '-q', '-m', 'start')
        git(anderes, 'branch', BRANCH)
        projekte.anmelden(anderes, 'anderes')
        vorschlag.verwerfen(VID, 'dashboard')
        self.assertFalse(self.hat_branch())
        self.assertTrue(self.hat_branch(repo=anderes))

    def test_neu_angelegter_gleichnamiger_branch_bleibt(self):
        v = self.bereit()
        git(self.repo, 'worktree', 'remove', '--force', v['worktree'])
        git(self.repo, 'branch', '-D', BRANCH)
        git(self.repo, 'branch', BRANCH, 'main')  # gleicher Name, anderer Stand
        vorschlag.verwerfen(VID, 'dashboard')
        self.assertEqual(self.kopf(), git(self.repo, 'rev-parse', 'main~1'))
        neu = vorschlag.laden(VID)
        self.assertEqual(neu['zustand'], 'verworfen')
        self.assertIn('bleibt', neu['hinweis'])

    def test_fremdes_verzeichnis_am_worktreepfad_bleibt(self):
        v = self.bereit()
        git(self.repo, 'worktree', 'remove', '--force', v['worktree'])
        Path(v['worktree']).mkdir()
        (Path(v['worktree']) / 'fremd.txt').write_text('fremd\n')
        vorschlag.verwerfen(VID, 'dashboard')
        self.assertTrue((Path(v['worktree']) / 'fremd.txt').exists())
        self.assertFalse(self.hat_branch())

    def test_worktree_auf_fremdem_branch_bleibt(self):
        v = self.bereit()
        git(v['worktree'], 'switch', '-q', '-c', 'claude-fremd')
        vorschlag.verwerfen(VID, 'dashboard')
        self.assertTrue(Path(v['worktree']).exists())
        self.assertTrue(self.hat_branch('claude-fremd'))

    def test_nicht_solange_etwas_laeuft(self):
        v = self.bereit()
        l = self.eintrag('review', v)
        laeufe.speichern({**l, 'zustand': 'laeuft', 'pid': os.getpid()})
        with mock.patch.object(laeufe, 'lebt', return_value=True), self.assertRaisesRegex(Fehler, 'läuft gerade'):
            vorschlag.verwerfen(VID, 'dashboard')
        self.assertTrue(self.hat_branch())

    def test_nicht_nach_dem_merge(self):
        v = self.bereit()
        vorschlag.uebernehmen(VID, v['commit'], 'dashboard')
        self.ruhig()
        with self.assertRaisesRegex(Fehler, 'gemergt'):
            vorschlag.verwerfen(VID, 'dashboard')


class PflegeTest(Hilfe):
    def fund(self):
        return dict(projekt='demo', tag='2026-10-31', zeit=time.time(), funde=[], veraltet=[dict(paket='a', von='1.0.0', nach='1.0.1')], nicht_geprueft=[])

    def test_pflegefund_im_traum_startet_keine_update_aufgabe(self):
        lauf = dict(id='p1', projekt='demo', traum=TID, bis=time.time() + 60)
        with mock.patch.object(pflege, 'pruefen', return_value=self.fund()), mock.patch.object(pflege, 'abschliessen', return_value=[1]), \
                mock.patch.object(pflegeplan, 'nach_pruefung') as nach:
            pflege.ausfuehren(lauf, io.StringIO())
        nach.assert_not_called()
        self.assertEqual(ereignis.lesen(), [])
        with mock.patch.object(pflegeplan, 'laden'):  # auch unter gültigem Pflegeplan
            with self.assertRaisesRegex(Fehler, 'aus einem Traum'):
                pflegeplan.aufgabe_anlegen(self.p)
            with self.assertRaisesRegex(Exception, 'aus einem Traum'):
                mcp_server.pflege_aufgabe('demo')
        self.assertNotIn('P.', self.roadmap())

    def test_normale_pruefung_laeuft_weiter_durch(self):
        lauf = dict(id='p1', projekt='demo')
        with mock.patch.object(pflege, 'pruefen', return_value=self.fund()), mock.patch.object(pflege, 'abschliessen', return_value=[]), \
                mock.patch.object(pflegeplan, 'nach_pruefung') as nach:
            pflege.ausfuehren(lauf, io.StringIO())
        nach.assert_called_once()

    def test_traumpruefung_verschiebt_die_wochenpruefung_nicht(self):
        self.eintrag('pflege', dict(projekt='demo', id=None, branch=None, worktree=''), traum=TID)
        self.assertTrue(pflege.faellig('demo'))


class WebTest(Hilfe):
    def setUp(self):
        super().setUp()
        self.web = TestClient(web.anwendung(login='inhaber@github'), raise_server_exceptions=True, follow_redirects=False)

    def client(self):
        return self.web

    def test_braucht_dich_zeigt_den_vorschlag(self):
        self.bereit()
        seite = self.client().get('/', headers=LOGIN).text
        self.assertIn('Vorschlag', seite)
        self.assertIn(f'/traum#{VID}', seite)

    def test_lage_nennt_den_vorschlag(self):
        self.bereit()
        self.assertEqual(ablauf.lage()['projekte']['demo']['vorschlaege'], [f'{VID} Aufräumen'])

    def test_seite_bindet_den_knopf_an_den_commit(self):
        v = self.bereit()
        seite = self.client().get('/traum', headers=LOGIN).text
        self.assertIn(f'value="{v["commit"]}"', seite)
        self.assertIn('Gegenreview bestanden', seite)
        self.assertIn('Der Merge startet sofort.', seite)
        self.assertIn(f'/traum/vorschlag/{VID}/verwerfen', seite)

    def test_ohne_review_sagt_die_seite_was_passiert(self):
        self.bereit(review=None)
        self.assertIn('Das Gegenreview startet', self.client().get('/traum', headers=LOGIN).text)

    def test_uebernehmen_mit_falschem_commit(self):
        self.bereit()
        r = self.client().post(f'/traum/vorschlag/{VID}/uebernehmen', data={'gesehen': 'abc'}, headers=HERKUNFT)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.merges(), [])

    def test_uebernehmen_ohne_herkunft(self):
        v = self.bereit()
        r = self.client().post(f'/traum/vorschlag/{VID}/uebernehmen', data={'gesehen': v['commit']}, headers=LOGIN)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.merges(), [])

    def test_uebernehmen_und_verwerfen_ueber_die_seite(self):
        v = self.bereit(review=None)
        self.drehbuch(codex=[{'tue': 'schlafen', 's': 30}])
        r = self.client().post(f'/traum/vorschlag/{VID}/uebernehmen', data={'gesehen': v['commit']}, headers=HERKUNFT)
        self.assertEqual((r.status_code, r.headers['location']), (303, '/traum?meldung=vorschlag-review#vorschlaege'))
        r = self.client().post(f'/traum/vorschlag/{VID}/verwerfen', data={'gesehen': v['commit']}, headers=HERKUNFT)
        self.assertEqual(r.status_code, 409)  # das Review läuft noch
        for l in laeufe.alle('demo', VID):
            if l['zustand'] in laeufe.AKTIV:
                laeufe.anhalten(l['id'])
        r = self.client().post(f'/traum/vorschlag/{VID}/verwerfen', headers=HERKUNFT)
        self.assertEqual(r.status_code, 409)  # ohne angezeigten Stand wird nichts entfernt
        self.assertTrue(self.hat_branch())
        r = self.client().post(f'/traum/vorschlag/{VID}/verwerfen', data={'gesehen': v['commit']}, headers=HERKUNFT)
        self.assertEqual((r.status_code, r.headers['location']), (303, '/traum?meldung=vorschlag-verworfen#vorschlaege'))
        self.assertFalse(self.hat_branch())

    def test_unbekannter_vorschlag(self):
        self.assertEqual(self.client().post('/traum/vorschlag/%2e%2e%2fx/verwerfen', headers=HERKUNFT).status_code, 404)
        self.assertEqual(self.client().post('/traum/vorschlag/traum-x/verwerfen', headers=HERKUNFT).status_code, 409)

    def test_aufgabenlink_vor_der_uebernahme(self):
        self.bereit()
        r = self.client().get(f'/p/demo/a/{VID}', headers=LOGIN)
        self.assertEqual((r.status_code, r.headers['location']), (303, f'/traum#{VID}'))


class ZerlegenTest(Hilfe):
    def test_ohne_block_keine_themen(self):
        self.assertEqual(vorschlag.zerlegen('Nur Text'), ('Nur Text', []))

    def test_kaputter_block_keine_themen(self):
        for text in ('```json\n{"vorschlaege": 3}\n```', '```json\n{kaputt}\n```', '```json\n{"vorschlaege": [{"thema": "a"}]}\n```'):
            self.assertEqual(vorschlag.zerlegen(text), (text, []))

    def test_letzter_block_gilt_und_faellt_weg(self):
        text, themen = vorschlag.zerlegen('A\n```json\n{"x": 1}\n```\nB\n```json\n{"vorschlaege": [{"thema": "t", "titel": "T", "auftrag": "tu"}]}\n```')
        self.assertEqual(themen, [dict(thema='t', titel='T', auftrag='tu')])
        self.assertIn('B', text)
        self.assertNotIn('vorschlaege', text)

    def test_thema_passt_in_einen_branchnamen(self):
        self.assertEqual(vorschlag._thema('Äh ../../ ~^ Test', set()), 'h-test')
        self.assertEqual(vorschlag._thema('…', set()), 'pflege')
        self.assertEqual(vorschlag._thema('a', {'a', 'a-2'}), 'a-3')
