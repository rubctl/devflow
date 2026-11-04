"""Betrieb (S2.2): Einrichtungsskript wiederholbar, Deploy startet neu und prüft /health. Nur Attrappen, nichts Echtes."""
import os
os.environ.setdefault('DEVFLOW2_INSTANZ', os.devnull)  # nie die echte instanz.toml lesen
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

WURZEL = Path(__file__).resolve().parent.parent
OPS = WURZEL / 'ops'

SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$FAKE/aufrufe"
case "$*" in
  *is-active*) [ -e "$FAKE/laeuft" ] ;;
  *" start "*|*" restart "*) [ -e "$FAKE/startet_nicht" ] && exit 1; touch "$FAKE/laeuft" ;;
esac
"""
LOGINCTL = """#!/usr/bin/env bash
echo "loginctl $*" >> "$FAKE/aufrufe"
case "$1" in show-user) [ -e "$FAKE/linger" ] && echo yes || echo no ;; enable-linger) touch "$FAKE/linger" ;; esac
"""
TAILSCALE = """#!/usr/bin/env bash
echo "tailscale $*" >> "$FAKE/aufrufe"
if [ "$2" = status ]; then cat "$FAKE/serve"; exit 0; fi
for a in "$@"; do case "$a" in --https=*) port=${a#--https=} ;; http*) ziel=$a ;; esac; done
awk -v k="https://server.example.net:$port " 'index($0,k)==1{skip=3} skip>0{skip--; next} 1' "$FAKE/serve" > "$FAKE/neu"   # ersetzt nur den eigenen Port
printf 'https://server.example.net:%s (tailnet only)\\n|-- / proxy %s\\n\\n' "$port" "$ziel" >> "$FAKE/neu"
mv "$FAKE/neu" "$FAKE/serve"
"""
SERVE_VORHER = 'https://server.example.net (tailnet only)\n|-- / proxy http://127.0.0.1:8765\n\n'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == '/health' and self.headers.get('Tailscale-User-Login') == 'inhaber@github'
        self.send_response(200 if ok else 403)
        self.end_headers()
        self.wfile.write(b'{"ok":true}' if ok else b'')

    def log_message(self, *args):
        pass


class BetriebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = Path(self.tmp.name) / 'fake'
        self.fake.mkdir()
        for name, text in (('systemctl', SYSTEMCTL), ('loginctl', LOGINCTL), ('tailscale', TAILSCALE)):
            (self.fake / name).write_text(text)
            (self.fake / name).chmod(0o755)
        (self.fake / 'serve').write_text(SERVE_VORHER)
        self.einheiten = Path(self.tmp.name) / 'einheiten'
        self.env = dict(os.environ, FAKE=str(self.fake), DEVFLOW2_SYSTEMCTL=str(self.fake / 'systemctl'),
                        DEVFLOW2_LOGINCTL=str(self.fake / 'loginctl'), DEVFLOW2_TAILSCALE=str(self.fake / 'tailscale'),
                        DEVFLOW2_UNIT_DIR=str(self.einheiten), XDG_RUNTIME_DIR=self.tmp.name,
                        DEVFLOW2_WEB_WARTEN='2')
        self.env.pop('DEVFLOW2_WEB_LOGIN', None)

    def skript(self, name, **env):
        return subprocess.run([str(OPS / name)], env=dict(self.env, **env), capture_output=True, text=True, timeout=60)

    def aufrufe(self):
        p = self.fake / 'aufrufe'
        return p.read_text().splitlines() if p.exists() else []

    def health_server(self):
        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    # --- Einrichtung ---

    def test_einrichten_legt_alles_an(self):
        r = self.skript('web_einrichten.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.fake / 'linger').exists())
        self.assertEqual((self.einheiten / 'devflow2-web.service').read_text(), (OPS / 'devflow2-web.service').read_text())
        a = self.aufrufe()
        self.assertIn('systemctl --user enable devflow2-web.service', a)
        self.assertIn('systemctl --user start devflow2-web.service', a)
        self.assertIn('tailscale serve --bg --https=8472 http://127.0.0.1:8478', a)
        serve = (self.fake / 'serve').read_text()
        self.assertIn('proxy http://127.0.0.1:8765', serve, 'fremde Freigabe muss bleiben')
        self.assertIn(':8472 ', serve)

    def test_einrichten_wiederholt_doppelt_nichts(self):
        self.assertEqual(self.skript('web_einrichten.sh').returncode, 0)
        serve, einheit = (self.fake / 'serve').read_text(), (self.einheiten / 'devflow2-web.service').read_text()
        (self.fake / 'aufrufe').unlink()
        r = self.skript('web_einrichten.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.aufrufe()
        self.assertEqual((self.fake / 'serve').read_text(), serve)
        self.assertEqual((self.einheiten / 'devflow2-web.service').read_text(), einheit)
        self.assertEqual(serve.count(':8472 '), 1)
        for nicht in ('enable-linger', 'daemon-reload', ' start ', ' restart ', 'serve --bg'):
            self.assertFalse([x for x in a if nicht in x], f'{nicht!r} bei Wiederholung: {a}')
        self.assertIn('systemctl --user enable devflow2-web.service', a)  # enable ist selbst wiederholbar

    def test_einrichten_aendert_abweichende_einheit_und_freigabe(self):
        self.assertEqual(self.skript('web_einrichten.sh').returncode, 0)
        (self.einheiten / 'devflow2-web.service').write_text('veraltet\n')
        (self.fake / 'serve').write_text(SERVE_VORHER + 'https://server.example.net:8472 (tailnet only)\n|-- / proxy http://127.0.0.1:1\n\n')
        r = self.skript('web_einrichten.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.einheiten / 'devflow2-web.service').read_text(), (OPS / 'devflow2-web.service').read_text())
        self.assertIn('daemon-reload', '\n'.join(self.aufrufe()))
        serve = (self.fake / 'serve').read_text()
        self.assertEqual(serve.count(':8472 '), 1)
        self.assertIn('proxy http://127.0.0.1:8478', serve)
        self.assertNotIn('127.0.0.1:1\n', serve)

    def test_einheit_laeuft_als_benutzerdienst_mit_neustart(self):
        text = (OPS / 'devflow2-web.service').read_text()
        self.assertIn('WantedBy=default.target', text)
        self.assertRegex(text, r'(?m)^Restart=always$')
        self.assertIn('-m devflow2 web', text)
        self.assertNotRegex(text, r'0\.0\.0\.0|--host')
        self.assertNotRegex(text, r'(?i)(secret|token|password)')

    # --- Deploy ---

    def test_deploy_startet_neu_und_prueft_health(self):
        port = self.health_server()
        r = self.skript('web_deploy.sh', DEVFLOW2_WEB_PORT=str(port))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('systemctl --user restart devflow2-web.service', self.aufrufe())
        self.assertIn('/health antwortet', r.stdout)

    def test_deploy_scheitert_ohne_health(self):
        port = self.health_server()
        r = self.skript('web_deploy.sh', DEVFLOW2_WEB_PORT=str(port + 1 if port < 65535 else port - 1))
        self.assertEqual(r.returncode, 1)
        self.assertIn('/health antwortet nicht', r.stderr)

    def test_deploy_frist_gilt_auch_bei_stummem_endpunkt(self):
        # Verbindung wird angenommen (Backlog), aber nie beantwortet: Frist 2 s darf nicht auf ein Vielfaches wachsen
        stumm = socket.socket()
        self.addCleanup(stumm.close)
        stumm.bind(('127.0.0.1', 0))
        stumm.listen(8)
        beginn = time.monotonic()
        r = self.skript('web_deploy.sh', DEVFLOW2_WEB_PORT=str(stumm.getsockname()[1]))
        dauer = time.monotonic() - beginn
        self.assertEqual(r.returncode, 1)
        self.assertLess(dauer, 4.5, dauer)

    def test_deploy_scheitert_bei_falschem_login(self):
        # Dienst erwartet inhaber@github; das Skript schickt den eingestellten Login mit
        port = self.health_server()
        r = self.skript('web_deploy.sh', DEVFLOW2_WEB_PORT=str(port), DEVFLOW2_WEB_LOGIN='fremd@github')
        self.assertEqual(r.returncode, 1)

    def test_deploy_scheitert_wenn_neustart_scheitert(self):
        (self.fake / 'startet_nicht').touch()
        port = self.health_server()
        r = self.skript('web_deploy.sh', DEVFLOW2_WEB_PORT=str(port))
        self.assertEqual(r.returncode, 1)
        self.assertIn('Neustart von devflow2-web fehlgeschlagen', r.stderr)

    def test_deploy_in_devflow_toml(self):
        text = (WURZEL / 'devflow.toml').read_text()
        befehl = re.search(r'(?m)^deploy = "([^"]+)"', text)
        self.assertTrue(befehl, 'deploy fehlt in devflow.toml')
        self.assertTrue(os.access(WURZEL / befehl.group(1), os.X_OK), befehl.group(1))

    def test_skripte_ausfuehrbar_und_syntaktisch_ok(self):
        for name in ('web_einrichten.sh', 'web_deploy.sh'):
            self.assertTrue(os.access(OPS / name, os.X_OK), name)
            self.assertEqual(subprocess.run(['bash', '-n', str(OPS / name)]).returncode, 0, name)


if __name__ == '__main__':
    unittest.main()
