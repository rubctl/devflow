"""Ideen erfassen (S3.4): Eingang im Dashboard, Ablage in plan/eingang.md, Zustand per MCP."""
import logging
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import Fehler, ablauf, eingang, ereignis, mcp_server, plaene, web  # noqa: E402
from hilfen import Fall, git  # noqa: E402

HERKUNFT = {'Tailscale-User-Login': 'inhaber@github', 'Origin': 'http://testserver'}
logging.getLogger('httpx').setLevel(logging.WARNING)


class Basis(Fall):
    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)

    def idee(self, text, **kw):
        return self.client.post('/p/demo/idee', data={'idee': text}, **kw)

    def datei(self):
        return (self.repo / 'plan' / 'eingang.md').read_text()

    def anzahl(self):
        return int(git(self.repo, 'rev-list', '--count', 'main'))


class AnlegenTest(Basis):
    def test_idee_landet_als_commit_mit_eingang_und_protokoll(self):
        vorher = self.anzahl()
        r = self.idee('Wetter  auf der\nLage anzeigen')
        self.assertEqual((r.status_code, r.headers['location']), (303, '/p/demo?meldung=idee#ideen'))
        self.assertEqual(self.anzahl(), vorher + 1)
        self.assertEqual(git(self.repo, 'show', '--name-only', '--format=%s', 'HEAD').split('\n'), ['plan: neue Idee im Eingang', '', 'ROADMAP.md', 'plan/eingang.md'])
        self.assertIn(f'## Idee 1 · {ablauf.datum()} · dashboard\n\n**Zustand:** neu\n\n> Wetter auf der Lage anzeigen\n', self.datei())
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual(eingang.liste(self.p), [dict(nr=1, datum=ablauf.datum(), herkunft='dashboard', text='Wetter auf der Lage anzeigen', zustand='neu', plan='')])

    def test_weitere_idee_bekommt_naechste_nummer(self):
        self.idee('Erste')
        self.idee('Zweite')
        self.assertEqual([(i['nr'], i['text']) for i in eingang.liste(self.p)], [(1, 'Erste'), (2, 'Zweite')])

    def test_doppelsenden_ergibt_eine_idee_und_einen_commit(self):
        self.idee('Nur einmal')
        nach_erster, datei = self.anzahl(), self.datei()
        r = self.idee('Nur  einmal\n')
        self.assertEqual(r.headers['location'], '/p/demo?meldung=idee-schon#ideen')
        self.assertEqual((self.anzahl(), self.datei(), len(eingang.liste(self.p))), (nach_erster, datei, 1))
        self.assertEqual([e['art'] for e in ereignis.lesen()], ['neue-idee'])

    def test_neue_idee_meldet_ereignis_ohne_text(self):
        self.idee('Geheimer Text')
        e, = ereignis.lesen()
        self.assertEqual((e['art'], e['projekt'], e['bezug']), ('neue-idee', 'demo', 'plan/eingang.md'))
        self.assertNotIn('Geheimer', str(e))

    def test_ereignis_nicht_schreibbar_speichert_trotzdem_und_sagt_es(self):
        ereignis.datei().mkdir()
        r = self.idee('Trotzdem')
        self.assertEqual(r.headers['location'], '/p/demo?meldung=idee-ohne-ereignis#ideen')
        self.assertEqual(len(eingang.liste(self.p)), 1)

    def test_leer_und_zu_lang_aendern_nichts(self):
        vorher = self.anzahl()
        for text, code in (('  \n ', 400), ('x' * (eingang.MAX_TEXT + 1), 400)):
            self.assertEqual(self.idee(text).status_code, code)
        self.assertEqual((self.anzahl(), (self.repo / 'plan' / 'eingang.md').exists()), (vorher, False))

    def test_fremde_herkunft_und_get_aendern_nichts(self):
        vorher = self.anzahl()
        self.assertEqual(self.idee('x', headers={'Origin': 'http://boese.example'}).status_code, 403)
        self.assertEqual(self.idee('x', headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertEqual(self.client.get('/p/demo/idee').status_code, 405)
        self.assertEqual(self.client.post('/p/unbekannt/idee', data={'idee': 'x'}).status_code, 404)
        self.assertEqual((self.anzahl(), (self.repo / 'plan' / 'eingang.md').exists()), (vorher, False))

    def test_nicht_committete_aenderung_am_eingang_blockiert(self):
        (self.repo / 'plan' / 'eingang.md').write_text('von Hand\n')
        r = self.idee('x')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.datei(), 'von Hand\n')

    def test_eingang_ist_kein_plan(self):
        self.idee('Idee')
        self.assertEqual(plaene.namen(self.p), ['wetter'])
        self.assertEqual(self.client.get('/p/demo/plan/eingang').status_code, 404)
        with self.assertRaises(Fehler):
            ablauf.plan_uebernehmen(self.p, 'plan/eingang.md')


class ZeigenTest(Basis):
    def test_html_wird_maskiert(self):
        self.idee('<script>alert(1)</script> <b>fett</b> & "x"')
        html = self.client.get('/p/demo').text
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt; &lt;b&gt;fett&lt;/b&gt; &amp;', html)
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('<b>fett', html)
        self.assertEqual(self.client.get('/p/demo/md/plan/eingang.md').status_code, 200)
        self.assertNotIn('<script>alert', self.client.get('/p/demo/md/plan/eingang.md').text)

    def test_projektseite_zeigt_formular_und_leere_liste(self):
        html = self.client.get('/p/demo').text
        self.assertIn('action="/p/demo/idee"', html)
        self.assertIn('Noch keine Ideen.', html)

    def test_liste_zeigt_zustand_neueste_oben_und_meldung(self):
        self.idee('Erste')
        self.idee('Zweite')
        html = self.client.get('/p/demo?meldung=idee').text
        self.assertLess(html.index('Zweite'), html.index('Erste'))
        self.assertIn('Idee ist im Eingang gespeichert', html)

    def test_zustandswechsel_per_mcp_ist_im_dashboard_sichtbar(self):
        self.idee('Plan daraus')
        self.idee('Unsinn')
        html = self.client.get('/p/demo').text
        self.assertEqual(html.count('neu</span>'), 2)
        mcp_server.idee_zustand('demo', 1, 'in Planung', 'wetter')
        mcp_server.idee_zustand('demo', 2, 'verworfen')
        html = self.client.get('/p/demo').text
        self.assertIn('in Planung</span>', html)
        self.assertIn('href="/p/demo/plan/wetter"', html)
        self.assertIn('verworfen</span>', html)
        self.assertNotIn('neu</span>', html)
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%s'), 'plan: Idee 2 → verworfen')


class ZustandTest(Basis):
    def setUp(self):
        super().setUp()
        self.idee('Eins')
        self.idee('Zwei')

    def test_mcp_liste_und_zustand(self):
        self.assertEqual([(i['nr'], i['zustand']) for i in mcp_server.eingang('demo')], [(1, 'neu'), (2, 'neu')])
        i = mcp_server.idee_zustand('demo', 1, 'in Planung', 'plan/wetter.md')
        self.assertEqual((i['zustand'], i['plan'], i['text']), ('in Planung', 'plan/wetter.md', 'Eins'))
        self.assertIn('**Zustand:** in Planung → plan/wetter.md\n', self.datei())
        self.assertEqual(eingang.liste(self.p)[1]['zustand'], 'neu')

    def test_gleicher_zustand_zweimal_ergibt_keinen_zweiten_commit(self):
        mcp_server.idee_zustand('demo', 1, 'verworfen')
        n = self.anzahl()
        mcp_server.idee_zustand('demo', 1, 'verworfen')
        self.assertEqual(self.anzahl(), n)

    def test_zurueck_auf_neu_entfernt_den_plan(self):
        mcp_server.idee_zustand('demo', 1, 'in Planung', 'wetter')
        mcp_server.idee_zustand('demo', 1, 'neu')
        self.assertIn('**Zustand:** neu\n', self.datei())
        self.assertEqual([i['plan'] for i in eingang.liste(self.p)], ['', ''])

    def test_fehlerfaelle_aendern_nichts(self):
        n, datei = self.anzahl(), self.datei()
        for args in ((9, 'neu', ''), (1, 'erledigt', ''), (1, 'in Planung', ''), (1, 'in Planung', 'gibtsnicht'), (1, 'in Planung', 'eingang'),
                     (1, 'in Planung', '../ROADMAP'), (1, 'verworfen', 'wetter')):
            with self.assertRaises(Exception, msg=args):
                mcp_server.idee_zustand('demo', *args)
        self.assertEqual((self.anzahl(), self.datei()), (n, datei))

    def test_eingang_ohne_datei_ist_leer(self):
        (self.repo / 'plan' / 'eingang.md').unlink()
        git(self.repo, 'commit', '-qam', 'weg')
        self.assertEqual(mcp_server.eingang('demo'), [])


if __name__ == '__main__':
    unittest.main()
