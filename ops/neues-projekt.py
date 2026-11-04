#!/usr/bin/env python3
"""Legt ein Projekt aus `vorlage/projekt` an oder ergänzt ein vorhandenes Repo darum: Masterplan, md-Roadmap mit Kanban und grober
Roadmap, Pläne, Berichte, Wiki (`docs/` mit Betrieb und Entscheidungen), Modellregeln, `devflow.toml` mit `basis`.

Aufruf:
  ops/neues-projekt.py <verzeichnis> [Name] [--basis BRANCH]   grüne Wiese oder vorhandenes lokales Repo
  ops/neues-projekt.py <verzeichnis> [Name] --url URL          Repo klonen (`gh repo clone` für GitHub, sonst `git clone`)

- Neues oder leeres Verzeichnis: Vorlage kopieren, `git init -b main`, erster Commit.
- Vorhandenes Repo (auch frisch geklont, auch ein zweiter Lauf): nur fehlende Dateien ergänzen, nichts überschreiben, nicht committen.
  Vorhandene Dateien, die von der Vorlage abweichen, werden als Konflikt genannt und bleiben unverändert.
- `basis` in `devflow.toml` ist der Branch, den das Repo schon hat (bei einem Klon der Standardbranch des Remotes), nicht fest `main`.
- Keine Anmeldung und kein Remote: das macht der Inhaber bzw. der Skill (`projekt_anmelden`).
"""
import argparse
import datetime
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
VORLAGE = WURZEL / 'vorlage' / 'projekt'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')  # wie bei der Anmeldung (devflow2.projekte.anmelden)
GITHUB = re.compile(r'(?:https?://|git@|ssh://git@)?github\.com[:/]|[\w.-]+/[\w.-]+')
PLATZHALTER = ('{{PROJEKT}}', '{{DATUM}}', '{{TAG}}', '{{BASIS}}')
GEHEIMNISSE = ('.env', '*.key', '*.pem')


class Fehler(SystemExit):
    pass


def git(ziel, *args, check=True):
    r = subprocess.run(['git', *args], cwd=ziel, capture_output=True, text=True)
    if check and r.returncode:
        raise Fehler(f'git {" ".join(args)} in {ziel}: {r.stderr.strip() or r.stdout.strip()}')
    return r.stdout.strip() if r.returncode == 0 else ''


def ist_repo(ziel):
    return (ziel / '.git').exists()


def zweig(ziel):
    return git(ziel, 'symbolic-ref', '--short', 'HEAD', check=False)


def klonen(url, ziel):
    """Klont `url` nach `ziel`; ein schon vorhandener Klon derselben URL wird weiterverwendet."""
    if ziel.exists() and any(ziel.iterdir()):
        if not ist_repo(ziel):
            raise Fehler(f'{ziel} ist nicht leer und kein Git-Repo; kann dort nicht klonen')
        vorhanden = git(ziel, 'remote', 'get-url', 'origin', check=False)
        if _norm(vorhanden) != _norm(url):
            raise Fehler(f'{ziel} ist schon ein Repo mit anderem origin ({vorhanden or "keiner"}), nicht {url}')
        return False
    ziel.parent.mkdir(parents=True, exist_ok=True)
    befehl = ['gh', 'repo', 'clone', url, str(ziel)] if GITHUB.match(url) and shutil.which('gh') else ['git', 'clone', '-q', url, str(ziel)]
    r = subprocess.run(befehl, capture_output=True, text=True)
    if r.returncode:
        raise Fehler(f'Klonen fehlgeschlagen ({" ".join(befehl[:3])}): {(r.stderr or r.stdout).strip()}')
    return True


def _norm(url):
    url = url.strip().removesuffix('.git').rstrip('/')
    m = re.fullmatch(r'(?:https?://|ssh://git@|git@)?github\.com[:/](.+)', url) or re.fullmatch(r'([\w.-]+/[\w.-]+)', url)
    return 'github:' + m.group(1).lower() if m else url


def vorlagen_dateien():
    return sorted(p for p in VORLAGE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')


def fuellen(text, werte):
    for k, v in werte.items():
        text = text.replace(k, v)
    return text


def kanban_text(text):
    """Roadmap-Text mit frischem Kanban, wie ihn das Werkzeug der Vorlage erzeugt (damit ein zweiter Lauf sie als gleich erkennt)."""
    spec = importlib.util.spec_from_file_location('vorlage_roadmap', VORLAGE / 'werkzeuge' / 'roadmap.py')
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul.update(text)


def toml_basis(text):
    m = re.search(r'^basis\s*=\s*"([^"]*)"', text, re.M)
    return m.group(1) if m else None


def gleich_wie_vorlage(roh, text):
    """Passt `text` auf die Vorlage `roh`? Platzhalter (Name, Datum, Basis) dürfen beliebig gefüllt sein, damit ein Lauf an einem anderen Tag nichts meldet."""
    muster = re.escape(roh)
    for k in PLATZHALTER:
        muster = muster.replace(re.escape(k), '.*?')
    return re.fullmatch(muster, text, re.S) is not None


def konflikte_pruefen(ziel, rel, roh, basis):
    """Gründe, warum eine vorhandene Datei neben der Vorlage Aufmerksamkeit braucht (leer = kein Problem)."""
    try:
        text = (ziel / rel).read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return ['nicht als Text lesbar']
    grund = []
    if not gleich_wie_vorlage(roh, text):
        grund.append('weicht von der Vorlage ab')
    name = str(rel)
    if name == 'devflow.toml':
        gesetzt = toml_basis(text)
        if gesetzt is None and basis != 'main':
            grund.append(f'setzt keine `basis`, das Repo steht auf `{basis}` (Standard wäre `main`): `basis = "{basis}"` ergänzen')
        elif gesetzt is not None and gesetzt != basis:
            grund.append(f'`basis = "{gesetzt}"`, das Repo steht auf `{basis}`')
    elif name == 'CLAUDE.md' and '@AGENTS.md' not in text:
        grund.append('verweist nicht auf `@AGENTS.md`')
    elif name == '.gitignore':
        zeilen = {z.strip() for z in text.split('\n')}
        fehlt = [g for g in GEHEIMNISSE if g not in zeilen]
        if fehlt:
            grund.append(f'schließt {", ".join(fehlt)} nicht aus (Geheimnisse nie ins Repo)')
    elif name == 'ROADMAP.md':
        try:
            pruefen_roadmap(text)
        except Fehler as e:
            grund.append(f'für devflow nicht lesbar: {e}')
    return grund


def pruefen_roadmap(text):
    """Liest die Roadmap so, wie devflow es tut (Marker, Aufgabentabellen, Status)."""
    sys.path.insert(0, str(WURZEL))
    try:
        from devflow2 import Fehler as DFFehler, roadmap
    finally:
        sys.path.remove(str(WURZEL))
    try:
        roadmap.kanban(text)
    except DFFehler as e:
        raise Fehler(str(e)) from None


def main(ziel, name=None, url=None, basis=None):
    ziel = Path(ziel).expanduser().resolve()
    name = name or ziel.name
    if not NAME.fullmatch(name):
        raise Fehler(f'Projektname {name!r} ungeeignet (Buchstaben, Ziffern, Punkt, Unterstrich, Bindestrich)')
    geklont = klonen(url, ziel) if url else False
    neu = not ist_repo(ziel)
    if neu and ziel.exists() and any(ziel.iterdir()):
        raise Fehler(f'{ziel} ist nicht leer und kein Git-Repo; bitte leeres Verzeichnis oder bestehendes Repo angeben')
    if neu:
        basis = basis or 'main'
    else:
        vorhanden = zweig(ziel)
        if basis and vorhanden and basis != vorhanden:
            raise Fehler(f'Das Repo steht auf {vorhanden}, nicht auf {basis}; bitte zuerst den Branch wechseln')
        basis = basis or vorhanden
        if not basis:
            raise Fehler(f'{ziel} steht auf keinem Branch (losgelöst); mit --basis den Basisbranch nennen und dorthin wechseln')
    heute = datetime.date.today()
    werte = {'{{PROJEKT}}': name, '{{DATUM}}': heute.strftime('%d.%m.%Y'), '{{TAG}}': heute.strftime('%d.%m.'), '{{BASIS}}': basis}
    angelegt, gleich, konflikte = [], [], []
    for quelle in vorlagen_dateien():
        rel = quelle.relative_to(VORLAGE)
        dest = ziel / rel
        roh = quelle.read_text(encoding='utf-8')
        if str(rel) == 'ROADMAP.md':
            roh = kanban_text(roh)
        text = fuellen(roh, werte)
        if dest.exists() or dest.is_symlink():
            grund = konflikte_pruefen(ziel, rel, roh, basis) if dest.is_file() else ['ist keine Datei']
            (konflikte.append((rel, grund)) if grund else gleich.append(rel))
            continue
        if any(not (ziel / q).is_dir() for q in rel.parents if (ziel / q).exists() and q != Path('.')):
            konflikte.append((rel, ['ein Elternpfad ist eine Datei']))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')
        shutil.copymode(quelle, dest)
        angelegt.append(rel)
    if neu:
        git(ziel, 'init', '-q', '-b', basis)
        git(ziel, 'add', '-A')
        git(ziel, 'commit', '-q', '-m', f'docs: {name} aus Projektvorlage angelegt')
    print(f'{"Neues Repo" if neu else "Geklontes Repo ergänzt" if geklont else "Bestehendes Repo ergänzt"}: {ziel} (basis: {basis})')
    print('Angelegt:', ', '.join(map(str, angelegt)) or '–')
    if gleich:
        print('Schon vorhanden, wie die Vorlage:', ', '.join(map(str, gleich)))
    for rel, grund in konflikte:
        print(f'Konflikt, nicht geändert: {rel}: {"; ".join(grund)}')
    if not neu and angelegt:
        print('Nicht committet; bitte prüfen und selbst committen.')
    return dict(neu=neu, basis=basis, angelegt=angelegt, gleich=gleich, konflikte=konflikte)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('verzeichnis')
    ap.add_argument('name', nargs='?')
    ap.add_argument('--url', help='Repo klonen (GitHub über gh, sonst git clone)')
    ap.add_argument('--basis', help='Basisbranch; Standard: der Branch, auf dem das Repo steht (neu: main)')
    a = ap.parse_args()
    main(a.verzeichnis, a.name, a.url, a.basis)
