"""`devflow2 aktualisieren` (S8.3): Release holen, prüfen, umschalten; auf der Arbeitsinstanz (installiert mit `ops/installieren.sh`).

Aufbau unter `DEVFLOW2_HOME` (Standard `~/.local/share/devflow2`):

    versionen/v2.N/         Code des Releases, darin `.venv`; `.fertig` markiert eine geprüfte Fassung
    aktiv  → versionen/v2.N  daraus laufen Dienst, MCP-Server und jeder neue Lauf
    vorherige → versionen/…  die Fassung davor (Rückweg: `aktualisieren --zurueck`)

Ablauf: neuestes Tag `v2.N` aus der Quelle lesen (öffentliches Release-Repo, nur lesend, kein Schlüssel) → **Sperre** setzen
(neue Läufe warten, bis zum Ende des Updates) → in ein neues Verzeichnis holen → venv aus `requirements.txt` → Format des
Zustands prüfen → Tests der neuen Fassung → warten, bis kein Lauf mehr aktiv ist → Format noch einmal prüfen → `aktiv`
umlegen → Zustand vorwärts umstellen → Dienst neu starten → Sperre aufheben. Schlägt etwas fehl (auch der Neustart des
Dienstes), bleibt oder wird die alte Fassung wieder aktiv und die neue entfernt; nur wenn der Zustand schon umgestellt ist und
die alte Fassung ihn nicht mehr kennt, bleibt die neue aktiv und die Meldung sagt es. Der Zustand (Läufe, Projekte, Schlüssel) liegt außerhalb und bleibt.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import Fehler, format as zustandsformat, instanz, laeufe, projekte, schreiben

TAG = re.compile(r'v2\.(\d+)')
RUHIG = ('laeuft', 'kontingent', 'abschluss')  # Zustände, die ein Update abwartet; `wartet` ist noch nicht gestartet
EINHEIT = 'devflow2-web.service'


def einheit():
    """Name des Dienstes; `DEVFLOW2_EINHEIT` gibt es nur für Proben neben der echten Instanz (ops/probe.sh)."""
    return os.environ.get('DEVFLOW2_EINHEIT') or EINHEIT


def home():
    return Path(os.environ.get('DEVFLOW2_HOME') or '~/.local/share/devflow2').expanduser()


def versionen():
    return home() / 'versionen'


def _aktiv():
    return home() / 'aktiv'


def _vorherige():
    return home() / 'vorherige'


def _python(wurzel):
    return wurzel / '.venv' / 'bin' / 'python'


def _nummer(name):
    m = TAG.fullmatch(name)
    return int(m.group(1)) if m else None


# --- Sperre für neue Läufe -----------------------------------------------------------------------

def sperrdatei():
    return projekte.zustand() / 'aktualisierung.json'


def sperre():
    """True, solange ein Update neue Läufe zurückhält (der Prozess, der sie gesetzt hat, lebt noch)."""
    try:
        s = json.loads(sperrdatei().read_text(encoding='utf-8'))
        return laeufe.startzeit(s['pid']) == s['pid_start']
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _sperre_setzen(ziel):
    with laeufe._platzsperre():  # derselbe Riegel wie beim Platznehmen: danach ist jeder Lauf entweder sichtbar gestartet oder wartet
        schreiben(sperrdatei(), json.dumps({'pid': os.getpid(), 'pid_start': laeufe.startzeit(os.getpid()), 'ziel': ziel,
                                            'seit': laeufe.jetzt()}) + '\n')


def _sperre_aufheben():
    sperrdatei().unlink(missing_ok=True)


def neu_starten(lid):
    """Für einen Laufprozess, der auf das Update gewartet hat: läuft er noch aus einer älteren Fassung als `aktiv`, mit der aktiven
    Fassung neu beginnen. Kehrt nur zurück, wenn es nichts zu tun gibt."""
    aktiv = _aktiv()
    if not aktiv.is_symlink():
        return
    neu, hier = aktiv.resolve(), Path(__file__).resolve().parents[1]
    python = _python(neu)
    if neu == hier or not python.is_file():
        return
    pfade = [str(neu)] + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p and p != str(hier)]
    os.chdir(neu)
    os.execve(str(python), [str(python), '-B', '-m', 'devflow2', 'lauf', lid], dict(os.environ, PYTHONPATH=os.pathsep.join(pfade)))


# --- Hilfen --------------------------------------------------------------------------------------

def _lauf(argv, **kw):
    try:
        return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)
    except FileNotFoundError as err:
        raise Fehler(f'{argv[0]} fehlt: {err}') from None


def _ende(r, zeilen=25):
    return '\n'.join((r.stdout + r.stderr).strip().split('\n')[-zeilen:])


def _schritt(name, argv, **kw):
    r = _lauf(argv, **kw)
    if r.returncode:
        raise Fehler(f'{name} fehlgeschlagen:\n{_ende(r)}')
    return r


def quelle_url(quelle=None):
    return quelle or instanz.release_quelle()


def neuestes_tag(quelle):
    r = _schritt('Release-Tags lesen', ['git', 'ls-remote', '--tags', '--refs', quelle, 'refs/tags/v2.*'])
    nummern = [n for n in (_nummer(z.split('refs/tags/')[-1]) for z in r.stdout.splitlines() if 'refs/tags/' in z) if n is not None]
    if not nummern:
        raise Fehler(f'In {quelle} gibt es kein Release (Tag v2.N)')
    return f'v2.{max(nummern)}'


def venv_bauen(wurzel):
    """venv der Fassung aus der festen Paketliste (Python-Version aus `.python-version`, über uv)."""
    version = (wurzel / '.python-version').read_text(encoding='utf-8').strip() if (wurzel / '.python-version').is_file() else '3.11'
    _schritt('venv anlegen', ['uv', 'venv', '--python', version, str(wurzel / '.venv')])
    _schritt('Pakete installieren', ['uv', 'pip', 'install', '--python', str(_python(wurzel)), '-r', str(wurzel / 'requirements.txt')])


def _umgebung(wurzel, **extra):
    env = {k: v for k, v in os.environ.items() if k != 'VIRTUAL_ENV'}
    env.update(PYTHONPATH=str(wurzel), PYTHONDONTWRITEBYTECODE='1', **extra)
    return env


def ziel_format(wurzel):
    r = _schritt('Format der neuen Fassung lesen', [str(_python(wurzel)), '-B', '-m', 'devflow2', 'format'], cwd=wurzel, env=_umgebung(wurzel))
    try:
        return int(r.stdout.strip())
    except ValueError:
        raise Fehler(f'Format der Fassung {wurzel.name} nicht lesbar: {r.stdout.strip()[:80]!r}') from None


def format_pruefen(wurzel):
    """Rückweg nur auf eine Fassung, die das Format des Zustands kennt (Vorwärts-Umstellung macht die neue Fassung selbst)."""
    jetzt, ziel = zustandsformat.stand() or 1, ziel_format(wurzel)
    if ziel < jetzt:
        raise Fehler(f'{wurzel.name} kennt Format {ziel}, der Zustand hat Format {jetzt}. Zurück auf diese Fassung geht nicht, denn sie '
                     f'würde den Zustand beschädigen. Eine Sicherung des Zustands vor der Umstellung liegt in '
                     f'{projekte.zustand() / "sicherung"} (zurückspielen verliert alles seit der Umstellung).')
    return ziel


def tests_laufen(wurzel):
    """Alle Tests der Fassung mit eigenem HOME und eigenem Zustand, nichts vom echten System; wenn möglich über den Testläufer."""
    with tempfile.TemporaryDirectory(prefix='devflow-update-') as tmp:
        tmp = Path(tmp)
        for verz in ('home', 'zustand', 'konfig'):
            (tmp / verz).mkdir()
        env = _umgebung(wurzel, HOME=str(tmp / 'home'), DEVFLOW2_STATE=str(tmp / 'zustand'), DEVFLOW2_CONFIG=str(tmp / 'konfig'),
                        DEVFLOW2_INSTANZ=os.devnull)
        env['PYTHONPATH'] = f'{wurzel}{os.pathsep}{wurzel / "tests2"}'
        for name in ('DEVFLOW2_WEB_PORT', 'DEVFLOW2_WEB_LOGIN', 'DEVFLOW2_WEB_ORIGIN', 'DEVFLOW2_TESTLAUF'):
            env.pop(name, None)
        lauf = wurzel / 'ops' / 'testlauf.sh'
        vorne = [str(lauf)] if os.access(lauf, os.X_OK) and shutil.which('flock') else []
        r = _lauf(vorne + [str(_python(wurzel)), '-B', '-m', 'unittest', 'discover', '-s', 'tests2'], cwd=wurzel, env=env)
    if r.returncode:
        raise Fehler(f'Die Tests der neuen Fassung sind rot; die alte bleibt aktiv:\n{_ende(r)}')


def holen(tag, quelle, ausgabe):
    """Fassung `tag` in ein neues Verzeichnis holen, venv bauen, Format und Tests prüfen. Bei jedem Fehler entsteht nichts Bleibendes."""
    ziel = versionen() / tag
    if (ziel / '.fertig').is_file():
        ausgabe(f'{tag} liegt schon geprüft in {ziel}')
        return ziel
    if ziel.exists():
        shutil.rmtree(ziel)
    versionen().mkdir(parents=True, exist_ok=True)
    try:
        ausgabe(f'Hole {tag} aus {quelle} …')
        _schritt('Holen', ['git', 'clone', '--quiet', '--depth', '1', '--branch', tag, quelle, str(ziel)])
        shutil.rmtree(ziel / '.git', ignore_errors=True)
        ausgabe('Baue venv …')
        venv_bauen(ziel)
        format_pruefen(ziel)
        ausgabe('Tests der neuen Fassung …')
        tests_laufen(ziel)
        schreiben(ziel / '.fertig', tag + '\n')
    except BaseException:
        shutil.rmtree(ziel, ignore_errors=True)
        raise
    return ziel


# --- Umschalten ----------------------------------------------------------------------------------

def aktive_laeufe():
    return [l for l in laeufe.alle() if l['zustand'] in RUHIG and laeufe.lebt(l)]


def ruhe_abwarten(warten, ausgabe):
    takt = float(os.environ.get('DEVFLOW2_UPDATE_TAKT', '10'))
    ende, gemeldet = time.monotonic() + warten, None
    while True:
        aktiv = aktive_laeufe()
        if not aktiv:
            return
        if time.monotonic() >= ende:
            raise Fehler(f'Nach {warten:.0f} s laufen noch {len(aktiv)} Läufe ({", ".join(l["id"] for l in aktiv[:3])}); nichts umgeschaltet, '
                         'die alte Fassung bleibt aktiv. Später erneut aufrufen.')
        ids = sorted(l['id'] for l in aktiv)
        if ids != gemeldet:
            ausgabe(f'Warte auf {len(ids)} aktive Läufe (neue Läufe warten): {", ".join(ids[:5])}')
            gemeldet = ids
        time.sleep(takt)


def _link(pfad, ziel):
    tmp = pfad.with_name(f'.{pfad.name}.{os.getpid()}')
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(ziel)
    os.replace(tmp, pfad)


def dienst_neu_starten(ausgabe, immer=False):
    """`immer`: auch starten, wenn der Dienst nicht läuft (Rückweg nach einem gescheiterten Neustart: `restart` startet ihn dann)."""
    systemctl = os.environ.get('DEVFLOW2_SYSTEMCTL', 'systemctl')
    os.environ.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    if not immer and _lauf([systemctl, '--user', 'is-active', '--quiet', einheit()]).returncode:
        ausgabe(f'{einheit()} läuft nicht; nicht neu gestartet')
        return
    _schritt('Dienst neu starten', [systemctl, '--user', 'restart', einheit()])
    ausgabe(f'{einheit()} neu gestartet (laufende Läufe bleiben)')


def umschalten(ziel, warten, ausgabe):
    """Setzt die Startsperre voraus (`aktualisieren` hält sie vom Holen bis zum Ende)."""
    aktuell = _aktiv().resolve()
    ruhe_abwarten(warten, ausgabe)
    format_pruefen(ziel)  # der Zustand kann sich seit der ersten Prüfung geändert haben
    davor = _vorherige().resolve() if _vorherige().is_symlink() else None

    def zurueck():
        _link(_aktiv(), aktuell)
        if davor:
            _link(_vorherige(), davor)
        else:
            _vorherige().unlink(missing_ok=True)

    _link(_vorherige(), aktuell)
    _link(_aktiv(), ziel)
    try:
        r = _schritt('Zustand umstellen', [str(_python(ziel)), '-B', '-m', 'devflow2', 'format', 'migrieren'], cwd=ziel, env=_umgebung(ziel))
    except Fehler:
        zurueck()
        raise
    if r.stdout.strip():
        ausgabe(r.stdout.strip())
    try:
        dienst_neu_starten(ausgabe)
    except Fehler as err:
        try:
            format_pruefen(aktuell)  # die alte Fassung kennt den (evtl. gerade umgestellten) Zustand nur, wenn das Format gleich blieb
        except Fehler:
            raise Fehler(f'{err}\nDie neue Fassung {ziel.name} bleibt aktiv, weil der Zustand schon umgestellt ist. Dienst prüfen: '
                         f'journalctl --user -u {einheit()}') from None
        zurueck()
        try:
            dienst_neu_starten(ausgabe, immer=True)
        except Fehler:
            pass
        raise Fehler(f'{err}\nNichts umgeschaltet, die alte Fassung {aktuell.name} ist wieder aktiv.') from None


def _aufraeumen(behalten):
    for p in versionen().iterdir():
        if p.is_dir() and p.resolve() not in behalten:
            shutil.rmtree(p, ignore_errors=True)


def aktualisieren(version=None, quelle=None, warten=None, zurueck=False, ausgabe=print):
    """Gibt die Abschlussmeldung zurück; wirft `Fehler`, wenn nichts umgeschaltet wurde."""
    if not _aktiv().is_symlink():
        raise Fehler(f'{_aktiv()} gibt es nicht: devflow ist hier nicht mit ops/installieren.sh eingerichtet')
    if version is not None and _nummer(version) is None:
        raise Fehler(f'Version {version!r} ist kein Release (erwartet v2.N)')
    warten = float(os.environ.get('DEVFLOW2_UPDATE_WARTEN', 6 * 3600)) if warten is None else warten
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / 'aktualisieren.lock', 'a') as riegel:
        try:
            fcntl.flock(riegel, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Fehler('Ein anderes Update läuft gerade') from None
        aktuell = _aktiv().resolve()
        if zurueck:
            if not _vorherige().is_symlink() or not _vorherige().resolve().is_dir():
                raise Fehler('Es gibt keine vorherige Fassung')
            ziel = _vorherige().resolve()
            format_pruefen(ziel)
            tag = ziel.name
        else:
            tag = version or neuestes_tag(quelle_url(quelle))
            neu, alt = _nummer(tag), _nummer(aktuell.name)
            if tag == aktuell.name or (version is None and alt is not None and neu <= alt):
                return f'Schon aktuell: {aktuell.name}'
        _sperre_setzen(tag)  # ab hier keine neuen Läufe, bis das Update fertig oder gescheitert ist (auch nicht beim Holen und Testen)
        try:
            if not zurueck:
                ziel = holen(tag, quelle_url(quelle), ausgabe)
                format_pruefen(ziel)
            umschalten(ziel, warten, ausgabe)
        finally:
            _sperre_aufheben()
        _aufraeumen({ziel.resolve(), aktuell, _vorherige().resolve()})
    return f'{ziel.name} ist aktiv (vorher {aktuell.name}); zurück mit: python -m devflow2 aktualisieren --zurueck'


def main(argv):
    hilfe = 'Aufruf: devflow2 aktualisieren [--version v2.N] [--quelle URL] [--warten SEKUNDEN] [--zurueck]'
    optionen, i = {}, 0
    while i < len(argv):
        if argv[i] == '--zurueck':
            optionen['zurueck'] = True
            i += 1
        elif argv[i] in ('--version', '--quelle', '--warten') and i + 1 < len(argv):
            optionen[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            raise SystemExit(hilfe)
    if 'warten' in optionen:
        try:
            optionen['warten'] = float(optionen['warten'])
        except ValueError:
            raise SystemExit(hilfe) from None
    print(aktualisieren(**optionen, ausgabe=lambda t: print(t, flush=True)))
