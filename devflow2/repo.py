"""Git-Hilfen: Befehle, Sperre je Repo, Commits auf main für Roadmap und Berichte."""
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from . import Fehler, roadmap, schreiben


PUSH_ZEIT = 60
# Autor der Commits, die devflow selbst auf main schreibt. Zusammen mit den Pfaden weist er einen Statuscommit nach (`statuscommit`).
AUTOR = 'devflow <devflow@devflow.invalid>'
STATUSPFADE = ('ROADMAP.md',)


def git(cwd, *args, check=True):
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise Fehler(f'git {" ".join(args[:3])}: {(r.stderr or r.stdout).strip()[-600:]}')
    return r.stdout.strip()


def ok(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True).returncode == 0


def tag():
    return datetime.date.today().strftime('%d.%m.')


@contextmanager
def sperre(projekt):
    """Eine Sperre je Repo für Änderungen an main (Roadmap-Commits, Merge). Nicht verschachteln."""
    with open(projekt.repo / '.git' / 'devflow2.lock', 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    nachschieben(projekt)  # außerhalb der Sperre: ein langsames Netz blockiert keine Läufe


def main_bereit(projekt, pfade):
    zweig = git(projekt.repo, 'symbolic-ref', '--short', 'HEAD', check=False)
    if zweig != projekt.cfg['basis']:
        raise Fehler(f'{projekt.repo} steht nicht auf {projekt.cfg["basis"]} (sondern {zweig or "losgelöst"})')
    fremd = git(projekt.repo, 'status', '--porcelain', '--', *pfade)
    if fremd:
        raise Fehler(f'Nicht committete Änderungen in {", ".join(pfade)}; bitte zuerst klären:\n{fremd}')


def auf_main(projekt, aenderung, nachricht, pfade=('ROADMAP.md',)):
    """`aenderung(texte) -> texte` auf die Dateien in `pfade` anwenden und nur diese auf main committen.

    Die Roadmap bekommt dabei immer ein frisches Kanban. Rückgabe: Ergebnis der Änderung (zweiter Wert, falls vorhanden).
    """
    with sperre(projekt):
        return auf_main_gesperrt(projekt, aenderung, nachricht, pfade)


def auf_main_gesperrt(projekt, aenderung, nachricht, pfade=('ROADMAP.md',)):
    """Wie auf_main, wenn die Sperre schon gehalten wird."""
    main_bereit(projekt, pfade)
    alt = {p: (projekt.repo / p).read_text(encoding='utf-8') if (projekt.repo / p).exists() else '' for p in pfade}
    ergebnis = aenderung(dict(alt))
    neu, extra = ergebnis if isinstance(ergebnis, tuple) else (ergebnis, None)
    if 'ROADMAP.md' in neu:
        neu['ROADMAP.md'] = roadmap.kanban(neu['ROADMAP.md'])
    geaendert = [p for p in pfade if neu[p] != alt[p]]
    if not geaendert:
        return extra
    vorher = {p: (projekt.repo / p).exists() for p in geaendert}
    for p in geaendert:
        schreiben(projekt.repo / p, neu[p])
    try:
        git(projekt.repo, 'add', '--', *geaendert)
        git(projekt.repo, 'commit', '-q', '-m', nachricht, '--author', AUTOR, '--only', '--', *geaendert)
    except Fehler:
        # Eigene Änderung zurücknehmen: main_bereit hat geprüft, dass diese Pfade vorher sauber waren.
        ok(projekt.repo, 'reset', '-q', '--', *geaendert)
        for p in geaendert:
            if vorher[p]:
                schreiben(projekt.repo / p, alt[p])
            else:
                (projekt.repo / p).unlink(missing_ok=True)
        raise
    push_vormerken(projekt)
    return extra


def _als_texte(fn):
    def aenderung(texte):
        r = fn(texte['ROADMAP.md'])
        return ({'ROADMAP.md': r[0]}, r[1]) if isinstance(r, tuple) else {'ROADMAP.md': r}
    return aenderung


def roadmap_aendern(projekt, fn, nachricht, gesperrt=False):
    """`fn(text) -> text` oder `(text, extra)` auf ROADMAP.md anwenden und committen."""
    return (auf_main_gesperrt if gesperrt else auf_main)(projekt, _als_texte(fn), nachricht)


def hat_remote(projekt):
    return bool(git(projekt.repo, 'remote', check=False))


def push(projekt):
    """Basisbranch zum Upstream pushen, ohne Upstream zu `origin` (bzw. dem einzigen Remote). Fehler → Fehler."""
    basis = projekt.cfg['basis']
    remotes = git(projekt.repo, 'remote', check=False).split()
    if not remotes:
        return 'kein Remote'
    ziel = git(projekt.repo, 'config', f'branch.{basis}.remote', check=False)
    zweig = git(projekt.repo, 'config', f'branch.{basis}.merge', check=False) or f'refs/heads/{basis}'
    if not ziel:
        ziel = 'origin' if 'origin' in remotes else (remotes[0] if len(remotes) == 1 else '')
    if not ziel:
        raise Fehler(f'Mehrere Remotes ({", ".join(remotes)}) und kein Upstream für {basis}; Push nicht eindeutig')
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    try:  # nie --force: ein Remote, das weiter ist, weist ab, und das bleibt so
        r = subprocess.run(['git', 'push', '-q', ziel, f'{basis}:{zweig}'], cwd=projekt.repo, capture_output=True, text=True, env=env, timeout=PUSH_ZEIT)
        meldung = (r.stderr or r.stdout).strip()[-300:]
        fehler = r.returncode != 0
    except subprocess.TimeoutExpired:
        meldung, fehler = f'keine Antwort nach {PUSH_ZEIT} s', True
    if fehler:
        text = f'Push nach {ziel} fehlgeschlagen: {meldung}'
        push_vormerken(projekt, text)
        raise Fehler(text)
    _marke(projekt).unlink(missing_ok=True)
    return f'gepusht nach {ziel}'


def _marke(projekt):
    return projekt.repo / '.git' / 'devflow2-push-offen'


def push_vormerken(projekt, fehler=''):
    """Vermerken, dass main noch nicht gepusht ist (nur mit Remote). Der erste Zeitpunkt bleibt erhalten."""
    if not hat_remote(projekt):
        return
    alt = push_offen(projekt) or {}
    schreiben(_marke(projekt), json.dumps({'seit': alt.get('seit') or int(time.time()), 'fehler': fehler or alt.get('fehler', '')}) + '\n')


def push_offen(projekt):
    """`{'seit', 'fehler'}`, wenn ein Push aussteht; sonst None. `fehler` ist leer, solange noch kein Versuch scheiterte."""
    try:
        d = json.loads(_marke(projekt).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def nachschieben(projekt):
    """Vorgemerkten Push versuchen. Scheitert er, bleibt der Vermerk (nächster Versuch beim nächsten Commit); Rückgabe: ob nichts mehr offen ist."""
    if not push_offen(projekt):
        return True
    try:
        push(projekt)
    except Fehler:
        return False
    return True


def kopf(projekt, branch):
    """Commit eines lokalen Branches, leer, wenn es ihn nicht gibt."""
    return git(projekt.repo, 'rev-parse', '-q', '--verify', f'refs/heads/{branch}', check=False)


def worktrees(projekt):
    """{Pfad: Branch-Ref} aller Worktrees des Repos; losgelöste haben eine leere Ref."""
    out, pfad = {}, ''
    for zeile in git(projekt.repo, 'worktree', 'list', '--porcelain').split('\n'):
        if zeile.startswith('worktree '):
            pfad = zeile.split(' ', 1)[1]
            out[pfad] = ''
        elif zeile.startswith('branch '):
            out[pfad] = zeile.split(' ', 1)[1]
    return out


def committet(projekt, pfad):
    """Inhalt von `pfad` im letzten Commit des Haupt-Checkouts (nicht im Arbeitsverzeichnis)."""
    return git(projekt.repo, 'show', f'HEAD:{pfad}')


# --- Rebase und Statuscommits (S14.2) -------------------------------------------------------------

def rebase_laeuft(wt):
    """Steht der Worktree in einem halben Rebase (auch von einem abgebrochenen Prozess)?"""
    for name in ('rebase-merge', 'rebase-apply'):
        pfad = git(wt, 'rev-parse', '--path-format=absolute', '--git-path', name, check=False)
        if pfad and Path(pfad).exists():
            return True
    return False


def rebase_abbrechen(wt):
    """Rest eines Rebase abbrechen; danach steht der Branch wieder auf dem Ausgangsstand. Rückgabe: ob es einen Rest gab."""
    if not rebase_laeuft(wt):
        return False
    ok(wt, 'rebase', '--abort')
    if rebase_laeuft(wt):
        raise Fehler(f'Halber Rebase in {wt} lässt sich nicht abbrechen; bitte von Hand klären')
    return True


def rebase(wt, ziel):
    """Branch im Worktree auf den Commit `ziel` setzen. Rückgabe: None bei Erfolg, sonst die Konfliktpfade; der Rebase ist dann
    schon abgebrochen und der Branch steht auf dem Ausgangsstand. Kein Aufrufer findet danach einen halben Rebase vor."""
    rebase_abbrechen(wt)
    r = subprocess.run(['git', 'rebase', '-q', ziel], cwd=wt, capture_output=True, text=True)
    if r.returncode == 0:
        return None
    pfade = sorted({x for x in git(wt, 'diff', '--name-only', '-z', '--diff-filter=U', check=False).split('\0') if x})
    rebase_abbrechen(wt)
    if not pfade:
        raise Fehler(f'Rebase auf {ziel[:7]} gescheitert: {(r.stderr or r.stdout).strip()[-400:]}')
    return pfade


def pfade(projekt, sha):
    """Geänderte Pfade eines Commits gegen seinen ersten Elternteil, Umbenennungen mit beiden Pfaden."""
    return [x for x in git(projekt.repo, 'diff-tree', '--no-commit-id', '--name-only', '--no-renames', '-r', '-z', sha).split('\0') if x]


def statuscommit(projekt, sha):
    """Nachweislich ein Statuscommit von devflow: Autor `AUTOR`, genau ein Elternteil und nur ROADMAP.md (Status, Protokoll) geändert.
    Der Dateiname allein genügt nicht, und der Autor allein auch nicht."""
    kopf = git(projekt.repo, 'show', '-s', '--format=%an <%ae>%x00%P', sha).split('\0')
    return (len(kopf) == 2 and kopf[0] == AUTOR and len(kopf[1].split()) == 1
            and pfade(projekt, sha) == list(STATUSPFADE))


def fachliche_commits(projekt, von, bis):
    """Commits in `von..bis`, die keine Statuscommits von devflow sind (ältester zuerst)."""
    return [c for c in git(projekt.repo, 'rev-list', '--reverse', f'{von}..{bis}').split() if not statuscommit(projekt, c)]


def nur_status_dazu(projekt, alt, neu):
    """Unterscheidet sich `neu` von `alt` höchstens in ROADMAP.md? So sieht ein Rebase über reine Statuscommits aus."""
    return all(x in STATUSPFADE for x in git(projekt.repo, 'diff', '--name-only', '-z', alt, neu).split('\0') if x)
