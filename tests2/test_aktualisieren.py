"""Installation und Update (S8.3): `devflow2 aktualisieren`, Formatversion des Zustands, `ops/installieren.sh`.

Die Release-Quelle ist immer ein lokales Repo in einem Temp-Verzeichnis, der Zustand liegt in `DEVFLOW2_STATE`, das
Installationsverzeichnis in `DEVFLOW2_HOME`; `uv`, `systemctl`, `claude` und `codex` sind Attrappen. Nichts geht zu GitHub.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import hilfen  # noqa: F401  (setzt DEVFLOW2_INSTANZ auf /dev/null)
from devflow2 import Fehler, aktualisieren, format as zustandsformat, laeufe, projekte

WURZEL = Path(__file__).resolve().parent.parent
PAKET = WURZEL / 'devflow2'
SKRIPT = WURZEL / 'ops' / 'installieren.sh'


def git(wo, *args):
    return subprocess.run(['git', '-C', str(wo), '-c', 'user.name=T', '-c', 'user.email=t@t.invalid', '-c', 'commit.gpgsign=false', *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def python_attrappe(venv):
    """`.venv/bin/python` als Skript, das den Python der Tests startet (statt uv und Netz)."""
    (venv / 'bin').mkdir(parents=True)
    py = venv / 'bin' / 'python'
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)


class Grund(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-akt-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state, self.home, self.repo = self.tmp / 'state', self.tmp / 'home', self.tmp / 'release'
        self.state.mkdir()
        (self.state / 'projekte.json').write_text('{}')  # ein Zustand, der schon benutzt wurde
        self.systemctl = self.tmp / 'systemctl'
        self.aufrufe = self.tmp / 'systemctl.log'
        self.systemctl.write_text(f'#!/bin/sh\necho "$@" >> "{self.aufrufe}"\nexit 0\n')
        self.systemctl.chmod(0o755)
        env = mock.patch.dict(os.environ, DEVFLOW2_STATE=str(self.state), DEVFLOW2_HOME=str(self.home), DEVFLOW2_SYSTEMCTL=str(self.systemctl),
                              DEVFLOW2_UPDATE_TAKT='0.05', DEVFLOW2_TAKT='0.05', XDG_RUNTIME_DIR=str(self.tmp))
        env.start()
        self.addCleanup(env.stop)
        venv = mock.patch.object(aktualisieren, 'venv_bauen', lambda wurzel: python_attrappe(wurzel / '.venv'))
        venv.start()
        self.addCleanup(venv.stop)
        self.meldungen = []
        git(self.tmp, 'init', '-q', str(self.repo))

    def release(self, tag, format_=1, migration=True, rot=False):
        """Ein Release mit dem echten Paket (Format und Tests nach Bedarf verändert) als Commit mit Tag im lokalen Quell-Repo."""
        for p in self.repo.iterdir():
            if p.name != '.git':
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        shutil.copytree(PAKET, self.repo / 'devflow2', ignore=shutil.ignore_patterns('__pycache__', 'static'))
        f = self.repo / 'devflow2' / 'format.py'
        text = f.read_text().replace('FORMAT = 1\n', f'FORMAT = {format_}\n')
        if migration:
            text = text.replace('MIGRATIONEN = {}', 'MIGRATIONEN = {n: (lambda z: None) for n in range(1, 20)}')
        f.write_text(text)
        (self.repo / 'tests2').mkdir()
        (self.repo / 'tests2' / 'test_x.py').write_text('import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n'
                                                       f'        self.assertTrue({not rot})\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '--allow-empty', '-m', tag)
        git(self.repo, 'tag', tag)

    def installieren(self, tag='v2.1', **kw):
        """Ausgangslage: Fassung `tag` ist installiert und aktiv."""
        self.release(tag, **kw)
        ziel = aktualisieren.holen(tag, str(self.repo), self.meldungen.append)
        aktualisieren._link(aktualisieren._aktiv(), ziel)

    def update(self, **kw):
        return aktualisieren.aktualisieren(quelle=str(self.repo), ausgabe=self.meldungen.append, **kw)

    def aktiv(self):
        return aktualisieren._aktiv().resolve().name

    def neustarts(self):
        return self.aufrufe.read_text().count('restart') if self.aufrufe.exists() else 0


class UpdateTest(Grund):
    def test_gruenes_update_schaltet_um_und_behaelt_die_alte_fassung(self):
        self.installieren('v2.1')
        self.release('v2.2')
        meldung = self.update()
        self.assertEqual(self.aktiv(), 'v2.2')
        self.assertEqual(aktualisieren._vorherige().resolve().name, 'v2.1')
        self.assertIn('v2.2 ist aktiv', meldung)
        self.assertEqual(self.neustarts(), 1)
        self.assertFalse(aktualisieren.sperre())
        self.assertFalse(aktualisieren.sperrdatei().exists())
        self.assertIn('v2.2', os.listdir(self.home / 'versionen'))
        self.assertEqual(self.update(), 'Schon aktuell: v2.2')  # nichts Neueres da
        self.assertEqual(self.neustarts(), 1)

    def test_rote_tests_nach_dem_holen_lassen_die_alte_fassung_aktiv(self):
        self.installieren('v2.1')
        self.release('v2.2', rot=True)
        with self.assertRaisesRegex(Fehler, 'Tests der neuen Fassung sind rot'):
            self.update()
        self.assertEqual(self.aktiv(), 'v2.1')
        self.assertFalse((self.home / 'versionen' / 'v2.2').exists())  # nichts Halbes bleibt liegen
        self.assertEqual(self.neustarts(), 0)
        self.assertFalse(aktualisieren.sperrdatei().exists())
        self.assertFalse(aktualisieren._vorherige().exists())

    def test_ohne_installation_oder_mit_falscher_version_passiert_nichts(self):
        with self.assertRaisesRegex(Fehler, 'nicht mit ops/installieren.sh eingerichtet'):
            self.update()
        self.installieren('v2.1')
        with self.assertRaisesRegex(Fehler, 'kein Release'):
            self.update(version='main')
        with self.assertRaisesRegex(Fehler, 'zurück|vorherige'):
            self.update(zurueck=True)

    def test_aktiver_lauf_verzoegert_das_umschalten_und_neue_laeufe_warten(self):
        self.installieren('v2.1')
        self.release('v2.2')
        projekte.zustand().mkdir(parents=True, exist_ok=True)
        lid = 'demo-test-20261104-100000'
        prozess = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', lid])
        self.addCleanup(prozess.wait)
        self.addCleanup(prozess.kill)
        laeufe.verzeichnis(lid).mkdir(parents=True)
        alt = dict(id=lid, projekt='demo', art='test', modell='shell', cwd='.', auftrag='', zustand='laeuft', angelegt=time.time(), pid=prozess.pid)
        laeufe.speichern(alt)
        ergebnis = []

        def lauf():
            try:
                ergebnis.append(self.update(warten=60))
            except Fehler as e:
                ergebnis.append(e)
        t = threading.Thread(target=lauf)
        with mock.patch.object(aktualisieren, 'neu_starten'):  # sonst würde der Test-Prozess durch exec ersetzt
            t.start()
            ende = time.time() + 60
            while not aktualisieren.sperre() and time.time() < ende:
                time.sleep(0.05)
            self.assertTrue(aktualisieren.sperre(), ergebnis)
            # Ein neuer Lauf, der jetzt Platz nehmen will, bleibt `wartet`
            neu = dict(id='demo-test-20261104-100001', projekt='demo', art='test', modell='shell', cwd='.', auftrag='', zustand='wartet',
                       angelegt=time.time())
            laeufe.verzeichnis(neu['id']).mkdir(parents=True)
            laeufe.speichern(neu)
            u = threading.Thread(target=laeufe._platz_nehmen, args=(neu,), daemon=True)
            u.start()
            time.sleep(0.6)
            self.assertEqual(laeufe.laden(neu['id'])['zustand'], 'wartet')
            self.assertEqual(neu['zustand'], 'wartet')
            self.assertEqual(self.aktiv(), 'v2.1')  # der Lauf ist noch aktiv: nichts umgeschaltet
            self.assertTrue(t.is_alive())
            self.assertIn('Warte auf 1 aktive', '\n'.join(self.meldungen))
            alt.update(zustand='fertig')  # der Lauf endet
            laeufe.speichern(alt)
            prozess.kill()
            t.join(30)
            self.assertFalse(t.is_alive())
            u.join(10)
            self.assertFalse(u.is_alive())
        self.assertIn('v2.2 ist aktiv', ergebnis[0])
        self.assertEqual(self.aktiv(), 'v2.2')
        self.assertEqual(neu['zustand'], 'laeuft')  # erst nach dem Umschalten darf der neue Lauf beginnen
        self.assertFalse(aktualisieren.sperre())

    def test_neue_laeufe_warten_schon_beim_holen_venv_und_tests(self):
        self.installieren('v2.1')
        self.release('v2.2')
        gesehen = []
        echt = aktualisieren.tests_laufen
        venv = aktualisieren.venv_bauen
        aktualisieren.venv_bauen = lambda w: (gesehen.append(('venv', aktualisieren.sperre())), venv(w))[1]
        self.addCleanup(setattr, aktualisieren, 'venv_bauen', venv)
        with mock.patch.object(aktualisieren, 'tests_laufen', lambda w: (gesehen.append(('tests', aktualisieren.sperre())), echt(w))[1]):
            self.update()
        self.assertEqual(gesehen, [('venv', True), ('tests', True)])
        self.assertFalse(aktualisieren.sperre())

    def test_rote_tests_heben_die_sperre_wieder_auf(self):
        self.installieren('v2.1')
        self.release('v2.2', rot=True)
        with self.assertRaises(Fehler):
            self.update()
        self.assertFalse(aktualisieren.sperrdatei().exists())

    def test_scheitert_der_dienstneustart_bleibt_die_alte_fassung_aktiv(self):
        self.installieren('v2.1')
        self.release('v2.2')
        self.systemctl.write_text(f'#!/bin/sh\necho "$@" >> "{self.aufrufe}"\n[ "$2" = restart ] && [ ! -e "{self.tmp}/ok" ] && exit 1\nexit 0\n')
        with self.assertRaisesRegex(Fehler, 'Dienst neu starten fehlgeschlagen'):
            self.update()
        self.assertEqual(self.aktiv(), 'v2.1')
        self.assertFalse(aktualisieren._vorherige().exists())
        self.assertFalse(aktualisieren.sperrdatei().exists())

    def test_gescheiterter_neustart_laesst_den_zuvor_laufenden_dienst_unter_der_alten_fassung_laufen(self):
        self.installieren('v2.1')
        self.release('v2.2')
        zustand = self.tmp / 'dienst-aktiv'
        zustand.write_text('')
        # zustandsabhängige Attrappe: der erste restart scheitert und lässt den Dienst inaktiv, jeder weitere startet ihn
        self.systemctl.write_text(f'#!/bin/sh\necho "$@" >> "{self.aufrufe}"\n'
                                  f'case "$2" in\nis-active) [ -e "{zustand}" ]; exit $?;;\n'
                                  f'restart) if [ -e "{self.tmp}/schon" ]; then : > "{zustand}"; exit 0; fi; : > "{self.tmp}/schon"; rm -f "{zustand}"; exit 1;;\nesac\nexit 0\n')
        with self.assertRaisesRegex(Fehler, 'Dienst neu starten fehlgeschlagen'):
            self.update()
        self.assertEqual(self.aktiv(), 'v2.1')
        self.assertTrue(zustand.exists())  # Dienst läuft wieder
        self.assertEqual(self.aufrufe.read_text().count('--user restart'), 2)

    def test_scheitert_der_dienstneustart_nach_formatumstellung_bleibt_die_neue_fassung_und_die_meldung_sagt_es(self):
        self.installieren('v2.1', format_=1)
        self.release('v2.2', format_=2)
        self.systemctl.write_text(f'#!/bin/sh\necho "$@" >> "{self.aufrufe}"\n[ "$2" = restart ] && exit 1\nexit 0\n')
        with self.assertRaisesRegex(Fehler, 'v2.2 bleibt aktiv'):
            self.update()
        self.assertEqual(self.aktiv(), 'v2.2')  # die alte Fassung würde den umgestellten Zustand beschädigen
        self.assertFalse(aktualisieren.sperrdatei().exists())

    def test_zeitablauf_beim_warten_laesst_alles_beim_alten(self):
        self.installieren('v2.1')
        self.release('v2.2')
        lid = 'demo-test-20261104-100000'
        prozess = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', lid])
        self.addCleanup(prozess.wait)
        self.addCleanup(prozess.kill)
        laeufe.verzeichnis(lid).mkdir(parents=True)
        laeufe.speichern(dict(id=lid, projekt='demo', art='test', modell='shell', cwd='.', auftrag='', zustand='kontingent',
                              angelegt=time.time(), pid=prozess.pid))
        with self.assertRaisesRegex(Fehler, 'laufen noch 1 Läufe'):
            self.update(warten=0.3)
        self.assertEqual(self.aktiv(), 'v2.1')
        self.assertFalse(aktualisieren.sperre())  # die Sperre ist wieder weg
        self.assertEqual(self.neustarts(), 0)

    def test_gestorbener_update_prozess_haelt_die_sperre_nicht_fest(self):
        aktualisieren.sperrdatei().write_text(json.dumps({'pid': 999999, 'pid_start': 1}))
        self.assertFalse(aktualisieren.sperre())

    def test_wartender_laufprozess_startet_mit_der_aktiven_fassung_neu(self):
        self.installieren('v2.1')
        self.release('v2.2')
        self.update()
        with mock.patch.object(aktualisieren.os, 'execve') as ex, mock.patch.object(aktualisieren.os, 'chdir'):
            aktualisieren.neu_starten('lid-1')
        argv, env = ex.call_args.args[1:]
        neu = (self.home / 'versionen' / 'v2.2').resolve()
        self.assertEqual(argv[1:], ['-B', '-m', 'devflow2', 'lauf', 'lid-1'])
        self.assertTrue(argv[0].startswith(str(neu)))
        self.assertTrue(env['PYTHONPATH'].startswith(str(neu)))

    def test_laufprozess_prueft_die_aktive_fassung_auch_ohne_die_sperre_gesehen_zu_haben(self):
        neu = dict(id='demo-test-20261104-100002', projekt='demo', art='test', modell='shell', cwd='.', auftrag='', zustand='wartet',
                   angelegt=time.time())
        laeufe.verzeichnis(neu['id']).mkdir(parents=True)
        laeufe.speichern(neu)
        with mock.patch.object(aktualisieren, 'neu_starten') as ns:
            laeufe._platz_nehmen(neu)
        ns.assert_called_once_with(neu['id'])
        self.assertEqual(neu['zustand'], 'laeuft')

    def test_ohne_installation_bleibt_ein_laufprozess_wie_er_ist(self):
        with mock.patch.object(aktualisieren.os, 'execve') as ex:
            aktualisieren.neu_starten('lid-1')
        ex.assert_not_called()


class FormatTest(Grund):
    def test_rueckweg_auf_eine_fassung_mit_anderem_format_wird_abgelehnt(self):
        self.installieren('v2.1', format_=1)
        self.release('v2.2', format_=2)
        self.update()
        self.assertEqual(zustandsformat.stand(), 2)
        sicherungen = list((self.state / 'sicherung').glob('format-1-*'))
        self.assertEqual(len(sicherungen), 1)
        self.assertTrue((sicherungen[0] / 'projekte.json').is_file())  # Sicherung vor der Umstellung
        with self.assertRaisesRegex(Fehler, 'kennt Format 1, der Zustand hat Format 2'):
            self.update(zurueck=True)
        self.assertEqual(self.aktiv(), 'v2.2')
        with self.assertRaisesRegex(Fehler, 'kennt Format 1, der Zustand hat Format 2'):
            self.update(version='v2.1')
        self.assertEqual(self.aktiv(), 'v2.2')
        self.assertEqual(zustandsformat.stand(), 2)

    def test_rueckweg_mit_gleichem_format_geht(self):
        self.installieren('v2.1')
        self.release('v2.2')
        self.update()
        self.assertIn('v2.1 ist aktiv', self.update(zurueck=True))
        self.assertEqual(self.aktiv(), 'v2.1')
        self.assertEqual(aktualisieren._vorherige().resolve().name, 'v2.2')

    def test_fehlende_umstellung_haelt_die_alte_fassung_aktiv(self):
        self.installieren('v2.1')
        self.release('v2.2', format_=2, migration=False)
        with self.assertRaisesRegex(Fehler, 'Keine Umstellung von Format 1 auf 2'):
            self.update()
        self.assertEqual(self.aktiv(), 'v2.1')
        self.assertIn(zustandsformat.stand(), (None, 1))
        self.assertEqual(self.neustarts(), 0)
        self.assertFalse(aktualisieren.sperrdatei().exists())

    def test_zustand_ohne_marke_ist_format_1_und_leerer_zustand_bekommt_das_aktuelle(self):
        self.assertEqual(zustandsformat.bereit(), '')
        self.assertEqual(zustandsformat.stand(), zustandsformat.FORMAT)
        leer = self.tmp / 'leer'
        with mock.patch.dict(os.environ, DEVFLOW2_STATE=str(leer)):
            zustandsformat.bereit()
            self.assertEqual(zustandsformat.stand(), zustandsformat.FORMAT)

    def test_neuere_marke_stoppt_diese_fassung(self):
        (self.state / 'format.json').write_text('{"format": 99}')
        for f in (zustandsformat.pruefen, zustandsformat.bereit):
            with self.assertRaisesRegex(Fehler, 'Format 99'):
                f()
        r = subprocess.run([sys.executable, '-B', '-m', 'devflow2', 'lage'], cwd=WURZEL, capture_output=True, text=True,
                           env={**os.environ, 'PYTHONPATH': str(WURZEL)})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Format 99', r.stderr)

    def test_umstellung_sichert_vorher_und_geht_nur_vorwaerts(self):
        (self.state / 'format.json').write_text('{"format": 1}')
        (self.state / 'laeufe').mkdir()
        (self.state / 'laeufe' / 'gross.log').write_text('x')
        gesehen = []
        with mock.patch.object(zustandsformat, 'FORMAT', 3), \
                mock.patch.dict(zustandsformat.MIGRATIONEN, {1: lambda z: gesehen.append((1, (z / 'projekte.json').read_text())),
                                                             2: lambda z: gesehen.append((2, ''))}):
            meldung = zustandsformat.bereit()
        self.assertEqual(zustandsformat.stand(), 3)
        self.assertEqual([n for n, _ in gesehen], [1, 2])
        self.assertIn('von Format 1 auf 3', meldung)
        sicherung = next((self.state / 'sicherung').glob('format-1-*'))
        self.assertTrue((sicherung / 'projekte.json').is_file())
        self.assertFalse((sicherung / 'laeufe').exists())  # Läufe werden nicht kopiert
        self.assertFalse((sicherung / 'sicherung').exists())

    def test_befehl_format_nennt_die_nummer(self):
        r = subprocess.run([sys.executable, '-B', '-m', 'devflow2', 'format'], cwd=WURZEL, capture_output=True, text=True,
                           env={**os.environ, 'PYTHONPATH': str(WURZEL)})
        self.assertEqual(r.stdout.strip(), str(zustandsformat.FORMAT))


class EinheitTest(Grund):
    def test_dienst_neu_starten_nutzt_den_namen_aus_der_umgebung(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('DEVFLOW2_EINHEIT', None)
            aktualisieren.dienst_neu_starten(lambda t: None)
        with mock.patch.dict(os.environ, DEVFLOW2_EINHEIT='devflow2-probe-x.service'):
            aktualisieren.dienst_neu_starten(lambda t: None)
        zeilen = self.aufrufe.read_text().splitlines()
        self.assertIn('--user restart devflow2-web.service', zeilen)
        self.assertIn('--user restart devflow2-probe-x.service', zeilen)
        self.assertNotIn('--user is-active --quiet devflow2-probe-x.service restart', zeilen)


class SkriptTest(unittest.TestCase):
    """`ops/installieren.sh` mit Attrappen für uv, systemctl, loginctl, claude, codex und node, eigenem HOME und freiem Port."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-inst-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        fremd = self.tmp / 'bin'
        fremd.mkdir()
        self.log = self.tmp / 'aufrufe.log'
        py = sys.executable

        def skript(name, text):
            p = fremd / name
            p.write_text('#!/bin/bash\n' + text)
            p.chmod(0o755)
            return p
        skript('uv', f'echo "uv $*" >> "{self.log}"\nif [ "$1" = run ]; then while [ "$1" != python ]; do shift; done; shift; exec "{py}" "$@"; fi\n'
                     f'if [ "$1" = venv ]; then for a; do d=$a; done; mkdir -p "$d/bin"; '
                     f'printf \'#!/bin/sh\\nexec "{py}" "$@"\\n\' > "$d/bin/python"; chmod +x "$d/bin/python"; fi\n')
        skript('node', 'echo 22\n')
        self.speicher = self.tmp / 'mcp-speicher'  # was `claude mcp add` eingetragen hat (Attrappe, nie das echte claude mcp)
        self.claude = skript('claude', f'echo "claude $*" >> "{self.log}"\nS="{self.speicher}"\n'
                                       'case "$1 $2" in\n'
                                       '  "auth status") exit 0;;\n'
                                       '  "mcp get") [ -f "$S" ] || exit 1; cat "$S";;\n'
                                       '  "mcp add") [ -z "${FAKE_MCP_ADD_FEHLER:-}" ] || exit 1; shift 2; : > "$S.neu"\n'
                                       '    while [ $# -gt 0 ]; do case $1 in --) echo "  Command: $2" >> "$S.neu"; break;; *=*) echo "    $1" >> "$S.neu";; esac; shift; done\n'
                                       '    mv "$S.neu" "$S";;\n'
                                       'esac\nexit 0\n')
        self.codex = skript('codex', 'exit 0\n')
        skript('systemctl', f'echo "systemctl $*" >> "{self.log}"\nz="{self.tmp}/aktiv"\n'
                            'case "$2" in start) touch "$z";; is-active) [ -e "$z" ] || exit 3;; esac\nexit 0\n')
        skript('loginctl', 'echo no\n')
        (self.tmp / 'os-release').write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
        self.checkout = self.tmp / 'checkout'
        shutil.copytree(PAKET, self.checkout / 'devflow2', ignore=shutil.ignore_patterns('__pycache__', 'static'))
        (self.checkout / 'requirements.txt').write_text('# leer\n')
        shutil.copytree(WURZEL / 'skills', self.checkout / 'skills')
        self.env = {**os.environ, 'HOME': str(self.tmp / 'h'), 'PATH': f'{fremd}:{os.environ["PATH"]}', 'DEVFLOW2_OS_RELEASE': str(self.tmp / 'os-release'),
                    'DEVFLOW2_CLAUDE': str(self.claude), 'DEVFLOW2_CODEX': str(self.codex), 'DEVFLOW2_WEB_PORT': str(self.port),
                    'XDG_RUNTIME_DIR': str(self.tmp)}
        for name in ('DEVFLOW2_HOME', 'DEVFLOW2_CONFIG', 'DEVFLOW2_INSTANZ', 'DEVFLOW2_STATE', 'DEVFLOW2_SYSTEMCTL', 'DEVFLOW2_UNIT_DIR', 'DEVFLOW2_EINHEIT',
                     'CLAUDE_CONFIG_DIR', 'FAKE_MCP_ADD_FEHLER'):
            self.env.pop(name, None)
        (self.tmp / 'h').mkdir()
        self.home = self.tmp / 'h' / '.local/share/devflow2'
        self.einheit = self.tmp / 'h' / '.config/systemd/user/devflow2-web.service'

    def lauf(self, *args, **env):
        return subprocess.run(['bash', str(SKRIPT), '--version', 'v2.1', *args], cwd=self.checkout, capture_output=True, text=True,
                              env={**self.env, **env})

    def test_transkription_nur_mit_option_in_eigener_umgebung(self):
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn('ops/installieren.sh --mit-transkription', r.stdout)  # sagt klar, was fehlt
        self.assertFalse((self.home / 'transkription').exists())
        self.assertNotIn('faster-whisper', self.log.read_text())
        r = self.lauf('--mit-transkription')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        tvenv = self.home / 'transkription' / '.venv'
        self.assertTrue((tvenv / 'bin' / 'python').is_file())
        log = self.log.read_text()
        self.assertIn(f'uv venv --clear --python 3.11 {tvenv}', log)
        self.assertIn(f'uv pip install --python {tvenv}/bin/python faster-whisper', log)
        self.assertIn('faster-whisper eingerichtet', r.stdout)

    def test_transkription_scheitert_laut_ohne_den_rest_zu_beschaedigen(self):
        (self.tmp / 'bin' / 'uv').write_text((self.tmp / 'bin' / 'uv').read_text().replace('if [ "$1" = venv ]', 'if [[ " $* " == *" faster-whisper "* ]]; then exit 1; fi\nif [ "$1" = venv ]'))
        r = self.lauf('--mit-transkription')
        self.assertEqual(r.returncode, 1)
        self.assertIn('faster-whisper ließ sich nicht installieren', r.stderr)
        self.assertFalse((self.home / 'transkription' / '.venv').exists())
        self.assertTrue((self.home / 'versionen' / 'v2.1' / '.fertig').is_file())  # alles andere ist fertig
        self.assertTrue(self.einheit.is_file())

    def test_installation_und_wiederholung_aendern_beim_zweiten_mal_nichts(self):
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual((self.home / 'aktiv').resolve(), (self.home / 'versionen' / 'v2.1').resolve())
        self.assertTrue((self.home / 'versionen' / 'v2.1' / '.fertig').is_file())
        self.assertFalse((self.home / 'versionen' / 'v2.1' / '.git').exists())
        self.assertEqual((self.tmp / 'h' / '.config/devflow2/instanz.toml').read_text(), 'profil = "arbeit"\n')
        einheit = self.einheit.read_text()
        self.assertIn(f'ExecStart={self.home}/aktiv/.venv/bin/python -B -m devflow2 web', einheit)  # aus dem aktiven Verzeichnis, nicht aus einem Checkout
        self.assertIn(f'nur 127.0.0.1:{self.port}', einheit)
        self.assertIn('sudo loginctl enable-linger', r.stdout)  # der eine root-Schritt
        self.assertIn(f'ssh -N -L {self.port}:127.0.0.1:{self.port}', r.stdout)
        log = self.log.read_text()
        self.assertIn('systemctl --user enable devflow2-web.service', log)
        self.assertIn('systemctl --user start devflow2-web.service', log)
        # zweiter Aufruf: gleiches Ergebnis, nichts neu gebaut, Einheit nicht neu geladen, Dienst nicht neu gestartet
        vorher = (self.einheit.read_bytes(), self.log.read_text().count('uv '), log.count('daemon-reload'), log.count('start'))
        toml = (self.tmp / 'h' / '.config/devflow2/instanz.toml')
        toml.write_text('profil = "arbeit"\n[web]\nport = %d\n' % self.port)  # eigene Einstellungen bleiben
        r2 = self.lauf()
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        nachher = self.log.read_text()
        self.assertEqual(vorher, (self.einheit.read_bytes(), nachher.count('uv '), nachher.count('daemon-reload'), nachher.count('start')))
        self.assertIn('liegt schon fertig', r2.stdout)
        self.assertIn('läuft schon', r2.stdout)
        self.assertIn('[web]', toml.read_text())
        self.assertEqual((self.home / 'aktiv').resolve(), (self.home / 'versionen' / 'v2.1').resolve())

    def mcp_aufrufe(self):
        return [z for z in self.log.read_text().splitlines() if z.startswith('claude mcp')]

    def test_claude_mcp_und_skills_werden_auf_aktiv_eingerichtet_und_nicht_doppelt(self):
        """S8.7: Registrierung und Links zeigen auf `aktiv` (überleben `aktualisieren`); der zweite Aufruf ändert nichts."""
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        aktiv = self.home / 'aktiv'
        self.assertEqual([z.split(' devflow')[0] for z in self.mcp_aufrufe()], ['claude mcp get', 'claude mcp add'])
        add = self.mcp_aufrufe()[1]
        self.assertIn('-s user', add)
        self.assertIn(f'-e PYTHONPATH={aktiv}', add)
        self.assertIn(f'-- {aktiv}/.venv/bin/python -B -m devflow2 mcp', add)
        self.assertNotIn('DEVFLOW2_HOME', add)  # nicht gesetzt, also kein Eintrag
        gespeichert = self.speicher.read_text()
        self.assertIn(f'Command: {aktiv}/.venv/bin/python', gespeichert)
        self.assertIn('MCP-Server \'devflow\' (Scope user)', r.stdout)
        skills = self.tmp / 'h' / '.claude' / 'skills'
        for name in ('planungssitzung', 'neues-projekt', 'discovery'):
            link = skills / name
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), f'{aktiv}/skills/{name}')  # über aktiv, nicht über versionen/v2.1
            self.assertTrue((link / 'SKILL.md').is_file())
        # nach dem Umhängen von aktiv (Update) zeigen beide weiter in die aktive Fassung
        neu = self.home / 'versionen' / 'v2.2'
        shutil.copytree(self.home / 'versionen' / 'v2.1', neu, symlinks=True)
        (neu / 'skills' / 'planungssitzung' / 'SKILL.md').write_text('neu\n')
        os.replace(self._link(neu), aktiv)
        self.assertEqual((skills / 'planungssitzung' / 'SKILL.md').read_text(), 'neu\n')
        # zweiter Aufruf: kein weiteres `mcp add`, nichts verändert
        vorher = (gespeichert, [os.readlink(skills / n) for n in ('planungssitzung', 'neues-projekt', 'discovery')])
        r2 = self.lauf()
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        self.assertEqual(sum(1 for z in self.mcp_aufrufe() if z.startswith('claude mcp add')), 1)
        self.assertEqual(vorher, (self.speicher.read_text(), [os.readlink(skills / n) for n in ('planungssitzung', 'neues-projekt', 'discovery')]))
        self.assertIn('zeigt schon auf', r2.stdout)
        self.assertIn('Skill planungssitzung ist schon verlinkt', r2.stdout)

    def _link(self, ziel):
        tmp = ziel.parent / 'aktiv-neu'
        tmp.symlink_to(ziel)
        return tmp

    def test_eigene_verzeichnisse_gehen_in_die_registrierung(self):
        r = self.lauf(DEVFLOW2_HOME=str(self.tmp / 'code'), DEVFLOW2_CONFIG=str(self.tmp / 'konfig'))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        add = self.mcp_aufrufe()[1]
        self.assertIn(f'-e PYTHONPATH={self.tmp}/code/aktiv', add)
        self.assertIn(f'-e DEVFLOW2_HOME={self.tmp}/code', add)
        self.assertIn(f'-e DEVFLOW2_CONFIG={self.tmp}/konfig', add)

    def test_anders_eingerichteter_mcp_server_wird_gemeldet_und_nicht_ueberschrieben(self):
        """Wie auf server: `devflow` zeigt mit PYTHONPATH auf einen Checkout."""
        alt = '  Command: /x/venv/bin/python\n    PYTHONPATH=/x/dev/devflow\n'
        self.speicher.write_text(alt)
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.speicher.read_text(), alt)
        self.assertEqual([z.split(' devflow')[0] for z in self.mcp_aufrufe()], ['claude mcp get'])
        self.assertIn('anders eingerichtet und bleibt unverändert', r.stdout)
        self.assertTrue((self.tmp / 'h' / '.claude' / 'skills' / 'planungssitzung').is_symlink())  # Skills trotzdem

    def test_fremde_skills_bleiben_unberuehrt(self):
        skills = self.tmp / 'h' / '.claude' / 'skills'
        (skills / 'planungssitzung').mkdir(parents=True)
        (skills / 'planungssitzung' / 'SKILL.md').write_text('eigene Fassung\n')
        (self.tmp / 'ausserhalb').mkdir()
        (skills / 'neues-projekt').symlink_to(self.tmp / 'ausserhalb')  # Link auf etwas anderes
        r = self.lauf()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual((skills / 'planungssitzung' / 'SKILL.md').read_text(), 'eigene Fassung\n')
        self.assertFalse((skills / 'planungssitzung').is_symlink())
        self.assertEqual(os.readlink(skills / 'neues-projekt'), str(self.tmp / 'ausserhalb'))
        self.assertEqual(r.stdout.count('anderer Ursprung'), 2)

    def test_ohne_claude_laesst_claude_unberuehrt(self):
        r = self.lauf('--ohne-claude')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.mcp_aufrufe(), [])
        self.assertFalse((self.tmp / 'h' / '.claude').exists())

    def test_aktiv_ohne_venv_laesst_mcp_server_und_skills_unberuehrt(self):
        """`aktiv` zeigt auf eine andere, unbrauchbare Fassung (kein venv): der Installer fasst Claude nicht an."""
        (self.home / 'versionen' / 'v2.0').mkdir(parents=True)
        (self.home / 'aktiv').symlink_to(self.home / 'versionen' / 'v2.0')
        r = self.lauf('--ohne-dienst')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn('kein \'aktiv\' mit venv', r.stdout)
        self.assertEqual(self.mcp_aufrufe(), [])
        self.assertFalse((self.tmp / 'h' / '.claude').exists())

    def test_scheitert_mcp_add_bleibt_die_installation_gelungen_und_nennt_den_befehl(self):
        r = self.lauf(FAKE_MCP_ADD_FEHLER='1')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn('ließ sich nicht einrichten', r.stderr)
        self.assertIn('claude mcp add devflow -s user', r.stderr)
        self.assertFalse(self.speicher.exists())
        self.assertTrue((self.tmp / 'h' / '.claude' / 'skills' / 'neues-projekt').is_symlink())

    def test_eigener_einheitsname_und_home_fuer_proben_neben_der_echten_instanz(self):
        """S8.5: ohne DEVFLOW2_EINHEIT bleibt es bei devflow2-web.service; mit Namen entsteht nur diese Einheit, und sie trägt das HOME."""
        r = self.lauf(DEVFLOW2_EINHEIT='devflow2-probe-x.service')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        probe = self.einheit.with_name('devflow2-probe-x.service')
        self.assertTrue(probe.is_file())
        self.assertFalse(self.einheit.exists())  # die Einheit der echten Instanz wird nie angelegt oder überschrieben
        text = probe.read_text()
        self.assertIn(f'Environment=HOME={self.tmp / "h"}', text)  # sonst liefe der Dienst auf dem Zustand des echten Benutzers
        self.assertIn('Environment=DEVFLOW2_EINHEIT=devflow2-probe-x.service', text)
        log = self.log.read_text()
        self.assertIn('systemctl --user enable devflow2-probe-x.service', log)
        self.assertNotIn('devflow2-web.service', log)

    def test_belegter_standardport_der_echten_instanz_stoppt_die_installation_ohne_aenderung(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            s.listen()
            r = self.lauf(DEVFLOW2_WEB_PORT=str(s.getsockname()[1]))
            self.assertEqual(r.returncode, 1)
            self.assertIn('ist belegt', r.stderr)
            self.assertFalse(self.home.exists())
            self.assertFalse(self.einheit.exists())

    def test_fehlende_voraussetzungen_werden_alle_gemeldet_und_nichts_wird_geaendert(self):
        (self.tmp / 'os-release').write_text('ID=debian\n')
        self.claude.write_text('#!/bin/bash\nexit 1\n')  # nicht angemeldet
        r = self.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn('Kein Ubuntu', r.stderr)
        self.assertIn('claude ist nicht angemeldet', r.stderr)
        self.assertNotIn('codex', r.stderr)
        self.assertFalse(self.home.exists())
        self.assertFalse(self.einheit.exists())
        aufrufe = self.log.read_text() if self.log.exists() else ''
        self.assertNotIn('venv', aufrufe)
        self.assertNotIn('systemctl', aufrufe.replace('systemctl --user is-active', ''))

    def test_ohne_version_und_ohne_tag_bricht_ab(self):
        r = subprocess.run(['bash', str(SKRIPT)], cwd=self.checkout, capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn('Fassung unbekannt', r.stderr)
        self.assertFalse(self.home.exists())

    def test_belegter_port_stoppt_vor_dem_dienst(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', self.port))
            s.listen()
            r = self.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn(f'Port {self.port}', r.stderr)
        self.assertFalse(self.einheit.exists())
        self.assertIn('nichts wurde geändert', r.stderr)
        self.assertFalse(self.home.exists())  # nicht einmal ein Versionsverzeichnis
        self.assertFalse((self.tmp / 'h' / '.config/devflow2').exists())
        self.assertNotIn('start', self.log.read_text())

    def test_abgemeldetes_codex_wird_gemeldet(self):
        self.codex.write_text('#!/bin/bash\nexit 1\n')
        r = self.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn('codex ist nicht angemeldet', r.stderr)


if __name__ == '__main__':
    unittest.main()
