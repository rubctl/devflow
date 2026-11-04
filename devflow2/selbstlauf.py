"""Selbstlauf (S3.5): Ereignisse wecken die steuernde KI, ohne dass eine Sitzung offen ist.

Die Hintergrundschleife des Dashboard-Dienstes ruft `pruefen()` im Takt auf. Liegen Ereignisse, startet höchstens ein
Lauf der Art `steuern` zur Zeit (über alle Projekte): `claude -p` im Haupt-Checkout des Projekts, nur mit dem Werkzeugsatz
aus `SAETZE` und lesendem Zugriff. `neue-idee` und `plan-rueckmeldung` tragen Text von außen ins Spiel: Sie wecken einen
Planungslauf, der nur Pläne schreiben kann, und nie zusammen mit anderen Ereignissen. Sonst übernimmt ein Lauf alle offenen
Ereignisse seiner Art im Projekt; was übrig bleibt oder währenddessen dazukommt, kommt im nächsten Lauf dran.

Der Satz gilt doppelt: `--allowedTools` und der MCP-Server selbst (`mcp <satz>`), der fremde Werkzeuge gar nicht anbietet.

Es startet nichts, wenn der Schalter aus ist (Standard), `anhalten alles` gilt, ein Traum aktiv ist (`traum.py`, gleiche
Startsperre) oder das Tagesbudget verbraucht ist. Das
Budget (`selbstlauf_budget`, kleinster Wert der angemeldeten Projekte) zählt die Steuerläufe aller Projekte seit
Mitternacht. Ist es verbraucht, bleiben die Ereignisse liegen, und `hinweise()` liefert einmal je Tag den Push-Schlüssel.

Stand: `selbstlauf.json` im Zustandsverzeichnis (`an`, `budget_hinweis`).
"""
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from pathlib import Path
import sys

from . import Fehler, auftraege, ereignis, grundlage, laeufe, projekte, schreiben
from .projekte import Projekt

TAKT = 10.0
ZEITLIMIT = 1800  # Sekunden; ein Steuerlauf ist kurz, ein hängender soll die nächsten nicht stundenlang sperren
LESEN = ('lage', 'aufgaben', 'aufgabe', 'lauf')
# Nicht dabei: antwort_eintragen (antwortet nur der Inhaber), status_setzen, aufgabe_anlegen, projekt_anmelden, anhalten;
# Eingang und Entwürfe gehören dem Planungslauf, damit kein Lauf Ideentexte liest und zugleich ausführen kann.
WERKZEUGE = LESEN + ('plan_uebernehmen', 'pflege_aufgabe', 'frage_stellen', 'protokoll', 'entwickeln', 'testen', 'review_anfordern', 'nacharbeit',
                     'mergen', 'deployen', 'meinung_anfordern', 'fortsetzen')
PLANUNG = LESEN + ('eingang', 'plan_entwurf', 'idee_zustand', 'frage_stellen')
IDEEN = tuple(w for w in PLANUNG if w != 'frage_stellen')  # im Traum: Die Roadmap ist kein Schreibziel, Entwürfe stehen auch so unter „Braucht dich“
SAETZE = {'steuerung': WERKZEUGE, 'planung': PLANUNG, 'ideen': IDEEN}
PLANUNG_ANLAESSE = ('neue-idee', 'plan-rueckmeldung')
EINGEBAUT = 'Read,Grep,Glob'  # keine Shell und kein Schreibzugriff: Pläne gehen über plan_entwurf


def _datei():
    return projekte.zustand() / 'selbstlauf.json'


def _laden():
    try:
        d = json.loads(_datei().read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _sichern(d):
    schreiben(_datei(), json.dumps(d, indent=1, ensure_ascii=False) + '\n')


@contextmanager
def startsperre():
    """Eine Sperre für Selbstlauf und Traum: Wer eine Steuerung startet, prüft und startet darunter."""
    pfad = _datei().with_name('selbstlauf.lock')
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _heute():
    return datetime.date.today().isoformat()


def an():
    return bool(_laden().get('an'))


def schalten(wert):
    with startsperre():
        d = _laden()
        d['an'] = bool(wert)
        _sichern(d)


def budget():
    werte = []
    for name in projekte.alle():
        try:
            werte.append(Projekt(name).cfg['selbstlauf_budget'])
        except Fehler:
            continue
    return min(werte, default=projekte.STANDARD['selbstlauf_budget'])


def _steuerlaeufe():
    return [l for l in laeufe.alle() if l['art'] == 'steuern']


def _verbraucht(ls):
    return sum(datetime.date.fromtimestamp(l['angelegt']).isoformat() == _heute() for l in ls)


def _traum():
    from . import traum  # erst beim Aufruf: traum baut auf diesem Modul auf
    return traum


def stand():
    """Für Dashboard und `lage()`."""
    ls = _steuerlaeufe()
    laeuft = next((l['id'] for l in ls if l['zustand'] in laeufe.AKTIV), None)
    return dict(an=an(), budget=budget(), heute=_verbraucht(ls), ereignisse=len(ereignis.lesen()), laeuft=laeuft, traum=bool(_traum().aktiv()))


def wartet_arbeit():
    """Würde der nächste Takt einen Steuerlauf starten? Ist das Budget verbraucht, warten die Ereignisse ohnehin bis morgen."""
    return bool(an() and ereignis.lesen() and _verbraucht(_steuerlaeufe()) < budget())


def hinweise():
    """Push-Schlüssel für den heutigen Tag, wenn ein Start am Budget gescheitert ist."""
    tag = _laden().get('budget_hinweis')
    return {f'budget:selbstlauf:{tag}'} if tag == _heute() else set()


def _mcp_konfig(satz):
    """Eigener Eintrag statt der Anmeldung in Claude Code: gleicher Code wie der Lauf, gleiches Zustandsverzeichnis."""
    wurzel = str(Path(__file__).resolve().parents[1])
    env = {k: v for k, v in os.environ.items() if k.startswith('DEVFLOW2_')}
    server = dict(command=sys.executable, args=['-B', '-m', 'devflow2', 'mcp', satz], env={**env, 'PYTHONPATH': wurzel})
    pfad = projekte.zustand() / f'selbstlauf-{satz}-mcp.json'
    schreiben(pfad, json.dumps({'mcpServers': {'devflow': server}}, indent=1, ensure_ascii=False) + '\n')
    return str(pfad)


def _argumente(satz):
    return ['--mcp-config', _mcp_konfig(satz), '--strict-mcp-config', '--tools', EINGEBAUT,
            '--allowedTools', ','.join(f'mcp__devflow__{w}' for w in SAETZE[satz])]


def _projekt_wahl(offen):
    """(Projekt, seine Ereignisse) für das älteste Ereignis eines lesbaren Projekts. Abgemeldete Projekte fallen weg."""
    bekannt = projekte.alle()
    weg = [e for e in offen if e['projekt'] not in bekannt]
    if weg:
        ereignis.entfernen(weg)
    for name in dict.fromkeys(e['projekt'] for e in offen if e not in weg):
        try:
            return Projekt(name), [e for e in offen if e['projekt'] == name]
        except Fehler as err:
            print(f'devflow2 selbstlauf: {err}', file=sys.stderr)
    return None, []


def pruefen():
    """Ein Takt: Steuerlauf starten, wenn Ereignisse liegen und nichts dagegen spricht. Gibt ihn zurück, sonst None."""
    if not ereignis.lesen():
        return None
    with startsperre():
        d = _laden()
        if not d.get('an') or laeufe.angehalten() or _traum().aktiv():
            return None
        ls = _steuerlaeufe()
        if any(l['zustand'] in laeufe.AKTIV for l in ls):
            return None
        p, eigene = _projekt_wahl(ereignis.lesen())
        if p is None:
            return None
        if _verbraucht(ls) >= budget():
            if d.get('budget_hinweis') != _heute():
                _sichern({**d, 'budget_hinweis': _heute()})
            return None
        planung = eigene[0]['art'] in PLANUNG_ANLAESSE  # das älteste Ereignis bestimmt die Art des Laufs
        eigene = [e for e in eigene if (e['art'] in PLANUNG_ANLAESSE) == planung]
        satz, auftrag = ('planung', auftraege.planen) if planung else ('steuerung', auftraege.steuern)
        g = grundlage.fuer(p)
        lauf = laeufe.starten(p, 'steuern', 'claude', p.repo, auftrag(p.name, eigene, g), ereignisse=eigene, satz=satz, grundlage_stand=g['stand'],
                              cli_zusatz=_argumente(satz), zeitlimit=ZEITLIMIT)
        ereignis.entfernen(eigene)  # erst nach dem Start: scheitert er, bleiben sie für den nächsten Takt
        return lauf


async def schleife():
    import asyncio
    from starlette.concurrency import run_in_threadpool
    takt = float(os.environ.get('DEVFLOW2_SELBSTLAUF_TAKT') or TAKT)
    while True:
        try:
            await run_in_threadpool(pruefen)
        except Exception as err:
            print(f'devflow2 selbstlauf: Prüfung fehlgeschlagen: {type(err).__name__}: {err}', file=sys.stderr)
        await asyncio.sleep(takt)
