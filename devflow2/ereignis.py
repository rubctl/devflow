"""Ereignisse für den Selbstlauf: was die steuernde KI wecken soll, als Zeilen in `ereignisse.jsonl` im Zustandsverzeichnis.

Arten: `plan-abgenommen`, `plan-rueckmeldung`, `neue-idee`, `antwort`, `lauf-ende`, `kontingent-frei`, `pflege-routine`. Inhalte (Texte des
Inhabers) stehen nicht darin, nur Art, Projekt und Bezug (Datei, Frage oder Lauf). `selbstlauf.py` liest die Datei und
entfernt, was ein Steuerlauf übernommen hat; Anhängen und Entfernen laufen unter derselben Dateisperre.
"""
from contextlib import contextmanager
import fcntl
import json
import os
import sys

from . import Fehler, laeufe, projekte, schreiben


class EreignisFehler(Fehler):
    """Die Änderung ist schon committet, nur das Ereignis ging nicht durch."""


def datei():
    return projekte.zustand() / 'ereignisse.jsonl'


@contextmanager
def _sperre():
    with open(datei().with_name('ereignisse.lock'), 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _zeile(e):
    return json.dumps(e, ensure_ascii=False) + '\n'


def melden(art, projekt, bezug=''):
    zeile = _zeile(dict(zeit=laeufe.jetzt(), art=art, projekt=projekt, bezug=bezug))
    try:
        datei().parent.mkdir(parents=True, exist_ok=True)
        with _sperre():
            fd = os.open(datei(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, zeile.encode('utf-8'))
            finally:
                os.close(fd)
    except OSError as err:
        raise EreignisFehler(f'Ereignis {art} konnte nicht gemeldet werden: {err}') from None


def still(art, projekt, bezug=''):
    """Für Stellen, die wegen eines Ereignisses nicht scheitern dürfen (Laufprozess, Antwort nach dem Commit)."""
    try:
        melden(art, projekt, bezug)
    except EreignisFehler as err:
        print(f'devflow2: {err}', file=sys.stderr)


def lesen():
    try:
        zeilen = datei().read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return []
    out = []
    for z in zeilen:
        try:
            e = json.loads(z)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get('projekt'), str):
            out.append(e)
    return out


def entfernen(erledigt):
    """Übernommene Ereignisse aus der Datei nehmen; was seit dem Lesen dazukam, bleibt."""
    with _sperre():
        schreiben(datei(), ''.join(_zeile(e) for e in lesen() if e not in erledigt))
        datei().chmod(0o600)
