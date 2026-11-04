"""Dashboard-Handlungen (S2.3): Antwort, Lauf anhalten und fortsetzen, alles anhalten und fortsetzen, Schutz."""
import logging
import threading
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import ablauf, laeufe, roadmap, web, inhaber  # noqa: E402
from devflow2.web_aktion import Herkunft  # noqa: E402
from hilfen import Fall, git  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
HERKUNFT = {**LOGIN, 'Origin': 'http://testserver'}
logging.getLogger('httpx').setLevel(logging.WARNING)


class Basis(Fall):
    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')

    def post(self, pfad, daten=None, **kw):
        return self.client.post(pfad, data=daten or {}, **kw)

    def commits(self, text=None):
        text = text or f'Antwort {inhaber.kennung()}'
        return len([z for z in git(self.repo, 'log', '--format=%s', 'main').split('\n') if text in z])

    def stand(self):
        return git(self.repo, 'rev-parse', 'HEAD'), self.roadmap()


class AntwortTest(Basis):
    def setUp(self):
        super().setUp()
        ablauf.frage_stellen(self.p, 'Celsius oder Fahrenheit?', ['Celsius', 'Fahrenheit'], 'Celsius', 'A1')
        self.assertEqual(self.zeile('A1')[3], 'Braucht dich')

    def test_antwort_landet_als_commit_und_aufgabe_ist_bereit(self):
        # Formular steht auf der Aufgabenseite und auf der Projektseite
        for pfad in ('/p/demo/a/A1', '/p/demo'):
            s = self.client.get(pfad).text
            self.assertIn('action="/p/demo/frage/1/antwort"', s)
            self.assertIn('<textarea class="rc-input" name="antwort"', s)
        vorher = git(self.repo, 'rev-parse', 'HEAD')
        r = self.post('/p/demo/frage/1/antwort', {'antwort': 'Celsius, bitte'})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers['location'], '/p/demo/a/A1?meldung=antwort#fragen')
        self.assertIn(f'**Antwort {inhaber.kennung()} (', self.roadmap())
        self.assertIn('Celsius, bitte', self.roadmap())
        self.assertEqual(self.zeile('A1')[3], 'bereit')
        self.assertEqual(git(self.repo, 'rev-list', '--count', f'{vorher}..main'), '1')
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%s', 'main'), f'roadmap: Antwort {inhaber.kennung()} auf F1')
        self.assertEqual(roadmap.fragen(self.roadmap()), [])
        # Bestätigung auf der Zielseite, Formular nur für offene Fragen
        s = self.client.get(r.headers['location']).text
        self.assertIn('Antwort ist in der Roadmap eingetragen.', s)
        self.assertNotIn('<textarea', s)

    def test_zweimal_senden_ergibt_eine_antwort(self):
        erste = self.post('/p/demo/frage/1/antwort', {'antwort': 'Celsius'})
        zweite = self.post('/p/demo/frage/1/antwort', {'antwort': 'Celsius'})
        self.assertEqual((erste.status_code, zweite.status_code), (303, 303))
        self.assertEqual(zweite.headers['location'], erste.headers['location'])  # gleiche Bestätigung, kein Fehler
        self.assertEqual(self.commits(), 1)
        self.assertEqual(self.roadmap().count(f'**Antwort {inhaber.kennung()} ('), 1)

    def test_gleichzeitig_senden_ergibt_eine_antwort(self):
        codes = []
        los = threading.Barrier(2)

        def senden():
            c = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)
            los.wait()
            codes.append(c.post('/p/demo/frage/1/antwort', data={'antwort': 'Celsius'}).headers['location'])
        threads = [threading.Thread(target=senden) for _ in range(2)]
        [x.start() for x in threads]
        [x.join() for x in threads]
        self.assertEqual(len(codes), 2)
        self.assertEqual(self.commits(), 1)
        self.assertEqual(self.roadmap().count(f'**Antwort {inhaber.kennung()} ('), 1)

    def test_schon_beantwortete_frage_gibt_hinweis(self):
        ablauf.antwort_eintragen(self.p, 'F1', 'Fahrenheit')
        vorher = self.stand()
        r = self.post('/p/demo/frage/1/antwort', {'antwort': 'Celsius'})
        self.assertEqual((r.status_code, r.headers['location']), (303, '/p/demo/a/A1?meldung=schon#fragen'))
        self.assertEqual(self.stand(), vorher)
        s = self.client.get(r.headers['location']).text
        self.assertIn('Diese Frage war schon beantwortet', s)
        self.assertNotIn('Traceback', s)

    def test_antwort_ist_eine_zeile_und_wird_maskiert(self):
        self.post('/p/demo/frage/1/antwort', {'antwort': 'Zeile eins\n\n## Neuer Abschnitt\r\n| x |'})
        zeilen = [z for z in self.roadmap().split('\n') if f'Antwort {inhaber.kennung()}' in z]
        self.assertEqual(len(zeilen), 1)
        self.assertTrue(zeilen[0].endswith('Zeile eins ## Neuer Abschnitt | x |'))
        self.assertEqual(self.zeile('A1')[3], 'bereit')

    def test_ungueltige_antworten_aendern_nichts(self):
        vorher = self.stand()
        for daten, code in (({'antwort': '  \n '}, 400), ({}, 400), ({'antwort': 'x' * 4001}, 400)):
            with self.subTest(code=code, laenge=len(str(daten))):
                self.assertEqual(self.post('/p/demo/frage/1/antwort', daten).status_code, code)
        self.assertEqual(self.post('/p/demo/frage/7/antwort', {'antwort': 'a'}).status_code, 404)
        self.assertEqual(self.post('/p/gibtsnicht/frage/1/antwort', {'antwort': 'a'}).status_code, 404)
        self.assertEqual(self.post('/p/demo/frage/1/antwort', content=b'{"antwort": "a"}',
                                   headers={'content-type': 'application/json'}).status_code, 415)
        self.assertEqual(self.post('/p/demo/frage/1/antwort', content=b'antwort=' + b'x' * 40000,
                                   headers={'content-type': 'application/x-www-form-urlencoded'}).status_code, 413)
        self.assertEqual(self.stand(), vorher)

    def test_schutz_ohne_aenderung(self):
        vorher = self.stand()
        pfad = '/p/demo/frage/1/antwort'
        daten = {'antwort': 'Celsius'}
        faelle = {
            'ohne Origin': (dict(LOGIN), 403),
            'fremde Origin': ({**LOGIN, 'Origin': 'https://boese.example'}, 403),
            'Origin null': ({**LOGIN, 'Origin': 'null'}, 403),
            'Origin mit Pfad': ({**LOGIN, 'Origin': 'http://testserver/x'}, 403),
            'cross-site': ({**HERKUNFT, 'Sec-Fetch-Site': 'cross-site'}, 403),
            'ohne Login': ({'Origin': 'http://testserver'}, 403),
            'falscher Login': ({'Origin': 'http://testserver', 'Tailscale-User-Login': 'fremd@github'}, 403),
        }
        roh = TestClient(web.anwendung(), follow_redirects=False)
        for name, (kopf, code) in faelle.items():
            with self.subTest(name):
                r = roh.post(pfad, data=daten, headers=kopf)
                self.assertEqual(r.status_code, code)
        for methode in ('get', 'put', 'delete', 'patch'):
            with self.subTest(methode):
                self.assertEqual(getattr(self.client, methode)(pfad).status_code, 405)
        self.assertEqual(self.stand(), vorher)
        self.assertEqual(self.post(pfad, daten).status_code, 303)  # mit passender Herkunft geht es

    def test_herkunft_weitergeleiteter_host_und_einstellung(self):
        pfad = '/p/demo/frage/1/antwort'
        extern = 'https://server.example:8472'
        roh = TestClient(web.anwendung(), follow_redirects=False)
        r = roh.post(pfad, data={'antwort': 'Celsius'}, headers={**LOGIN, 'Origin': extern})
        self.assertEqual(r.status_code, 403)
        r = roh.post(pfad, data={'antwort': 'Celsius'}, headers={**LOGIN, 'Origin': extern, 'X-Forwarded-Host': 'server.example:8472'})
        self.assertEqual(r.status_code, 303)
        h = Herkunft(None, f'{extern}/, https://zweite.example')
        self.assertTrue(h.passt({'origin': 'https://zweite.example'}))
        self.assertTrue(h.passt({'origin': extern}))
        self.assertFalse(h.passt({'origin': 'https://dritte.example', 'host': 'x'}))


class LaufTest(Basis):
    def test_abgelaufene_anmeldung_steht_im_dashboard(self):
        """S8.4: Startseite nennt den Schritt zur erneuten Anmeldung (fester Text, kein Logausschnitt) samt Fortsetzen."""
        geheim = 'sk-ant-oat01-GEHEIM'
        self.drehbuch(claude=[{'tue': 'fehler', 'text': f'OAuth session expired {geheim}'}])
        l = ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.warten(lambda: self.zeile('A1')[3] == 'Braucht dich')
        s = self.client.get('/').text
        self.assertIn('Anmeldung von claude abgelaufen', s)
        self.assertIn('per SSH', s)
        self.assertIn('/login', s)
        self.assertNotIn(geheim, s)
        self.assertIn(f'action="/lauf/{l["id"]}/fortsetzen"', s)

    def lauf_starten(self):
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}, {'tue': 'commit'}], codex=[self.bestanden()])
        l = ablauf.entwickeln(self.p, 'A1')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'laeuft' and len(self.aufrufe()) == 1, text='Lauf läuft')
        self.warten(lambda: laeufe.laden(l['id']).get('session'), text='Sitzung gesichert')
        return l['id']

    def test_anhalten_beendet_und_fortsetzen_nimmt_wieder_auf(self):
        lid = self.lauf_starten()
        s = self.client.get(f'/lauf/{lid}').text
        self.assertIn(f'action="/lauf/{lid}/anhalten"', s)
        self.assertNotIn('/fortsetzen"', s)
        self.assertIn(f'action="/lauf/{lid}/anhalten"', self.client.get('/').text)
        r = self.post(f'/lauf/{lid}/anhalten')
        self.assertEqual((r.status_code, r.headers['location']), (303, f'/lauf/{lid}?meldung=angehalten'))
        self.assertEqual(laeufe.laden(lid)['zustand'], 'angehalten')
        self.warten(lambda: not laeufe.lebt(laeufe.laden(lid)), sekunden=10, text='Prozessende')
        s = self.client.get(r.headers['location']).text
        self.assertIn('Lauf angehalten.', s)
        self.assertIn(f'action="/lauf/{lid}/fortsetzen"', s)
        self.assertIn(f'action="/lauf/{lid}/fortsetzen"', self.client.get('/').text)  # liegt beim Inhaber
        # zweiter Klick: nichts mehr zu tun, kein Fehler
        zweit = self.post(f'/lauf/{lid}/anhalten')
        self.assertEqual(zweit.headers['location'], f'/lauf/{lid}?meldung=nicht-aktiv')
        r = self.post(f'/lauf/{lid}/fortsetzen')
        self.assertEqual((r.status_code, r.headers['location']), (303, f'/lauf/{lid}?meldung=fortgesetzt'))
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt', text='Kette nach Fortsetzen')
        self.assertIn('--resume', self.aufrufe('claude')[1]['argv'])

    def test_fortsetzen_zweimal_startet_nur_einen_lauf(self):
        lid = self.lauf_starten()
        self.post(f'/lauf/{lid}/anhalten')
        self.drehbuch(claude=[{'tue': 'schlafen', 's': 60}])
        erste = self.post(f'/lauf/{lid}/fortsetzen')
        zweite = self.post(f'/lauf/{lid}/fortsetzen')
        self.assertTrue(erste.headers['location'].endswith('meldung=fortgesetzt'))
        self.assertTrue(zweite.headers['location'].endswith('meldung=laeuft'))
        self.warten(lambda: len(self.aufrufe('claude')) == 2, text='ein Aufruf nach Fortsetzen')
        self.assertEqual(len(self.aufrufe('claude')), 2)
        self.assertEqual(len([l for l in laeufe.alle() if l['zustand'] in laeufe.AKTIV]), 1)

    def test_schutz_ohne_aenderung(self):
        lid = self.lauf_starten()
        roh = TestClient(web.anwendung(), follow_redirects=False)
        for pfad in (f'/lauf/{lid}/anhalten', '/alles/anhalten'):
            for kopf in ({**LOGIN}, {**LOGIN, 'Origin': 'https://boese.example'}, {'Origin': 'http://testserver'}):
                with self.subTest(pfad=pfad, kopf=kopf):
                    self.assertEqual(roh.post(pfad, headers=kopf).status_code, 403)
            with self.subTest(pfad=pfad, methode='GET'):
                self.assertEqual(self.client.get(pfad).status_code, 405)
        self.assertEqual(laeufe.laden(lid)['zustand'], 'laeuft')
        self.assertFalse(laeufe.angehalten())

    def test_unbekannter_lauf_und_unpassender_zustand(self):
        self.assertEqual(self.post('/lauf/gibtsnicht/anhalten').status_code, 404)
        self.assertEqual(self.post('/lauf/gibtsnicht/fortsetzen').status_code, 404)
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        l = ablauf.entwickeln(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        r = self.post(f'/lauf/{l["id"]}/fortsetzen')  # fertig: klare Meldung statt Absturz
        self.assertEqual(r.status_code, 409)
        self.assertIn('fortsetzen geht nur nach Anhalten oder Fehler', r.text)

    def test_alles_anhalten_und_fortsetzen(self):
        lid = self.lauf_starten()
        s = self.client.get('/').text
        self.assertIn('action="/alles/anhalten"', s)
        self.assertIn('data-bestaetigen=', s)
        r = self.post('/alles/anhalten')
        self.assertEqual((r.status_code, r.headers['location']), (303, '/?meldung=alles-angehalten'))
        self.assertTrue(laeufe.angehalten())
        self.assertEqual(laeufe.laden(lid)['zustand'], 'angehalten')
        self.warten(lambda: not laeufe.lebt(laeufe.laden(lid)), sekunden=10, text='Prozessende')
        s = self.client.get(r.headers['location']).text
        self.assertIn('Alles angehalten', s)
        self.assertIn('action="/alles/fortsetzen"', s)
        self.assertNotIn('action="/alles/anhalten"', s)
        self.assertIn('devflow ist angehalten', s)
        # einzelnes Fortsetzen ist gesperrt, solange alles angehalten ist
        self.assertNotIn(f'action="/lauf/{lid}/fortsetzen"', s)
        r2 = self.post(f'/lauf/{lid}/fortsetzen')
        self.assertEqual(r2.status_code, 409)
        self.assertIn('angehalten', r2.text)
        self.assertEqual(laeufe.laden(lid)['zustand'], 'angehalten')
        # zweimal anhalten schadet nicht
        self.assertEqual(self.post('/alles/anhalten').status_code, 303)
        r = self.post('/alles/fortsetzen')
        self.assertEqual((r.status_code, r.headers['location']), (303, '/?meldung=alles-fortgesetzt'))
        self.assertFalse(laeufe.angehalten())
        s = self.client.get(r.headers['location']).text
        self.assertIn('Neue Läufe sind wieder erlaubt', s)
        self.assertIn('action="/alles/anhalten"', s)
        self.assertIn(f'action="/lauf/{lid}/fortsetzen"', s)  # der angehaltene Lauf wartet auf den Klick des Inhabers
        self.assertEqual(self.post('/alles/fortsetzen').status_code, 303)
        self.assertEqual(self.post(f'/lauf/{lid}/fortsetzen').status_code, 303)
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt', text='Kette nach Fortsetzen')


class MeldungTest(Basis):
    def test_unbekannte_meldung_zeigt_nichts(self):
        s = self.client.get('/?meldung=<script>alert(1)</script>').text
        self.assertNotIn('role="status"', s)
        self.assertNotIn('alert(1)', s)


if __name__ == '__main__':
    unittest.main()
