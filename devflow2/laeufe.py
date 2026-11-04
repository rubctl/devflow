"""Hintergrundläufe: Claude (`claude -p`), Sol (`codex exec`), Shellbefehle und interne Schritte.

Jeder Lauf ist ein Verzeichnis unter ~/.local/state/devflow2/laeufe/<id>/ mit lauf.json, log.jsonl und
stderr.log. Ausgeführt wird er von einem eigenen Prozess (`python -m devflow2 lauf <id>`), der auch die
Kontingentpause abwartet und danach in derselben Sitzung weitermacht. Läufe haben dieselben Rechte wie
die Sitzungen von Claude und Sol: derselbe Benutzer, eigene CLI-Konfiguration, keine Sandbox von devflow.
"""
from contextlib import contextmanager
import datetime
import email.utils
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

from . import Fehler, instanz, projekte, schreiben

QUOTA = re.compile(r'usage limit|quota exceeded|rate_limit_error|rate limit reached|hit your limit|5.hour limit', re.I)
# Abgelaufene Anmeldung der CLI (nur Muster, der Meldungstext selbst wandert nie in den Hinweis)
LOGIN = re.compile(r'oauth (?:session|token)[^\n]{0,40}(?:expired|invalid|revoked)|session expired|token (?:has )?expired|'
                   r'(?:please|must) (?:run )?/?(?:login|log in|sign in)|run `?(?:claude|codex) login|not logged in|log(?:ged)? in again|'
                   r'sign in again|invalid authentication credentials|authentication_error|refresh token[^\n]{0,40}(?:expired|reused|invalid)', re.I)
SESSION = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{7,100}$')
AKTIV = ('wartet', 'laeuft', 'kontingent', 'abschluss')
TRAUM_ENDE = 'traum-beendet'  # Endzustand eines Traumlaufs: Er wird nie fortgesetzt
TRAUM_FORT = 'Traumläufe werden nicht fortgesetzt; der nächste Traum beginnt in einem neuen Fenster'
WEITER = 'Mach dort weiter, wo du durch das Kontingent unterbrochen wurdest.'
WIEDER = 'Der Lauf wurde unterbrochen. Mach dort weiter, wo du aufgehört hast, und schließe den Auftrag ab.'
FORMAT = ('Deine letzte Antwort war kein gültiges JSON nach dem vorgegebenen Schema. '
          'Antworte jetzt ausschließlich mit diesem JSON (urteil, befunde, bericht).')


class TraumEnde(Exception):
    """Ein Traumlauf endet hier: Grenze, Fensterende, Kontingent, Halt oder Vorrang. Kein Fehler des Laufs."""


def ZEITLIMIT():
    return float(os.environ.get('DEVFLOW2_ZEITLIMIT', 4 * 3600))


def verzeichnis(lid=''):
    return projekte.zustand() / 'laeufe' / lid


def jetzt():
    return time.time()


def laden(lid):
    try:
        return json.loads((verzeichnis(lid) / 'lauf.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise Fehler(f'Lauf {lid} unbekannt') from None


def speichern(lauf):
    schreiben(verzeichnis(lauf['id']) / 'lauf.json', json.dumps(lauf, indent=1, ensure_ascii=False) + '\n')


HAKEN = {}  # Nur für Tests: Name → Funktion, die an genau dieser Stelle läuft (siehe `_haken`)


def _haken(name):
    if name in HAKEN:
        HAKEN[name]()


@contextmanager
def sperre(lid):
    """Lesen, Ändern und Schreiben von `lauf.json` beim Start und beim Anhalten geschieht darunter, nie die Modellarbeit."""
    pfad = verzeichnis(lid) / 'lauf.lock'
    with open(pfad, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def lebt(lauf):
    pid = lauf.get('pid')
    if not pid:
        return False
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            return lauf['id'].encode() in f.read()
    except OSError:
        return False


def verwaist_pruefen(lauf):
    """Aktiver Lauf ohne lebenden Prozess (nach 30 s Anlauffrist) → Zustand `fehler`. Gibt den Lauf zurück."""
    if lauf['zustand'] in AKTIV and not lebt(lauf) and jetzt() - lauf.get('angelegt', 0) > 30:
        with sperre(lauf['id']):  # Der Lauf kann sich inzwischen eingetragen haben oder angehalten worden sein
            lauf = laden(lauf['id'])
            if lauf['zustand'] in AKTIV and not lebt(lauf):
                lauf.update(zustand='fehler', fehler='Prozess unerwartet beendet')
                speichern(lauf)
    return lauf


def alle(projekt=None, aufgabe=None):
    wurzel = verzeichnis()
    out = []
    for d in sorted(wurzel.iterdir()) if wurzel.is_dir() else []:
        try:
            lauf = laden(d.name)
        except (Fehler, ValueError):
            continue
        if (projekt is None or lauf['projekt'] == projekt) and (aufgabe is None or lauf.get('aufgabe') == aufgabe):
            out.append(verwaist_pruefen(lauf))
    return sorted(out, key=lambda l: l['angelegt'])


def angehalten():
    return (projekte.zustand() / 'angehalten').exists()


def halt():
    """Datum und Herkunft des globalen Halts, leer ohne Halt."""
    try:
        return (projekte.zustand() / 'angehalten').read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return ''


def starten(projekt, art, modell, cwd, auftrag='', **extra):
    """Lauf anlegen und im Hintergrund starten. `modell`: claude, sol, shell oder intern.

    Ein globaler Halt verhindert das Anlegen nicht: der Lauf beginnt dann keine Arbeit und steht sichtbar auf `angehalten`.
    Die Werkzeuge prüfen den Halt selbst vor ihren Nebenwirkungen.
    """
    stempel = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    basis = '-'.join(projekte.slug(x) for x in (projekt.name, extra.get('aufgabe') or '', art) if x)
    lid, n = f'{basis}-{stempel}', 1
    while verzeichnis(lid).exists():
        n += 1
        lid = f'{basis}-{stempel}-{n}'
    lauf = dict(id=lid, projekt=projekt.name, art=art, modell=modell, cwd=str(cwd), auftrag=auftrag,
                zustand='wartet', angelegt=jetzt(), **extra)
    if modell in ('claude', 'sol') and not extra.get('cli_modell') and art != 'messung':  # Traumläufe bringen ihr Modell zum Teil selbst mit
        lauf.update(projekt.steuer_cli() if art == 'steuern' else projekt.cli(modell, extra.get('aufgabe')))
    verzeichnis(lid).mkdir(parents=True)
    speichern(lauf)
    _spawn(lid)
    return lauf


def _scope():
    """Im Dashboard-Dienst: Lauf in eine eigene Scope-Einheit, sonst erbt er dessen Speichergrenze (MemoryMax)."""
    if os.environ.get('INVOCATION_ID') and shutil.which('systemd-run'):
        return ['systemd-run', '--user', '--scope', '--quiet', '--collect']
    return []


def _spawn(lid):
    wurzel = str(Path(__file__).resolve().parents[1])
    pfade = [str(Path.home() / '.local/bin')]  # fehlt im PATH des Dienstes; dort liegen claude, codex und node
    pfade += [x for x in os.environ.get('PATH', '').split(os.pathsep) if x and x not in pfade]
    env = dict(os.environ, PATH=os.pathsep.join(pfade),
               PYTHONPATH=os.pathsep.join(x for x in (wurzel, os.environ.get('PYTHONPATH')) if x))
    # setsid -f: eigene Sitzung (anhalten trifft die ganze Gruppe), kein Kindprozess des Servers, keine Zombies.
    start = ['setsid', '-f', sys.executable, '-B', '-m', 'devflow2', 'lauf', lid]
    with open(verzeichnis(lid) / 'lauf.out', 'ab') as out:
        def los(argv):
            subprocess.run(argv, cwd=wurzel, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out, check=True)
        try:
            los(_scope() + start)
        except subprocess.CalledProcessError:
            if not _scope():
                raise
            los(start)  # systemd-run ging nicht: dann eben ohne eigene Einheit


def _stat(pid):
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()
    except (OSError, IndexError):
        return None


def startzeit(pid):
    st = _stat(pid)
    return int(st[19]) if st else None


def gruppe(pgid):
    """Lebende Mitglieder einer Prozessgruppe (Zombies zählen nicht)."""
    out = []
    for e in os.listdir('/proc'):
        if e.isdigit():
            st = _stat(e)
            if st and st[0] != 'Z' and int(st[2]) == pgid:
                out.append(int(e))
    return out


def gruppe_beenden(pgid, start=None):
    """Ganze Prozessgruppe beenden und ihr Ende abwarten (TERM, nach 10 s KILL).

    `start` ist die Startzeit des Gruppenführers; passt sie nicht, gehört die Nummer inzwischen einem fremden Prozess.
    """
    if not pgid:
        return
    mitglieder = gruppe(pgid)
    if not mitglieder or (pgid in mitglieder and start is not None and startzeit(pgid) != start):
        return
    for sig, frist in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        ende = jetzt() + frist
        while jetzt() < ende:
            if not gruppe(pgid):
                return
            time.sleep(0.1)


def _sitzung_im_log(lid, ab=0):
    try:
        with open(verzeichnis(lid) / 'log.jsonl', 'rb') as f:
            f.seek(ab)
            text = f.read().decode(errors='replace')
    except FileNotFoundError:
        return None
    return auswerten(text, '')[0]


def _als_angehalten(lauf, herkunft):
    if not lauf.get('session') and lauf['modell'] in ('claude', 'sol'):
        lauf['session'] = _sitzung_im_log(lauf['id'])
    lauf.update(zustand=TRAUM_ENDE if lauf.get('traum') else 'angehalten', ende=jetzt(), kind_pid=None, halt_herkunft=herkunft)
    speichern(lauf)


def _halt_anfrage(lid):
    """Datei, die nur während `anhalten` besteht; Inhalt ist die Herkunft des Halts."""
    return verzeichnis(lid) / 'halt.anfrage'


def anhalten(lid, herkunft=None):
    """Laufprozess und die von ihm gestartete Arbeit beenden; Sitzung für das Fortsetzen sichern. Die Herkunft bleibt am Lauf:
    Einen Halt aus dem Dashboard setzt nur das Dashboard fort (`ablauf.fortsetzen`). Traumläufe enden dagegen endgültig.

    Der Zustand wird unter der Sperre des Laufs gesetzt. Trägt sich der Prozess zwischen dem Beenden und dem Setzen noch ein
    (Start im selben Augenblick), wird er im nächsten Durchgang beendet; ein später startender Prozess sieht den Halt.
    """
    lauf = laden(lid)
    if lauf['zustand'] not in AKTIV:
        return lauf
    # Anfrage vor allem anderen und ohne Sperre sichtbar machen: Ein Start, der gerade die Sperre hält, beginnt dann
    # keine Modellarbeit mehr (`_start_eintragen`), statt erst nach der Freigabe vom Halter beendet zu werden.
    anfrage = _halt_anfrage(lid)
    anfrage.write_text(herkunft or '', encoding='utf-8')
    try:
        for versuch in range(5):
            lauf = laden(lid)
            if lebt(lauf):
                gruppe_beenden(lauf['pid'], lauf.get('pid_start'))
            lauf = laden(lid)
            gruppe_beenden(lauf.get('kind_pid'), lauf.get('kind_start'))
            _haken('anhalten_beendet')
            with sperre(lid):
                lauf = laden(lid)
                if lauf['zustand'] not in AKTIV:
                    return lauf
                if lebt(lauf) and versuch < 4:
                    continue
                _als_angehalten(lauf, herkunft)
                return lauf
    finally:
        anfrage.unlink(missing_ok=True)


def fortsetzen(lid):
    lauf = laden(lid)
    if lauf.get('traum'):
        raise Fehler(TRAUM_FORT)
    if lauf['zustand'] in AKTIV and lebt(lauf):
        raise Fehler(f'Lauf {lid} läuft noch')
    if lauf['zustand'] not in ('angehalten', 'fehler') + AKTIV:
        raise Fehler(f'Lauf {lid} ist {lauf["zustand"]}; fortsetzen geht nur nach Anhalten oder Fehler')
    gruppe_beenden(lauf.get('kind_pid'), lauf.get('kind_start'))
    with sperre(lid):
        lauf = laden(lid)
        if not lauf.get('session') and lauf['modell'] in ('claude', 'sol'):
            lauf['session'] = _sitzung_im_log(lid)
        lauf.update(zustand='wartet', fortsetzen=True, fortgesetzt=jetzt(), fehler=None, login=None, kind_pid=None, halt_herkunft=None)
        speichern(lauf)
        _halt_anfrage(lid).unlink(missing_ok=True)  # Rest eines abgebrochenen Anhaltens
    _spawn(lid)
    return lauf


def ergebnis(lauf):
    """Vollständige letzte Antwort eines Laufs (lauf.json hält nur eine gekürzte Vorschau)."""
    f = verzeichnis(lauf['id']) / 'ergebnis.txt'
    return f.read_text(encoding='utf-8', errors='replace') if f.exists() else (lauf.get('ergebnis') or '')


# --- Ausführung im Laufprozess -------------------------------------------------------------------

def _cli(name, umgebung):
    return os.environ.get(umgebung) or shutil.which(name) or str(Path.home() / '.local/bin' / name)


def kind_umgebung():
    """Umgebung für Modellläufe: wie die Sitzung, aber ohne Kennungen der steuernden Claude-Sitzung."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith('CLAUDE_CODE_') and k not in ('CLAUDECODE', 'CLAUDE_PID', 'CLAUDE_EFFORT', 'AI_AGENT')}


def befehl(lauf, prompt):
    d = verzeichnis(lauf['id'])
    if lauf['modell'] == 'claude':
        # cli_zusatz vor --output-format: Listenoptionen wie --tools würden sonst den Prompt am Ende mitnehmen
        argv = [_cli('claude', 'DEVFLOW2_CLAUDE'), '-p', *lauf.get('cli_zusatz', ()), '--output-format', 'stream-json', '--verbose',
                '--permission-mode', lauf.get('cli_modus') or os.environ.get('DEVFLOW2_CLAUDE_MODUS', 'auto')]
        if lauf.get('cli_modell'):
            argv += ['--model', lauf['cli_modell']]
        if lauf.get('cli_effort'):
            argv += ['--effort', lauf['cli_effort']]
        if lauf.get('schema'):
            argv += ['--json-schema', json.dumps(lauf['schema'])]
        if lauf.get('session'):
            argv += ['--resume', lauf['session']]
        return argv + [prompt]
    if lauf['modell'] == 'sol':
        argv = [_cli('codex', 'DEVFLOW2_CODEX'), 'exec', *lauf.get('cli_zusatz', ())]
        if lauf.get('session'):
            argv += ['resume', lauf['session']]
        argv += ['--json', '-o', str(d / 'ergebnis.txt')]
        if lauf.get('cli_modell'):
            argv += ['-m', lauf['cli_modell']]
        if lauf.get('cli_effort'):
            argv += ['-c', f'model_reasoning_effort="{lauf["cli_effort"]}"']
        if lauf.get('schema'):
            schreiben(d / 'schema.json', json.dumps(lauf['schema']))
            argv += ['--output-schema', str(d / 'schema.json')]
        return argv + [prompt]
    if lauf['modell'] == 'shell':
        testlauf = instanz.testlaeufer()
        if lauf.get('testlauf') and testlauf and not Path(testlauf).is_file():
            raise Fehler(f'Testläufer {testlauf} fehlt (instanz.toml: testlaeufer; "" schaltet ihn bewusst ab)')
        vorne = [testlauf] if lauf.get('testlauf') and testlauf else []
        return vorne + ['bash', '-c', prompt]
    raise Fehler(f'Modell {lauf["modell"]} unbekannt')


def auswerten(stdout, stderr):
    """Sitzung, Kontingent, Freigabezeit und Ergebnistext aus JSON-Ereignissen (aus devflow 1 übernommen)."""
    session, quota, release, result = None, False, None, ''
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        sid = item.get('thread_id') or item.get('session_id')
        if isinstance(sid, str) and SESSION.fullmatch(sid):
            session = sid
        kind = item.get('type')
        if kind == 'rate_limit_event':
            info = item.get('rate_limit_info', {})
            if info.get('status') == 'rejected':
                quota, release = True, info.get('resetsAt') or info.get('resets_at')
        if kind in ('error', 'turn.failed', 'result'):
            payload = item.get('error', item)
            msg = json.dumps(payload)
            if QUOTA.search(msg) and (kind != 'result' or item.get('is_error')):
                quota = True
                if isinstance(payload, dict):
                    release = payload.get('resets_at') or payload.get('reset_at') or payload.get('retry_after') or release
                release = release or item.get('resets_at') or item.get('reset_at')
                if release is None:
                    m = re.search(r'(?:resets?|try again at)\s+([^"\\\n]{1,100})', msg, re.I)
                    release = m.group(1) if m else None
        if kind == 'result':
            if isinstance(item.get('structured_output'), (dict, list)):
                result = json.dumps(item['structured_output'], ensure_ascii=False)
            elif isinstance(item.get('result'), str):
                result = item['result']
    if re.search(r'(?im)^(?:error: |ERROR: ).*(?:usage limit|quota exceeded|rate limit reached)', stderr):
        quota = True
    if session is None:
        m = re.search(r'(?im)^session id:\s*([a-zA-Z0-9_-]+)\s*$', stderr)
        if m and SESSION.fullmatch(m.group(1)):
            session = m.group(1)
    return session, quota, release, result


def frei_ab(release, jetzt_=None):
    """Freigabezeit als Epoche; unbekannt → in 30 Minuten, höchstens 7 Tage, frühestens in 60 s."""
    jetzt_ = jetzt_ or jetzt()
    wert = None
    if isinstance(release, (int, float)) or (isinstance(release, str) and re.fullmatch(r'\d{9,13}(\.\d+)?', release.strip())):
        wert = float(release)
        wert = wert / 1000 if wert > 1e12 else wert
    elif isinstance(release, str):
        text = release.strip().rstrip('.')
        for parse in (lambda t: datetime.datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp(),
                      lambda t: email.utils.parsedate_to_datetime(t).timestamp()):
            try:
                wert = parse(text)
                break
            except (ValueError, TypeError):
                pass
        if wert is None:
            m = re.search(r'(\d{1,2}):(\d{2})\s*([AP]M)?', text, re.I)
            if m:
                h, mi = int(m.group(1)) % 12 if m.group(3) else int(m.group(1)), int(m.group(2))
                h += 12 if (m.group(3) or '').upper() == 'PM' else 0
                ziel = datetime.datetime.fromtimestamp(jetzt_).replace(hour=h, minute=mi, second=0, microsecond=0)
                if ziel.timestamp() > jetzt_:
                    ziel -= datetime.timedelta(days=1)
                # ziel ist jetzt das letzte Mal, dass es diese Uhrzeit war (heute oder gestern). Kurz danach ist das
                # Kontingent meist nur noch nicht freigeschaltet (Codex 30.10.: Wiederholung um 10:15:20 → „10:15 AM“):
                # in 5 Minuten erneut versuchen statt morgen. Sonst gilt das nächste Mal.
                wert = jetzt_ + 300 if jetzt_ - ziel.timestamp() < 3 * 3600 else (ziel + datetime.timedelta(days=1)).timestamp()
    if wert is None:
        wert = jetzt_ + 1800
    return min(max(wert, jetzt_ + 60 * float(os.environ.get('DEVFLOW2_KONTINGENT_MIN', '1'))), jetzt_ + 7 * 86400)


@contextmanager
def _platzsperre():
    pfad = projekte.zustand() / 'platz.lock'
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _platz_nehmen(lauf):
    """Höchstens DEVFLOW2_MAX Modellläufe gleichzeitig; Shell- und interne Schritte zählen nicht.

    Steuerläufe auch nicht: Sie sind kurz, und mit `DEVFLOW2_MAX=1` warteten sie sonst Stunden auf einen Entwicklerlauf.
    """
    from . import aktualisieren
    grenze = int(os.environ.get('DEVFLOW2_MAX', '1'))
    while True:
        with _platzsperre():
            update = aktualisieren.sperre()  # Während eines Updates beginnt kein Lauf (S8.3)
            if not angehalten() and not update:
                aktualisieren.neu_starten(lauf['id'])  # kehrt zurück, wenn der Code schon der aktive ist (auch nach einer verpassten Sperre)
                belegt = [l for l in alle() if l['id'] != lauf['id'] and l['zustand'] == 'laeuft'
                          and l['modell'] in ('claude', 'sol') and l['art'] not in ('steuern', 'messung') and lebt(l)]
                if lauf['modell'] not in ('claude', 'sol') or lauf['art'] in ('steuern', 'messung') or len(belegt) < grenze:
                    lauf.update(zustand='laeuft', start=lauf.get('start') or jetzt())
                    speichern(lauf)
                    return
        time.sleep(float(os.environ.get('DEVFLOW2_TAKT', '20')))


def login_hinweis(lauf):
    """Was der Inhaber tun muss, wenn die Anmeldung der CLI abgelaufen ist. Bewusst fester Text, nichts aus dem Log."""
    cli = lauf['login']
    schritt = 'claude` starten und mit /login anmelden' if cli == 'claude' else 'codex login`'
    return (f'Anmeldung von {cli} abgelaufen (Lauf {lauf["id"]}): auf dem Server (Arbeitsinstanz: per SSH) `{schritt}, '
            f'danach fortsetzen("{lauf["id"]}")')


def login_cli(lauf, text):
    """`claude` oder `codex`, wenn der Fehlertext eine abgelaufene Anmeldung dieser CLI meldet, sonst None."""
    if lauf.get('modell') in ('claude', 'sol') and LOGIN.search(text or ''):
        return 'claude' if lauf['modell'] == 'claude' else 'codex'
    return None


def befehl_ausfuehren(lauf, argv, out, err, cwd=None, env=None):
    """Befehl in eigener Prozessgruppe. Sitzungskennung sofort sichern; Zeitlimit und Ende beenden die ganze Gruppe."""
    d = verzeichnis(lauf['id'])
    marke = (d / 'log.jsonl').stat().st_size if (d / 'log.jsonl').exists() else 0
    proc = subprocess.Popen(argv, cwd=cwd or lauf['cwd'], stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                            env=env or kind_umgebung(), start_new_session=True)
    lauf.update(kind_pid=proc.pid, kind_start=startzeit(proc.pid))
    speichern(lauf)
    grenze = lauf.get('zeitlimit') or ZEITLIMIT()
    ende = min(jetzt() + grenze, lauf.get('bis') or float('inf'))  # `bis`: feste Grenze eines Traumlaufs, Warten zählt mit
    try:
        while proc.poll() is None:
            if not lauf.get('session') and lauf['modell'] in ('claude', 'sol'):
                sid = _sitzung_im_log(lauf['id'], marke)
                if sid:
                    lauf['session'] = sid
                    speichern(lauf)
            if jetzt() > ende:
                gruppe_beenden(proc.pid, lauf['kind_start'])
                proc.wait()
                if lauf.get('bis') and jetzt() >= lauf['bis']:
                    raise TraumEnde('Fensterende erreicht')
                raise Fehler(f'Zeitlimit von {grenze / 3600:g} h überschritten')
            time.sleep(0.2)
    finally:
        gruppe_beenden(proc.pid, lauf['kind_start'])  # Reste, z. B. Hintergrundprozesse aus Tests
        lauf['kind_pid'] = None
        speichern(lauf)
    return proc.returncode


def _melden(art, lauf):
    """Ereignis für den Selbstlauf; ein Steuerlauf weckt sich nicht selbst, eine Pflegeprüfung hat nichts zu steuern, den Traum steuert `traum.takt`."""
    from . import ereignis
    if lauf['art'] not in ('steuern', 'pflege', 'messung') and not lauf.get('traum'):
        ereignis.still(art, lauf['projekt'], lauf['id'])


def _ende_melden(lauf):
    """Fertig oder Fehler, und die Kette läuft nicht von selbst weiter: Sonst gäbe es für jeden Schritt einen Steuerlauf."""
    if lauf['zustand'] not in ('fertig', 'fehler'):
        return
    if lauf.get('aufgabe') and any(l['id'] != lauf['id'] and l['zustand'] in AKTIV for l in alle(lauf['projekt'], lauf['aufgabe'])):
        return
    _melden('lauf-ende', lauf)


def _traum_tor(lauf):
    """Traumläufe passieren vor jedem Start das Tor des Traums: Grenzen, Fenster, Halt, Vorrang. Wiederholungen zählen dort mit."""
    if lauf.get('traum'):
        from . import traum
        traum.tor(lauf)


def _modell_laufen(lauf, ablauf):
    """CLI- oder Shellaufruf mit Kontingentpause; bei Reviews eine Nachfrage, falls das JSON ungültig ist."""
    d = verzeichnis(lauf['id'])
    fort = lauf.pop('fortsetzen', False)
    prompt = (FORMAT if lauf.get('formatnachfrage') else WIEDER) if fort and lauf.get('session') else lauf['auftrag']
    while True:
        _platz_nehmen(lauf)
        _traum_tor(lauf)
        marke_out = (d / 'log.jsonl').stat().st_size if (d / 'log.jsonl').exists() else 0
        marke_err = (d / 'stderr.log').stat().st_size if (d / 'stderr.log').exists() else 0
        with open(d / 'log.jsonl', 'ab') as out, open(d / 'stderr.log', 'ab') as err:
            # Shellbefehle (Tests, Deploy): stderr mit ins Log, damit Fehlermeldungen im Ergebnis stehen
            rc = befehl_ausfuehren(lauf, befehl(lauf, prompt), out, out if lauf['modell'] == 'shell' else err)
        stdout = (d / 'log.jsonl').read_bytes()[marke_out:].decode(errors='replace')
        stderr = (d / 'stderr.log').read_bytes()[marke_err:].decode(errors='replace')
        if lauf['modell'] == 'shell':
            schreiben(d / 'ergebnis.txt', stdout[-20000:])
            lauf.update(rc=rc, ergebnis=stdout[-6000:])
            return
        sid, quota, release, result = auswerten(stdout, stderr)
        lauf['session'] = sid or lauf.get('session')
        if quota:
            if lauf['art'] == 'messung':
                raise Fehler('Kontingent erschöpft: Der Messaufruf wurde abgelehnt')
            if lauf.get('traum'):
                raise TraumEnde('Kontingent erschöpft: Traumläufe warten nicht auf die Freigabe')
            lauf.update(zustand='kontingent', frei_ab=frei_ab(release))
            speichern(lauf)
            while jetzt() < lauf['frei_ab']:
                time.sleep(min(30, max(0.2, lauf['frei_ab'] - jetzt())))
            prompt = WEITER if lauf.get('session') else lauf['auftrag']
            lauf.update(zustand='wartet', frei_ab=None)
            _melden('kontingent-frei', lauf)
            continue
        if lauf['modell'] == 'sol' and (d / 'ergebnis.txt').exists():
            result = (d / 'ergebnis.txt').read_text(encoding='utf-8', errors='replace')
        else:
            schreiben(d / 'ergebnis.txt', result)
        lauf.update(rc=rc, ergebnis=result[-6000:])
        if rc:
            lauf['login'] = login_cli(lauf, f'{stderr}\n{result}\n{stdout}')  # codex meldet nur im JSON-Strom auf stdout
            raise Fehler(f'Exit {rc}: {stderr.strip()[-800:] or result[-800:]}')
        if lauf['art'] == 'review':
            try:
                ablauf.urteil(result)
            except Fehler:
                if lauf.get('formatnachfrage') or not lauf.get('session'):
                    raise
                lauf['formatnachfrage'] = True
                prompt = FORMAT
                continue
        lauf.pop('formatnachfrage', None)
        return


def _start_eintragen(lid, pid=None):
    """Prozess im Lauf eintragen, unter derselben Sperre wie das Anhalten. Gibt den Lauf zurück, oder None, wenn er nicht
    mehr starten darf: Ist er beim Start schon angehalten (einzeln) oder gilt der globale Halt, beginnt er keine Arbeit und
    bleibt `angehalten`. Der globale Halt liegt vor dem Anhalten der Läufe; wer hier nach ihm kommt, sieht ihn. Ein Einzelhalt
    zeigt sich über `halt.anfrage` schon vor der Sperre, geprüft wird nach dem Eintragen der PID."""
    pid = pid or os.getpid()
    with sperre(lid):
        lauf = laden(lid)
        _haken('start_geladen')
        if lauf['zustand'] not in AKTIV:
            return None
        grund = halt()
        if grund:
            _als_angehalten(lauf, grund.rsplit(' · ', 1)[-1])
            return None
        lauf.update(pid=pid, pid_start=startzeit(pid))
        speichern(lauf)
        _haken('start_eingetragen')
        # Nach dem Eintragen prüfen: Kam die Anfrage früher, bricht der Start hier ab; kam sie später, sieht der Halter
        # die PID und beendet den Prozess.
        try:
            herkunft = _halt_anfrage(lid).read_text(encoding='utf-8')
        except FileNotFoundError:
            return lauf
        lauf.update(pid=None, pid_start=None)
        _als_angehalten(lauf, herkunft or None)
    return None


def ausfuehren(lid):
    """Hauptschleife des Laufprozesses: Arbeit (falls noch nicht erledigt), dann Abschluss mit nächstem Schritt.

    Nach einem Fehler im Abschluss setzt `fortsetzen` nur den Abschluss fort, die Modellarbeit bleibt erhalten.
    """
    from . import ablauf
    _haken('vor_start')
    lauf = _start_eintragen(lid)
    if lauf is None:
        return
    try:
        if not lauf.get('arbeit_fertig'):
            if lauf['modell'] == 'intern':
                _platz_nehmen(lauf)
                _traum_tor(lauf)
                with open(verzeichnis(lid) / 'log.jsonl', 'a', encoding='utf-8') as log:
                    ablauf.intern(lauf, log)
            else:
                _modell_laufen(lauf, ablauf)
            lauf['arbeit_fertig'] = True
        lauf.pop('fortsetzen', None)
        lauf.update(zustand='abschluss', fehler=None)
        speichern(lauf)
        ablauf.abschluss(lauf)
        lauf.update(zustand='fertig', ende=jetzt())
        speichern(lauf)
    except TraumEnde as e:
        lauf.update(zustand=TRAUM_ENDE, grund=str(e), ende=jetzt())
        speichern(lauf)
    except Exception as e:  # Lauf darf nie stumm hängen bleiben
        lauf.update(zustand='fehler', fehler=f'{e}' if isinstance(e, Fehler) else f'{type(e).__name__}: {e}', ende=jetzt())
        lauf['fehler'] = lauf['fehler'][-1500:]
        lauf['login'] = lauf.get('login') or login_cli(lauf, lauf['fehler'])
        speichern(lauf)
        try:
            ablauf.fehlgeschlagen(lauf)
        except Exception:
            pass
        if not isinstance(e, Fehler):
            raise
    finally:
        _ende_melden(lauf)


def log_text(lauf, zeilen=40):
    """Lesbare letzte Ereignisse eines Laufs: Modelltext, Befehle, Werkzeuge."""
    d = verzeichnis(lauf['id'])
    try:
        roh = (d / 'log.jsonl').read_text(encoding='utf-8', errors='replace').splitlines()[-400:]
    except FileNotFoundError:
        return []
    out = []
    for line in roh:
        try:
            item = json.loads(line)
        except ValueError:
            out.append(line[:300])
            continue
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'assistant':
            for teil in item.get('message', {}).get('content', []):
                if teil.get('type') == 'text' and teil.get('text', '').strip():
                    out.append(teil['text'].strip()[:500])
                elif teil.get('type') == 'tool_use':
                    eingabe = teil.get('input', {})
                    out.append(f'▸ {teil.get("name")}: {str(eingabe.get("command") or eingabe.get("file_path") or eingabe)[:200]}')
        elif item.get('type') == 'item.completed':
            it = item.get('item', {})
            if it.get('type') == 'agent_message':
                out.append(str(it.get('text', ''))[:500])
            elif it.get('type') == 'command_execution':
                out.append(f'▸ {str(it.get("command", ""))[:200]} → {it.get("exit_code")}')
            elif it.get('type') == 'file_change':
                out.append(f'▸ Dateien: {", ".join(c.get("path", "") for c in it.get("changes", []))[:200]}')
        elif item.get('type') == 'result':
            out.append(f'Ergebnis: {str(item.get("result", ""))[:500]}')
        elif item.get('type') == 'schritt':
            out.append(str(item.get('text', ''))[:500])
    return out[-zeilen:]
