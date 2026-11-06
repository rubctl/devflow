"""S14.5: Der MCP-Server wechselt nach einem Deploy zwischen zwei Aufrufen auf `aktiv`, in derselben Sitzung und ohne doppelte Ausführung.

Die Sitzung ist echt (stdio-Client gegen `python -m devflow2 mcp`), die beiden „Fassungen“ sind Kopien des Pakets in einem Temp-Home mit
einem zusätzlichen Werkzeug `zaehlen`, das jeden Aufruf in eine Datei schreibt. Nichts davon fasst den echten Zustand oder `aktiv` an.
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import devflow2
from devflow2 import mcp_server, mcp_wechsel

PAKET = Path(devflow2.__file__).parent
WERKZEUG = '''

import os as _os, time as _time
_START = _time.time_ns()


@werkzeug
def zaehlen() -> str:
    """Schreibender Testaufruf: eine Zeile je Ausführung."""
    with open(_os.environ['DEVFLOW2_ZAEHLER'], 'a') as f:
        f.write('%s\\n' % VERSION)
    return '%s %s' % (VERSION, _START)
'''


def fassung(home, name, kaputt=False, zustand_neuer=False, vor_s14_5=False):
    wurzel = home / 'versionen' / name
    shutil.copytree(PAKET, wurzel / 'devflow2', ignore=shutil.ignore_patterns('__pycache__'))
    server = wurzel / 'devflow2' / 'mcp_server.py'
    if vor_s14_5:  # wie eine Fassung vor S14.5: eigener Transport und Übergabe unbekannt, sonst startbar
        (wurzel / 'devflow2' / 'mcp_wechsel.py').unlink()
        server.write_text(server.read_text().replace('ablauf, mcp_wechsel,', 'ablauf,').replace('mcp_wechsel.stdio(wechseln=not satz)', 'stdio_server()')
                          .replace('\nimport ', '\nfrom mcp.server.stdio import stdio_server\nimport ', 1))
    server.write_text(server.read_text() + f"\nVERSION = {name!r}\n" + WERKZEUG + ('\nraise RuntimeError("kaputt")\n' if kaputt else ''))
    if zustand_neuer:  # wie eine Fassung, die den Zustand nicht mehr liest (Rückweg nach einer Umstellung)
        f = wurzel / 'devflow2' / 'format.py'
        f.write_text(f.read_text() + '\n\ndef bereit(*a, **k):\n    raise Fehler("Zustand ist neuer als diese Fassung")\n')
    py = wurzel / '.venv' / 'bin' / 'python'
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    return wurzel


def umhaengen(home, name):
    """Wie `aktualisieren`: `aktiv` atomar auf eine andere Fassung."""
    tmp = home / 'aktiv.neu'
    tmp.symlink_to(home / 'versionen' / name)
    os.replace(tmp, home / 'aktiv')


class Grund(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-mcpw-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stderr = open(os.devnull, 'w')
        self.addCleanup(self.stderr.close)
        self.home, self.zaehler = self.tmp / 'home', self.tmp / 'zaehler'
        (self.tmp / 'state').mkdir()
        for name in ('v2.1', 'v2.2', 'v2.3'):
            fassung(self.home, name)
        umhaengen(self.home, 'v2.1')

    def parameter(self):
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG')}
        env.update(DEVFLOW2_HOME=str(self.home), DEVFLOW2_STATE=str(self.tmp / 'state'), DEVFLOW2_INSTANZ=os.devnull,
                   DEVFLOW2_ZAEHLER=str(self.zaehler), PYTHONPATH=str(self.home / 'aktiv'), PYTHONDONTWRITEBYTECODE='1')
        return StdioServerParameters(command=str(self.home / 'aktiv' / '.venv' / 'bin' / 'python'), args=['-B', '-m', 'devflow2', 'mcp'],
                                     env=env, cwd=str(self.tmp))

    def sitzung(self, ablauf):
        """`ablauf(sitzung)` läuft in einer einzigen MCP-Sitzung (ein `initialize`)."""
        async def lauf():
            async with stdio_client(self.parameter(), errlog=self.stderr) as (lesen, schreiben):
                async with ClientSession(lesen, schreiben) as s:
                    await s.initialize()
                    with anyio.fail_after(60):
                        return await ablauf(s)
        return anyio.run(lauf)

    @staticmethod
    async def zaehlen(s):
        r = await s.call_tool('zaehlen', {})
        assert not r.isError, r
        return r.content[0].text

    def aufrufe(self):
        return self.zaehler.read_text().split() if self.zaehler.exists() else []


class WechselTest(Grund):
    def test_naechster_aufruf_laeuft_auf_der_neuen_fassung_genau_einmal(self):
        async def ablauf(s):
            a = await self.zaehlen(s)
            umhaengen(self.home, 'v2.2')  # Deploy simulieren
            b = await self.zaehlen(s)
            c = await self.zaehlen(s)
            return a, b, c
        a, b, c = self.sitzung(ablauf)
        self.assertTrue(a.startswith('v2.1 ') and b.startswith('v2.2 '), (a, b, c))
        self.assertEqual(self.aufrufe(), ['v2.1', 'v2.2', 'v2.2'])  # jeder Aufruf genau einmal, der zweite schon auf der neuen Fassung
        self.assertNotEqual(a.split()[1], b.split()[1])  # anderer Prozessstand
        self.assertEqual(b, c)  # ohne neuen Deploy kein zweiter Wechsel

    def test_ohne_deploy_bleibt_der_prozess(self):
        async def ablauf(s):
            return [await self.zaehlen(s) for _ in range(3)]
        a, b, c = self.sitzung(ablauf)
        self.assertEqual({a, b, c}, {a})
        self.assertEqual(self.aufrufe(), ['v2.1'] * 3)

    def test_gleichzeitige_aufrufe_nach_dem_deploy_jeder_genau_einmal(self):
        async def ablauf(s):
            await self.zaehlen(s)
            umhaengen(self.home, 'v2.2')
            erg = []

            async def ruf():
                erg.append(await self.zaehlen(s))
            async with anyio.create_task_group() as tg:
                for _ in range(5):
                    tg.start_soon(ruf)
            return erg
        erg = self.sitzung(ablauf)
        self.assertEqual([e.split()[0] for e in erg], ['v2.2'] * 5)
        self.assertEqual(self.aufrufe(), ['v2.1'] + ['v2.2'] * 5)

    def test_zwei_deploys_hintereinander(self):
        async def ablauf(s):
            r = [await self.zaehlen(s)]
            umhaengen(self.home, 'v2.2')
            r.append(await self.zaehlen(s))
            umhaengen(self.home, 'v2.3')
            r.append(await self.zaehlen(s))
            return [x.split()[0] for x in r]
        self.assertEqual(self.sitzung(ablauf), ['v2.1', 'v2.2', 'v2.3'])
        self.assertEqual(self.aufrufe(), ['v2.1', 'v2.2', 'v2.3'])

    def test_fassung_die_nicht_laedt_bleibt_draussen_und_der_aufruf_laeuft_einmal(self):
        shutil.rmtree(self.home / 'versionen' / 'v2.2')
        fassung(self.home, 'v2.2', kaputt=True)

        async def ablauf(s):
            umhaengen(self.home, 'v2.2')
            r = [await self.zaehlen(s), await self.zaehlen(s)]
            return [x.split()[0] for x in r]
        self.assertEqual(self.sitzung(ablauf), ['v2.1', 'v2.1'])  # Verbindung bleibt nutzbar
        self.assertEqual(self.aufrufe(), ['v2.1', 'v2.1'])

    def test_fassung_vor_s14_5_ohne_uebergabe_bleibt_draussen_und_die_sitzung_nutzbar(self):
        shutil.rmtree(self.home / 'versionen' / 'v2.2')
        fassung(self.home, 'v2.2', vor_s14_5=True)

        async def ablauf(s):
            umhaengen(self.home, 'v2.2')
            return [(await self.zaehlen(s)).split()[0] for _ in range(2)]
        self.assertEqual(self.sitzung(ablauf), ['v2.1', 'v2.1'])  # kein execve in einen Server, der initialize und den Aufruf verlöre
        self.assertEqual(self.aufrufe(), ['v2.1', 'v2.1'])

    def test_fassung_die_den_zustand_nicht_liest_bleibt_draussen(self):
        shutil.rmtree(self.home / 'versionen' / 'v2.2')
        fassung(self.home, 'v2.2', zustand_neuer=True)

        async def ablauf(s):
            umhaengen(self.home, 'v2.2')
            return [(await self.zaehlen(s)).split()[0] for _ in range(2)]
        self.assertEqual(self.sitzung(ablauf), ['v2.1', 'v2.1'])
        self.assertEqual(self.aufrufe(), ['v2.1', 'v2.1'])

    def test_aktiv_ohne_python_wird_nicht_angefasst(self):
        shutil.rmtree(self.home / 'versionen' / 'v2.2' / '.venv')

        async def ablauf(s):
            umhaengen(self.home, 'v2.2')
            return await self.zaehlen(s)
        self.assertTrue(self.sitzung(ablauf).startswith('v2.1 '))
        self.assertEqual(self.aufrufe(), ['v2.1'])


class ZielTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-mcpz-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.aktiv = self.tmp / 'aktiv'
        self.hier = self.tmp / 'versionen' / 'v2.1'

    def fassung(self, name):
        w = self.tmp / 'versionen' / name
        (w / 'devflow2').mkdir(parents=True, exist_ok=True)
        (w / 'devflow2' / '__main__.py').write_text('')
        (w / '.venv' / 'bin').mkdir(parents=True, exist_ok=True)
        (w / '.venv' / 'bin' / 'python').write_text('')
        return w

    def test_kein_symlink_kein_ziel(self):
        self.fassung('v2.1')
        self.assertIsNone(mcp_wechsel.ziel(self.aktiv, self.hier))  # fehlt: Checkout wie auf server
        self.aktiv.mkdir()
        self.assertIsNone(mcp_wechsel.ziel(self.aktiv, self.hier))  # Verzeichnis statt Verweis

    def test_gleiche_fassung_kein_ziel(self):
        self.aktiv.symlink_to(self.fassung('v2.1'))
        self.assertIsNone(mcp_wechsel.ziel(self.aktiv, self.hier.resolve()))

    def test_andere_fassung_ist_ziel(self):
        neu = self.fassung('v2.2')
        self.aktiv.symlink_to(neu)
        self.assertEqual(mcp_wechsel.ziel(self.aktiv, self.hier), (neu.resolve(), neu.resolve() / '.venv' / 'bin' / 'python'))

    def test_unvollstaendige_fassung_kein_ziel(self):
        neu = self.fassung('v2.2')
        self.aktiv.symlink_to(neu)
        (neu / '.venv' / 'bin' / 'python').unlink()
        self.assertIsNone(mcp_wechsel.ziel(self.aktiv, self.hier))

    def test_nur_die_sitzung_wechselt(self):
        gesehen = []

        class Attrappe:
            async def __aenter__(self):
                raise RuntimeError('stop')

            async def __aexit__(self, *a):
                return False

        def stdio(**kw):
            gesehen.append(kw)
            return Attrappe()
        for satz, soll in (('', True), ('steuerung', False)):
            with mock.patch.object(mcp_wechsel, 'stdio', stdio), mock.patch.object(mcp_server, 'satz', satz):
                with self.assertRaises(RuntimeError):
                    anyio.run(mcp_server._laufen)
            self.assertEqual(gesehen[-1], {'wechseln': soll})


if __name__ == '__main__':
    unittest.main()
