"""Testumgebung: eigenes Zustandsverzeichnis, Projekt-Repo aus Vorlage-ähnlichem Stand, nachgebaute CLIs."""
import json
import os
os.environ.setdefault('DEVFLOW2_INSTANZ', os.devnull)  # nie die echte instanz.toml lesen
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from devflow2 import laeufe, projekte, roadmap
from devflow2.projekte import Projekt

ROADMAP = """# demo · Roadmap

Einzige Quelle für Stand und Aufgaben.

## Aktuell

<!-- aktuell:start -->
<!-- aktuell:end -->

## Offene Fragen an den Inhaber

Neueste oben.

_Keine._

## Aufgaben

| ID | Aufgabe | Wer | Status | Datum | Details |
|---|---|---|---|---|---|
| 0.1 | Projekt angelegt | Claude | erledigt | 29.10. | |

## Protokoll

- 29.10.2026 · Projekt angelegt.
"""

PLAN = """# Vorhaben Wetter · Claude · 29.10.2026

**Stand:** abgenommen vom Inhaber am 29.10.2026

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium |
|---|---|---|---|---|
| A1 | Wetter-Kachel | claude | `app/wetter.py` | Kachel zeigt Temperatur |
| A2 | Suche | sol | `app/suche.py` | Suche liefert Treffer |
"""

TEST = 'python3 -c "import os,sys; os.path.exists(\'kaputt\') and sys.exit(\'kaputt gefunden\')"'
CLI = Path(__file__).with_name('fake_cli.py')


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Fall(unittest.TestCase):
    """Basis: frisches Projekt `demo` mit Plan, Tests über devflow.toml, nachgebaute CLIs."""
    test_befehl = TEST
    deploy = ''
    weitere_toml = ''
    plan_text = PLAN
    basis = 'main'  # Basisbranch des Test-Repos; ein anderer Wert wird auch in devflow.toml gesetzt

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-'))
        self.fake = self.tmp / 'fake'
        self.fake.mkdir()
        for w in ('claude', 'codex'):
            exe = self.fake / w
            exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{CLI}" {w} "$@"\n')
            exe.chmod(0o755)
            (self.fake / f'{w}.json').write_text('[]')
        env = dict(DEVFLOW2_STATE=str(self.tmp / 'state'), DEVFLOW2_CONFIG=str(self.tmp / 'config'), DEVFLOW2_CLAUDE=str(self.fake / 'claude'),
                   DEVFLOW2_CODEX=str(self.fake / 'codex'), DEVFLOW2_CODEX_SITZUNGEN=str(self.tmp / 'codex-sitzungen'), DEVFLOW2_TESTLAUF='', DEVFLOW2_TAKT='0.2',
                   DEVFLOW2_KONTINGENT_MIN='0.02', DEVFLOW2_MAX='2', FAKE_DIR=str(self.fake),
                   GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='t@example.invalid',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='t@example.invalid')
        self._alt = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.repo = self.tmp / 'demo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', self.basis)
        (self.repo / 'ROADMAP.md').write_text(ROADMAP)
        (self.repo / 'AGENTS.md').write_text('# Regeln\n')
        (self.repo / 'plan').mkdir()
        (self.repo / 'plan' / 'wetter.md').write_text(self.plan_text)
        (self.repo / '.gitignore').write_text('__pycache__/\n')
        toml = f'test = {json.dumps(self.test_befehl)}\n' + (f'deploy = {json.dumps(self.deploy)}\n' if self.deploy else '') + (f'basis = {json.dumps(self.basis)}\n' if self.basis != 'main' else '') + self.weitere_toml
        (self.repo / 'devflow.toml').write_text(toml)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'start')
        projekte.anmelden(self.repo, 'demo')
        self.p = Projekt('demo')

    def tearDown(self):
        for l in laeufe.alle():
            if l['zustand'] in laeufe.AKTIV:
                laeufe.anhalten(l['id'])
        for k, v in self._alt.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        subprocess.run(['rm', '-rf', str(self.tmp)])

    def drehbuch(self, claude=(), codex=()):
        (self.fake / 'claude.json').write_text(json.dumps(list(claude)))
        (self.fake / 'codex.json').write_text(json.dumps(list(codex)))

    def aufrufe(self, werkzeug=None):
        try:
            zeilen = (self.fake / 'aufrufe.jsonl').read_text().splitlines()
        except FileNotFoundError:
            return []
        return [a for a in map(json.loads, zeilen) if werkzeug in (None, a['werkzeug'])]

    def roadmap(self):
        return (self.repo / 'ROADMAP.md').read_text()

    def zeile(self, aufgabe):
        return roadmap.finde(self.roadmap(), aufgabe)[1]

    def warten(self, bedingung, sekunden=60, text=''):
        ende = time.time() + sekunden
        while time.time() < ende:
            if bedingung():
                return
            time.sleep(0.2)
        laeufe_info = [(l['id'], l['zustand'], l.get('fehler')) for l in laeufe.alle()]
        self.fail(f'Zeitüberschreitung {text}: Status {[(a["id"], a["status"], a["details"]) for a in roadmap.aufgaben(self.roadmap())]}, Läufe {laeufe_info}')

    def ruhig(self):
        """Warten, bis kein Lauf mehr aktiv ist."""
        self.warten(lambda: all(l['zustand'] not in laeufe.AKTIV for l in laeufe.alle()), text='bis Ruhe')

    @staticmethod
    def bestanden(bericht='# Review\n\nBestanden.'):
        return {'tue': 'urteil', 'urteil': {'urteil': 'bestanden', 'befunde': [], 'bericht': bericht}}

    @staticmethod
    def nacharbeit(was='Grenzfall fehlt'):
        return {'tue': 'urteil', 'urteil': {'urteil': 'nacharbeit', 'bericht': '# Review\n\nNacharbeit.',
                                            'befunde': [{'nr': 'M1', 'schwere': 'P2', 'ort': 'arbeit.txt:1', 'was': was}]}}
