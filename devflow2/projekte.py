"""Angemeldete Projekte und ihre Einstellungen (`devflow.toml` im Repo)."""
import json
import os
import re
import subprocess
from pathlib import Path
import tomllib

from . import Fehler, instanz, roadmap, schreiben

STANDARD = {'basis': 'main', 'test': '', 'deploy': '', 'entwickler': 'claude', 'reviewer': 'sol', 'claude_modell': '',
            'stufe': '', 'karte_auslassen': [], 'selbstlauf_budget': 30,
            'traum_grenze': {'claude': 90, 'sol': 90}, 'traum_reserve': {'claude': 15, 'sol': 15}}
# Stufe je Aufgabe (Spalte im Plan) oder je Projekt (`stufe`); in devflow.toml unter [stufen.<name>] änderbar.
STUFEN = {
    'leicht': {'claude_modell': 'sonnet', 'claude_effort': 'medium', 'sol_modell': '', 'sol_effort': 'medium'},
    'normal': {'claude_modell': 'sonnet', 'claude_effort': 'high', 'sol_modell': '', 'sol_effort': 'high'},
    'schwer': {'claude_modell': 'opus', 'claude_effort': 'high', 'sol_modell': '', 'sol_effort': 'high'},
}
# Steuerläufe des Selbstlaufs (selbstlauf.py); in devflow.toml unter [stufen.steuern], im Plan nicht wählbar.
STEUERN = {'claude_modell': 'sonnet', 'claude_effort': 'medium'}
NAMEN = {'claude': 'Claude', 'sol': 'Sol'}


def zustand():
    return Path(os.environ.get('DEVFLOW2_STATE') or Path.home() / '.local/state/devflow2')


def alle():
    try:
        return json.loads((zustand() / 'projekte.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def _basis_pruefen(repo):
    """Der Basisbranch aus `devflow.toml` (sonst `main`) muss im Repo existieren, sobald es Commits gibt; sonst scheitert später jede Kette."""
    try:
        datei = repo / 'devflow.toml'
        eigen = tomllib.loads(datei.read_text(encoding='utf-8')) if datei.is_file() else {}
    except tomllib.TOMLDecodeError as e:
        raise Fehler(f'{repo}/devflow.toml: {e}') from None
    basis = eigen.get('basis', STANDARD['basis'])
    if not isinstance(basis, str) or not basis:
        raise Fehler('devflow.toml: basis muss ein Branchname sein')
    git = lambda *a: subprocess.run(['git', *a], cwd=repo, capture_output=True, text=True)
    if git('rev-parse', '--verify', '-q', 'HEAD').returncode:
        return  # noch kein Commit: es gibt noch nichts zu prüfen
    if git('rev-parse', '--verify', '-q', f'refs/heads/{basis}').returncode:
        aktuell = git('symbolic-ref', '--short', 'HEAD').stdout.strip()
        raise Fehler(f'Basisbranch {basis!r} gibt es in {repo} nicht' + (f' (das Repo steht auf {aktuell!r})' if aktuell else '')
                     + '; `basis` in devflow.toml auf den Hauptbranch setzen')


def anmelden(pfad, name=None):
    repo = instanz.projektpfad(pfad).resolve()
    if not (repo / '.git').is_dir():
        raise Fehler(f'{repo} ist kein Haupt-Checkout eines Git-Repos')
    if not (repo / 'ROADMAP.md').is_file():
        raise Fehler(f'{repo}/ROADMAP.md fehlt; Projekt zuerst mit ops/neues-projekt.py aus devflow ergänzen')
    name = name or repo.name
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise Fehler(f'Projektname {name!r} ungeeignet')
    _basis_pruefen(repo)
    daten = alle()
    daten[name] = str(repo)
    schreiben(zustand() / 'projekte.json', json.dumps(daten, indent=1, ensure_ascii=False) + '\n')
    return name


def slug(text):
    return re.sub(r'[^a-z0-9.]+', '-', text.lower()).strip('-.') or 'x'


class Projekt:
    def __init__(self, name):
        repos = alle()
        if name not in repos:
            raise Fehler(f'Projekt {name!r} unbekannt; angemeldet: {", ".join(repos) or "keins"}')
        self.name, self.repo = name, Path(repos[name])
        datei = self.repo / 'devflow.toml'
        try:
            eigen = tomllib.loads(datei.read_text(encoding='utf-8')) if datei.is_file() else {}
        except tomllib.TOMLDecodeError as e:
            raise Fehler(f'{datei}: {e}') from None
        self.cfg = {**STANDARD, **eigen}
        eigene_stufen = dict(eigen.get('stufen', {}))
        self.cfg['steuern'] = {**STEUERN, **eigene_stufen.pop('steuern', {})}
        self.cfg['stufen'] = {s: {**STUFEN.get(s, {}), **eigene_stufen.get(s, {})} for s in {*STUFEN, *eigene_stufen}}
        for rolle in ('entwickler', 'reviewer'):
            if self.cfg[rolle] not in NAMEN:
                raise Fehler(f'devflow.toml: {rolle} muss claude oder sol sein')
        budget = self.cfg['selbstlauf_budget']
        if type(budget) is not int or budget < 0:
            raise Fehler('devflow.toml: selbstlauf_budget muss eine ganze Zahl ab 0 sein')
        for name in ('traum_grenze', 'traum_reserve'):  # Prozent je Modell, einzelne Modelle dürfen fehlen
            wert = eigen.get(name, {})
            if (not isinstance(wert, dict) or set(wert) - set(NAMEN)
                    or any(type(x) not in (int, float) or not 0 <= x <= 100 for x in wert.values())):
                raise Fehler(f'devflow.toml: {name} braucht je Modell (claude, sol) eine Zahl von 0 bis 100')
            self.cfg[name] = {**STANDARD[name], **wert}

    def stufe(self, aufgabe=None):
        """Stufe aus der Planzeile der Aufgabe, sonst die des Projekts; leer = CLI-Standard."""
        for datei in sorted((self.repo / 'plan').glob('*.md')) if aufgabe else []:
            if datei.name.startswith('_'):
                continue
            try:
                zeilen = roadmap.plan(datei.read_text(encoding='utf-8'))['aufgaben']
            except Fehler:
                continue
            for z in zeilen:
                if z['id'] == aufgabe and z['stufe']:
                    return z['stufe']
        return self.cfg['stufe']

    def cli(self, modell, aufgabe=None):
        """Modell und Effort für einen Lauf von `modell` (claude/sol)."""
        stufe = self.stufe(aufgabe)
        if stufe and stufe not in self.cfg['stufen']:
            raise Fehler(f'Stufe {stufe!r} unbekannt; bekannt: {", ".join(sorted(self.cfg["stufen"]))}')
        werte = self.cfg['stufen'].get(stufe, {})
        out = dict(stufe=stufe, cli_modell=werte.get(f'{modell}_modell', ''), cli_effort=werte.get(f'{modell}_effort', ''))
        if modell == 'claude' and not out['cli_modell']:
            out['cli_modell'] = self.cfg['claude_modell']
        return {k: v for k, v in out.items() if v}

    def steuer_cli(self):
        """Modell und Effort für Steuerläufe."""
        s = self.cfg['steuern']
        return {k: v for k, v in dict(stufe='steuern', cli_modell=s.get('claude_modell'), cli_effort=s.get('claude_effort')).items() if v}

    @property
    def roadmap(self):
        return self.repo / 'ROADMAP.md'

    def branch(self, aufgabe, entwickler):
        return f'{entwickler}-{slug(aufgabe)}'

    def worktree(self, name):
        return self.repo.parent / f'{self.repo.name}-{name}'
