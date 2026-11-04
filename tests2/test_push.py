"""Web-Push (S2.4): Verschlüsselung (RFC 8291/8292), Schlüssel, Ereignisse, Versand mit Attrappe, Routen."""
import base64
import contextlib
import io
import hashlib
import hmac
import json
import os
import stat
import tempfile
import unittest
from unittest import mock
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import ablauf, push, push_protokoll as pp, web  # noqa: E402
from hilfen import Fall  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
HERKUNFT = {**LOGIN, 'Origin': 'http://testserver'}


def dec(wert):
    return base64.urlsafe_b64decode(wert + '=' * (-len(wert) % 4))


def geraet(name='x'):
    """Ein Empfänger: (privater Schlüssel, Abo)."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key, {'endpoint': f'https://fcm.googleapis.com/fcm/send/{name}-TOKEN:abc',
                 'keys': {'p256dh': pp.b64(pp.oeffentlicher_schluessel(key)), 'auth': pp.b64(os.urandom(16))}}


def entschluesseln(roh, key, sub):
    """Unabhängig vom Produktionscode (HMAC statt HKDF-Klasse) nach RFC 8291 entschlüsseln; gibt den Klartext zurück."""
    salt, laenge = roh[:16], roh[20]
    absender = roh[21:21 + laenge]
    gemeinsam = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), absender))
    mac = lambda k, v: hmac.new(k, v, hashlib.sha256).digest()
    ikm = mac(mac(dec(sub['keys']['auth']), gemeinsam), b'WebPush: info\0' + dec(sub['keys']['p256dh']) + absender + b'\1')
    prk = mac(salt, ikm)
    klar = AESGCM(mac(prk, b'Content-Encoding: aes128gcm\0\1')[:16]).decrypt(mac(prk, b'Content-Encoding: nonce\0\1')[:12], roh[21 + laenge:], None)
    return klar.rstrip(b'\0')[:-1]


class Attrappe:
    """Push-Dienst: merkt sich jeden Versand, antwortet mit `status` (je Endpunkt änderbar)."""

    def __init__(self):
        self.aufrufe, self.status, self.fehler = [], {}, set()

    def __call__(self, url, kopf, inhalt):
        self.aufrufe.append((url, kopf, inhalt))
        if url in self.fehler:
            raise OSError('Netz weg')
        return self.status.get(url, 201)


class ProtokollTest(unittest.TestCase):
    def test_rfc8291_anhang_a_ganzer_testvektor(self):
        sub = {'endpoint': 'https://fcm.googleapis.com/fcm/send/x',
               'keys': {'p256dh': 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4', 'auth': 'BTBZMqHH6r4Tts7J_aSIgg'}}
        roh = pp.verschluesseln(b'When I grow up, I want to be a watermelon', sub, salt=dec('DGv6ra1nlYgDCS1FRnbzlw'),
                                fluechtig=pp.privater_schluessel(dec('yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw')))
        self.assertEqual(pp.b64(roh), 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_'
                                      'yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN')

    def test_hinweis_rundreise_gleich_lang_und_zufaellig(self):
        key, sub = geraet()
        a = pp.verschluesseln_hinweis(pp.nutzlast(2, 'frage'), sub)
        b = pp.verschluesseln_hinweis(pp.nutzlast(2, 'frage'), sub)
        c = pp.verschluesseln_hinweis(pp.nutzlast(9999, 'fehler'), sub)
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), len(c))
        self.assertEqual(json.loads(entschluesseln(a, key, sub)), {'count': 2, 'kind': 'frage', 'url': '/'})
        with self.assertRaises(pp.PushFehler):
            pp.verschluesseln_hinweis({'count': 1, 'kind': 'frage', 'url': '/', 'titel': 'GEHEIM'}, sub)
        for schlecht in ((0, 'frage'), (1, 'andere'), (True, 'frage')):
            with self.assertRaises(pp.PushFehler):
                pp.nutzlast(*schlecht)

    def test_vapid_signatur_audience_ablauf(self):
        roh = os.urandom(32)
        privat = pp.privater_schluessel(roh)
        sub = geraet()[1]
        kopf = pp.vapid(roh, sub['endpoint'], 'https://server.example.net:8472', 1000)
        token, oeffentlich = kopf.removeprefix('vapid t=').split(', k=')
        h, c, s = token.split('.')
        self.assertEqual(json.loads(dec(h)), {'alg': 'ES256', 'typ': 'JWT'})
        self.assertEqual(json.loads(dec(c)), {'aud': 'https://fcm.googleapis.com', 'exp': 4600, 'sub': 'https://server.example.net:8472'})
        sig = dec(s)
        privat.public_key().verify(encode_dss_signature(int.from_bytes(sig[:32], 'big'), int.from_bytes(sig[32:], 'big')),
                                   f'{h}.{c}'.encode(), ec.ECDSA(hashes.SHA256()))
        self.assertEqual(dec(oeffentlich), pp.oeffentlicher_schluessel(privat))
        with self.assertRaises(pp.PushFehler):
            pp.vapid(roh, sub['endpoint'], 'http://unsicher.example', 1000)

    def test_endpunkt_und_schluessel_werden_streng_geprueft(self):
        sub = geraet()[1]
        for url in ('http://fcm.googleapis.com/fcm/send/x', 'https://127.0.0.1/x', 'https://fcm.googleapis.com:443/x',
                    'https://fcm.googleapis.com.boese.test/x', 'https://fcm.googleapis.com/x?q=1', 'https://u@fcm.googleapis.com/x',
                    'https://fcm.googleapis.com/../x', 'https://fcm.googleapis.com/x\r\nX: y'):
            with self.subTest(url=url), self.assertRaises(pp.PushFehler):
                pp.abo(dict(sub, endpoint=url))
        for keys in ({'auth': 'kurz', 'p256dh': sub['keys']['p256dh']}, {'auth': sub['keys']['auth'], 'p256dh': pp.b64(b'\4' + bytes(64))}):
            with self.assertRaises(pp.PushFehler):
                pp.abo(dict(sub, keys=keys))
        with self.assertRaises(pp.PushFehler):
            pp.abo(dict(sub, expirationTime=1))


class Basis(Fall):
    def setUp(self):
        super().setUp()
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')
        self.dienst = Attrappe()
        self.client = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)

    def melden(self, name='a', gerat=None):
        key, sub = gerat or geraet(name)
        r = self.client.post('/hinweise/abo', json=sub, headers={'User-Agent': 'Mozilla/5.0 (iPhone) Safari/604'})
        self.assertEqual(r.status_code, 200, r.text)
        return key, sub

    def hinweise(self, *geraete):
        """Alle bisher gesendeten Hinweise als (Endpunkt, Klartext-JSON), je Gerät entschlüsselt."""
        schluessel = {s['endpoint']: (k, s) for k, s in geraete}
        return [(u, json.loads(entschluesseln(b, *schluessel[u]))) for u, _, b in self.dienst.aufrufe]


class SchluesselTest(Fall):
    def test_schluessel_entsteht_mit_0600_und_bleibt(self):
        pfad = push.konfig() / 'vapid.key'
        self.assertFalse(pfad.exists())
        roh = push.schluessel()
        self.assertEqual(len(roh), 32)
        self.assertEqual(stat.S_IMODE(pfad.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(pfad.parent.stat().st_mode), 0o700)
        self.assertEqual(push.schluessel(), roh)
        pfad.chmod(0o644)
        self.assertEqual(push.schluessel(), roh)
        self.assertEqual(stat.S_IMODE(pfad.stat().st_mode), 0o600)
        self.assertEqual(dec(push.oeffentlich())[0], 4)
        self.assertEqual([p.name for p in pfad.parent.iterdir()], ['vapid.key'])

    def test_kaputter_schluessel_wird_nicht_ersetzt(self):
        pfad = push.konfig() / 'vapid.key'
        push.schluessel()
        pfad.write_text('kaputt\n')
        with self.assertRaises(ablauf.Fehler):
            push.schluessel()
        self.assertEqual(pfad.read_text(), 'kaputt\n')


class EreignisTest(Basis):
    def test_neue_frage_ergibt_genau_einen_hinweis_ohne_texte(self):
        g = self.melden()
        self.assertEqual(push.pruefen(self.dienst), [])  # erster Lauf: nur Stand merken
        self.assertEqual(self.dienst.aufrufe, [])
        ablauf.frage_stellen(self.p, 'GEHEIMFRAGE zu Wetter?', ['Ja', 'Nein'], 'Ja', 'A1')  # A1 → Braucht dich + Frage
        self.assertEqual(push.pruefen(self.dienst), [('frage', 1)])
        self.assertEqual(self.hinweise(g), [(g[1]['endpoint'], {'count': 1, 'kind': 'frage', 'url': '/'})])
        url, kopf, inhalt = self.dienst.aufrufe[0]
        self.assertEqual(kopf['Content-Encoding'], 'aes128gcm')
        self.assertTrue(kopf['Authorization'].startswith('vapid t='))
        # Der Inhalt ist verschlüsselt (Klartext siehe oben), Signatur und Schlüssel im Kopf sind
        # Zufallsbytes: kurze Muster wie b'A1' kämen dort zufällig vor. Deshalb nur lange Wörter
        # im Chiffrat und die lesbaren Teile des Kopfs (Felder und JWT-Ansprüche) prüfen.
        jwt = kopf['Authorization'].split('t=')[1].split(',')[0].split('.')
        lesbar = json.dumps({k: v for k, v in kopf.items() if k != 'Authorization'}).encode()
        lesbar += base64.urlsafe_b64decode(jwt[0] + '==') + base64.urlsafe_b64decode(jwt[1] + '==')
        for geheim in (b'GEHEIM', b'demo', b'A1', b'Wetter'):
            self.assertNotIn(geheim, lesbar)
        for geheim in (b'GEHEIM', b'Wetter'):
            self.assertNotIn(geheim, inhalt)
        # dasselbe Ereignis, auch nach Neustart (Stand liegt in push.json): kein zweiter Hinweis
        self.assertEqual(push.pruefen(self.dienst), [])
        self.assertEqual(len(self.dienst.aufrufe), 1)
        self.assertEqual(stat.S_IMODE((self.tmp / 'state' / 'push.json').stat().st_mode), 0o600)

    def test_braucht_dich_ohne_frage_und_fehlgeschlagener_lauf(self):
        g = self.melden()
        push.pruefen(self.dienst)
        ablauf.status_setzen(self.p, 'A2', 'Braucht dich', details='GEHEIMDETAIL')
        self.assertEqual(push.pruefen(self.dienst), [('braucht', 1)])
        lauf = {'id': 'l1', 'projekt': 'demo', 'art': 'entwickeln', 'aufgabe': 'A1', 'zustand': 'fehler', 'modell': 'm', 'fehler': 'GEHEIM'}
        with mock.patch('devflow2.push_ereignisse.laeufe.alle', return_value=[lauf]):
            self.assertEqual(push.pruefen(self.dienst), [('fehler', 1)])
            self.assertEqual(push.pruefen(self.dienst), [])
        self.assertEqual([h[1]['kind'] for h in self.hinweise(g)], ['braucht', 'fehler'])
        self.assertNotIn(b'GEHEIM', b''.join(a[2] for a in self.dienst.aufrufe))

    def test_mehrere_neue_ereignisse_einer_art_ergeben_einen_hinweis_mit_anzahl(self):
        g = self.melden()
        push.pruefen(self.dienst)
        ablauf.frage_stellen(self.p, 'eins?', [], '', 'A1')
        ablauf.frage_stellen(self.p, 'zwei?', [], '', 'A2')
        self.assertEqual(push.pruefen(self.dienst), [('frage', 2)])
        self.assertEqual([h[1]['count'] for h in self.hinweise(g)], [2])

    def test_bestehendes_beim_ersten_lauf_ist_nicht_neu_und_erledigtes_kommt_wieder(self):
        ablauf.frage_stellen(self.p, 'alt?', [], '', 'A1')
        self.melden()
        self.assertEqual(push.pruefen(self.dienst), [])
        ablauf.antwort_eintragen(self.p, 1, 'ja')
        self.assertEqual(push.pruefen(self.dienst), [])
        ablauf.frage_stellen(self.p, 'neu?', [], '', 'A1')
        self.assertEqual(push.pruefen(self.dienst), [('frage', 1)])

    def test_unlesbares_projekt_laesst_seine_ereignisse_nicht_neu_werden(self):
        self.melden()
        ablauf.frage_stellen(self.p, 'alt?', [], '', 'A1')
        push.pruefen(self.dienst)
        roadmap = self.repo / 'ROADMAP.md'
        text = roadmap.read_text()
        roadmap.rename(self.repo / 'weg.md')
        self.assertEqual(push.pruefen(self.dienst), [])
        (self.repo / 'weg.md').rename(roadmap)
        self.assertEqual(roadmap.read_text(), text)
        self.assertEqual(push.pruefen(self.dienst), [])
        self.assertEqual(self.dienst.aufrufe, [])

    def test_abgelaufenes_abo_wird_entfernt_andere_fehler_nicht(self):
        a, b, c = self.melden('a'), self.melden('b'), self.melden('c')
        push.pruefen(self.dienst)
        self.dienst.status = {a[1]['endpoint']: 410, b[1]['endpoint']: 404}
        ablauf.frage_stellen(self.p, 'x?', [], '', 'A1')
        push.pruefen(self.dienst)
        self.assertEqual([g['id'] for g in push.geraete()], [pp.abo_id(c[1]['endpoint'])])
        d = self.melden('d')
        self.dienst.status = {d[1]['endpoint']: 500}
        self.dienst.fehler = {c[1]['endpoint']}
        ablauf.frage_stellen(self.p, 'y?', [], '', 'A2')
        with contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(push.pruefen(self.dienst), [('frage', 1)])  # Netzfehler und 500 brechen nichts ab
        self.assertNotIn('fcm.googleapis.com', log.getvalue())  # Endpunkte (Geheimnisse) stehen nicht im Log
        self.assertEqual(len(push.geraete()), 2)

    def test_ohne_geraet_wird_nichts_gesendet_aber_stand_gemerkt(self):
        push.pruefen(self.dienst)
        ablauf.frage_stellen(self.p, 'x?', [], '', 'A1')
        self.assertEqual(push.pruefen(self.dienst), [('frage', 1)])
        self.assertEqual(self.dienst.aufrufe, [])
        self.melden()
        self.assertEqual(push.pruefen(self.dienst), [])


class RoutenTest(Basis):
    def test_manifest_und_service_worker(self):
        m = self.client.get('/manifest.webmanifest')
        self.assertEqual(m.headers['content-type'], 'application/manifest+json')
        d = m.json()
        self.assertEqual((d['display'], d['start_url'], d['scope']), ('standalone', '/', '/'))
        for i in d['icons']:
            r = self.client.get(i['src'])
            self.assertEqual((r.status_code, r.headers['content-type'], r.content[:4]), (200, 'image/png', b'\x89PNG'))
        sw = self.client.get('/sw.js')
        self.assertEqual((sw.status_code, sw.headers['content-type'].split(';')[0]), (200, 'text/javascript'))
        self.assertIn("addEventListener('push'", sw.text)
        self.assertEqual(self.client.get('/sw.js').text, sw.text)  # ohne Änderung an den Dateien bleibt der Worker gleich
        kopf = self.client.get('/').text
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', kopf)
        self.assertIn('href="/hinweise"', kopf)
        self.assertEqual(TestClient(web.anwendung()).get('/sw.js').status_code, 403)  # auch hier nur mit Login

    def test_seite_anmelden_abmelden_probe_entfernen(self):
        s = self.client.get('/hinweise').text
        self.assertIn('data-schluessel="' + push.oeffentlich() + '"', s)
        self.assertIn('Noch kein Gerät angemeldet.', s)
        a, b = self.melden('a'), self.melden('b')
        self.melden(gerat=a)  # derselbe Endpunkt ersetzt statt zu verdoppeln
        s = self.client.get('/hinweise').text
        self.assertEqual(s.count('action="/hinweise/entfernen"'), 2)
        self.assertIn('iPhone · Safari', s)
        self.assertNotIn('fcm.googleapis.com', s)
        self.assertEqual(stat.S_IMODE((self.tmp / 'state' / 'push.json').stat().st_mode), 0o600)
        with mock.patch('devflow2.push.post', self.dienst):
            r = self.client.post('/hinweise/test', json={'endpoint': a[1]['endpoint']})
        self.assertEqual(r.json(), {'ok': True, 'status': 201})
        self.assertEqual(self.hinweise(a, b), [(a[1]['endpoint'], {'count': 1, 'kind': 'test', 'url': '/'})])  # nur dieses Gerät
        self.assertEqual(self.client.post('/hinweise/test', json={'endpoint': 'https://fcm.googleapis.com/fcm/send/unbekannt'}).status_code, 404)
        status = lambda g: self.client.post('/hinweise/status', json={'endpoint': g[1]['endpoint']}).json()
        self.assertEqual(status(a), {'angemeldet': True})
        r = self.client.post('/hinweise/abmelden', json={'endpoint': a[1]['endpoint']})
        self.assertEqual(r.json(), {'ok': True, 'war_angemeldet': True})
        self.assertEqual(status(a), {'angemeldet': False})  # Browser-Abo bleibt, Server kennt es nicht: Seite bietet Anmeldung an
        self.assertEqual(self.client.post('/hinweise/status', json={}).status_code, 400)
        self.assertEqual([g['id'] for g in push.geraete()], [pp.abo_id(b[1]['endpoint'])])
        r = self.client.post('/hinweise/entfernen', data={'id': pp.abo_id(b[1]['endpoint'])})
        self.assertEqual((r.status_code, r.headers['location']), (303, '/hinweise?meldung=entfernt'))
        self.assertEqual(push.geraete(), [])
        r = self.client.post('/hinweise/entfernen', data={'id': 'weg'})
        self.assertEqual(r.headers['location'], '/hinweise?meldung=unbekannt')

    def test_ungueltige_und_fremde_anfragen_aendern_nichts(self):
        sub = geraet()[1]
        post = self.client.post
        self.assertEqual(post('/hinweise/abo', json=dict(sub, endpoint='https://boese.example/x')).status_code, 400)
        self.assertEqual(post('/hinweise/abo', json=dict(sub, extra=1)).status_code, 400)
        self.assertEqual(post('/hinweise/abo', json=[sub]).status_code, 400)
        self.assertEqual(post('/hinweise/abo', content=b'{kaputt', headers={'content-type': 'application/json'}).status_code, 400)
        self.assertEqual(post('/hinweise/abo', data={'a': 'b'}).status_code, 415)
        self.assertEqual(post('/hinweise/abo', content=b'{"x":"' + b'a' * 40000 + b'"}', headers={'content-type': 'application/json'}).status_code, 413)
        self.assertEqual(self.client.get('/hinweise/abo').status_code, 405)
        fremd = TestClient(web.anwendung(), headers={**LOGIN, 'Origin': 'https://boese.example'})
        self.assertEqual(fremd.post('/hinweise/abo', json=sub).status_code, 403)
        ohne = TestClient(web.anwendung(), headers=LOGIN)
        self.assertEqual(ohne.post('/hinweise/abo', json=sub).status_code, 403)
        self.assertEqual(push.geraete(), [])

    def test_zu_viele_geraete(self):
        for i in range(pp.MAX_GERAETE):
            self.melden(f'g{i}')
        key, sub = geraet('zuviel')
        self.assertEqual(self.client.post('/hinweise/abo', json=sub).status_code, 400)
        self.assertEqual(len(push.geraete()), pp.MAX_GERAETE)

    def test_dienst_prueft_im_hintergrund(self):
        self.melden()
        with mock.patch.dict(os.environ, {'DEVFLOW2_PUSH_TAKT': '0.05'}):
            with TestClient(web.anwendung(hinweise=True), headers=HERKUNFT):
                self.warten(lambda: (self.tmp / 'state' / 'push.json').exists() and json.loads((self.tmp / 'state' / 'push.json').read_text())['gemeldet'] is not None,
                            text='bis die Prüfung lief')


if __name__ == '__main__':
    unittest.main()
