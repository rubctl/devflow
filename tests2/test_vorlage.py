"""Projektvorlage und `ops/neues-projekt.py`: Anlegen, Ergänzen, Klonen, Wiederholen; Anmeldung; Basisbranch in der ganzen Kette."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from devflow2 import Fehler, ablauf, auftraege, laeufe, projekte, roadmap
from devflow2.projekte import Projekt
from hilfen import Fall, git

WURZEL = Path(__file__).resolve().parents[1]
SKRIPT = WURZEL / 'ops' / 'neues-projekt.py'
GIT_ENV = dict(GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='t@example.invalid')


def stand(ordner):
    """Pfad → Inhaltsprüfsumme aller Dateien außer .git."""
    return {str(p.relative_to(ordner)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ordner.rglob('*')) if p.is_file() and '.git' not in p.relative_to(ordner).parts}


class VorlageBasis(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-vorlage-'))
        self.addCleanup(subprocess.run, ['rm', '-rf', str(self.tmp)])
        self.env = {**os.environ, **GIT_ENV, 'DEVFLOW2_STATE': str(self.tmp / 'state'), 'PATH': f'{self.tmp / "bin"}:{os.environ["PATH"]}'}
        (self.tmp / 'bin').mkdir()
        alt = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in alt.items()])

    def skript(self, *args, check=True):
        r = subprocess.run([sys.executable, '-B', str(SKRIPT), *map(str, args)], capture_output=True, text=True, env=self.env)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def repo(self, name='alt', zweig='main', dateien=None):
        ziel = self.tmp / name
        ziel.mkdir()
        git(ziel, 'init', '-q', '-b', zweig)
        for pfad, text in (dateien or {}).items():
            (ziel / pfad).parent.mkdir(parents=True, exist_ok=True)
            (ziel / pfad).write_text(text)
        if dateien:
            git(ziel, 'add', '-A')
            git(ziel, 'commit', '-q', '-m', 'start')
        return ziel


class NeuesProjektTest(VorlageBasis):
    def test_neu_angelegt_gelesen_und_angemeldet(self):
        ziel = self.tmp / 'demo'
        out = self.skript(ziel, 'Demo').stdout
        self.assertIn('Neues Repo', out)
        self.assertEqual(git(ziel, 'status', '--porcelain'), '')
        self.assertIn('Demo aus Projektvorlage angelegt', git(ziel, 'log', '--format=%s'))
        for name in ('AGENTS.md', 'CLAUDE.md', 'MASTERPLAN.md', 'ROADMAP.md', 'devflow.toml', 'docs/betrieb.md', 'docs/architektur.md',
                     'docs/entscheidungen/_vorlage.md', 'plan/_vorlage.md', 'berichte/_vorlage.md', 'werkzeuge/roadmap.py', '.gitignore'):
            self.assertTrue((ziel / name).is_file(), name)
        alles = ''.join(p.read_text() for p in ziel.rglob('*') if p.is_file() and '.git' not in p.parts and p.suffix in ('.md', '.toml'))
        self.assertNotIn('{{', alles)
        self.assertIn('# Demo · Roadmap', alles)
        self.assertIn('Zielumgebung', (ziel / 'docs/betrieb.md').read_text())
        self.assertIn('Kernzusagen', (ziel / 'MASTERPLAN.md').read_text())
        text = (ziel / 'ROADMAP.md').read_text()
        self.assertIn('## Vorhaben (grob)', text)
        self.assertEqual([a['id'] for a in roadmap.aufgaben(text)], ['0.1', '0.2', '0.3'])  # von devflow lesbar, grobe Roadmap ist keine Tabelle
        self.assertEqual(roadmap.kanban(text), text)                                          # Kanban ist aktuell
        self.assertEqual(projekte.anmelden(ziel), 'demo')
        p = Projekt('demo')
        self.assertEqual(p.cfg['basis'], 'main')
        self.assertEqual(p.cfg['deploy'], '')

    def test_wiederholung_aendert_nichts(self):
        ziel = self.tmp / 'demo'
        self.skript(ziel, 'Demo')
        vorher, kopf = stand(ziel), git(ziel, 'rev-parse', 'HEAD')
        out = self.skript(ziel, 'Demo').stdout
        self.assertIn('Angelegt: –', out)
        self.assertNotIn('Konflikt', out)
        self.assertEqual(stand(ziel), vorher)
        self.assertEqual(git(ziel, 'rev-parse', 'HEAD'), kopf)
        self.assertEqual(git(ziel, 'status', '--porcelain'), '')
        # eigene Änderungen (Masterplan ausgefüllt) bleiben erhalten; der Konflikt wird genannt, nichts überschrieben
        (ziel / 'MASTERPLAN.md').write_text('# Demo\n\nEigener Text.\n')
        out = self.skript(ziel, 'Demo').stdout
        self.assertEqual((ziel / 'MASTERPLAN.md').read_text(), '# Demo\n\nEigener Text.\n')
        self.assertIn('Konflikt, nicht geändert: MASTERPLAN.md', out)

    def test_fehlende_dateien_werden_nachgeholt_ohne_doppelte(self):
        ziel = self.tmp / 'demo'
        self.skript(ziel, 'Demo')
        (ziel / 'docs' / 'betrieb.md').unlink()
        out = self.skript(ziel, 'Demo').stdout
        self.assertIn('Angelegt: docs/betrieb.md', out)
        self.assertEqual(self.skript(ziel, 'Demo').stdout.count('Angelegt: –'), 1)
        self.assertEqual((ziel / 'ROADMAP.md').read_text().count('| 0.1 |'), 1)

    def test_nicht_leeres_verzeichnis_ohne_git_und_schlechter_name(self):
        (self.tmp / 'x').mkdir()
        (self.tmp / 'x' / 'datei').write_text('')
        r = self.skript(self.tmp / 'x', check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('nicht leer', r.stderr)
        r = self.skript(self.tmp / 'y', 'schlecht name!', check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('ungeeignet', r.stderr)
        self.assertFalse((self.tmp / 'y').exists())

    def test_kanban_und_statuspruefung_der_vorlage(self):
        ziel = self.tmp / 'k'
        self.skript(ziel)
        pfad = ziel / 'ROADMAP.md'
        pfad.write_text(pfad.read_text().replace('| 0.2 |', '| 1.1 | **Wetter-Kachel** mit Ort | Claude | Review | 28.10. | |\n| 0.2 |'))
        subprocess.run([sys.executable, '-B', 'werkzeuge/roadmap.py'], cwd=ziel, check=True)
        self.assertIn('- `1.1` Wetter-Kachel · Claude · 28.10.', pfad.read_text())
        pfad.write_text(pfad.read_text().replace('| Review |', '| fertig |'))
        r = subprocess.run([sys.executable, '-B', 'werkzeuge/roadmap.py'], cwd=ziel, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Status „fertig“ unbekannt', r.stderr)


class VorhandenesRepoTest(VorlageBasis):
    def test_nur_ergaenzen_konflikte_nennen_nicht_committen(self):
        ziel = self.repo(dateien={'README.md': 'eigenes README\n', 'AGENTS.md': '# eigene Regeln\n', 'CLAUDE.md': 'nichts\n',
                                  'devflow.toml': 'test = "make"\n'})
        (ziel / '.gitignore').write_text('build/\n')
        git(ziel, 'add', '-A')
        git(ziel, 'commit', '-q', '-m', 'mehr')
        vorher, kopf = stand(ziel), git(ziel, 'rev-parse', 'HEAD')
        out = self.skript(ziel).stdout
        nachher = stand(ziel)
        for name in ('README.md', 'AGENTS.md', 'CLAUDE.md', 'devflow.toml', '.gitignore'):
            self.assertEqual(nachher[name], vorher[name], name)  # unverändert
            self.assertIn(f'Konflikt, nicht geändert: {name}', out)
        self.assertIn('verweist nicht auf `@AGENTS.md`', out)
        self.assertIn('schließt .env, *.key, *.pem nicht aus', out)
        self.assertTrue((ziel / 'MASTERPLAN.md').is_file())
        self.assertTrue((ziel / 'ROADMAP.md').is_file())
        self.assertEqual(git(ziel, 'rev-parse', 'HEAD'), kopf)                    # nichts committet
        self.assertIn('?? MASTERPLAN.md', git(ziel, 'status', '--porcelain'))
        self.assertIn('Nicht committet', out)
        # Wiederholung: keine Änderung, keine neuen Dateien, dieselben Konflikte, kein Doppel
        zweit = self.skript(ziel).stdout
        self.assertEqual(stand(ziel), nachher)
        self.assertIn('Angelegt: –', zweit)
        self.assertEqual(sorted(l for l in zweit.split('\n') if l.startswith('Konflikt')), sorted(l for l in out.split('\n') if l.startswith('Konflikt')))
        self.assertEqual(self.skript(ziel).stdout.count('Konflikt, nicht geändert: README.md'), 1)
        # Nach dem Committen ist es ein normales, anmeldbares Projekt
        git(ziel, 'add', '-A')
        git(ziel, 'commit', '-q', '-m', 'Vorlage ergänzt')
        projekte.anmelden(ziel)
        projekte.anmelden(ziel)
        self.assertEqual(list(projekte.alle()), ['alt'])
        self.assertEqual(len(roadmap.aufgaben((ziel / 'ROADMAP.md').read_text())), 3)

    def test_basis_kommt_aus_dem_repo(self):
        ziel = self.repo('trunk-repo', 'trunk', {'README.md': 'x\n'})
        out = self.skript(ziel).stdout
        self.assertIn('(basis: trunk)', out)
        self.assertIn('basis = "trunk"', (ziel / 'devflow.toml').read_text())
        git(ziel, 'add', '-A')
        git(ziel, 'commit', '-q', '-m', 'Vorlage')
        projekte.anmelden(ziel)
        self.assertEqual(Projekt('trunk-repo').cfg['basis'], 'trunk')

    def test_vorhandene_toml_ohne_passende_basis_wird_genannt(self):
        ziel = self.repo('r', 'trunk', {'devflow.toml': 'test = ""\n'})
        out = self.skript(ziel).stdout
        self.assertIn('setzt keine `basis`, das Repo steht auf `trunk`', out)
        self.assertEqual((ziel / 'devflow.toml').read_text(), 'test = ""\n')
        with self.assertRaisesRegex(Fehler, "Basisbranch 'main' gibt es .* nicht"):  # die Anmeldung fängt dasselbe ab
            projekte.anmelden(ziel)
        self.assertEqual(projekte.alle(), {})

    def test_unlesbare_roadmap_wird_genannt(self):
        ziel = self.repo('r', dateien={'ROADMAP.md': '# Alt\n\nOhne Marker.\n'})
        out = self.skript(ziel).stdout
        self.assertIn('Konflikt, nicht geändert: ROADMAP.md', out)
        self.assertIn('für devflow nicht lesbar', out)
        self.assertEqual((ziel / 'ROADMAP.md').read_text(), '# Alt\n\nOhne Marker.\n')

    def test_losgeloester_kopf_braucht_basis(self):
        ziel = self.repo('r', dateien={'README.md': 'x\n'})
        git(ziel, 'checkout', '-q', '--detach')
        r = self.skript(ziel, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--basis', r.stderr)

    def test_falscher_branch_zu_basis(self):
        ziel = self.repo('r', 'trunk', {'README.md': 'x\n'})
        r = self.skript(ziel, '--basis', 'main', check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('steht auf trunk', r.stderr)


class KlonenTest(VorlageBasis):
    def quelle(self, zweig='master'):
        q = self.repo('quelle', zweig, {'README.md': 'fremdes Projekt\n'})
        bare = self.tmp / 'bare.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(q), str(bare)], check=True, env=self.env)
        subprocess.run(['git', '--git-dir', str(bare), 'symbolic-ref', 'HEAD', f'refs/heads/{zweig}'], check=True)
        return bare

    def test_klonen_ergaenzen_und_wiederholen(self):
        bare = self.quelle()
        ziel = self.tmp / 'geklont'
        out = self.skript(ziel, 'Geklont', '--url', bare).stdout
        self.assertIn('Geklontes Repo ergänzt', out)
        self.assertIn('(basis: master)', out)
        self.assertEqual((ziel / 'README.md').read_text(), 'fremdes Projekt\n')
        self.assertIn('Konflikt, nicht geändert: README.md', out)
        self.assertIn('basis = "master"', (ziel / 'devflow.toml').read_text())
        nachher = stand(ziel)
        zweit = self.skript(ziel, 'Geklont', '--url', bare).stdout
        self.assertIn('Angelegt: –', zweit)
        self.assertEqual(stand(ziel), nachher)
        self.assertEqual(git(ziel, 'remote').split(), ['origin'])
        git(ziel, 'add', '-A')
        git(ziel, 'commit', '-q', '-m', 'Vorlage')
        projekte.anmelden(ziel, 'geklont')
        self.assertEqual(Projekt('geklont').cfg['basis'], 'master')

    def test_anderer_origin_wird_abgelehnt(self):
        bare = self.quelle()
        ziel = self.repo('vorhanden', dateien={'README.md': 'x\n'})
        git(ziel, 'remote', 'add', 'origin', 'https://example.invalid/anderes.git')
        r = self.skript(ziel, '--url', bare, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('anderem origin', r.stderr)

    def test_nicht_leeres_ziel_ohne_git_wird_nicht_geklont(self):
        bare = self.quelle()
        (self.tmp / 'voll').mkdir()
        (self.tmp / 'voll' / 'x').write_text('')
        r = self.skript(self.tmp / 'voll', '--url', bare, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('kein Git-Repo', r.stderr)

    def test_github_url_nimmt_gh(self):
        bare = self.quelle()
        gh = self.tmp / 'bin' / 'gh'
        gh.write_text(f'#!/bin/sh\necho "$@" >> "{self.tmp}/gh.log"\nexec git clone -q "{bare}" "$4"\n')
        gh.chmod(0o755)
        ziel = self.tmp / 'ghklon'
        self.skript(ziel, '--url', 'https://github.com/besitzer/projekt')
        self.assertEqual((self.tmp / 'gh.log').read_text().strip(), f'repo clone https://github.com/besitzer/projekt {ziel}')
        self.assertTrue((ziel / 'MASTERPLAN.md').is_file())
        # derselbe Klon unter anderer Schreibweise der URL: weiterverwenden, nicht erneut klonen
        git(ziel, 'remote', 'set-url', 'origin', 'git@github.com:besitzer/projekt.git')
        self.assertIn('Angelegt: –', self.skript(ziel, '--url', 'besitzer/projekt').stdout)
        self.assertEqual(len((self.tmp / 'gh.log').read_text().splitlines()), 1)

    def test_klonfehler_wird_gemeldet(self):
        r = self.skript(self.tmp / 'z', '--url', self.tmp / 'gibtsnicht.git', check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Klonen fehlgeschlagen', r.stderr)


class AnmeldeTest(VorlageBasis):
    def test_basisbranch_muss_existieren_und_doppelt_anmelden_bleibt_ein_eintrag(self):
        ziel = self.tmp / 'demo'
        self.skript(ziel)
        projekte.anmelden(ziel)
        projekte.anmelden(ziel)
        self.assertEqual(projekte.alle(), {'demo': str(ziel)})
        (ziel / 'devflow.toml').write_text('basis = "gibtsnicht"\n')
        with self.assertRaisesRegex(Fehler, "Basisbranch 'gibtsnicht'"):
            projekte.anmelden(ziel, 'zweiter')
        self.assertEqual(list(projekte.alle()), ['demo'])

    def test_repo_ohne_commit_laesst_sich_anmelden(self):
        ziel = self.repo('leer')
        (ziel / 'ROADMAP.md').write_text('x')
        self.assertEqual(projekte.anmelden(ziel), 'leer')


class TrunkKetteTest(Fall):
    """Die ganze Kette mit einem anderen Basisbranch als `main`: es gibt gar keinen Branch `main`."""
    basis = 'trunk'

    def starten(self, **kw):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        return ablauf.entwickeln(self.p, 'A1', **kw)

    def test_kette_bis_merge_mit_rebase(self):
        self.assertEqual(self.p.cfg['basis'], 'trunk')
        self.assertFalse(git(self.repo, 'branch', '--list', 'main'))
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'app/wetter.py': 'TEMP = 12\n', 'berichte/A1.md': '# A1\n'}}], codex=[self.bestanden()])
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        wt = self.tmp / 'demo-claude-a1'
        self.assertTrue(wt.is_dir())
        ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.ruhig()
        (self.repo / 'fremd.txt').write_text('anderer Code auf der Basis\n')   # die Basis bewegt sich nach dem Review
        git(self.repo, 'add', 'fremd.txt')
        git(self.repo, 'commit', '-q', '-m', 'fremd auf trunk')
        ablauf.mergen(self.p, 'A1', automatisch=False)
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        self.assertEqual(git(self.repo, 'symbolic-ref', '--short', 'HEAD'), 'trunk')
        self.assertEqual((self.repo / 'app/wetter.py').read_text(), 'TEMP = 12\n')
        self.assertTrue((self.repo / 'fremd.txt').exists())
        log = git(self.repo, 'log', '--format=%s', 'trunk')
        self.assertIn('roadmap: A1 → erledigt', log)
        self.assertIn('docs: A1 Review Sol Runde 1: bestanden', log)
        self.assertFalse(git(self.repo, 'branch', '--list', 'main'))
        self.assertFalse(git(self.repo, 'branch', '--list', 'claude-a1'))
        self.assertEqual(git(self.repo, 'worktree', 'list').count('\n'), 0)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual([(l['art'], l['zustand']) for l in laeufe.alle()],
                         [('entwickeln', 'fertig'), ('review', 'fertig'), ('mergen', 'fertig')])
        self.assertIn('abgezweigt von `trunk`', self.aufrufe('claude')[0]['argv'][-1])
        review = self.aufrufe('codex')[0]['argv'][-1]
        self.assertIn('gegenüber `trunk`', review)
        self.assertNotIn('`main`', review)

    def test_automatische_kette_mit_tests(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'app/wetter.py': 'TEMP = 12\n', 'berichte/A1.md': '# A1\n'}}], codex=[self.bestanden()])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        self.assertEqual((self.repo / 'app/wetter.py').read_text(), 'TEMP = 12\n')
        self.assertEqual([(l['art'], l['zustand']) for l in laeufe.alle()],
                         [('entwickeln', 'fertig'), ('test', 'fertig'), ('review', 'fertig'), ('mergen', 'fertig')])
        self.assertIn('roadmap: A1 → erledigt', git(self.repo, 'log', '--format=%s', 'trunk'))
        self.assertFalse(git(self.repo, 'branch', '--list', 'main'))

    def test_branch_darf_roadmap_nicht_aendern_nennt_die_basis(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'ROADMAP.md': 'kaputt\n'}}, {'tue': 'commit', 'dateien': {'ROADMAP.md': 'wieder\n'}}])
        self.starten()
        self.warten(lambda: self.zeile('A1')[3] == 'blockiert')
        text = self.aufrufe('claude')[1]['argv'][-1]
        self.assertIn('git merge-base trunk HEAD', text)
        self.assertNotIn('merge-base main', text)

    def test_zweitmeinung_nennt_die_basis(self):
        self.drehbuch(codex=[{'tue': 'text', 'text': '# Zweitmeinung\n\nOk.'}])
        ablauf.meinung_anfordern(self.p, 'plan/wetter.md', 'Passt das?')
        self.ruhig()
        text = self.aufrufe('codex')[0]['argv'][-1]
        self.assertIn('Kopie von `trunk`', text)
        self.assertNotIn('Kopie von main', text)
        self.assertIn('Kopie von `x`', auftraege.meinung('p', 'plan/a.md', '', 'Sol', None, 'x'))

    def test_roadmap_commit_nur_auf_der_basis(self):
        git(self.repo, 'checkout', '-q', '-b', 'nebenbei')
        with self.assertRaisesRegex(Fehler, 'steht nicht auf trunk'):
            ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')


if __name__ == '__main__':
    unittest.main()
