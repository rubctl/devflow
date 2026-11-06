"""S7.3: Commits auf main werden gepusht; Fehler bleiben vermerkt, erscheinen unter „Braucht dich“ und werden nachgeholt. Remote ist immer ein lokales Bare-Repo."""
import json
import os
import subprocess
import time
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import plaene, repo, roadmap, web  # noqa: E402
from hilfen import Fall, git  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
ENTWURF = """# Vorhaben X · Claude · 03.11.2026

**Stand:** Entwurf

## Kurz

- Test.

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| X1 | Etwas | claude | `a.py` | geht | leicht |
"""


class Basis(Fall):
    def setUp(self):
        super().setUp()
        self.bare = self.tmp / 'remote.git'
        git(self.tmp, 'init', '-q', '--bare', '-b', 'main', str(self.bare))
        git(self.repo, 'remote', 'add', 'origin', str(self.bare))
        git(self.repo, 'push', '-q', 'origin', 'main')
        git(self.repo, 'branch', '--set-upstream-to=origin/main')

    def remote(self):
        return git(self.bare, 'rev-parse', 'main')

    def lokal(self):
        return git(self.repo, 'rev-parse', 'main')

    def notiz(self, text='x'):
        repo.auf_main(self.p, lambda t: {'ROADMAP.md': t['ROADMAP.md'] + f'\n{text}\n'}, f'roadmap: {text}')


class PushTest(Basis):
    def test_roadmap_traumbericht_und_planentwurf_landen_ohne_merge_im_remote(self):
        repo.roadmap_aendern(self.p, lambda t: t + '\nNotiz\n', 'roadmap: Notiz')
        self.assertEqual(self.remote(), self.lokal())
        repo.auf_main(self.p, lambda t: {'berichte/traum.md': '# Traum\n'}, 'traum: Bericht', ('berichte/traum.md',))
        self.assertEqual(self.remote(), self.lokal())
        plaene.entwurf(self.p, 'x', ENTWURF)
        self.assertEqual(self.remote(), self.lokal())
        self.assertIn('plan: ', git(self.bare, 'log', '-1', '--format=%s'))
        self.assertIsNone(repo.push_offen(self.p))

    def test_gesperrter_commit_pusht_nach_der_sperre(self):
        with repo.sperre(self.p):
            repo.auf_main_gesperrt(self.p, lambda t: {'ROADMAP.md': t['ROADMAP.md'] + '\nA\n'}, 'roadmap: A')
            self.assertNotEqual(self.remote(), self.lokal())  # noch in der Sperre: kein Netz
            self.assertIsNotNone(repo.push_offen(self.p))
        self.assertEqual(self.remote(), self.lokal())
        self.assertIsNone(repo.push_offen(self.p))

    def test_kein_remote_bleibt_unveraendert(self):
        git(self.repo, 'remote', 'remove', 'origin')
        self.notiz()
        self.assertIsNone(repo.push_offen(self.p))
        self.assertFalse((self.repo / '.git' / 'devflow2-push-offen').exists())
        self.assertEqual(repo.push(self.p), 'kein Remote')

    def test_nicht_erreichbar_commit_bleibt_vermerkt_und_wird_nachgeholt(self):
        weg = self.tmp / 'weg.git'
        self.bare.rename(weg)
        self.notiz('eins')
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%s'), 'roadmap: eins')
        offen = repo.push_offen(self.p)
        self.assertIn('fehlgeschlagen', offen['fehler'])
        seit = offen['seit']
        self.notiz('zwei')  # weiter nicht erreichbar: Zeitpunkt bleibt
        self.assertEqual(repo.push_offen(self.p)['seit'], seit)
        weg.rename(self.bare)
        self.notiz('drei')
        self.assertEqual(self.remote(), self.lokal())
        self.assertIsNone(repo.push_offen(self.p))

    def test_remote_weiter_wird_nicht_ueberschrieben(self):
        fremd = self.tmp / 'fremd'
        git(self.tmp, 'clone', '-q', str(self.bare), str(fremd))
        (fremd / 'f.txt').write_text('fremd\n')
        git(fremd, 'add', '-A')
        git(fremd, 'commit', '-q', '-m', 'fremd')
        git(fremd, 'push', '-q', 'origin', 'main')
        stand = self.remote()
        self.notiz()
        self.assertEqual(self.remote(), stand)  # kein Force
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%s'), 'roadmap: x')
        self.assertTrue(repo.push_offen(self.p)['fehler'])
        git(self.repo, 'pull', '-q', '--no-rebase', '--no-edit', 'origin', 'main')
        self.assertTrue(repo.nachschieben(self.p))
        self.assertEqual(self.remote(), self.lokal())

    def test_push_ohne_zeitlimit_antwort_ist_fehler_mit_vermerk(self):
        alt = repo.PUSH_ZEIT
        repo.PUSH_ZEIT = 0.001
        try:
            self.notiz()
        finally:
            repo.PUSH_ZEIT = alt
        self.assertTrue(repo.push_offen(self.p)['fehler'])

    def test_merge_push_und_vermerk_kommen_sich_nicht_in_die_quere(self):
        self.notiz()
        self.assertEqual(repo.push(self.p), 'gepusht nach origin')  # nichts mehr zu tun, auch zweimal
        self.assertEqual(repo.push(self.p), 'gepusht nach origin')
        self.assertIsNone(repo.push_offen(self.p))


class LageTest(Basis):
    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=LOGIN, follow_redirects=False)

    def test_offener_push_steht_unter_braucht_dich_und_verschwindet(self):
        weg = self.tmp / 'weg.git'
        self.bare.rename(weg)
        self.notiz()
        html = self.client.get('/').text
        self.assertIn('Push offen', html)
        weg.rename(self.bare)
        self.notiz('zwei')
        self.assertNotIn('Push offen', self.client.get('/').text)


if __name__ == '__main__':
    unittest.main()
