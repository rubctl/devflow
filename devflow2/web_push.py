"""Handy und Web-Push im Dashboard (S2.4): Manifest, Service Worker, Seite „Hinweise“, Geräte an- und abmelden.

Der Browser holt sich auf `/hinweise` ein Push-Abo und schickt es als JSON (POST, wie alle schreibenden Anfragen mit
passender `Origin`). Ein Hinweis nennt später nur Anzahl und Art; was das heißt, steht im Service Worker (`sw.js`).
"""
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import Fehler, projekte, push
from .push_protokoll import PushFehler, abo_id
from .web_aktion import MAX_FORM, _formular, _knopf
from .web_html import grund, kicker, seite, t

MANIFEST = {
    'id': '/', 'name': 'devflow', 'short_name': 'devflow', 'lang': 'de', 'dir': 'ltr', 'start_url': '/', 'scope': '/', 'display': 'standalone',
    'description': 'Leitstand für die Arbeit der Modelle: was dich braucht, was läuft, Roadmap, Eingang und Kontingent.',
    'categories': ['productivity', 'developer tools'],
    'background_color': grund()[0], 'theme_color': grund()[0],
    'icons': [{'src': f'/static/icons/icon-{px}-{zweck}.png', 'sizes': f'{px}x{px}', 'type': 'image/png', 'purpose': zweck}
              for px in (192, 512) for zweck in ('any', 'maskable')],
}
STATIC = Path(__file__).with_name('static')
MELDUNG = {'entfernt': 'Gerät entfernt.', 'unbekannt': 'Dieses Gerät war nicht (mehr) angemeldet.'}


def _name(kopf):
    ua = kopf.get('user-agent', '')
    geraet = next((n for k, n in (('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'), ('Windows', 'Windows'),
                                  ('Macintosh', 'Mac'), ('Linux', 'Linux')) if k in ua), 'Gerät')
    browser = next((n for k, n in (('Edg/', 'Edge'), ('Firefox/', 'Firefox'), ('Chrome/', 'Chrome'), ('CriOS/', 'Chrome'),
                                   ('Safari/', 'Safari')) if k in ua), '')
    return f'{geraet} · {browser}' if browser else geraet


async def _json(request):
    roh = b''
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > MAX_FORM:
            raise HTTPException(413, 'Zu groß')
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'Erwartet JSON')
    try:
        wert = json.loads(roh)
    except ValueError:
        raise HTTPException(400, 'Kein gültiges JSON') from None
    if not isinstance(wert, dict):
        raise HTTPException(400, 'Objekt erwartet')
    return wert


def kurzbefehle():
    """Kurzbefehle der App (langes Drücken auf dem Symbol). Roadmap und Eingang gehören zu einem Projekt: das erste angemeldete,
    ohne Projekt fällt der Befehl auf die Übersicht zurück. „Braucht dich“ ist der erste Block der Übersicht."""
    erstes = next(iter(projekte.alle()), None)
    bei = f'/p/{quote(erstes, safe="")}' if erstes else None
    symbol = [{'src': '/static/icons/icon-192-any.png', 'sizes': '192x192', 'type': 'image/png'}]
    return [{'name': name, 'short_name': kurz, 'url': url, 'icons': symbol} for name, kurz, url in (
        ('Braucht dich', 'Braucht dich', '/'),
        ('Roadmap', 'Roadmap', f'{bei}/roadmap' if bei else '/'),
        ('Eingang', 'Eingang', f'{bei}#ideen' if bei else '/'),
        ('Kontingent', 'Kontingent', '/kontingent'))]


def manifest(request):
    return Response(json.dumps({**MANIFEST, 'shortcuts': kurzbefehle()}), media_type='application/manifest+json')


def stand():
    """Prüfsumme der statischen Dateien: Der Browser vergleicht `sw.js` Byte für Byte, so löst auch eine neue `app.js` die Meldung aus."""
    h = hashlib.sha256()
    for f in sorted(STATIC.rglob('*')):
        if f.is_file() and 'fonts' not in f.relative_to(STATIC).parts:
            h.update(f.relative_to(STATIC).as_posix().encode() + b'\0' + f.read_bytes())
    return h.hexdigest()[:16]


def service_worker(request):
    return Response((STATIC / 'sw.js').read_bytes() + f'\n// Stand der Dateien: {stand()}\n'.encode(), media_type='text/javascript')


def hinweise_seite(request):
    try:
        schluessel = push.oeffentlich()
        liste = push.geraete()
    except Fehler as err:
        return HTMLResponse(seite('Hinweise', t('h1', 'Hinweise'), t('p', f'Fehler: {err}', class_='fehler'),
                                  pfad=[('Hinweise', None)]), status_code=500)
    text = MELDUNG.get(request.query_params.get('meldung', ''))
    zeilen = [t('li', t('strong', g['name'] or 'Gerät'), ' ', t('span', f'seit {time.strftime("%d.%m.%Y", time.localtime(g["seit"]))}', class_='leise'),
                _knopf('/hinweise/entfernen', 'Entfernen', t('input', type='hidden', name='id', value=g['id']),
                       bestaetigen='Dieses Gerät aus der Liste entfernen?'))
              for g in liste]
    return HTMLResponse(seite(
        'Hinweise', t('h1', 'Hinweise'), t('p', text, class_='hinweis', role='status') if text else None,
        t('p', 'devflow meldet sich auf diesem Gerät, wenn eine Aufgabe „Braucht dich“ wird, eine neue Frage offen ist oder ein Lauf '
               'fehlschlägt. Der Hinweis nennt nur Anzahl und Art, nie Namen oder Texte.'),
        t('p', 'Auf dem iPhone geht das nur, wenn die Seite zum Home-Bildschirm hinzugefügt ist (Teilen → „Zum Home-Bildschirm“) '
               'und von dort geöffnet wird.', class_='leise'),
        t('section', t('p', 'Prüfe …', id='hinweis-status', role='status'),
          t('div', t('button', 'Hinweise erlauben', type='button', id='hinweis-an', hidden=True, class_='rc-btn rc-btn-primary'),
            t('button', 'Hinweise abschalten', type='button', id='hinweis-aus', hidden=True, class_='rc-btn'),
            t('button', 'Probehinweis senden', type='button', id='hinweis-test', hidden=True, class_='rc-btn'), class_='knopfreihe'),
          id='hinweise', data_schluessel=schluessel),
        kicker('Geräte mit Hinweisen'),
        t('ul', zeilen, class_='liste') if zeilen else t('p', 'Noch kein Gerät angemeldet.', class_='leise'),
        pfad=[('Hinweise', None)]))


async def abo_anmelden(request):
    wert = await _json(request)
    try:
        gid = await run_in_threadpool(push.anmelden, wert, _name(request.headers))
    except (PushFehler, Fehler) as err:
        return JSONResponse({'fehler': str(err)}, status_code=400)
    return JSONResponse({'ok': True, 'id': gid, 'geraete': len(await run_in_threadpool(push.geraete))})


async def abo_abmelden(request):
    wert = await _json(request)
    endpunkt = wert.get('endpoint')
    if not isinstance(endpunkt, str):
        return JSONResponse({'fehler': 'endpoint fehlt'}, status_code=400)
    da = await run_in_threadpool(push.abmelden, abo_id(endpunkt))
    return JSONResponse({'ok': True, 'war_angemeldet': da})


async def abo_status(request):
    wert = await _json(request)
    endpunkt = wert.get('endpoint')
    if not isinstance(endpunkt, str):
        return JSONResponse({'fehler': 'endpoint fehlt'}, status_code=400)
    liste = await run_in_threadpool(push.geraete)
    return JSONResponse({'angemeldet': abo_id(endpunkt) in {g['id'] for g in liste}})


async def probehinweis(request):
    wert = await _json(request)
    endpunkt = wert.get('endpoint')
    if not isinstance(endpunkt, str):
        return JSONResponse({'fehler': 'endpoint fehlt'}, status_code=400)
    try:
        status = await run_in_threadpool(push.probe, abo_id(endpunkt))
    except Fehler as err:
        return JSONResponse({'fehler': str(err)}, status_code=404)
    return JSONResponse({'ok': status is not None and 200 <= status < 300, 'status': status})


async def geraet_entfernen(request):
    gid = (await _formular(request)).get('id', '')
    da = await run_in_threadpool(push.abmelden, gid)
    return RedirectResponse(f'/hinweise?meldung={"entfernt" if da else "unbekannt"}', status_code=303)


def routen():
    post = ['POST']
    return [
        Route('/manifest.webmanifest', manifest),
        Route('/sw.js', service_worker),
        Route('/hinweise', hinweise_seite),
        Route('/hinweise/abo', abo_anmelden, methods=post),
        Route('/hinweise/abmelden', abo_abmelden, methods=post),
        Route('/hinweise/status', abo_status, methods=post),
        Route('/hinweise/test', probehinweis, methods=post),
        Route('/hinweise/entfernen', geraet_entfernen, methods=post),
    ]
