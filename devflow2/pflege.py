"""Pflegeprüfung (S5.1): Sicherheitslücken und veraltete Pakete der Projekte, ohne KI. Routine-Updates (S5.2) stehen in `pflegeplan.py`.

Ein Lauf der Art `pflege` (intern) prüft den committeten Stand des Basisbranchs in einer Kopie (`git archive` in ein
Temp-Verzeichnis) mit eigener Umgebung, nie im laufenden Dienst und nie in den Arbeitsdateien. Die Werkzeuge fragen öffentliche
Datenbanken (OSV, PyPI, npm) nur lesend ab und senden Paketnamen und Versionen, keinen Code:

- Python: `pip-audit` (Lücken) und `uv pip list --outdated` (veraltet) in einer Umgebung, die `uv` aus den Anforderungen baut
- Node: `npm audit --json` und `npm outdated --json` (nach `npm ci --ignore-scripts`)
- sonst: `osv-scanner` über die Lockdateien

Ergebnis: `pflege/<projekt>.json` im Zustandsverzeichnis (für Seite „Pflege“ und Push), `berichte/pflege-JJJJ-MM-TT.md` im Projekt und je
neuer Lücke ein Eintrag im Eingang mit Herkunft `pflege`. Ein Werkzeug, das fehlt oder scheitert, ergibt „nicht geprüft“ mit Grund, nie
einen Fehler des Laufs. Doppelfunde (mehrere Werkzeuge, mehrere Kennungen derselben Lücke) werden zu einem Fund. Eingang-Texte enthalten nur
Paket, Version, Kennungen und Schwere, nie Freitext aus den Datenbanken.

`[pflege]` in `devflow.toml` (alles optional, devflow erkennt Standardwerte aus den Dateien im Basisbranch): `aktiv`, `pruefungen`,
`umgebung` (Pfad einer Python-Umgebung, deren installierte Pakete geprüft werden), `python_quellen`, `node_verzeichnisse`, `lockdateien`.
"""
from contextlib import contextmanager
import datetime
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from urllib.parse import quote

from . import Fehler, eingang, laeufe, projekte, repo, roadmap, schreiben
from .ablauf import datum
from .projekte import Projekt

PRUEFUNGEN = ('pip-audit', 'pip-outdated', 'npm-audit', 'npm-outdated', 'osv-scanner')
SCHLUESSEL = ('aktiv', 'pruefungen', 'umgebung', 'python_quellen', 'node_verzeichnisse', 'lockdateien')
LOCKDATEIEN = ('Cargo.lock', 'go.mod', 'poetry.lock', 'uv.lock', 'Pipfile.lock', 'pnpm-lock.yaml', 'yarn.lock', 'Gemfile.lock',
               'composer.lock', 'pubspec.lock', 'mix.lock', 'package-lock.json')
SCHWEREN = ('kritisch', 'hoch', 'mittel', 'niedrig', 'unbekannt')
RANG = {'unbekannt': 0, 'niedrig': 1, 'mittel': 2, 'hoch': 3, 'kritisch': 4}
PUSH_AB = ('hoch', 'kritisch')
WORTE = {'critical': 'kritisch', 'high': 'hoch', 'moderate': 'mittel', 'medium': 'mittel', 'low': 'niedrig'}
INTERVALL = 7 * 86400
WIEDERHOLUNG = 86400  # nach einem Fehler früher erneut versuchen
ZEIT = 1200
MAX_NEU = 25  # neue Eingangseinträge je Lauf; die schwersten zuerst, der Rest folgt im nächsten Lauf (der Bericht nennt alle)
EINGANG_TEXT = re.compile(r'Pflege-Lücke \(([^)]+)\) ([^:]*): ')
KENNUNG = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}')
FREEZE = ("import importlib.metadata as m;"
          "print('\\n'.join(sorted({f'{d.metadata[\"Name\"]}=={d.version}' for d in m.distributions() if d.metadata['Name']})))")
PYPROJEKT = 'pyproject.toml'


# --- Einstellungen und Erkennung ---------------------------------------------------------------

def _dateien(p):
    r = subprocess.run(['git', 'ls-tree', '-r', '--name-only', '-z', p.cfg['basis']], cwd=p.repo, capture_output=True, text=True)
    if r.returncode:
        raise Fehler(f'{p.name}: Dateiliste von {p.cfg["basis"]} nicht lesbar')
    return [x for x in r.stdout.split('\0') if x and 'node_modules/' not in x]


def _pyproject_hat_pakete(p):
    r = subprocess.run(['git', 'show', f'{p.cfg["basis"]}:{PYPROJEKT}'], cwd=p.repo, capture_output=True, text=True)
    try:
        return bool(tomllib.loads(r.stdout).get('project', {}).get('dependencies')) if r.returncode == 0 else False
    except tomllib.TOMLDecodeError:
        return False


def erkennen(p, umgebung=''):
    """Standardwerte aus den Dateien im Basisbranch."""
    dateien = _dateien(p)
    py = sorted(f for f in dateien if re.fullmatch(r'(?:.*/)?requirements[^/]*\.txt', f))
    if PYPROJEKT in dateien and _pyproject_hat_pakete(p):
        py.append(PYPROJEKT)
    node = sorted({str(Path(f).parent) for f in dateien if Path(f).name == 'package.json'})
    lock = sorted(f for f in dateien if Path(f).name in LOCKDATEIEN)
    pruefungen = []
    if py or umgebung:
        pruefungen += ['pip-audit', 'pip-outdated']
    if node:
        pruefungen += ['npm-audit', 'npm-outdated']
    if not pruefungen and lock:
        pruefungen = ['osv-scanner']
    return dict(aktiv=True, pruefungen=pruefungen, umgebung=umgebung, python_quellen=py, node_verzeichnisse=node, lockdateien=lock)


def _pfad_ok(x):
    return isinstance(x, str) and x and not x.startswith('/') and '..' not in Path(x).parts and '\0' not in x


def konfig(p):
    """`[pflege]` mit erkannten Standardwerten. Unbekannte Schlüssel und falsche Typen sind ein Fehler (Tippfehler sollen auffallen)."""
    roh = p.cfg.get('pflege', {})
    if not isinstance(roh, dict):
        raise Fehler('devflow.toml: [pflege] muss eine Tabelle sein')
    if set(roh) - set(SCHLUESSEL):
        raise Fehler(f'devflow.toml: [pflege] kennt {", ".join(sorted(set(roh) - set(SCHLUESSEL)))} nicht; erlaubt: {", ".join(SCHLUESSEL)}')
    umgebung = roh.get('umgebung', '')
    if not isinstance(umgebung, str):
        raise Fehler('devflow.toml: [pflege] umgebung muss ein Pfad sein')
    cfg = {**erkennen(p, umgebung), **roh}
    if type(cfg['aktiv']) is not bool:
        raise Fehler('devflow.toml: [pflege] aktiv muss true oder false sein')
    for k in ('pruefungen', 'python_quellen', 'node_verzeichnisse', 'lockdateien'):
        if not isinstance(cfg[k], list) or not all(isinstance(x, str) for x in cfg[k]):
            raise Fehler(f'devflow.toml: [pflege] {k} muss eine Liste von Texten sein')
    falsch = [x for x in cfg['pruefungen'] if x not in PRUEFUNGEN]
    if falsch:
        raise Fehler(f'devflow.toml: [pflege] pruefungen unbekannt: {", ".join(falsch)}; erlaubt: {", ".join(PRUEFUNGEN)}')
    if not all(_pfad_ok(x) for k in ('python_quellen', 'lockdateien') for x in cfg[k]) or not all(_pfad_ok(x) or x == '.' for x in cfg['node_verzeichnisse']):
        raise Fehler('devflow.toml: [pflege] Pfade müssen relativ zum Projekt sein und dürfen nicht nach oben zeigen')
    return cfg


# --- Werkzeuge ---------------------------------------------------------------------------------

def werkzeug(name):
    """Pfad des Prüfwerkzeugs: `DEVFLOW2_PFLEGE_<NAME>`, dann das devflow-venv, `~/.local/bin`, `PATH`; sonst None."""
    kandidaten = [os.environ.get('DEVFLOW2_PFLEGE_' + name.upper().replace('-', '_')), Path(sys.executable).parent / name,
                  Path.home() / '.local/bin' / name]
    for k in kandidaten:
        if k and Path(k).is_file() and os.access(k, os.X_OK):
            return str(k)
    return shutil.which(name)


def ausfuehren_befehl(argv, cwd, env, zeit=ZEIT):
    """(Exitcode, stdout, stderr); die Attrappen der Tests haben dieselbe Form."""
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors='replace', timeout=zeit)
    except subprocess.TimeoutExpired:
        raise Fehler(f'Zeitlimit von {zeit // 60} min überschritten') from None
    except OSError as err:
        raise Fehler(f'{argv[0]} nicht startbar: {err.strerror or err}') from None
    return r.returncode, r.stdout, r.stderr


def _http_json(url, zeit=10):
    import urllib.request
    anfrage = urllib.request.Request(url, headers={'User-Agent': 'devflow2-pflege', 'Accept': 'application/json'})
    with urllib.request.urlopen(anfrage, timeout=zeit) as r:
        return json.loads(r.read(1_000_000).decode('utf-8', 'replace'))


class Rahmen:
    """Alles, was nach außen greift: Befehle, Werkzeugsuche, Schwere-Nachschlagen. Tests tauschen es aus."""

    def __init__(self, run=ausfuehren_befehl, finde=werkzeug, hole=_http_json, anfragen=60):
        self.run, self.finde, self.hole, self.uebrig = run, finde, hole, anfragen

    def schwere(self, ids):
        """Schwere einer Lücke bei OSV nachschlagen (pip-audit nennt keine); Fehler → `unbekannt`."""
        for i in sorted(ids, key=lambda x: (not x.startswith('GHSA'), x)):
            if self.uebrig <= 0:
                break
            self.uebrig -= 1
            try:
                d = self.hole(f'https://api.osv.dev/v1/vulns/{quote(i)}')
            except laeufe.TraumEnde:
                raise
            except Exception:  # Netz, Format: die Schwere ist nur eine Zugabe
                continue
            s = schwere_aus_osv(d if isinstance(d, dict) else {})
            if s != 'unbekannt':
                return s
        return 'unbekannt'


def _frist(lauf):
    """Restzeit eines Traumlaufs; am Fensterende endet er, statt weiter zu prüfen."""
    rest = lauf['bis'] - laeufe.jetzt()
    if rest <= 0:
        raise laeufe.TraumEnde('Fensterende erreicht')
    return rest


def traum_rahmen(lauf):
    """Rahmen eines Traumlaufs: `laeufe.befehl_ausfuehren` beendet den Befehl samt Prozessgruppe an `bis`, auch ohne Dashboard-Takte."""
    lauf['zeitlimit'] = ZEIT

    def run(argv, cwd, env):
        _frist(lauf)
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                rc = laeufe.befehl_ausfuehren(lauf, argv, out, err, cwd, env)
            except OSError as e:
                raise Fehler(f'{argv[0]} nicht startbar: {e.strerror or e}') from None
            out.seek(0)
            err.seek(0)
            return rc, out.read().decode(errors='replace'), err.read().decode(errors='replace')

    return Rahmen(run=run, hole=lambda url: _http_json(url, min(10, _frist(lauf))))


# --- Schwere und Versionen ---------------------------------------------------------------------

def schwere_wort(wort):
    return WORTE.get(str(wort).strip().lower(), 'unbekannt')


def schwere_punkte(punkte):
    try:
        x = float(punkte)
    except (TypeError, ValueError):
        return 'unbekannt'
    return 'kritisch' if x >= 9 else 'hoch' if x >= 7 else 'mittel' if x >= 4 else 'niedrig' if x > 0 else 'unbekannt'


def cvss3(vektor):
    """Basiswert eines CVSS-3.x-Vektors; None bei anderen Vektoren (z. B. CVSS 4)."""
    m = re.fullmatch(r'CVSS:3\.[01]/(.*)', str(vektor))
    if not m:
        return None
    w = dict(x.split(':', 1) for x in m.group(1).split('/') if ':' in x)
    try:
        geaendert = w['S'] == 'C'
        av = {'N': .85, 'A': .62, 'L': .55, 'P': .2}[w['AV']]
        ac = {'L': .77, 'H': .44}[w['AC']]
        pr = {'N': .85, 'L': .68 if geaendert else .62, 'H': .5 if geaendert else .27}[w['PR']]
        ui = {'N': .85, 'R': .62}[w['UI']]
        c, i, a = ({'H': .56, 'L': .22, 'N': 0}[w[k]] for k in 'CIA')
    except KeyError:
        return None
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    wirkung = 7.52 * (iss - .029) - 3.25 * (iss - .02) ** 15 if geaendert else 6.42 * iss
    if wirkung <= 0:
        return 0.0
    technisch = 8.22 * av * ac * pr * ui
    return math.ceil(round(min((1.08 if geaendert else 1) * (wirkung + technisch), 10) * 10, 6)) / 10


def schwere_aus_osv(eintrag):
    """Schwere aus einem OSV-Eintrag: `database_specific.severity` (GHSA), sonst der CVSS-3-Vektor."""
    s = schwere_wort((eintrag.get('database_specific') or {}).get('severity', ''))
    if s != 'unbekannt':
        return s
    for x in eintrag.get('severity') or ():
        punkte = cvss3(x.get('score', '')) if isinstance(x, dict) else None
        if punkte is not None:
            return schwere_punkte(punkte)
    return 'unbekannt'


def hoeher(a, b):
    return a if RANG[a] >= RANG[b] else b


def zahlen(version):
    return [int(x) for x in re.findall(r'\d+', str(version))[:3]]


def sprung(alt, neu):
    """`major`, `minor`, `patch` oder `unklar` für den Weg von `alt` nach `neu`."""
    a, n = zahlen(alt), zahlen(neu)
    if not a or not n:
        return 'unklar'
    a, n = a + [0] * (3 - len(a)), n + [0] * (3 - len(n))
    return 'major' if n[0] != a[0] else 'minor' if n[1] != a[1] else 'patch' if n[2] != a[2] else 'unklar'


def _name(x):
    return re.sub(r'[-_.]+', '-', str(x)).lower()


def _text(x, muster=r'[^A-Za-z0-9@/._+~-]'):
    """Wert aus fremder Ausgabe, der in Berichte und Eingang gelangt: nur unverfängliche Zeichen."""
    return re.sub(muster, '', str(x))[:80]


def _kennungen(ids):
    return sorted({k for k in (_text(i, r'[^A-Za-z0-9._-]') for i in ids) if KENNUNG.fullmatch(k)},
                  key=lambda k: (not k.startswith('CVE-'), not k.startswith('GHSA-'), k))


# --- Parser: feste Ausgabeformate der Werkzeuge ------------------------------------------------

def _json(text, was):
    try:
        return json.loads(text)
    except ValueError:
        raise Fehler(f'Ausgabe von {was} nicht lesbar') from None


def fund(paket, version, eco, ids, schwere, behoben, quelle, ort=''):
    return dict(paket=_text(paket), version=_text(version), eco=eco, ids=_kennungen(ids), schwere=schwere,
                behoben=sorted({_text(b) for b in behoben if b}), quellen=[quelle], orte=[ort] if ort else [])


def pip_audit(text, schwere):
    """Funde aus `pip-audit -f json`. Die Schwere liefert `schwere(ids)` (OSV), das Werkzeug nennt keine."""
    d = _json(text, 'pip-audit')
    liste = d.get('dependencies', []) if isinstance(d, dict) else d
    if not isinstance(liste, list):
        raise Fehler('Ausgabe von pip-audit nicht lesbar')
    funde, uebersprungen = [], []
    for dep in liste:
        if not isinstance(dep, dict):
            continue
        if dep.get('skip_reason'):
            uebersprungen.append(_text(dep.get('name', '?')))
        for v in dep.get('vulns') or ():
            ids = [v.get('id', ''), *(v.get('aliases') or ())]
            funde.append(fund(dep.get('name', ''), dep.get('version', ''), 'pypi', ids, schwere(_kennungen(ids)), v.get('fix_versions') or (), 'pip-audit'))
    return funde, uebersprungen


def lock_versionen(text):
    """Installierte Versionen aus einer `package-lock.json` (Pfad wie `node_modules/a/node_modules/b` → Version)."""
    try:
        d = json.loads(text)
    except ValueError:
        return {}
    pakete = (d.get('packages') or {}) if isinstance(d, dict) else {}
    return {pfad: v['version'] for pfad, v in pakete.items() if isinstance(v, dict) and v.get('version')}


def npm_audit(text, ort='', versionen=None):
    """Funde aus `npm audit --json` (Berichtsformat 2). Nur eigene Hinweise (`via` als Objekt); weitergereichte stehen beim Ursprung.
    `npm audit` nennt nur den betroffenen Bereich, die Version kommt aus `versionen` (Lockdatei) über die betroffenen Pfade (`nodes`)."""
    d = _json(text, 'npm audit')
    if not isinstance(d, dict):
        raise Fehler('Ausgabe von npm audit nicht lesbar')
    if isinstance(d.get('error'), dict):
        grund = _text(d['error'].get('summary') or d['error'].get('code') or 'Fehler', r'[^\w .,:/-]')
        raise Fehler(f'npm audit: {grund}')
    funde = []
    for name, v in (d.get('vulnerabilities') or {}).items():
        if not isinstance(v, dict):
            continue
        fix = v.get('fixAvailable')
        pfade = [n for n in v.get('nodes') or () if isinstance(n, str)]
        for via in v.get('via') or ():
            if not isinstance(via, dict):
                continue
            url = str(via.get('url', ''))
            ids = [url.rsplit('/', 1)[-1]] if 'GHSA-' in url else []
            ids += [f'npm-{via["source"]}'] if via.get('source') and not ids else []
            paket = via.get('name') or name
            # Die Fix-Version gilt für das Paket in `fixAvailable.name`; muss ein Elternpaket aktualisiert werden, ist sie keine Version von `paket`.
            behoben = [fix['version']] if isinstance(fix, dict) and fix.get('version') and fix.get('name', name) == paket else []
            # Ein Fund je betroffener Version (auch verschachtelte Kopien); ohne `nodes` die oberste Ebene.
            orte = pfade if paket == name and pfade else [f'node_modules/{paket}']
            for version in sorted({(versionen or {}).get(pf, '') for pf in orte}):
                funde.append(fund(paket, version, 'npm', ids, schwere_wort(via.get('severity', v.get('severity', ''))), behoben, 'npm audit', ort))
    return funde


def osv_scanner(text):
    """Funde aus `osv-scanner --format json`; Gruppen fassen Kennungen derselben Lücke zusammen."""
    d = _json(text, 'osv-scanner')
    if not isinstance(d, dict):
        raise Fehler('Ausgabe von osv-scanner nicht lesbar')
    funde = []
    for res in d.get('results') or ():
        ort = _text((res.get('source') or {}).get('path', ''))
        for pk in res.get('packages') or ():
            info = pk.get('package') or {}
            vulns = {v.get('id'): v for v in pk.get('vulnerabilities') or () if isinstance(v, dict)}
            gruppen = [g for g in pk.get('groups') or () if isinstance(g, dict)]
            if not gruppen:
                gruppen = [dict(ids=[i]) for i in vulns]
            for g in gruppen:
                mitglieder = [vulns[i] for i in g.get('ids') or () if i in vulns]
                ids = [*(g.get('ids') or ()), *(a for v in mitglieder for a in v.get('aliases') or ()), *(g.get('aliases') or ())]
                s = schwere_punkte(g.get('max_severity'))
                for v in mitglieder:
                    s = hoeher(s, schwere_aus_osv(v))
                behoben = [e['fixed'] for v in mitglieder for a in v.get('affected') or () for r in a.get('ranges') or ()
                           for e in r.get('events') or () if isinstance(e, dict) and e.get('fixed')
                           and _name((a.get('package') or {}).get('name', info.get('name', ''))) == _name(info.get('name', ''))]
                funde.append(fund(info.get('name', ''), info.get('version', ''), str(info.get('ecosystem', '')).lower(), ids, s, behoben, 'osv-scanner', ort))
    return funde


def veraltet_liste(text, was='uv'):
    """Veraltete Pakete aus `uv/pip list --outdated --format json` (`version`, `latest_version`)."""
    d = _json(text, f'{was} list --outdated')
    if not isinstance(d, list):
        raise Fehler(f'Ausgabe von {was} list --outdated nicht lesbar')
    return [dict(paket=_text(x.get('name', '')), aktuell=_text(x.get('version', '')), neu=_text(x.get('latest_version', '')),
                 sprung=sprung(x.get('version', ''), x.get('latest_version', '')), quelle=was)
            for x in d if isinstance(x, dict) and x.get('name')]


def npm_outdated(text, ort=''):
    d = _json(text, 'npm outdated')
    if not isinstance(d, dict):
        raise Fehler('Ausgabe von npm outdated nicht lesbar')
    out = []
    for name, v in d.items():
        v = v[-1] if isinstance(v, list) and v else v
        if isinstance(v, dict) and v.get('latest') and v.get('current') != v.get('latest'):
            out.append(dict(paket=_text(name), aktuell=_text(v.get('current') or '–'), neu=_text(v['latest']),
                            sprung=sprung(v.get('current'), v['latest']) if v.get('current') else 'unklar', quelle='npm outdated', ort=ort))
    return out


def zusammenfuehren(funde):
    """Doppelfunde zu einem Fund: gleiches Paket und mindestens eine gemeinsame Kennung (auch über Werkzeuge hinweg)."""
    gruppen = []
    for f in funde:
        ids = {i.lower() for i in f['ids']}
        treffer = [g for g in gruppen if g['eco'] == f['eco'] and _name(g['paket']) == _name(f['paket']) and ids & {i.lower() for i in g['ids']}]
        if not treffer:
            gruppen.append(dict(f, quellen=list(f['quellen']), orte=list(f['orte']), behoben=list(f['behoben']), ids=list(f['ids'])))
            continue
        ziel = treffer[0]
        for g in treffer[1:]:
            gruppen.remove(g)
            _vereinen(ziel, g)
        _vereinen(ziel, f)
    for g in gruppen:
        g['ids'] = _kennungen(g['ids'])
        g['behoben'] = sorted(set(g['behoben']))
        g['quellen'] = sorted(set(g['quellen']))
        g['orte'] = sorted(set(g['orte']))
    return sorted(gruppen, key=lambda g: (-RANG[g['schwere']], _name(g['paket']), g['ids']))


def _vereinen(ziel, f):
    ziel['ids'] += f['ids']
    ziel['behoben'] += f['behoben']
    ziel['quellen'] += f['quellen']
    ziel['orte'] += f['orte']
    ziel['schwere'] = hoeher(ziel['schwere'], f['schwere'])
    ziel['version'] = ', '.join(sorted({*ziel['version'].split(', '), *f['version'].split(', ')} - {''}))


def kennung(f):
    """Stabiler Schlüssel eines Fundes für Push und Listen (ohne `:`)."""
    return f'{_name(f["paket"])}-{f["ids"][0] if f["ids"] else "ohne-kennung"}'.replace(':', '-')


# --- Prüfung -----------------------------------------------------------------------------------

def _env(extra=None):
    pfade = [str(Path.home() / '.local/bin'), str(Path(sys.executable).parent)]
    pfade += [x for x in os.environ.get('PATH', '').split(os.pathsep) if x and x not in pfade]
    env = {k: v for k, v in os.environ.items() if k in ('HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'XDG_CACHE_HOME')
           or k.lower() in ('http_proxy', 'https_proxy', 'no_proxy')}
    env.update(PATH=os.pathsep.join(pfade), NPM_CONFIG_IGNORE_SCRIPTS='true', NPM_CONFIG_FUND='false', NPM_CONFIG_UPDATE_NOTIFIER='false',
               PIP_DISABLE_PIP_VERSION_CHECK='1', UV_PYTHON_DOWNLOADS='never', UV_NO_PROGRESS='1', NO_COLOR='1', CI='1', **(extra or {}))
    return env


@contextmanager
def kopie(p):
    """Committeter Stand des Basisbranchs in einem Temp-Verzeichnis (ohne ignorierte Dateien, also ohne `.env`); wird danach gelöscht."""
    wurzel = Path(tempfile.mkdtemp(prefix='devflow2-pflege-'))
    try:
        r = subprocess.run(['git', 'archive', '--format=tar', p.cfg['basis']], cwd=p.repo, capture_output=True)
        if r.returncode:
            raise Fehler(f'Kopie von {p.cfg["basis"]} nicht möglich: {r.stderr.decode(errors="replace").strip()[-200:]}')
        ziel = wurzel / 'projekt'
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
            tar.extractall(ziel, filter='data')
        yield ziel
    finally:
        shutil.rmtree(wurzel, ignore_errors=True)


class Pruefer:
    """Eine Prüfung in einer Kopie. Jede Einzelprüfung endet als `geprueft` oder `nicht geprueft` mit Grund, nie als Ausnahme."""

    def __init__(self, cfg, ordner, rahmen):
        self.cfg, self.ordner, self.rahmen = cfg, Path(ordner), rahmen
        self.env = _env()
        self.funde, self.veraltet, self.geprueft, self.nicht, self.hinweise = [], [], [], [], []
        self._py = None

    def _befehl(self, argv, cwd=None, ok=(0,)):
        rc, out, err = self.rahmen.run(argv, str(cwd or self.ordner), self.env)
        if rc not in ok:
            rest = ' '.join((err or out or '').split())[-200:]
            raise Fehler(f'{Path(argv[0]).name} endete mit Exit {rc}' + (f': {rest}' if rest else ''))
        return out

    def _werkzeug(self, name, hinweis):
        pfad = self.rahmen.finde(name)
        if not pfad:
            raise Fehler(f'Werkzeug {name} fehlt; `ops/pflege-werkzeuge.sh` richtet es ein' if hinweis else f'Werkzeug {name} fehlt')
        return pfad

    def _einzeln(self, name, fn):
        try:
            fn()
        except Fehler as err:
            self.nicht.append(dict(pruefung=name, grund=str(err)[:300]))
        else:
            self.geprueft.append(name)

    def laufen(self):
        for name in self.cfg['pruefungen']:
            fn = {'pip-audit': self._pip_audit, 'pip-outdated': self._pip_outdated, 'npm-audit': self._npm_audit,
                  'npm-outdated': self._npm_outdated, 'osv-scanner': self._osv}[name]
            self._einzeln(name, fn)
        return dict(funde=zusammenfuehren(self.funde), veraltet=sorted(self.veraltet, key=lambda v: (v['sprung'] != 'major', v['paket'])),
                    geprueft=self.geprueft, nicht_geprueft=self.nicht, hinweise=self.hinweise)

    # Python
    def _python(self):
        """Eigene Umgebung in der Kopie aus den Anforderungen; Ergebnis (oder Fehler) wird für beide Python-Prüfungen gemerkt."""
        if self._py is None:
            try:
                self._py = self._python_bauen()
            except Fehler as err:
                self._py = err
        if isinstance(self._py, Fehler):
            raise Fehler(f'Umgebung nicht aufbaubar: {self._py}')
        return self._py

    def _python_bauen(self):
        uv = self._werkzeug('uv', False)
        ablage = self.ordner / '.pflege'
        ablage.mkdir(exist_ok=True)
        quellen = []
        umg = self.cfg.get('umgebung')
        if umg:
            py = Path(umg).expanduser() / 'bin' / 'python'
            pins = self._befehl([str(py), '-I', '-c', FREEZE])
            schreiben(ablage / 'umgebung.txt', pins)
            quellen.append(ablage / 'umgebung.txt')
        for q in self.cfg['python_quellen']:
            if Path(q).name == PYPROJEKT:
                if (self.ordner / q).with_name('uv.lock').is_file():  # festgelegter Stand statt der Bereiche aus pyproject.toml
                    pins = self._befehl([uv, 'export', '--frozen', '--no-hashes', '--no-dev', '--no-emit-project', '--no-header', '--no-annotate'],
                                        cwd=(self.ordner / q).parent)
                else:
                    try:
                        deps = tomllib.loads((self.ordner / q).read_text(encoding='utf-8')).get('project', {}).get('dependencies', [])
                    except (OSError, tomllib.TOMLDecodeError) as err:
                        raise Fehler(f'{q} nicht lesbar: {err}') from None
                    pins = '\n'.join(deps) + '\n'
                schreiben(ablage / 'pyproject.txt', pins)
                quellen.append(ablage / 'pyproject.txt')
            elif (self.ordner / q).is_file():
                quellen.append(self.ordner / q)
            else:
                raise Fehler(f'{q} fehlt im Basisbranch')
        if not quellen:
            raise Fehler('keine Python-Anforderungen gefunden')
        venv = ablage / 'venv'
        py = Path(umg).expanduser() / 'bin' / 'python' if umg else Path(sys.executable)
        self._befehl([uv, 'venv', '-q', '--python', str(py), str(venv)])
        # Nur fertige Pakete (Wheels): Es läuft kein Build-Code aus dem Netz.
        self._befehl([uv, 'pip', 'install', '-q', '--python', str(venv / 'bin' / 'python'), '--only-binary', ':all:',
                      *(a for q in quellen for a in ('-r', str(q)))])
        return venv

    def _pip_audit(self):
        pfad = self._werkzeug('pip-audit', True)
        venv = self._python()
        site = sorted(venv.glob('lib/python*/site-packages')) or [venv / 'lib' / 'site-packages']
        out = self._befehl([pfad, '--path', str(site[0]), '-f', 'json', '--progress-spinner', 'off'], ok=(0, 1))
        funde, uebersprungen = pip_audit(out, self.rahmen.schwere)
        self.funde += funde
        if uebersprungen:
            self.hinweise.append(f'pip-audit hat übersprungen: {", ".join(sorted(set(uebersprungen)))}')

    def _pip_outdated(self):
        uv = self._werkzeug('uv', False)
        venv = self._python()
        out = self._befehl([uv, 'pip', 'list', '--outdated', '--python', str(venv / 'bin' / 'python'), '--format', 'json'])
        self.veraltet += veraltet_liste(out)

    # Node
    def _node(self, name):
        """Verzeichnisse mit Lockdatei zu den Verzeichnissen mit package.json; Workspace-Pakete nutzen die Lockdatei weiter oben. Ohne sie ist die Prüfung nicht möglich."""
        orte = self.cfg['node_verzeichnisse'] or ['.']
        gut = []
        for o in orte:
            if not (self.ordner / o / 'package.json').is_file():
                raise Fehler(f'{o}/package.json fehlt im Basisbranch')
            d = Path(o)
            while not (self.ordner / d / 'package-lock.json').is_file():
                if str(d) == '.':
                    raise Fehler(f'{o}: keine package-lock.json, {name} braucht sie')
                d = d.parent
            if str(d) not in gut:
                gut.append(str(d))
        return gut

    def _npm_audit(self):
        npm = self._werkzeug('npm', False)
        for o in self._node('npm audit'):
            sperre = (self.ordner / o / 'package-lock.json').read_text(encoding='utf-8', errors='replace')
            self.funde += npm_audit(self._befehl([npm, 'audit', '--json', '--package-lock-only'], cwd=self.ordner / o, ok=(0, 1)), o, lock_versionen(sperre))

    def _npm_outdated(self):
        npm = self._werkzeug('npm', False)
        for o in self._node('npm outdated'):
            cwd = self.ordner / o
            self._befehl([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=cwd)
            self.veraltet += npm_outdated(self._befehl([npm, 'outdated', '--json'], cwd=cwd, ok=(0, 1)), o)

    # sonst
    def _osv(self):
        osv = self._werkzeug('osv-scanner', True)
        lock = [x for x in self.cfg['lockdateien'] if (self.ordner / x).is_file()]
        if not lock:
            raise Fehler('keine Lockdateien im Basisbranch')
        out = self._befehl([osv, 'scan', 'source', '--format', 'json', *(a for x in lock for a in ('-L', x))], ok=(0, 1, 128))
        if not out.strip():
            return  # 128: keine Pakete gefunden
        for f in osv_scanner(out):
            f['orte'] = [o.removeprefix(str(self.ordner) + '/') for o in f['orte']]  # nicht der Temp-Pfad der Kopie
            self.funde.append(f)


def pruefen(p, rahmen=None):
    """Ganze Prüfung eines Projekts. Rückgabe: Ergebnis als Dict (mit Zeit, Commit und den Funden); Fehler nur bei unlesbarer Einstellung/Kopie."""
    rahmen = rahmen or Rahmen()
    cfg = konfig(p)
    if not cfg['aktiv']:
        raise Fehler(f'Pflege ist für {p.name} in devflow.toml ausgeschaltet')
    commit = subprocess.run(['git', 'rev-parse', '--short', p.cfg['basis']], cwd=p.repo, capture_output=True, text=True).stdout.strip()
    ergebnis = dict(projekt=p.name, zeit=time.time(), tag=datetime.date.today().isoformat(), commit=commit, pruefungen=cfg['pruefungen'])
    if not cfg['pruefungen']:
        return {**ergebnis, 'funde': [], 'veraltet': [], 'geprueft': [], 'nicht_geprueft': [], 'hinweise': ['Keine Paketdateien erkannt: nichts zu prüfen.']}
    with kopie(p) as ordner:
        return {**ergebnis, **Pruefer(cfg, ordner, rahmen).laufen()}


# --- Ergebnis: Stand, Bericht, Eingang ---------------------------------------------------------

def _stand_datei(name):
    return projekte.zustand() / 'pflege' / f'{projekte.slug(name)}.json'


def stand_speichern(ergebnis):
    schreiben(_stand_datei(ergebnis['projekt']), json.dumps(ergebnis, indent=1, ensure_ascii=False) + '\n')


def letzter(name):
    """Letztes Ergebnis des Projekts oder None."""
    try:
        d = json.loads(_stand_datei(name).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get('funde'), list) else None


def bericht_pfad(ergebnis):
    return f'berichte/pflege-{ergebnis["tag"]}.md'


def bericht_text(e):
    tag = datetime.date.fromisoformat(e['tag']).strftime('%d.%m.%Y')
    z = [f'# Pflege {e["projekt"]} · {tag}', '',
         f'Stand: Commit `{e["commit"]}`, geprüft in einer Kopie. Werkzeuge fragen öffentliche Datenbanken (OSV, PyPI, npm) nur lesend ab.', '']
    z += ['## Zusammenfassung', '']
    nach = {s: sum(f['schwere'] == s for f in e['funde']) for s in SCHWEREN}
    z.append(f'- Lücken: {len(e["funde"])}' + (' (' + ', '.join(f'{n} {s}' for s, n in nach.items() if n) + ')' if e['funde'] else ''))
    z.append(f'- Veraltete Pakete: {len(e["veraltet"])}' + (f', davon {sum(v["sprung"] == "major" for v in e["veraltet"])} mit Major-Sprung' if e['veraltet'] else ''))
    z.append(f'- Geprüft: {", ".join(e["geprueft"]) or "nichts"}')
    z.append(f'- Nicht geprüft: {len(e["nicht_geprueft"])}' if e['nicht_geprueft'] else '- Nicht geprüft: –')
    if e['nicht_geprueft']:
        z += ['', '## Nicht geprüft', '']
        z += [f'- **{n["pruefung"]}**: {n["grund"]}' for n in e['nicht_geprueft']]
    z += ['', '## Lücken', '']
    if e['funde']:
        z += ['| Paket | Version | Schwere | Kennungen | Behoben in | Quelle |', '|---|---|---|---|---|---|']
        z += [f'| {f["paket"]} | {f["version"] or "–"} | {f["schwere"]} | {", ".join(f["ids"]) or "–"} | {", ".join(f["behoben"]) or "–"} | {", ".join(f["quellen"])} |'
              for f in e['funde']]
    else:
        z.append('Keine bekannten Lücken gefunden.' if e['geprueft'] else 'Nichts geprüft.')
    z += ['', '## Veraltet', '']
    if e['veraltet']:
        z += ['| Paket | Aktuell | Neu | Sprung |', '|---|---|---|---|']
        z += [f'| {v["paket"]} | {v["aktuell"]} | {v["neu"]} | {v["sprung"]} |' for v in e['veraltet'][:100]]
        if len(e['veraltet']) > 100:
            z.append(f'\n… und {len(e["veraltet"]) - 100} weitere.')
    else:
        z.append('Nichts veraltet.' if e['geprueft'] else 'Nichts geprüft.')
    if e.get('hinweise'):
        z += ['', '## Hinweise', ''] + [f'- {h}' for h in e['hinweise']]
    return '\n'.join(z) + '\n'


def eingang_text(f, projekt):
    """Text des Eingangseintrags; Kennungen stehen in fester Form, daran erkennt `bekannt` Wiederholungen."""
    behoben = ', '.join(f['behoben']) or 'unbekannt'
    paket = ' '.join(x for x in (f['paket'], f['version']) if x)
    return (f'Pflege-Lücke ({f["paket"]}) {" ".join(f["ids"]) or "ohne-Kennung"}: {paket} in {projekt} · Schwere {f["schwere"]} · '
            f'behoben in {behoben} · gefunden von {", ".join(f["quellen"])}')


def bekannt(f, ideen):
    """Gibt es zu dem Fund schon einen Eingangseintrag der Pflege (egal in welchem Zustand, auch verworfen)?"""
    ids = {i.lower() for i in f['ids']}
    for i in ideen:
        m = EINGANG_TEXT.match(i['text']) if i['herkunft'] == 'pflege' else None
        if m and _name(m.group(1)) == _name(f['paket']) and (ids & {x.lower() for x in m.group(2).split()} or not ids):
            return True
    return False


def abschliessen(p, ergebnis):
    """Bericht und neue Eingangseinträge in einem Commit auf main. Rückgabe: Nummern der neuen Einträge."""
    pfad = bericht_pfad(ergebnis)
    nummern = []
    with repo.sperre(p):
        ideen = eingang.liste(p)
        neu, texte = [], set()
        for f in ergebnis['funde']:
            t = eingang_text(f, p.name)
            if not bekannt(f, ideen) and t not in texte:
                texte.add(t)
                neu.append(t)
        neu = neu[:MAX_NEU]
        start = max((i['nr'] for i in ideen), default=0) + 1

        def aendern(dateien):
            aus = {pfad: bericht_text(ergebnis)}
            if neu:
                aus[eingang.DATEI] = eingang.anhaengen(dateien[eingang.DATEI], start, neu, 'pflege')
                zeile = (f'{datum()} · Pflege: {len(neu)} neue Lücke{"n" if len(neu) != 1 else ""} im Eingang '
                         f'(Idee {start}{f"–{start + len(neu) - 1}" if len(neu) > 1 else ""}) · Herkunft pflege · Bericht {pfad}')
                aus['ROADMAP.md'] = roadmap.protokoll(dateien['ROADMAP.md'], zeile)
            else:
                aus[eingang.DATEI], aus['ROADMAP.md'] = dateien[eingang.DATEI], dateien['ROADMAP.md']
            return aus

        repo.auf_main_gesperrt(p, aendern, f'pflege: Bericht {ergebnis["tag"]}', pfade=(pfad, eingang.DATEI, 'ROADMAP.md'))
        nummern = list(range(start, start + len(neu)))
    return nummern


def ausfuehren(lauf, log):
    """Der Lauf der Art `pflege` (aufgerufen von `ablauf.intern`)."""
    p = Projekt(lauf['projekt'])

    def schritt(text):
        log.write(json.dumps({'type': 'schritt', 'text': text}, ensure_ascii=False) + '\n')
        log.flush()

    schritt(f'Pflegeprüfung {p.name}')
    e = pruefen(p, traum_rahmen(lauf) if lauf.get('bis') else None)
    if lauf.get('bis'):
        _frist(lauf)  # nach dem Fensterende schreibt die Prüfung nichts mehr
    e['lauf'] = lauf['id']
    e['traum'] = lauf.get('traum')
    stand_speichern(e)  # zuerst: Seite und Push sehen das Ergebnis auch, wenn der Commit scheitert
    schritt(f'{len(e["funde"])} Lücken, {len(e["veraltet"])} veraltet, nicht geprüft: {", ".join(n["pruefung"] for n in e["nicht_geprueft"]) or "–"}')
    nr = abschliessen(p, e)
    lauf['neue_eintraege'] = nr
    schritt(f'Bericht {bericht_pfad(e)}, {len(nr)} neue Einträge im Eingang')
    if lauf.get('traum'):
        return  # Pflegefunde aus dem Traum lösen keine Update-Kette aus
    from . import pflegeplan  # erst hier: pflegeplan baut auf diesem Modul auf
    pflegeplan.nach_pruefung(p, e)


# --- Start: Knopf und Wochentimer --------------------------------------------------------------

@contextmanager
def _sperre():
    pfad = projekte.zustand() / 'pflege' / 'start.lock'
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def pflege_laeufe(name):
    return [l for l in laeufe.alle(name) if l['art'] == 'pflege']


def starten(p, **extra):
    """Pflegelauf starten. Rückgabe: der Lauf, oder None, wenn für das Projekt schon einer läuft (Doppelklick, Timer, Knopf, Traum)."""
    cfg = konfig(p)
    if not cfg['aktiv']:
        raise Fehler(f'Pflege ist für {p.name} in devflow.toml ausgeschaltet')
    with _sperre():
        if any(l['zustand'] in laeufe.AKTIV for l in pflege_laeufe(p.name)):
            return None
        return laeufe.starten(p, 'pflege', 'intern', p.repo, auftrag='Pflegeprüfung ohne KI', **extra)


def faellig(name, jetzt=None):
    """Ist die nächste Prüfung des Projekts dran? Wöchentlich; nach einem Fehler schon nach einem Tag."""
    jetzt = jetzt if jetzt is not None else time.time()
    ls = [l for l in pflege_laeufe(name) if not l.get('traum')]  # ein Traum ersetzt die Wochenprüfung nicht: Nur sie stößt die Update-Kette an
    if not ls:
        return True
    letzter_lauf = ls[-1]
    if letzter_lauf['zustand'] in laeufe.AKTIV:
        return False
    return jetzt - letzter_lauf['angelegt'] >= (WIEDERHOLUNG if letzter_lauf['zustand'] == 'fehler' else INTERVALL)


def faellige_starten(jetzt=None):
    """Ein Takt des Timers: für jedes fällige Projekt einen Lauf. Nicht bei globalem Halt. Rückgabe: die gestarteten Läufe."""
    if laeufe.angehalten():
        return []
    gestartet = []
    for name in projekte.alle():
        try:
            p = Projekt(name)
            if konfig(p)['aktiv'] and faellig(name, jetzt):
                lauf = starten(p)
                if lauf:
                    gestartet.append(lauf)
        except Fehler as err:
            print(f'devflow2 pflege: {name}: {err}', file=sys.stderr)
    return gestartet


async def schleife():
    import asyncio
    from starlette.concurrency import run_in_threadpool
    takt = float(os.environ.get('DEVFLOW2_PFLEGE_TAKT') or 3600)
    await asyncio.sleep(float(os.environ.get('DEVFLOW2_PFLEGE_ANLAUF') or 120))  # nicht mitten im Start und Deploy
    while True:
        try:
            await run_in_threadpool(faellige_starten)
        except Exception as err:
            print(f'devflow2 pflege: Prüfung fehlgeschlagen: {type(err).__name__}: {err}', file=sys.stderr)
        await asyncio.sleep(takt)


def push_schluessel(name):
    """Push-Schlüssel der hohen und kritischen Lücken im letzten Ergebnis (`pflege:<projekt>:<fund>`)."""
    e = letzter(name)
    return {f'pflege:{name}:{kennung(f)}' for f in (e['funde'] if e else []) if f.get('schwere') in PUSH_AB}
