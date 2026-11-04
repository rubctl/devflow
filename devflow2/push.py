"""Web-Push (S2.4): VAPID-Schlüssel, Geräteabos, Versand und die Prüfung auf neue Ereignisse.

- Schlüssel: `vapid.key` im Verzeichnis `push.schluessel` der Instanz (Standard `~/.config/devflow2`, `DEVFLOW2_CONFIG`), entsteht beim ersten Start mit 0600.
- Abos und schon gemeldete Ereignisse: `push.json` im Zustandsverzeichnis (0600). Abos sind Geheimnisse und stehen nie
  in Logs oder Antworten.
- `pruefen()` meldet jedes Ereignis aus `push_ereignisse` genau einmal: Ein Hinweis je Art mit der Anzahl der neuen
  Ereignisse, ohne Projekt- oder Aufgabentexte. Der erste Lauf ohne Stand merkt sich nur, was schon da ist.
- Versand höchstens einmal je Gerät und Ereignis (wer gerade nicht erreichbar ist, bekommt es nicht nachgeschickt);
  404/410 vom Push-Dienst entfernen das Abo.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time

from . import Fehler, instanz, projekte, push_ereignisse
from .push_protokoll import (ARTEN, MAX_GERAETE, PushFehler, abo, abo_id, b64, nutzlast, oeffentlicher_schluessel,
                             privater_schluessel, unb64, vapid, verschluesseln_hinweis)

SPERRE = threading.Lock()
TTL = 86400
TAKT = 30.0


def konfig():
    return instanz.schluessel_verzeichnis()


def kontakt():
    return instanz.kontakt()


def schluessel():
    """Der 32-Byte-Schlüssel (privat); wird beim ersten Aufruf angelegt (Verzeichnis 0700, Datei 0600)."""
    pfad = konfig() / 'vapid.key'
    if not pfad.exists():
        pfad.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = pfad.with_name(f'.vapid.key.{os.getpid()}.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(b64(os.urandom(32)) + '\n')
            os.link(tmp, pfad)  # scheitert, wenn ein anderer Start schneller war: dessen Schlüssel gilt
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    if pfad.stat().st_mode & 0o077:
        pfad.chmod(0o600)
    try:
        roh = unb64(pfad.read_text(encoding='utf-8').strip(), 32)
        privater_schluessel(roh)
    except (PushFehler, OSError, ValueError) as err:
        raise Fehler(f'{pfad}: VAPID-Schlüssel unbrauchbar ({err})') from None
    return roh


def oeffentlich():
    """Öffentlicher Schlüssel (base64url) für `applicationServerKey` im Browser."""
    return b64(oeffentlicher_schluessel(privater_schluessel(schluessel())))


# --- Zustand ------------------------------------------------------------------------------------

def _pfad():
    return projekte.zustand() / 'push.json'


def _laden():
    try:
        d = json.loads(_pfad().read_text(encoding='utf-8'))
    except FileNotFoundError:
        d = {}
    except (OSError, ValueError) as err:
        raise Fehler(f'{_pfad()}: nicht lesbar ({err})') from None
    return {'abos': d.get('abos') or {}, 'gemeldet': d.get('gemeldet')}


def _speichern(d):
    pfad = _pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(f'.{pfad.name}.{os.getpid()}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.replace(tmp, pfad)


def geraete():
    """Angemeldete Geräte ohne Schlüssel: [{'id', 'name', 'seit'}], älteste zuerst."""
    with SPERRE:
        d = _laden()
    return sorted(({'id': k, 'name': v.get('name', ''), 'seit': v.get('seit', 0)} for k, v in d['abos'].items()),
                  key=lambda g: (g['seit'], g['id']))


def anmelden(wert, name=''):
    """Abo eines Geräts speichern (ein bekannter Endpunkt wird ersetzt). Gibt die Geräte-ID zurück."""
    wert = abo(wert)
    gid = abo_id(wert['endpoint'])
    with SPERRE:
        d = _laden()
        if gid not in d['abos'] and len(d['abos']) >= MAX_GERAETE:
            raise Fehler(f'Höchstens {MAX_GERAETE} Geräte; erst eines abmelden')
        d['abos'][gid] = {'abo': wert, 'name': str(name)[:40], 'seit': d['abos'].get(gid, {}).get('seit') or time.time()}
        _speichern(d)
    return gid


def abmelden(gid):
    """Gerät entfernen; True, wenn es das Gerät gab."""
    with SPERRE:
        d = _laden()
        da = d['abos'].pop(gid, None) is not None
        if da:
            _speichern(d)
    return da


# --- Versand ------------------------------------------------------------------------------------

def post(url, kopf, inhalt):
    """Der echte Versand (Statuscode); Tests übergeben eine Attrappe mit derselben Form."""
    import httpx
    return httpx.post(url, content=inhalt, headers=kopf, timeout=10, follow_redirects=False).status_code


def senden_an(eintrag, art, anzahl, sender=None, zahlen=None):
    """Einen Hinweis an ein Gerät. Gibt den Statuscode des Push-Dienstes zurück."""
    w = eintrag['abo']
    kopf = {'Authorization': vapid(schluessel(), w['endpoint'], kontakt()), 'Content-Encoding': 'aes128gcm',
            'Content-Type': 'application/octet-stream', 'TTL': str(TTL), 'Urgency': 'normal'}
    return (sender or post)(w['endpoint'], kopf, verschluesseln_hinweis(nutzlast(anzahl, art, zahlen), w))


def _verteilen(art, anzahl, sender, nur=None, zahlen=None):
    """An alle Geräte (oder `nur` eines); abgelaufene Abos (404/410) fliegen raus. Gibt {Gerät: Status} zurück."""
    with SPERRE:
        ziele = {k: v for k, v in _laden()['abos'].items() if nur in (None, k)}
    ergebnis = {}
    for gid, eintrag in ziele.items():
        try:
            status = senden_an(eintrag, art, anzahl, sender, zahlen)
        except Exception as err:  # Netz oder Anbieter: nicht den ganzen Lauf der Prüfung abbrechen
            print(f'devflow2 push: Gerät {gid}: {type(err).__name__}', file=sys.stderr)
            status = None
        ergebnis[gid] = status
        if status in (404, 410):
            abmelden(gid)
        elif status is None or not 200 <= status < 300:
            print(f'devflow2 push: Gerät {gid}: Status {status}', file=sys.stderr)
    return ergebnis


def probe(gid, sender=None):
    """Probehinweis an ein Gerät. Gibt den Status zurück (None: nicht erreichbar) oder wirft Fehler, wenn unbekannt."""
    with SPERRE:
        da = gid in _laden()['abos']
    if not da:
        raise Fehler('Gerät unbekannt')
    return _verteilen('test', 1, sender, nur=gid).get(gid)


def pruefen(sender=None):
    """Neue Ereignisse melden. Gibt [(Art, Anzahl)] der versendeten Hinweise zurück."""
    aktuell, defekt = push_ereignisse.ereignisse()
    with SPERRE:
        d = _laden()
        alt = d['gemeldet']
        # Ereignisse unlesbarer Projekte bleiben im Stand, sonst gälten sie nach der Reparatur als neu.
        d['gemeldet'] = sorted(aktuell | {k for k in alt or () if k.split(':')[1] in defekt})
        neu = set() if alt is None else aktuell - set(alt)
        if alt != d['gemeldet']:
            _speichern(d)  # vor dem Senden: lieber ein Hinweis zu wenig als zwei
    versendet = []
    for art in ARTEN:
        n = sum(k.startswith(art + ':') for k in neu)
        if n:
            # Schlüssel `traum:traum:<id>:<vorschläge>:<pläne>`; es zählt der jüngste Traum
            zahlen = [int(x) for x in max(k for k in neu if k.startswith('traum:')).split(':')[3:5]] if art == 'traum' else None
            _verteilen(art, n, sender, zahlen=zahlen)
            versendet.append((art, n))
    return versendet


async def schleife(sender=None):
    import asyncio
    from starlette.concurrency import run_in_threadpool
    takt = float(os.environ.get('DEVFLOW2_PUSH_TAKT') or TAKT)
    while True:
        try:
            await run_in_threadpool(pruefen, sender)
        except Exception as err:
            print(f'devflow2 push: Prüfung fehlgeschlagen: {type(err).__name__}: {err}', file=sys.stderr)
        await asyncio.sleep(takt)
