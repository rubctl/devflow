"""Ganze Ketten mit Hintergrundläufen und nachgebauten CLIs."""
import os
from pathlib import Path
import time
import unittest
import unittest.mock

from devflow2 import Fehler, ablauf, inhaber, laeufe, roadmap
from hilfen import PLAN, Fall, git


class KetteTest(Fall):
    def starten(self, aufgabe='A1', **kw):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        return ablauf.entwickeln(self.p, aufgabe, **kw)

    def test_plan_bis_merge(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'app/wetter.py': 'TEMP = 12\n', 'berichte/A1.md': '# A1\n'}}],
                      codex=[self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        self.assertEqual((self.repo / 'app/wetter.py').read_text(), 'TEMP = 12\n')
        self.assertIn('Bestanden', (self.repo / 'berichte/A1-review-sol.md').read_text())
        self.assertFalse((self.tmp / 'demo-claude-a1').exists())
        self.assertFalse(git(self.repo, 'branch', '--list', 'claude-a1'))
        self.assertEqual(git(self.repo, 'worktree', 'list').count('\n'), 0)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        z = self.zeile('A1')
        self.assertEqual(z[2], 'Claude, Review Sol')
        self.assertIn('gemergt', z[5])
        self.assertIn('**in Arbeit** –', self.roadmap())
        self.assertIn('A1 gemergt', self.roadmap())
        self.assertEqual([(l['art'], l['zustand']) for l in laeufe.alle()],
                         [('entwickeln', 'fertig'), ('test', 'fertig'), ('review', 'fertig'), ('mergen', 'fertig')])
        review = self.aufrufe('codex')[0]
        self.assertIn('--output-schema', review['argv'])
        self.assertTrue(review['cwd'].endswith('demo-review-a1-1'))
        self.assertIn('Du bist Sol', review['argv'][-1])
        log = git(self.repo, 'log', '--format=%s', 'main')
        self.assertIn('roadmap: A1 → erledigt', log)
        self.assertIn('docs: A1 Review Sol Runde 1: bestanden', log)

    def test_review_nacharbeit_in_frischer_sitzung(self):
        self.drehbuch(claude=[{'tue': 'commit'}, {'tue': 'commit', 'dateien': {'grenzfall.txt': 'ok\n'}}],
                      codex=[self.nacharbeit(), self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        claude = self.aufrufe('claude')
        self.assertNotIn('--resume', claude[0]['argv'])
        self.assertNotIn('--resume', claude[1]['argv'])
        self.assertIn('frischen Sitzung', claude[1]['argv'][-1])
        self.assertIn('Aufgabe A1', claude[1]['argv'][-1])
        self.assertIn('M1 (P2) arbeit.txt:1: Grenzfall fehlt', claude[1]['argv'][-1])
        self.assertTrue((self.repo / 'berichte/A1-review-sol.md').exists())
        self.assertTrue((self.repo / 'berichte/A1-nachreview-sol.md').exists())
        nachpruefung = self.aufrufe('codex')[1]['argv'][-1]
        self.assertIn('M1', nachpruefung)
        self.assertIn('Nachprüfung', nachpruefung)
        erste = [l for l in laeufe.alle("demo", "A1") if l["art"] == "review"][0]
        self.assertIn(f"git diff {erste['commit']}..", nachpruefung)
        self.assertNotIn('Nachprüfung', self.aufrufe('codex')[0]['argv'][-1])
        self.assertIn('Im Zweifel P3', self.aufrufe('codex')[0]['argv'][-1])

    def test_rote_tests_fuehren_zu_nacharbeit(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'kaputt': 'x'}}, {'tue': 'commit', 'loeschen': ['kaputt']}],
                      codex=[self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.assertEqual([l['art'] for l in laeufe.alle()], ['entwickeln', 'test', 'nacharbeit', 'test', 'review', 'mergen'])
        self.assertIn('Projekttests schlagen fehl', self.aufrufe('claude')[1]['argv'][-1])
        self.assertIn('kaputt gefunden', self.aufrufe('claude')[1]['argv'][-1])

    def test_nach_zwei_runden_frage_an_inhaber(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {f'r{i}.txt': 'x'}} for i in range(3)],
                      codex=[self.nacharbeit()] * 3)
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich')
        self.ruhig()
        self.assertEqual(len(self.aufrufe('codex')), 3)
        fragen = roadmap.fragen(self.roadmap())
        self.assertEqual(fragen[0]['bezug'], 'A1')
        self.assertIn('nach 2 Nacharbeitsrunden', fragen[0]['text'])
        self.assertIn('**Braucht dich** (1)', self.roadmap())

    def test_frage_in_rollenform_bei_eingestelltem_namen(self):
        """Die Projektvorlage nennt die Rolle; auch mit eingestelltem Namen zählt `FRAGE AN DEN INHABER:` als Frage."""
        self.drehbuch(claude=[{'tue': 'frage', 'marker': 'FRAGE AN DEN INHABER:'}], codex=[])
        with unittest.mock.patch.object(inhaber, 'name', return_value='Anna'):
            self.assertEqual(inhaber.frage_marker_alle(), ('FRAGE AN ANNA:', 'FRAGE AN DEN INHABER:'))
            self.starten()
            self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich')
            self.ruhig()
        self.assertIn('Celsius oder Fahrenheit', roadmap.fragen(self.roadmap())[0]['text'])

    def test_frage_des_entwicklers_und_antwort(self):
        self.drehbuch(claude=[{'tue': 'frage'}, {'tue': 'commit'}], codex=[self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich')
        self.ruhig()
        self.assertIn('Celsius oder Fahrenheit', roadmap.fragen(self.roadmap())[0]['text'])
        self.assertEqual(ablauf.lage()['projekte']['demo']['offene_fragen'], ['F1 · A1'])
        ablauf.antwort_eintragen(self.p, 'F1', 'Celsius')
        self.assertEqual(self.zeile('A1')[3], 'bereit')
        ablauf.nacharbeit(self.p, 'A1', 'Inhaber: Celsius.')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.assertIn('Inhaber: Celsius.', self.aufrufe('claude')[1]['argv'][-1])

    def test_frische_nacharbeit_behaelt_auftrag_hinweise_und_antworten(self):
        """Review M1: Zusatzauftrag, Hinweise und Antworten gehen in jede frische Sitzung mit."""
        self.drehbuch(claude=[{'tue': 'frage'}, {'tue': 'commit'}, {'tue': 'commit', 'dateien': {'b.txt': 'b\n'}}],
                      codex=[self.nacharbeit(), self.bestanden()])
        self.starten(auftrag='Nur metrische Einheiten.')
        self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich')
        self.ruhig()
        ablauf.antwort_eintragen(self.p, 'F1', 'Celsius')
        ablauf.nacharbeit(self.p, 'A1', 'Inhaber: Celsius, bitte runden.')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        hinweis, review = self.aufrufe('claude')[1]['argv'], self.aufrufe('claude')[2]['argv']
        for argv in (hinweis, review):
            self.assertNotIn('--resume', argv)
            self.assertIn('Nur metrische Einheiten.', argv[-1])
            self.assertIn('steuerung):** Celsius', argv[-1])
            self.assertIn('Celsius', argv[-1])
        self.assertIn('bitte runden', review[-1])
        self.assertIn('Grenzfall fehlt', review[-1])

    def test_lange_frage_behaelt_die_antwort(self):
        """Nachprüfung M1: lange Frageblöcke werden nicht abgeschnitten."""
        from devflow2 import auftraege
        block = '### F1 · 29.10. · A1\n\n' + 'Lange Frage. ' * 200 + '\n\n**Antwort Inhaber (29.10.):** Celsius'
        text = auftraege._vorgaben({'vorgaben': [block]})
        self.assertGreater(len(block), 2000)
        self.assertIn('**Antwort Inhaber (29.10.):** Celsius', text)

    def test_kontingent_pause_und_fortsetzung(self):
        self.drehbuch(claude=[{'tue': 'kontingent', 'in': 1}, {'tue': 'commit'}], codex=[{'tue': 'kontingent', 'in': 1}, self.bestanden()])
        self.starten()
        self.warten(lambda: any(l['zustand'] == 'kontingent' for l in laeufe.alle()), text='Kontingentpause')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        claude, codex = self.aufrufe('claude'), self.aufrufe('codex')
        self.assertIn('--resume', claude[1]['argv'])
        self.assertIn('Mach dort weiter', claude[1]['argv'][-1])
        self.assertEqual(codex[1]['argv'][1:3], ['resume', codex[1]['argv'][2]])
        self.assertTrue(codex[1]['argv'][2].startswith('thread-codex-'))

    def test_anhalten_alles(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        l = self.starten()
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'laeuft' and len(self.aufrufe()) == 1)
        ablauf.anhalten('alles')
        self.assertEqual(laeufe.laden(l['id'])['zustand'], 'angehalten')
        self.warten(lambda: not laeufe.lebt(laeufe.laden(l['id'])), sekunden=10, text='Prozessende')
        with self.assertRaises(Fehler):
            ablauf.entwickeln(self.p, 'A2')
        self.assertEqual(ablauf.fortsetzen('alles', 'dashboard'), {'angehaltene_laeufe': [l['id']]})
        self.assertFalse(laeufe.angehalten())

    def test_ungueltiges_review_wird_nachgefragt(self):
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[{'tue': 'text', 'text': 'Sieht gut aus.'}, self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        codex = self.aufrufe('codex')
        self.assertEqual(codex[1]['argv'][1], 'resume')
        self.assertIn('kein gültiges JSON', codex[1]['argv'][-1])

    def test_ungueltiges_review_bleibt_fortsetzbar_auch_ohne_kopie(self):
        """M5: Kopie fehlt beim Fortsetzen → wird auf dem geprüften Commit neu angelegt."""
        ungueltig = {'tue': 'text', 'text': 'Sieht gut aus.'}
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[ungueltig, ungueltig])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'blockiert')
        self.ruhig()
        self.assertIn('ohne gültiges Urteil', self.zeile('A1')[5])
        review = [l for l in laeufe.alle() if l['art'] == 'review'][0]
        kopie = Path(review['cwd'])
        git(self.repo, 'worktree', 'remove', '--force', str(kopie))
        self.drehbuch(codex=[self.bestanden()])
        ablauf.fortsetzen(review['id'])
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.assertEqual(self.aufrufe('codex')[2]['cwd'], str(kopie))
        self.assertIn('kein gültiges JSON', self.aufrufe('codex')[2]['argv'][-1])

    def test_langer_reviewbericht_wird_vollstaendig_ausgewertet(self):
        """M3: kein Abschneiden vor dem Parsen."""
        bericht = '# Review\n\n' + 'Geprüft und in Ordnung. ' * 1500
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden(bericht)])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.assertGreater(len(bericht), 30000)
        self.assertEqual((self.repo / 'berichte/A1-review-sol.md').read_text(), bericht.rstrip() + '\n')

    def test_anhalten_sichert_die_sitzung(self):
        """M1: Stopp mitten im ersten Aufruf, Fortsetzen in derselben Sitzung."""
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}, {'tue': 'commit'}], codex=[self.bestanden()])
        l = self.starten()
        self.warten(lambda: laeufe.laden(l['id']).get('session'), text='Sitzung gesichert')
        sid = laeufe.laden(l['id'])['session']
        gestoppt = ablauf.anhalten(l['id'])
        self.assertEqual(gestoppt['zustand'], 'angehalten')
        self.assertFalse(laeufe.lebt(laeufe.laden(l['id'])))
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        zweiter = self.aufrufe('claude')[1]['argv']
        self.assertEqual(zweiter[zweiter.index('--resume') + 1], sid)
        self.assertIn('unterbrochen', zweiter[-1])

    def test_globaler_halt_ohne_nebenwirkungen(self):
        """M7: abgelehnter Start hinterlässt weder Branch noch Status noch Lauf."""
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        ablauf.anhalten('alles')
        with self.assertRaisesRegex(Fehler, 'angehalten'):
            ablauf.entwickeln(self.p, 'A1')
        self.assertEqual(self.zeile('A1')[3], 'bereit')
        self.assertFalse(git(self.repo, 'branch', '--list', 'claude-a1'))
        self.assertEqual(laeufe.alle(), [])
        ablauf.fortsetzen('alles', 'dashboard')
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')

    def test_branch_darf_roadmap_nicht_aendern(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'ROADMAP.md': 'kaputt\n'}},
                              {'tue': 'commit', 'dateien': {'ROADMAP.md': 'immer noch\n'}}])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'blockiert')
        self.assertIn('ändert ROADMAP.md', self.aufrufe('claude')[1]['argv'][-1])
        self.assertEqual(len(self.aufrufe('codex')), 0)

    def test_zeitlimit(self):
        os.environ['DEVFLOW2_ZEITLIMIT'] = '1'
        try:
            self.drehbuch(claude=[{'tue': 'schlafen', 's': 5}])
            self.starten()
            self.warten(lambda: self.zeile('A1')[3] == 'blockiert')
            self.assertIn('Zeitlimit', self.zeile('A1')[5])
        finally:
            os.environ.pop('DEVFLOW2_ZEITLIMIT')

    def test_nicht_committet_wird_nachgehakt(self):
        self.drehbuch(claude=[{'tue': 'ohne_commit'}, {'tue': 'commit'}], codex=[self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        zweiter = self.aufrufe('claude')[1]['argv']
        self.assertIn('nicht committet', zweiter[-1])
        self.assertEqual(zweiter[zweiter.index('--resume') + 1], laeufe.alle(aufgabe='A1')[0]['session'])
        self.assertTrue((self.repo / 'lose.txt').exists())


class HandTest(Fall):
    def review_bestanden(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.ruhig()

    def test_abgelaufene_claude_anmeldung_steht_unter_braucht_dich(self):
        """S8.4: OAuth abgelaufen → Braucht dich mit Hinweis, ohne Logtext; fortsetzen räumt den Eintrag ab."""
        geheim = 'sk-ant-oat01-GEHEIM'
        self.drehbuch(claude=[{'tue': 'fehler', 'text': f'OAuth session expired, token {geheim}. Please run /login'},
                              {'tue': 'commit', 'dateien': {'berichte/A1.md': '# A1\n'}}], codex=[self.bestanden()])
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        l = ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich')
        z = self.zeile('A1')
        self.assertIn('Anmeldung von claude abgelaufen', z[5])
        self.assertIn('per SSH', z[5])
        self.assertIn(f'fortsetzen("{l["id"]}")', z[5])
        self.assertNotIn(geheim, self.roadmap())
        self.assertTrue(any(b.startswith('A1 ') for b in ablauf.lage()['projekte']['demo']['braucht_dich']))
        hinweis = ablauf.lage()['projekte']['demo']['anmeldung'][0]
        self.assertIn('per SSH', hinweis)
        self.assertNotIn(geheim, hinweis)
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fertig')
        self.assertNotEqual(self.zeile('A1')[3], 'Braucht dich')
        self.assertFalse(any(b.startswith('A1 ') for b in ablauf.lage()['projekte']['demo']['braucht_dich']))
        self.assertNotIn('anmeldung', ablauf.lage()['projekte']['demo'])

    def test_abgelaufene_codex_anmeldung_nennt_codex_login(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[{'tue': 'fehler', 'text': 'Not logged in. Run codex login'}, self.bestanden()])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        r = ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich')
        self.assertIn('Anmeldung von codex abgelaufen', self.zeile('A1')[5])
        self.assertIn('codex login', self.zeile('A1')[5])
        ablauf.fortsetzen(r['id'])
        self.warten(lambda: laeufe.laden(r['id'])['zustand'] == 'fertig')
        self.assertNotEqual(self.zeile('A1')[3], 'Braucht dich')

    def test_anderer_fehler_bleibt_blockiert(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'fehler', 'text': 'Etwas anderes ging schief'}])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.warten(lambda: self.zeile('A1')[3] == 'blockiert')

    def test_kein_selbstreview(self):
        """M6"""
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'commit'}])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        with self.assertRaisesRegex(Fehler, 'das jeweils andere Modell'):
            ablauf.review_anfordern(self.p, 'A1', reviewer='claude')
        self.assertEqual(git(self.repo, 'worktree', 'list').count('\n'), 1)

    def test_fremde_roadmap_aenderung_stoppt_vor_dem_merge(self):
        """M4: Hindernis vor jeder Nebenwirkung erkennen, danach fortsetzbar."""
        self.review_bestanden()
        alt = self.roadmap()
        (self.repo / 'ROADMAP.md').write_text(alt + '\nvon Hand\n')
        l = ablauf.mergen(self.p, 'A1')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler')
        self.assertIn('Nicht committete Änderungen', laeufe.laden(l['id'])['fehler'])
        self.assertNotIn('arbeit.txt', git(self.repo, 'ls-files'))
        self.assertTrue(git(self.repo, 'branch', '--list', 'claude-a1'))
        (self.repo / 'ROADMAP.md').write_text(alt)
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.assertIn('arbeit.txt', git(self.repo, 'ls-files'))

    def test_abschluss_nach_merge_wird_nachgeholt(self):
        """M4: Merge ist passiert, Status und Aufräumen fehlen → fortsetzen holt sie nach, ohne neuen Entwicklerlauf."""
        self.review_bestanden()
        l = ablauf.mergen(self.p, 'A1', automatisch=False)
        self.ruhig()
        # Zustand nach Abbruch zwischen Merge und Statuscommit nachstellen
        lauf = laeufe.laden(l['id'])
        from devflow2 import repo
        repo.roadmap_aendern(self.p, lambda t: roadmap.setze(t, 'A1', '29.10.', 'Review'), 'test: Status zurück')
        lauf.update(zustand='fehler', fehler='Abbruch nachgestellt', arbeit_fertig=False)
        laeufe.speichern(lauf)
        with self.assertRaisesRegex(Fehler, 'schon gemergt'):
            ablauf.mergen(self.p, 'A1')
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.assertEqual(laeufe.laden(l['id'])['zustand'], 'fertig')
        self.assertEqual(self.roadmap().count('A1 gemergt'), 1)  # kein doppelter Protokolleintrag beim Nachholen

    def test_stop_nach_eigenem_rebase_ist_fortsetzbar(self):
        """R1: Rebase durch devflow ist kein neuer Branchstand; spätere fremde Commits schon."""
        self.review_bestanden()
        (self.repo / 'devflow.toml').write_text(f'test = "test -f {self.tmp}/frei || sleep 30"\n')
        git(self.repo, 'commit', '-q', '-am', 'langsamere Tests auf main')
        l = ablauf.mergen(self.p, 'A1', automatisch=False)
        log = laeufe.verzeichnis(l['id']) / 'log.jsonl'
        self.warten(lambda: log.exists() and 'Tests nach Rebase' in log.read_text() and laeufe.laden(l['id']).get('kind_pid'))
        ablauf.anhalten(l['id'])
        wt = self.tmp / 'demo-claude-a1'
        (wt / 'spaet.txt').write_text('x')
        git(wt, 'add', '-A')
        git(wt, 'commit', '-q', '-m', 'fremder Commit nach dem Rebase')
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler')
        self.assertIn('nach dem bestandenen Review geändert', laeufe.laden(l['id'])['fehler'])
        git(wt, 'reset', '-q', '--hard', 'HEAD~1')
        (self.tmp / 'frei').write_text('')
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt' and laeufe.laden(l['id'])['zustand'] == 'fertig')
        self.assertIn('arbeit.txt', git(self.repo, 'ls-files'))
        self.assertNotIn('spaet.txt', git(self.repo, 'ls-files'))

    def remote(self, upstream):
        bare = self.tmp / 'remote.git'
        git(self.tmp, 'init', '-q', '--bare', str(bare))
        git(self.repo, 'remote', 'add', 'origin', str(bare))
        if upstream:
            git(self.repo, 'push', '-q', '-u', 'origin', 'main')
        return bare

    def test_push_ohne_upstream(self):
        """R2: Remote ohne Upstream wird nicht stumm übersprungen."""
        bare = self.remote(upstream=False)
        self.review_bestanden()
        l = ablauf.mergen(self.p, 'A1')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fertig')
        self.assertEqual(git(bare, 'rev-parse', 'main'), git(self.repo, 'rev-parse', 'HEAD'))
        self.assertEqual(laeufe.laden(l['id'])['push'], 'gepusht nach origin')

    def test_fehlgeschlagener_push_bleibt_offen_und_wird_nachgeholt(self):
        """R2: Push-Fehler → Lauf fehlgeschlagen, kein Deploy, fortsetzen pusht nach Behebung."""
        bare = self.remote(upstream=True)
        hook = bare / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\necho abgelehnt >&2\nexit 1\n')
        hook.chmod(0o755)
        self.review_bestanden()
        l = ablauf.mergen(self.p, 'A1')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler')
        self.assertIn('Push nach origin fehlgeschlagen', laeufe.laden(l['id'])['fehler'])
        self.warten(lambda: 'Abschluss offen' in self.zeile('A1')[5])  # die Roadmap folgt erst nach dem Zustand `fehler`
        self.assertNotEqual(git(bare, 'rev-parse', 'main'), git(self.repo, 'rev-parse', 'HEAD'))
        hook.unlink()
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fertig')
        self.assertEqual(self.zeile('A1')[3], 'erledigt')
        self.assertEqual(git(bare, 'rev-parse', 'main'), git(self.repo, 'rev-parse', 'HEAD'))

    def test_gescheiterter_statuscommit_wird_zurueckgenommen_und_nachgeholt(self):
        """R3: Erfolg nur mit Commit; eigene uncommittete Änderung wird zurückgenommen."""
        self.review_bestanden()
        hook = self.repo / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\n[ "$(git rev-parse --abbrev-ref HEAD)" = main ] && exit 1\nexit 0\n')
        hook.chmod(0o755)
        l = ablauf.mergen(self.p, 'A1')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler')
        self.assertTrue(laeufe.laden(l['id']).get('merge'))
        self.warten(lambda: git(self.repo, 'status', '--porcelain') == '')  # die Rücknahme folgt erst nach dem Zustand `fehler`
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual(roadmap.finde(git(self.repo, 'show', 'HEAD:ROADMAP.md'), 'A1')[1][3], 'Review')
        hook.unlink()
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fertig')
        self.assertEqual(roadmap.finde(git(self.repo, 'show', 'HEAD:ROADMAP.md'), 'A1')[1][3], 'erledigt')
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_merge_nur_nach_bestandenem_review_auf_genau_dem_stand(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        with self.assertRaisesRegex(Fehler, 'kein bestandenes Review'):
            ablauf.mergen(self.p, 'A1')
        ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.ruhig()
        self.assertIn('Merge offen', self.zeile('A1')[5])
        wt = self.tmp / 'demo-claude-a1'
        (wt / 'spaet.txt').write_text('x')
        git(wt, 'add', '-A')
        git(wt, 'commit', '-q', '-m', 'später')
        with self.assertRaisesRegex(Fehler, 'nach dem bestandenen Review geändert'):
            ablauf.mergen(self.p, 'A1')
        git(wt, 'reset', '-q', '--hard', 'HEAD~1')
        ablauf.mergen(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')

    def test_rebase_ueber_neue_commits_auf_main_testet_erneut(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.ruhig()
        (self.repo / 'kaputt').write_text('von main')
        git(self.repo, 'add', 'kaputt')
        git(self.repo, 'commit', '-q', '-m', 'anderer Merge')
        l = ablauf.mergen(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'blockiert')
        self.assertIn('Tests nach dem Rebase rot', laeufe.laden(l['id'])['fehler'])
        self.assertNotIn('arbeit.txt', git(self.repo, 'ls-files'))

    def test_roadmap_mit_fremder_aenderung_wird_nicht_ueberschrieben(self):
        (self.repo / 'ROADMAP.md').write_text(self.roadmap() + '\nvon Hand\n')
        with self.assertRaisesRegex(Fehler, 'Nicht committete Änderungen'):
            ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.assertIn('von Hand', self.roadmap())

    def test_plan_ohne_abnahme_oder_uncommitted(self):
        datei = self.repo / 'plan/wetter.md'
        datei.write_text(datei.read_text().replace('abgenommen vom Inhaber am 29.10.2026', 'Entwurf'))
        with self.assertRaisesRegex(Fehler, 'nicht committete'):
            ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        git(self.repo, 'commit', '-q', '-am', 'Entwurf')
        with self.assertRaisesRegex(Fehler, 'nicht abgenommen'):
            ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')

    def test_zweitmeinung_landet_auf_main(self):
        self.drehbuch(codex=[{'tue': 'text', 'text': '# Zweitmeinung Sol · plan/wetter.md\n\nTragfähig.'}])
        l = ablauf.meinung_anfordern(self.p, 'plan/wetter.md', 'Reicht der Aufgabenschnitt?')
        self.ruhig()
        self.assertEqual(laeufe.laden(l['id'])['zustand'], 'fertig')
        self.assertIn('Tragfähig', (self.repo / 'berichte/wetter-meinung-sol.md').read_text())
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertIn('Reicht der Aufgabenschnitt?', self.aufrufe('codex')[0]['argv'][-1])
        self.assertEqual(git(self.repo, 'worktree', 'list').count('\n'), 0)

    def test_stand_werkzeuge(self):
        ablauf.aufgabe_anlegen(self.p, 'K1', 'Kleinigkeit', 'Claude', 'bereit')
        ablauf.status_setzen(self.p, 'K1', 'entfällt', details='doch nicht nötig')
        self.assertEqual(self.zeile('K1')[3], 'entfällt')
        ablauf.protokoll(self.p, 'Test')
        self.assertIn('· Test', self.roadmap())
        info = ablauf.aufgabe(self.p, 'K1')
        self.assertEqual((info['status'], info['zweig'], info['laeufe']), ('entfällt', None, []))
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')


class ZeitlimitTest(Fall):
    test_befehl = '(sleep 1.5; touch spaet) & sleep 5'

    def test_zeitlimit_beendet_auch_kindprozesse(self):
        """M2"""
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.drehbuch(claude=[{'tue': 'commit'}])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        os.environ['DEVFLOW2_ZEITLIMIT'] = '0.5'
        try:
            l = ablauf.testen(self.p, 'A1')
            self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler')
        finally:
            os.environ.pop('DEVFLOW2_ZEITLIMIT')
        time.sleep(2.5)
        self.assertFalse((self.tmp / 'demo-claude-a1' / 'spaet').exists())


class DeployTest(Fall):
    deploy = 'echo deployed > deploy.log'

    def test_deploy_nach_merge(self):
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        ablauf.entwickeln(self.p, 'A1')
        self.warten(lambda: 'Deploy ok' in self.roadmap())
        self.assertEqual((self.repo / 'deploy.log').read_text(), 'deployed\n')


class McpTest(unittest.TestCase):
    def test_werkzeuge_sind_angemeldet(self):
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest('mcp nicht installiert (Test mit ~/.local/share/devflow2/venv ausführen)')
        import asyncio
        from devflow2.mcp_server import server
        namen = {t.name for t in asyncio.run(server.list_tools())}
        self.assertTrue({'lage', 'plan_uebernehmen', 'entwickeln', 'review_anfordern', 'nacharbeit', 'mergen',
                         'frage_stellen', 'antwort_eintragen', 'anhalten', 'fortsetzen', 'meinung_anfordern'} <= namen)

class ModellTest(Fall):
    weitere_toml = 'claude_modell = "sonnet"\n'
    starten = KetteTest.starten

    def test_claude_modell_je_projekt(self):
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        argv = self.aufrufe('claude')[0]['argv']
        self.assertEqual(argv[argv.index('--model') + 1], 'sonnet')
        self.assertNotIn('--effort', argv)
        self.assertNotIn('-m', self.aufrufe('codex')[0]['argv'])
        self.assertIn('Repo-Karte', argv[-1])
        self.assertIn('plan/wetter.md', argv[-1])


class StufenTest(Fall):
    plan_text = PLAN.replace('| Abnahmekriterium |\n|---|---|---|---|---|', '| Abnahmekriterium | Stufe |\n|---|---|---|---|---|---|') \
        .replace('Kachel zeigt Temperatur |', 'Kachel zeigt Temperatur | schwer |').replace('Suche liefert Treffer |', 'Suche liefert Treffer | leicht |')
    weitere_toml = 'stufe = "leicht"\n[stufen.schwer]\nsol_effort = "xhigh"\n'
    starten = KetteTest.starten

    def test_stufe_aus_dem_plan(self):
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        claude, codex = self.aufrufe('claude')[0]['argv'], self.aufrufe('codex')[0]['argv']
        self.assertEqual(claude[claude.index('--model') + 1], 'opus')
        self.assertEqual(claude[claude.index('--effort') + 1], 'high')
        self.assertEqual(codex[codex.index('-c') + 1], 'model_reasoning_effort="xhigh"')
        self.assertNotIn('-m', codex)

    def test_stufe_des_projekts_und_unbekannte_stufe(self):
        self.assertEqual(self.p.cli('claude'), {'stufe': 'leicht', 'cli_modell': 'sonnet', 'cli_effort': 'medium'})
        self.assertEqual(self.p.cli('sol', 'A2'), {'stufe': 'leicht', 'cli_effort': 'medium'})
        self.p.cfg['stufe'] = 'riesig'
        with self.assertRaisesRegex(Fehler, 'Stufe .riesig. unbekannt'):
            self.p.cli('claude')


if __name__ == '__main__':
    unittest.main()

