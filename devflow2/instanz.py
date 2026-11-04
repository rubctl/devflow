"""Instanz-Einstellungen (S8.1): alles, was je Installation anders ist, in `instanz.toml`.

Die Datei liegt in `~/.config/devflow2/instanz.toml` (Verzeichnis `DEVFLOW2_CONFIG`, Datei `DEVFLOW2_INSTANZ`).
Fehlt sie, gelten die Standardwerte unten, und die server-Instanz verhält sich wie vor S8.1. Das sind die einzigen
Stellen im Paket, an denen server-Namen stehen; ein Test sucht den Rest des Pakets danach ab.

Reihenfolge, spätere gewinnen: Standard, `profil`, `instanz.toml`, Umgebungsvariable (`DEVFLOW2_WEB_PORT`,
`DEVFLOW2_WEB_LOGIN`, `DEVFLOW2_WEB_ORIGIN`, `DEVFLOW2_TESTLAUF`, `DEVFLOW2_CONFIG`).

```toml
profil = "arbeit"                # "server" (Standard) oder "arbeit"
host = "arbeit"                  # Name der Maschine in den Aufträgen an die Modelle
inhaber = "Inhaber"              # wer plant, entscheidet und abnimmt: ein Vorname oder die Rolle "Inhaber" (siehe inhaber.py)
projektwurzel = ""               # relative Pfade bei der Projektanmeldung gelten hier; "" = aktuelles Verzeichnis
testlaeufer = "ops/testlauf.sh"  # Befehl vor größeren Testläufen; "" schaltet ihn ab

[web]
adresse = "127.0.0.1"            # nur Loopback; alles andere lehnt devflow ab
port = 8478
login = "keine"                  # "tailscale" (Kopf Tailscale-User-Login) oder "keine"
login_name = "…"                 # erwarteter Tailscale-Login, nur bei login = "tailscale"
tailscale_port = 8472            # Port im Tailnet (nur ops/web_einrichten.sh)
origin = ["https://…"]           # zusätzlich erlaubte Origins für POST

[push]
schluessel = "~/.config/devflow2"  # Verzeichnis mit vapid.key
kontakt = "https://…"              # VAPID-Kontakt (https:// oder mailto:)
```
"""
import ipaddress
import os
from pathlib import Path
import tomllib

from . import Fehler

# --- Standardwerte: die server-Instanz -----------------------------------------------------------

SERVER = {
    'host': 'server',
    'inhaber': 'Inhaber',
    'projektwurzel': '',
    'testlaeufer': '~/dev/werkzeuge/ops/testlauf.sh',
    'web': {'adresse': '127.0.0.1', 'port': 8478, 'login': 'tailscale', 'login_name': 'inhaber@github',
            'tailscale_port': 8472, 'origin': []},
    'push': {'schluessel': '~/.config/devflow2', 'kontakt': 'https://server.example.net:8472'},
}

# Arbeitsinstanz: nur 127.0.0.1, kein Tailscale, Testläufer aus diesem Checkout.
ARBEIT = {
    'host': 'arbeit',
    'inhaber': 'Inhaber',
    'projektwurzel': '',
    'testlaeufer': None,  # → ops/testlauf.sh neben dem Paket
    'web': {'adresse': '127.0.0.1', 'port': 8478, 'login': 'keine', 'login_name': '', 'tailscale_port': 0, 'origin': []},
    'push': {'schluessel': '~/.config/devflow2', 'kontakt': 'https://localhost'},
}

# Öffentliches Release-Repo (nur Code, Tags v2.N), woher `devflow2 aktualisieren` liest; Umgebung `DEVFLOW2_RELEASE_QUELLE`.
RELEASE_QUELLE = 'https://github.com/rubctl/devflow.git'

PROFILE = {'server': SERVER, 'arbeit': ARBEIT}
LOGINARTEN = ('tailscale', 'keine')
CHECKOUT = Path(__file__).resolve().parent.parent


def datei():
    roh = os.environ.get('DEVFLOW2_INSTANZ')
    return Path(roh).expanduser() if roh else Path(os.environ.get('DEVFLOW2_CONFIG') or '~/.config/devflow2').expanduser() / 'instanz.toml'


def _text(wert, name, leer=False):
    if not isinstance(wert, str) or (not wert.strip() and not leer):
        raise Fehler(f'instanz.toml: {name} muss ein Text{"" if leer else " und nicht leer"} sein')
    return wert.strip()


def _lesen():
    pfad = datei()
    try:
        roh = tomllib.loads(pfad.read_text(encoding='utf-8'))
    except FileNotFoundError:
        if os.environ.get('DEVFLOW2_INSTANZ'):
            raise Fehler(f'{pfad} (DEVFLOW2_INSTANZ) gibt es nicht') from None
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as err:
        raise Fehler(f'{pfad}: {err}') from None
    bekannt = {'profil', 'host', 'inhaber', 'projektwurzel', 'testlaeufer', 'web', 'push'}
    unbekannt = sorted(set(roh) - bekannt)
    if unbekannt:
        raise Fehler(f'instanz.toml: unbekannte Einstellung {", ".join(unbekannt)}')
    for teil in ('web', 'push'):
        if not isinstance(roh.get(teil, {}), dict):
            raise Fehler(f'instanz.toml: [{teil}] muss eine Tabelle sein')
    return roh


def loopback(adresse):
    """True nur für 127.0.0.0/8, ::1 und `localhost`; nie für 0.0.0.0, `::` oder andere Namen."""
    if adresse == 'localhost':
        return True
    try:
        return ipaddress.ip_address(adresse).is_loopback
    except ValueError:
        return False


def _pruefen(werte):
    web, push = werte['web'], werte['push']
    _text(werte['host'], 'host')
    _text(werte['inhaber'], 'inhaber')
    _text(werte['projektwurzel'], 'projektwurzel', leer=True)
    if not isinstance(werte['testlaeufer'], str):
        raise Fehler('instanz.toml: testlaeufer muss ein Text sein')
    adresse = _text(web['adresse'], 'web.adresse')
    if not loopback(adresse):
        raise Fehler(f'instanz.toml: web.adresse {adresse!r} nicht erlaubt; das Dashboard lauscht nur auf Loopback (127.0.0.1). '
                     'Von außen geht es über SSH-Tunnel oder tailscale serve.')
    if isinstance(web['port'], bool) or not isinstance(web['port'], int) or not 1 <= web['port'] <= 65535:
        raise Fehler('instanz.toml: web.port muss eine Zahl von 1 bis 65535 sein')
    if web['login'] not in LOGINARTEN:
        raise Fehler(f'instanz.toml: web.login muss {" oder ".join(LOGINARTEN)} sein')
    if web['login'] == 'tailscale':
        _text(web['login_name'], 'web.login_name')
    if isinstance(web['tailscale_port'], bool) or not isinstance(web['tailscale_port'], int) or not 0 <= web['tailscale_port'] <= 65535:
        raise Fehler('instanz.toml: web.tailscale_port muss eine Zahl von 0 bis 65535 sein')
    if not isinstance(web['origin'], list) or not all(isinstance(o, str) for o in web['origin']):
        raise Fehler('instanz.toml: web.origin muss eine Liste von Texten sein')
    _text(push['schluessel'], 'push.schluessel')
    _text(push['kontakt'], 'push.kontakt')


def laden():
    """Die geltenden Einstellungen (Standard, Profil, Datei, Umgebung), geprüft; wirft `Fehler` bei Unsinn."""
    roh = _lesen()
    profil = roh.get('profil', 'server')
    if profil not in PROFILE:
        raise Fehler(f'instanz.toml: profil muss {" oder ".join(PROFILE)} sein')
    basis = PROFILE[profil]
    werte = {k: basis[k] for k in ('host', 'inhaber', 'projektwurzel', 'testlaeufer')}
    werte['web'], werte['push'] = dict(basis['web']), dict(basis['push'])
    for k in ('host', 'inhaber', 'projektwurzel', 'testlaeufer'):
        if k in roh:
            werte[k] = roh[k]
    for teil in ('web', 'push'):
        unbekannt = sorted(set(roh.get(teil, {})) - set(werte[teil]))
        if unbekannt:
            raise Fehler(f'instanz.toml: unbekannte Einstellung {teil}.{", ".join(unbekannt)}')
        werte[teil].update(roh.get(teil, {}))
    env = os.environ
    if env.get('DEVFLOW2_WEB_PORT'):
        try:
            werte['web']['port'] = int(env['DEVFLOW2_WEB_PORT'])
        except ValueError:
            raise Fehler('DEVFLOW2_WEB_PORT muss eine Zahl sein') from None
    if env.get('DEVFLOW2_WEB_LOGIN'):
        werte['web']['login_name'] = env['DEVFLOW2_WEB_LOGIN']
    if env.get('DEVFLOW2_WEB_ORIGIN'):
        werte['web']['origin'] = [o.strip() for o in env['DEVFLOW2_WEB_ORIGIN'].split(',') if o.strip()]
    if env.get('DEVFLOW2_CONFIG'):
        werte['push']['schluessel'] = env['DEVFLOW2_CONFIG']
    if 'DEVFLOW2_TESTLAUF' in env:  # auch leer: schaltet den Testläufer ab
        werte['testlaeufer'] = env['DEVFLOW2_TESTLAUF']
    if werte['testlaeufer'] is None:
        werte['testlaeufer'] = str(CHECKOUT / 'ops' / 'testlauf.sh')
    _pruefen(werte)
    return werte


# --- Zugriffe ------------------------------------------------------------------------------------

def host():
    return laden()['host']


def web():
    return laden()['web']


def testlaeufer_anzeige():
    """Der Testläufer so, wie er in Aufträgen steht (mit `~`); leer, wenn abgeschaltet."""
    return laden()['testlaeufer']


def testlaeufer():
    """Pfad des Testläufers (`~` aufgelöst) oder '' wenn abgeschaltet."""
    roh = testlaeufer_anzeige()
    return str(Path(roh).expanduser()) if roh else ''


def projektpfad(pfad):
    """Pfad eines Projekts; relative Pfade gelten ab `projektwurzel`, bei leerer Wurzel ab dem aktuellen Verzeichnis."""
    p = Path(pfad).expanduser()
    wurzel = laden()['projektwurzel']
    return p if p.is_absolute() or not wurzel else Path(wurzel).expanduser() / p


def schluessel_verzeichnis():
    return Path(laden()['push']['schluessel']).expanduser()


def release_quelle():
    return os.environ.get('DEVFLOW2_RELEASE_QUELLE') or RELEASE_QUELLE


def kontakt():
    """VAPID-Kontakt: die erste https-Origin aus `web.origin`, sonst `push.kontakt`."""
    cfg = laden()
    for o in cfg['web']['origin']:
        if o.startswith('https://'):
            return o.rstrip('/')
    return cfg['push']['kontakt']


def wert(schluessel):
    """Ein Wert als Text für Skripte, z. B. `web.port`; Listen kommagetrennt."""
    teile = schluessel.split('.')
    aktuell = laden()
    for teil in teile:
        if not isinstance(aktuell, dict) or teil not in aktuell:
            raise Fehler(f'Einstellung {schluessel!r} gibt es nicht')
        aktuell = aktuell[teil]
    if isinstance(aktuell, dict):
        raise Fehler(f'{schluessel!r} ist eine Gruppe; einen der Werte darin nennen')
    return ','.join(aktuell) if isinstance(aktuell, list) else str(aktuell)
