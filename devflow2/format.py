"""Formatversion der Zustandsdateien (S8.3).

Im Zustandsverzeichnis liegt `format.json` mit der Nummer des Formats, in dem die Dateien dort stehen. Jede Fassung von
devflow kennt ihre eigene Nummer `FORMAT`. Regeln:

- Ein Zustand ohne Marke ist Format 1 (so war es vor S8.3); ein leeres Zustandsverzeichnis bekommt gleich `FORMAT`.
- Eine neue Fassung darf den Zustand nur vorwärts umstellen (`MIGRATIONEN`), und sichert ihn vorher nach
  `sicherung/format-<N>-<Zeit>/` (ohne `laeufe/`, das nur wächst und beim Umstellen nicht angefasst wird).
- Eine Fassung mit kleinerem `FORMAT` als der Zustand startet nicht (`bereit`), und das Update schaltet nicht auf sie zurück.

Ändert eine Fassung das Format der Zustandsdateien, erhöht sie `FORMAT` und trägt unter `MIGRATIONEN[alt]` die Umstellung von
`alt` auf `alt + 1` ein.
"""
import datetime
import fcntl
import json
import shutil

from . import Fehler, projekte, schreiben

FORMAT = 1
MIGRATIONEN = {}  # alt → Funktion(zustandsverzeichnis), stellt von `alt` auf `alt + 1` um
NICHT_SICHERN = ('laeufe', 'sicherung')


def datei():
    return projekte.zustand() / 'format.json'


def stand():
    """Format des Zustands; None, wenn keine Marke da ist."""
    try:
        wert = json.loads(datei().read_text(encoding='utf-8'))['format']
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise Fehler(f'{datei()}: nicht lesbar ({err})') from None
    if isinstance(wert, bool) or not isinstance(wert, int) or wert < 1:
        raise Fehler(f'{datei()}: ungültige Formatversion')
    return wert


def _zu_neu(gefunden):
    return Fehler(f'Der Zustand unter {projekte.zustand()} hat Format {gefunden}, diese Fassung von devflow kennt nur Format {FORMAT}. '
                  f'Sie startet nicht, denn sie würde den Zustand beschädigen. Die Fassung mit Format {gefunden} wieder aktivieren '
                  f'oder den Zustand aus {projekte.zustand() / "sicherung"} zurückspielen (dabei gehen die Änderungen seit der Umstellung verloren).')


def pruefen():
    """Wirft `Fehler`, wenn der Zustand neuer ist als diese Fassung."""
    gefunden = stand()
    if gefunden is not None and gefunden > FORMAT:
        raise _zu_neu(gefunden)


def _sichern(ab):
    ziel = projekte.zustand() / 'sicherung' / f'format-{ab}-{datetime.datetime.now():%Y%m%d-%H%M%S}'
    quelle = projekte.zustand()
    shutil.copytree(quelle, ziel, ignore=lambda d, namen: [n for n in namen if d == str(quelle) and n in NICHT_SICHERN or n.endswith('.lock')])
    return ziel


def bereit():
    """Zustand auf das Format dieser Fassung bringen (vorwärts, mit Sicherung) oder `Fehler`, wenn er neuer ist. Gibt die Meldung zurück ('' = nichts getan)."""
    zustand = projekte.zustand()
    zustand.mkdir(parents=True, exist_ok=True)
    with open(zustand / 'format.lock', 'a') as sperre:
        fcntl.flock(sperre, fcntl.LOCK_EX)
        gefunden = stand()
        if gefunden is None:
            gefunden = 1 if any(p.name != 'format.lock' for p in zustand.iterdir()) else FORMAT
        if gefunden > FORMAT:
            raise _zu_neu(gefunden)
        if gefunden == FORMAT:
            if stand() is None:
                schreiben(datei(), json.dumps({'format': FORMAT}) + '\n')
            return ''
        fehlt = [n for n in range(gefunden, FORMAT) if n not in MIGRATIONEN]
        if fehlt:
            raise Fehler(f'Keine Umstellung von Format {fehlt[0]} auf {fehlt[0] + 1} vorhanden')
        sicherung = _sichern(gefunden)
        for n in range(gefunden, FORMAT):
            MIGRATIONEN[n](zustand)
            schreiben(datei(), json.dumps({'format': n + 1}) + '\n')
        return f'Zustand von Format {gefunden} auf {FORMAT} umgestellt; Sicherung: {sicherung}'
