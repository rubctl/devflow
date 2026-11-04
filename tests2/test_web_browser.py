"""Browserprobe für das Dashboard (tests2/browser/web.cjs): 390 px (Dark und Light) ohne waagrechtes Scrollen, Live-Log.

Läuft nur, wenn Node, `playwright-core` und ein Playwright-Chromium vorhanden sind (auf server der Fall), sonst
wird sie übersprungen. Pfad zu `playwright-core` notfalls über `DEVFLOW2_PLAYWRIGHT_CORE`. Kein Netz: Server und
Browser laufen lokal.
"""
import glob
import json
import os
os.environ.setdefault('DEVFLOW2_INSTANZ', os.devnull)  # nie die echte instanz.toml lesen
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import unittest

from devflow2 import laeufe, web, inhaber
from web_daten import aufbauen

HIER = Path(__file__).resolve().parent
SKIP = 77


def playwright_core():
    kandidaten = [os.environ.get('DEVFLOW2_PLAYWRIGHT_CORE', '')]
    kandidaten += sorted(glob.glob(os.path.expanduser('~/.npm/_npx/*/node_modules/playwright-core')))
    return next((k for k in kandidaten if k and Path(k, 'package.json').is_file()), None)


NODE = shutil.which('node')
PLAYWRIGHT = playwright_core()


@unittest.skipUnless(NODE and PLAYWRIGHT, 'Node oder playwright-core fehlt')
class BrowserTest(unittest.TestCase):
    def server(self):
        import uvicorn
        fall, ids = aufbauen()
        self.addCleanup(fall.tearDown)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(web.anwendung(), host='127.0.0.1', port=port, log_level='warning'))
        faden = threading.Thread(target=server.run, daemon=True)
        faden.start()
        self.addCleanup(faden.join, 5)
        self.addCleanup(setattr, server, 'should_exit', True)
        ende = time.time() + 10
        while not server.started and time.time() < ende:
            time.sleep(0.05)
        self.assertTrue(server.started)
        return fall, ids, port

    def test_handy_und_live_log(self):
        fall, ids, port = self.server()
        seiten = ['/', '/p/demo', '/p/demo/a/A1', '/p/demo/a/A2', '/p/demo/a/A3', f'/lauf/{ids["fertig"]}',
                  f'/lauf/{ids["review"]}', f'/lauf/{ids["laufend"]}', '/p/demo/md/', '/p/demo/md/plan/wetter.md',
                  '/p/demo/md/berichte/A1.md?zweig=claude-a1', '/p/demo/md/ROADMAP.md', '/p/demo/roadmap', '/p/demo/eingang', '/p/demo/pflege', '/kontingent', '/plaene', '/p/demo/plan/wetter', '/hinweise']
        r = subprocess.run([NODE, str(HIER / 'browser' / 'web.cjs'), PLAYWRIGHT, f'http://127.0.0.1:{port}', 'inhaber@github',
                            str(laeufe.verzeichnis(ids['laufend']) / 'log.jsonl'), ids['laufend'], json.dumps(seiten)],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == SKIP:
            self.skipTest(r.stderr.strip())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Browserprobe bestanden', r.stdout)

    def test_formulare_mit_echter_origin(self):
        # Der Browser schickt die Origin selbst (Referrer-Policy): Antwort und „Alles anhalten“ dürfen nicht an der
        # Herkunftsprüfung scheitern.
        fall, ids, port = self.server()
        r = subprocess.run([NODE, str(HIER / 'browser' / 'aktion.cjs'), PLAYWRIGHT, f'http://127.0.0.1:{port}',
                            'inhaber@github'], capture_output=True, text=True, timeout=120)
        if r.returncode == SKIP:
            self.skipTest(r.stderr.strip())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Aktionsprobe bestanden', r.stdout)
        self.assertIn(f'Antwort {inhaber.kennung()}', fall.roadmap())
        self.assertTrue(laeufe.angehalten())
    def test_app_offline_installieren_neue_version(self):
        # PWA (S4.2): Manifest gültig, Service Worker aktiv, Seite offline mit Hinweis, POST offline gesperrt.
        fall, ids, port = self.server()
        r = subprocess.run([NODE, str(HIER / 'browser' / 'app.cjs'), PLAYWRIGHT, f'http://127.0.0.1:{port}', 'inhaber@github'],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == SKIP:
            self.skipTest(r.stderr.strip())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('App-Probe bestanden', r.stdout)
        self.assertFalse(laeufe.angehalten())  # der gesperrte POST ist nie beim Server angekommen


if __name__ == '__main__':
    unittest.main()
