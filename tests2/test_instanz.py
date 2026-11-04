"""Instanz-Einstellungen (S8.1): Standardwerte der server-Instanz, Arbeitsprofil, Prüfung der Datei, keine festen Werte im Paket."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from starlette.testclient import TestClient

import os
os.environ.setdefault('DEVFLOW2_INSTANZ', os.devnull)
from devflow2 import Fehler, auftraege, instanz, laeufe, projekte, push, web
from test_betrieb import LOGINCTL, SYSTEMCTL, TAILSCALE

WURZEL = Path(__file__).resolve().parent.parent
PAKET = WURZEL / 'devflow2'
ARBEIT = 'profil = "arbeit"\n'
LOKAL = 'http://127.0.0.1:8478'


class Basis(unittest.TestCase):
    ENV = ('DEVFLOW2_INSTANZ', 'DEVFLOW2_WEB_PORT', 'DEVFLOW2_WEB_LOGIN', 'DEVFLOW2_WEB_ORIGIN', 'DEVFLOW2_TESTLAUF', 'DEVFLOW2_CONFIG')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ)  # echte Umgebung und echte instanz.toml bleiben draußen
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in self.ENV:
            os.environ.pop(name, None)
        os.environ['DEVFLOW2_INSTANZ'] = os.devnull
        os.environ['DEVFLOW2_STATE'] = str(self.dir / 'zustand')  # nie der echte Laufzustand (AGENTS.md)

    def toml(self, text):
        pfad = self.dir / 'instanz.toml'
        pfad.write_text(text, encoding='utf-8')
        os.environ['DEVFLOW2_INSTANZ'] = str(pfad)
        return pfad


class StandardTest(Basis):
    def test_ohne_datei_gilt_server(self):
        cfg = instanz.laden()
        self.assertEqual((cfg['host'], cfg['web']['adresse'], cfg['web']['port'], cfg['web']['login']), ('server', '127.0.0.1', 8478, 'tailscale'))
        self.assertEqual(cfg['web']['login_name'], 'inhaber@github')
        self.assertEqual(instanz.kontakt(), 'https://server.example.net:8472')
        self.assertEqual(instanz.testlaeufer_anzeige(), '~/dev/werkzeuge/ops/testlauf.sh')
        self.assertEqual(instanz.testlaeufer(), str(Path.home() / 'dev/werkzeuge/ops/testlauf.sh'))
        self.assertEqual(instanz.schluessel_verzeichnis(), Path.home() / '.config/devflow2')

    def test_fehlende_standarddatei_ist_kein_fehler_fehlende_benannte_schon(self):
        os.environ.pop('DEVFLOW2_INSTANZ')
        os.environ['DEVFLOW2_CONFIG'] = str(self.dir)
        self.assertEqual(instanz.laden()['host'], 'server')
        self.assertEqual(instanz.datei(), self.dir / 'instanz.toml')
        os.environ['DEVFLOW2_INSTANZ'] = str(self.dir / 'gibt-es-nicht.toml')
        with self.assertRaisesRegex(Fehler, 'gibt es nicht'):
            instanz.laden()

    def test_auftraege_nennen_host_und_testlaeufer(self):
        self.assertIn('Größere Läufe auf server über `~/dev/werkzeuge/ops/testlauf.sh <befehl>`', auftraege._tests({'test': 'make test'}))
        self.assertIn('alles außerhalb von server,', auftraege.nicht_ohne_inhaber())
        self.toml(ARBEIT)
        text = auftraege._tests({'test': 'make test'})
        self.assertIn('Größere Läufe auf arbeit über', text)
        self.assertIn(str(WURZEL / 'ops/testlauf.sh'), text)
        self.assertIn('alles außerhalb von arbeit,', auftraege.nicht_ohne_inhaber())

    def test_testlaeufer_abschaltbar(self):
        self.toml('testlaeufer = ""\n')
        self.assertEqual(instanz.testlaeufer(), '')
        self.assertEqual(auftraege._tests({'test': 'make test'}), 'Projekttests: `make test`.')
        os.environ['DEVFLOW2_TESTLAUF'] = '/x/lauf.sh'  # Umgebung geht vor
        self.assertEqual(instanz.testlaeufer(), '/x/lauf.sh')

    def test_projektpfad_ohne_wurzel_gilt_ab_arbeitsverzeichnis(self):
        self.assertEqual(instanz.laden()['projektwurzel'], '')
        self.assertEqual(instanz.projektpfad('demo'), Path('demo'))
        self.assertEqual(Path('demo').resolve(), Path.cwd() / 'demo')

    def test_zustand_ist_isoliert(self):
        self.assertEqual(os.environ['DEVFLOW2_STATE'], str(self.dir / 'zustand'))

    def test_projektpfad_relativ_ab_projektwurzel(self):
        self.toml(f'projektwurzel = "{self.dir}"\n')
        self.assertEqual(instanz.projektpfad('demo'), self.dir / 'demo')
        self.assertEqual(instanz.projektpfad('/tmp/x'), Path('/tmp/x'))
        self.assertEqual(instanz.projektpfad('~/y'), Path.home() / 'y')

    def test_push_schluessel_und_kontakt_aus_datei(self):
        self.toml(f'[push]\nschluessel = "{self.dir}/k"\nkontakt = "mailto:a@b.example"\n')
        self.assertEqual(push.konfig(), self.dir / 'k')
        self.assertEqual(push.kontakt(), 'mailto:a@b.example')
        os.environ['DEVFLOW2_WEB_ORIGIN'] = 'http://x.example, https://dash.example/'  # erste https-Origin geht vor
        self.assertEqual(push.kontakt(), 'https://dash.example')
        os.environ['DEVFLOW2_CONFIG'] = str(self.dir / 'env')
        self.assertEqual(push.konfig(), self.dir / 'env')


class PruefungTest(Basis):
    def fehler(self, text, muster):
        self.toml(text)
        with self.assertRaisesRegex(Fehler, muster):
            instanz.laden()

    def test_nur_loopback(self):
        for adresse in ('0.0.0.0', '::', '192.168.1.5', 'example.org', '', '127.0.0.1.evil'):
            self.fehler(f'[web]\nadresse = "{adresse}"\n', 'adresse')
        for adresse in ('127.0.0.1', '127.0.0.2', '::1', 'localhost'):
            self.toml(f'[web]\nadresse = "{adresse}"\n')
            self.assertEqual(instanz.web()['adresse'], adresse)

    def test_unsinn_wird_abgelehnt(self):
        self.fehler('profil = "mars"\n', 'profil')
        self.fehler('host = "a"\nunsinn = 1\n', 'unbekannte Einstellung unsinn')
        self.fehler('[web]\nlogn = "keine"\n', 'unbekannte Einstellung web.logn')
        self.fehler('[web]\nlogin = "passwort"\n', 'web.login')
        self.fehler('[web]\nport = 0\n', 'web.port')
        self.fehler('[web]\nport = "80"\n', 'web.port')
        self.fehler('[web]\nport = true\n', 'web.port')
        self.fehler('[web]\norigin = "https://x"\n', 'web.origin')
        self.fehler('[web]\nlogin_name = ""\n', 'login_name')
        self.fehler('web = 3\n', r'\[web\]')
        self.fehler('host = [\n', 'instanz.toml')

    def test_umgebung_geht_vor(self):
        self.toml('[web]\nport = 9000\nlogin_name = "a@b"\n')
        self.assertEqual(instanz.web()['port'], 9000)
        os.environ['DEVFLOW2_WEB_PORT'] = '9100'
        os.environ['DEVFLOW2_WEB_LOGIN'] = 'c@d'
        self.assertEqual((instanz.web()['port'], instanz.web()['login_name']), (9100, 'c@d'))
        os.environ['DEVFLOW2_WEB_PORT'] = 'x'
        with self.assertRaises(Fehler):
            instanz.laden()

    def test_wert_fuer_skripte(self):
        self.assertEqual(instanz.wert('web.port'), '8478')
        self.assertEqual(instanz.wert('web.login'), 'tailscale')
        for schlecht in ('web', 'web.gibtsnicht', 'nix'):
            with self.assertRaises(Fehler):
                instanz.wert(schlecht)


class ArbeitsprofilTest(Basis):
    def setUp(self):
        super().setUp()
        self.toml(ARBEIT)

    def test_profil(self):
        web_ = instanz.web()
        self.assertEqual((web_['adresse'], web_['login']), ('127.0.0.1', 'keine'))
        self.assertTrue(Path(instanz.testlaeufer()).is_file())  # ops/testlauf.sh im Checkout

    def test_dienst_bindet_nur_an_loopback(self):
        with mock.patch('uvicorn.run') as lauf, mock.patch.object(push, 'schluessel'):
            web.main()
        self.assertEqual(lauf.call_args.kwargs['host'], '127.0.0.1')
        self.assertEqual(lauf.call_args.kwargs['port'], 8478)
        self.toml('[web]\nadresse = "0.0.0.0"\n')
        with mock.patch('uvicorn.run') as lauf, mock.patch.object(push, 'schluessel'), self.assertRaises(Fehler):
            web.main()
        lauf.assert_not_called()

    def test_standard_bindet_nie_an_alle_adressen(self):
        os.environ['DEVFLOW2_INSTANZ'] = os.devnull
        with mock.patch('uvicorn.run') as lauf, mock.patch.object(push, 'schluessel'):
            web.main()
        self.assertEqual(lauf.call_args.kwargs['host'], '127.0.0.1')

    def client(self, **kw):
        return TestClient(web.anwendung(), base_url=LOKAL, **kw)

    def test_ohne_tailscale_kopf_erreichbar(self):
        c = self.client()
        for pfad in ('/health', '/'):
            self.assertEqual(c.get(pfad).status_code, 200, pfad)
        self.assertEqual(c.get('/health').json(), {'ok': True})
        self.assertEqual(self.client().get('/health', headers={'Host': '[::1]:8001'}).status_code, 200)
        self.assertEqual(self.client().get('/health', headers={'Host': 'localhost'}).status_code, 200)

    def test_fremder_host_ergibt_403(self):  # DNS-Rebinding
        for host in ('evil.example', '127.0.0.1.evil.example', 'evil.example:8478', '', 'localhost.evil'):
            r = self.client().get('/health', headers={'Host': host})
            self.assertEqual(r.status_code, 403, host)
        self.assertEqual(TestClient(web.anwendung()).get('/health').status_code, 403)  # testserver

    def test_fremder_origin_ergibt_403(self):
        c = self.client()
        for origin in ('https://boese.example', 'http://127.0.0.1:9999', 'null', 'http://localhost:8478'):
            self.assertEqual(c.post('/health', headers={'Origin': origin}).status_code, 403, origin)
        self.assertEqual(c.post('/health').status_code, 403)  # ohne Origin
        self.assertEqual(c.post('/health', headers={'Origin': LOKAL}).status_code, 405)  # Origin passt: erst die Route lehnt ab

    def test_x_forwarded_host_zaehlt_nicht(self):
        r = self.client().post('/health', headers={'Origin': 'https://boese.example', 'X-Forwarded-Host': 'boese.example'})
        self.assertEqual(r.status_code, 403)

    def test_tailscale_kopf_wird_nicht_gebraucht_und_nicht_geprueft(self):
        r = self.client().get('/health', headers={'Tailscale-User-Login': 'egal@x'})
        self.assertEqual(r.status_code, 200)

    def test_tailscale_login_verlangt_weiter_den_kopf(self):
        os.environ['DEVFLOW2_INSTANZ'] = os.devnull
        c = TestClient(web.anwendung())
        self.assertEqual(c.get('/health').status_code, 403)
        self.assertEqual(c.get('/health', headers={'Tailscale-User-Login': 'inhaber@github'}).status_code, 200)

    def test_projekt_anmelden_relativ(self):
        self.toml(ARBEIT + f'projektwurzel = "{self.dir}"\n')
        with self.assertRaisesRegex(Fehler, str(self.dir / 'nix')):
            projekte.anmelden('nix')

    def test_testlaeufer_vor_shell_befehlen(self):
        argv = laeufe.befehl({'id': 'x', 'modell': 'shell', 'testlauf': True}, 'echo hi')
        self.assertEqual(argv, [str(WURZEL / 'ops/testlauf.sh'), 'bash', '-c', 'echo hi'])
        os.environ['DEVFLOW2_TESTLAUF'] = ''
        self.assertEqual(laeufe.befehl({'id': 'x', 'modell': 'shell', 'testlauf': True}, 'echo hi'), ['bash', '-c', 'echo hi'])

    def test_fehlender_testlaeufer_ist_ein_fehler_kein_stilles_ohne(self):
        os.environ['DEVFLOW2_TESTLAUF'] = str(self.dir / 'fehlt.sh')
        with self.assertRaisesRegex(Fehler, 'fehlt.sh fehlt'):
            laeufe.befehl({'id': 'x', 'modell': 'shell', 'testlauf': True}, 'echo hi')
        self.assertEqual(laeufe.befehl({'id': 'x', 'modell': 'shell'}, 'echo hi'), ['bash', '-c', 'echo hi'])  # ohne testlauf-Marke unberührt


class SkriptTest(Basis):
    """ops/web_deploy.sh und ops/web_einrichten.sh lesen die Einstellungen aus instanz.toml."""

    def setUp(self):
        super().setUp()
        self.fake = self.dir / 'fake'
        self.fake.mkdir()
        for name, text in (('systemctl', SYSTEMCTL), ('loginctl', LOGINCTL), ('tailscale', TAILSCALE)):
            (self.fake / name).write_text(text)
            (self.fake / name).chmod(0o755)
        (self.fake / 'serve').write_text('')
        self.env = dict(os.environ, FAKE=str(self.fake), DEVFLOW2_SYSTEMCTL=str(self.fake / 'systemctl'),
                        DEVFLOW2_LOGINCTL=str(self.fake / 'loginctl'), DEVFLOW2_TAILSCALE=str(self.fake / 'tailscale'),
                        DEVFLOW2_UNIT_DIR=str(self.dir / 'einheiten'), XDG_RUNTIME_DIR=self.tmp.name, DEVFLOW2_WEB_WARTEN='3')

    def skript(self, name):
        env = dict(self.env, DEVFLOW2_INSTANZ=os.environ['DEVFLOW2_INSTANZ'])
        return subprocess.run([str(WURZEL / 'ops' / name)], env=env, capture_output=True, text=True, timeout=60)

    def aufrufe(self):
        p = self.fake / 'aufrufe'
        return p.read_text() if p.exists() else ''

    def test_einrichten_ohne_tailscale_bei_login_keine(self):
        self.toml(ARBEIT)
        r = self.skript('web_einrichten.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('login = keine', r.stdout)
        self.assertNotIn('tailscale', self.aufrufe())
        self.assertIn('systemctl --user start devflow2-web.service', self.aufrufe())

    def test_einrichten_proxy_ziel_folgt_der_adresse_auch_ipv6(self):
        self.toml('[web]\nadresse = "::1"\nport = 8123\ntailscale_port = 8999\n')
        r = self.skript('web_einrichten.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('tailscale serve --bg --https=8999 http://[::1]:8123', self.aufrufe())

    def test_einrichten_mit_tailscale_nutzt_port_aus_datei(self):
        self.toml('[web]\ntailscale_port = 8999\nport = 8123\n')
        self.assertEqual(self.skript('web_einrichten.sh').returncode, 0)
        self.assertIn('tailscale serve --bg --https=8999 http://127.0.0.1:8123', self.aufrufe())

    def test_deploy_ohne_kopf_bei_login_keine(self):
        gesehen = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                gesehen.append(self.headers.get('Tailscale-User-Login'))
                ok = self.path == '/health' and 'Tailscale-User-Login' not in self.headers
                self.send_response(200 if ok else 403)
                self.end_headers()
                self.wfile.write(b'{"ok":true}' if ok else b'')

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.toml(ARBEIT + f'[web]\nport = {server.server_address[1]}\n')
        r = self.skript('web_deploy.sh')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(gesehen, [None])


if __name__ == '__main__':
    unittest.main()
