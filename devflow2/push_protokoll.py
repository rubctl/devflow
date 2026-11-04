"""Web-Push-Format nach RFC 8291 (Verschlüsselung, aes128gcm) und RFC 8292 (VAPID), ohne IO.

Übernommen und umgeschrieben aus `devflow/push_protocol.py`. Ein Hinweis trägt nur Anzahl, Art (feste Liste) und den
Wurzellink, nie Projekt- oder Aufgabentexte: Die Push-Dienste von Google, Apple und Mozilla sehen nur Geheimtext, aber
sie vermitteln ihn.
"""
import base64
import hashlib
import json
import re
import secrets
import struct
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ANBIETER = frozenset(('fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'))
ARTEN = {'braucht': 'Aufgabe braucht dich', 'frage': 'Neue offene Frage', 'fehler': 'Lauf fehlgeschlagen',
         'budget': 'Selbstlauf: Tagesbudget verbraucht', 'pflege': 'Pflege: Sicherheitslücke', 'traum': 'Traum beendet', 'test': 'Probehinweis'}
MAX_GERAETE = 12
SATZ = 4096  # Recordgröße im Kopf; der Hinweis passt in einen Record


class PushFehler(ValueError):
    pass


def pruefen(wert, text='Ungültige Push-Daten'):
    if not wert:
        raise PushFehler(text)


def b64(roh):
    return base64.urlsafe_b64encode(roh).decode().rstrip('=')


def unb64(wert, groesse):
    pruefen(isinstance(wert, str) and re.fullmatch(r'[A-Za-z0-9_-]+', wert) and len(wert) < 200)
    try:
        roh = base64.urlsafe_b64decode(wert + '=' * ((-len(wert)) % 4))
    except ValueError:
        raise PushFehler('Ungültiger Push-Schlüssel') from None
    pruefen(len(roh) == groesse and b64(roh) == wert, 'Ungültiger Push-Schlüssel')
    return roh


def kodiert(wert):
    return json.dumps(wert, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def abo_id(endpunkt):
    return hashlib.sha256(endpunkt.encode()).hexdigest()[:16]


def endpunkt(wert):
    pruefen(isinstance(wert, str) and len(wert) <= 2048, 'Ungültiger Push-Endpunkt')
    try:
        u = urlsplit(wert)
    except ValueError:
        raise PushFehler('Ungültiger Push-Endpunkt') from None
    pruefen(u.scheme == 'https' and u.netloc in ANBIETER and u.hostname == u.netloc and not u.query and not u.fragment
            and re.fullmatch(r'/[A-Za-z0-9_~:./-]+', u.path) and all(v not in ('.', '..') for v in u.path.split('/')),
            'Push-Anbieter oder Endpunkt nicht erlaubt')
    return u


def abo(wert):
    """Prüft ein Abo, wie `PushSubscription.toJSON()` es liefert, und gibt nur Endpunkt und Schlüssel zurück."""
    pruefen(isinstance(wert, dict) and set(wert) <= {'endpoint', 'expirationTime', 'keys'}
            and {'endpoint', 'keys'} <= set(wert), 'Push-Abo als Objekt erforderlich')
    endpunkt(wert['endpoint'])
    keys = wert['keys']
    pruefen(isinstance(keys, dict) and set(keys) == {'p256dh', 'auth'})
    oeffentlich = unb64(keys['p256dh'], 65)
    unb64(keys['auth'], 16)
    pruefen(oeffentlich[0] == 4, 'Ungültiger Push-Schlüssel')
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), oeffentlich)
    except ValueError:
        raise PushFehler('Ungültiger Push-Schlüssel') from None
    ende = wert.get('expirationTime')
    pruefen(ende is None or type(ende) in (int, float) and ende > time.time() * 1000, 'Push-Abo abgelaufen')
    return {'endpoint': wert['endpoint'], 'keys': dict(keys)}


def nutzlast(anzahl, art, zahlen=None):
    """`zahlen`: nur beim Hinweis `traum` die Anzahl der Vorschläge und Pläne, keine Texte."""
    pruefen(type(anzahl) is int and 1 <= anzahl <= 10000 and art in ARTEN)
    if art != 'traum' or zahlen is None:
        return {'count': anzahl, 'kind': art, 'url': '/'}
    pruefen(len(zahlen) == 2 and all(type(z) is int and 0 <= z <= 10000 for z in zahlen))
    return {'count': anzahl, 'kind': art, 'url': '/', 'zahlen': list(zahlen)}


def oeffentlicher_schluessel(privat):
    return privat.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def privater_schluessel(roh):
    pruefen(isinstance(roh, bytes) and len(roh) == 32, 'Ungültiger VAPID-Schlüssel')
    try:
        return ec.derive_private_key(int.from_bytes(roh, 'big'), ec.SECP256R1())
    except ValueError:
        raise PushFehler('Ungültiger VAPID-Schlüssel') from None


def verschluesseln(klartext, empfaenger, *, salt=None, fluechtig=None, auffuellen=0):
    """Ein aes128gcm-Record nach RFC 8291 (Abschnitt 3.4): `klartext` + 0x02, auf `auffuellen` Bytes Nullen aufgefüllt.

    `salt` und `fluechtig` (der Einmalschlüssel des Senders) sind nur für den Testvektor aus Anhang A einstellbar.
    """
    empfaenger = abo(empfaenger)
    ua = unb64(empfaenger['keys']['p256dh'], 65)
    auth = unb64(empfaenger['keys']['auth'], 16)
    fluechtig = fluechtig or ec.generate_private_key(ec.SECP256R1())
    absender = oeffentlicher_schluessel(fluechtig)
    gemeinsam = fluechtig.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua))
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth, info=b'WebPush: info\x00' + ua + absender).derive(gemeinsam)
    salt = salt or secrets.token_bytes(16)
    pruefen(len(salt) == 16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b'Content-Encoding: aes128gcm\x00').derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b'Content-Encoding: nonce\x00').derive(ikm)
    pruefen(len(klartext) < SATZ - 17)
    gepolstert = klartext + b'\x02' + b'\x00' * max(0, auffuellen - len(klartext))
    return salt + struct.pack('!IB', SATZ, len(absender)) + absender + AESGCM(cek).encrypt(nonce, gepolstert, None)


def verschluesseln_hinweis(wert, empfaenger):
    """Der Hinweis: nur `count`, `kind`, `url`, beim Traum zwei Zahlen; immer gleich lang, damit die Länge nichts verrät."""
    pruefen(isinstance(wert, dict) and set(wert) - {'zahlen'} == {'count', 'kind', 'url'}
            and nutzlast(wert['count'], wert['kind'], wert.get('zahlen')) == wert)
    roh = kodiert(wert)
    pruefen(len(roh) < 255)
    return verschluesseln(roh, empfaenger, auffuellen=255)


def vapid(roh_schluessel, ziel, kontakt, jetzt=None):
    """Wert der Kopfzeile `Authorization` nach RFC 8292 für den Push-Dienst unter `ziel`."""
    u = endpunkt(ziel)
    privat = privater_schluessel(roh_schluessel)
    jetzt = time.time() if jetzt is None else jetzt
    pruefen(isinstance(kontakt, str) and kontakt.startswith('https://') and len(kontakt) < 255
            and not any(v in kontakt for v in ('\r', '\n', '"', ' ')), 'Ungültiger VAPID-Kontakt')
    kopf = b64(kodiert({'typ': 'JWT', 'alg': 'ES256'}))
    inhalt = b64(kodiert({'aud': 'https://' + u.netloc, 'exp': int(jetzt) + 3600, 'sub': kontakt}))
    nachricht = (kopf + '.' + inhalt).encode()
    r, s = decode_dss_signature(privat.sign(nachricht, ec.ECDSA(hashes.SHA256())))
    return 'vapid t=' + nachricht.decode() + '.' + b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big')) + ', k=' + b64(oeffentlicher_schluessel(privat))
