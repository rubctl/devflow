"""MCP-Server nach einem Deploy aktuell (S14.5): Der stdio-Server wechselt zwischen zwei Aufrufen auf die Fassung, auf die `aktiv` zeigt.

Der Prozess lädt seinen Code nur einmal. Damit eine laufende Claude-Sitzung nach `aktualisieren` nicht neu verbunden werden muss,
liest dieser Transport stdin selbst (Ersatz für `mcp.server.stdio.stdio_server`) und prüft vor jedem `tools/call`, ob `aktiv` auf eine
andere Fassung zeigt. Ist das so, passiert in dieser Reihenfolge:

1. Der Aufruf wird **nicht** an die Sitzung gegeben, sondern gehalten; der Prozess wartet, bis alle schon angenommenen Anfragen
   beantwortet und geschrieben sind (nie mitten in einem Aufruf).
2. Die neue Fassung wird mit ihrem Python einmal probeweise gestartet (Zustandsformat, Import). Scheitert das, bleibt der Prozess auf der alten Fassung und
   bedient den Aufruf selbst; für dieses Ziel wird nicht noch einmal versucht.
3. `execve` ersetzt den Prozess (gleiche PID, gleiche Pipes). Mitgegeben werden in einer Datei nur die Eingaben des Clients:
   die `initialize`-Anfrage, `notifications/initialized`, der gehaltene Aufruf und was schon aus stdin gelesen war.
4. Der neue Prozess spielt das ein, verwirft die Antwort auf `initialize` (der Client hat seine schon) und bedient dann den Aufruf.

Ein Aufruf läuft damit genau einmal: entweder im alten Prozess (kein Wechsel) oder im neuen (er kam im alten nie in die Sitzung).
Nur die Sitzung (`satz` leer) wechselt; ein Steuerlauf bleibt auf der Fassung, mit der er gestartet wurde.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from io import TextIOWrapper

import anyio
import anyio.lowlevel
import anyio.to_thread

import mcp.types as types
from mcp.shared.message import SessionMessage

UEBERGABE = 'DEVFLOW2_MCP_UEBERGABE'  # Pfad der Übergabedatei, nur im neuen Prozess gesetzt
WARTEN = 30  # Sekunden, die ein Wechsel auf laufende Anfragen wartet
PROBE = 60  # Sekunden für den Probe-Import der neuen Fassung
LESEN = 65536


GELADEN = Path(__file__).resolve().parents[1]  # beim Import festgehalten: `aktiv` kann inzwischen woandershin zeigen


def geladen():
    """Wurzel der Fassung, aus der dieser Prozess läuft."""
    return GELADEN


def ziel(aktiv=None, hier=None):
    """(Wurzel, Python) der Fassung, auf die `aktiv` zeigt, wenn sie von der geladenen abweicht und startbar aussieht, sonst None."""
    from . import aktualisieren
    aktiv = aktiv or aktualisieren._aktiv()
    hier = hier or geladen()
    if not aktiv.is_symlink():
        return None
    neu = aktiv.resolve()
    python = aktualisieren._python(neu)
    if neu == hier or not (neu / 'devflow2' / '__main__.py').is_file() or not python.is_file():
        return None
    return neu, python


def _umgebung(neu, hier):
    from . import aktualisieren
    weg = {str(hier), str(aktualisieren._aktiv())}
    pfade = [str(neu)] + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p and p not in weg]
    return dict(os.environ, PYTHONPATH=os.pathsep.join(pfade))


def pruefen(neu, python, hier):
    """Startet die neue Fassung mit ihrem Python? Geprüft wird zuerst, ob sie die Übergabe kennt (`mcp_wechsel`; eine Fassung vor S14.5 liest sie nicht und
    würde `initialize` und den gehaltenen Aufruf verlieren), dann, was `python -m devflow2 mcp` zuerst tut: das Format des Zustands prüfen
    (es stellt vorwärts um, wie der echte Start es gleich tut, und scheitert, wenn der Zustand neuer ist) und den Server laden.
    Nur dann wird gewechselt, sonst bliebe die Sitzung ohne Server."""
    env = _umgebung(neu, hier)
    env.pop(UEBERGABE, None)
    try:
        r = subprocess.run([str(python), '-B', '-c', 'from devflow2 import format, mcp_wechsel; mcp_wechsel.uebergabe; format.bereit(); import devflow2.mcp_server'], cwd=neu, env=env, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=PROBE)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _methode(zeile):
    try:
        nachricht = json.loads(zeile)
    except ValueError:
        return None, None
    if not isinstance(nachricht, dict):
        return None, None
    return nachricht.get('method'), nachricht.get('id')


def _ersetzen(neu, python, hier, args, eingaben):
    """Prozess durch die neue Fassung ersetzen. Kehrt nur bei einem Fehler zurück."""
    fd, pfad = tempfile.mkstemp(prefix='df2-mcp-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(eingaben, f)
        env = _umgebung(neu, hier)
        env[UEBERGABE] = pfad
        try:
            os.getcwd()
            if Path.cwd().resolve().is_relative_to(hier):
                os.chdir(neu)
        except OSError:
            os.chdir(neu)
        sys.stdout.flush()
        os.execve(str(python), [str(python), '-B', '-m', 'devflow2', *args], env)
    except OSError:
        pass
    try:
        os.unlink(pfad)
    except OSError:
        pass


def uebergabe():
    """Eingaben des alten Prozesses (einmalig gelesen und gelöscht), sonst None."""
    pfad = os.environ.pop(UEBERGABE, '')
    if not pfad:
        return None
    try:
        with open(pfad, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
    finally:
        try:
            os.unlink(pfad)
        except OSError:
            pass


@asynccontextmanager
async def stdio(wechseln=True, args=()):
    """Wie `stdio_server`, mit Fassungswechsel vor `tools/call` (wenn `wechseln`). Zeilen sind intern Bytes (Übergabe als latin-1)."""
    stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding='utf-8'))
    erbe = uebergabe() or {}
    hier = geladen()
    z = {
        'puffer': erbe.get('rest', '').encode('latin-1'),  # schon aus stdin gelesen, noch nicht verarbeitet
        'vorab': [v.encode('latin-1') for v in erbe.get('vorab', [])],  # werden vor stdin eingespielt
        'verwerfen': erbe.get('init_id'),  # id der eingespielten initialize-Anfrage: ihre Antwort kennt der Client schon
        'init': None, 'fertig': None,
        'offen': set(),  # ids der Anfragen in der Sitzung, deren Antwort noch nicht geschrieben ist
        'gescheitert': set(),
    }
    lese_in, lese = anyio.create_memory_object_stream(0)
    schreibe, schreib_aus = anyio.create_memory_object_stream(0)

    async def zeilen():
        while z['vorab']:
            yield z['vorab'].pop(0)
        while True:
            i = z['puffer'].find(b'\n')
            if i >= 0:
                zeile, z['puffer'] = z['puffer'][:i + 1], z['puffer'][i + 1:]
                yield zeile
                continue
            stueck = await anyio.to_thread.run_sync(os.read, 0, LESEN, abandon_on_cancel=True)
            if not stueck:
                if z['puffer']:
                    zeile, z['puffer'] = z['puffer'], b''
                    yield zeile
                return
            z['puffer'] += stueck

    async def wechsel(zeile):
        """Vor einem Aufruf auf die aktive Fassung wechseln. Kehrt nur zurück, wenn der Prozess bleibt."""
        t = ziel() if wechseln and z['init'] else None
        if not t or t[0] in z['gescheitert']:
            return
        for _ in range(WARTEN * 20):
            if not z['offen']:
                break
            await anyio.sleep(0.05)
        if z['offen']:
            return  # beim nächsten Aufruf noch einmal
        neu, python = t
        if not await anyio.to_thread.run_sync(pruefen, neu, python, hier):
            z['gescheitert'].add(neu)
            return
        z['gescheitert'].add(neu)  # greift nur, wenn das Ersetzen scheitert
        init = _methode(z['init'])[1]
        vorab = [z['init'], z['fertig'], zeile] if z['fertig'] else [z['init'], zeile]
        _ersetzen(neu, python, hier, list(args) or sys.argv[1:],
                  {'vorab': [v.decode('latin-1') for v in vorab], 'rest': z['puffer'].decode('latin-1'), 'init_id': init})

    async def leser():
        try:
            async with lese_in:
                async for zeile in zeilen():
                    methode, nr = _methode(zeile)
                    if methode == 'initialize':
                        z['init'] = zeile
                    elif methode == 'notifications/initialized':
                        z['fertig'] = zeile
                    elif methode == 'tools/call':
                        await wechsel(zeile)
                    elif methode == 'notifications/cancelled':
                        try:
                            z['offen'].discard(json.loads(zeile)['params']['requestId'])
                        except (ValueError, KeyError, TypeError):
                            pass
                    if methode is not None and nr is not None:
                        z['offen'].add(nr)
                    try:
                        nachricht = types.JSONRPCMessage.model_validate_json(zeile)
                    except Exception as e:
                        await lese_in.send(e)
                        continue
                    await lese_in.send(SessionMessage(nachricht))
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def schreiber():
        try:
            async with schreib_aus:
                async for sm in schreib_aus:
                    wurzel = sm.message.root
                    if isinstance(wurzel, (types.JSONRPCResponse, types.JSONRPCError)):
                        nr, verworfen = wurzel.id, z['verwerfen'] is not None and wurzel.id == z['verwerfen']
                        if verworfen:
                            z['verwerfen'] = None
                        else:
                            await stdout.write(sm.message.model_dump_json(by_alias=True, exclude_none=True) + '\n')
                            await stdout.flush()
                        z['offen'].discard(nr)
                    else:
                        await stdout.write(sm.message.model_dump_json(by_alias=True, exclude_none=True) + '\n')
                        await stdout.flush()
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(leser)
        tg.start_soon(schreiber)
        yield lese, schreibe
