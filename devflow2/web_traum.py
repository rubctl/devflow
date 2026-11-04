"""Seite „Traum“ (S5.3, S5.4): Knöpfe „Träumen“ und „Traum beenden“, Stand des laufenden Traums, Vorschläge mit „Übernehmen“ und „Verwerfen“,
alles, was in früheren Träumen passiert ist.

Die Seite liest nur den Zustand aus `traum.py` und `vorschlag.py`. Geschrieben wird nur über `traum.starten`, `traum.beenden`, `vorschlag.uebernehmen`
und `vorschlag.verwerfen`; ein Traum startet nur hier, ein Vorschlag wird nur hier entschieden. „Übernehmen“ trägt den angezeigten Commit mit.
"""
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.routing import Route

from . import Fehler, kontingent, traum, vorschlag, web_html
from .projekte import NAMEN, Projekt
from .web_aktion import NAME, SPERRE, ZUGANG, _formular, _knopf, _nicht_moeglich, _weiter, meldung
from .web_html import t
from .web_md import md

SCHRITT_ART = {'fertig': 'ok', 'laeuft': 'aktiv', 'wiederholen': 'warn', 'fehler': 'error', 'ausgelassen': 'idle', 'abgebrochen': 'warn'}
SCHRITT_TEXT = {'fertig': 'fertig', 'laeuft': 'läuft', 'wiederholen': 'wird wiederholt', 'fehler': 'Fehler', 'ausgelassen': 'ausgelassen', 'abgebrochen': 'abgebrochen'}
VORSCHLAG_TEXT = {'entsteht': ('entsteht', 'aktiv'), 'offen': ('wartet auf dich', 'warn'), 'uebernommen': ('übernommen, in Prüfung', 'aktiv'), 'gemergt': ('gemergt', 'ok'),
                  'verworfen': ('verworfen', 'idle'), 'gescheitert': ('gescheitert', 'idle')}


def _w():
    from . import web  # erst beim Aufruf: web importiert dieses Modul
    return web


def baustein():
    """Stand und Knopf auf Übersicht und Traumseite. Der Text erklärt, warum gerade kein Traum starten kann."""
    w = _w()
    a = traum.aktiv()
    if a:
        text = f'Läuft bis höchstens {w.zeit(a["fenster_ende"])} · ' + ' · '.join(f'{NAMEN[m]} {n} Modellstarts' for m, n in a['zaehler'].items())
        if a.get('stopp'):
            return t('div', t('p', web_html.status('Traum endet', 'warn')), t('p', f'{a["stopp"]}. Der laufende Schritt läuft zu Ende.', class_='leise'))
        return t('div', t('p', web_html.status('Traum läuft', 'aktiv')), t('p', text, class_='leise'),
                 _knopf('/traum/beenden', 'Traum beenden', bestaetigen='Traum beenden? Der laufende Schritt läuft noch zu Ende.'))
    grund = traum.startgrund()
    if grund:
        return t('div', t('p', web_html.status('Kein Traum möglich', 'idle')), t('p', grund, class_='hinweis'))
    return t('div', t('p', web_html.status('Bereit', 'idle')), t('p', 'Höchstens der Rest des laufenden 5-Stunden-Fensters; gemergt wird nichts ohne dich.', class_='leise'),
             _knopf('/traum/starten', 'Träumen', bestaetigen='Den Rest dieses 5-Stunden-Fensters für den Traum nutzen?'))


def _prozent(f):
    return '–' if f['prozent'] is None else f'{f["prozent"]:g} %'


def _grenzen():
    """Je Modell Messung und Grenzen, wie `traum.tor` sie prüft."""
    stand = kontingent.stand()
    zeilen = []
    for m, g in traum.grenzen().items():
        zeilen.append(t('tr', t('td', NAMEN[m]), t('td', f'{_prozent(stand[m]["5h"])} von {g["grenze"]:g} %'),
                        t('td', f'{_prozent(stand[m]["woche"])}, Reserve {g["reserve"]:g} %')))
    return t('table', t('thead', t('tr', t('th', 'Modell'), t('th', '5 Stunden'), t('th', 'Woche'))), t('tbody', zeilen), class_='stufen')


def _schritt(s):
    w = _w()
    ls = [w.lauf_link({'id': lid}, f'Lauf {i}') for i, lid in enumerate(s['laeufe'], 1)]
    return t('tr', t('td', traum.TITEL[s['art']], t('div', s['datei'], class_='leise') if s.get('datei') else None), t('td', s['projekt']),
             t('td', web_html.status(SCHRITT_TEXT.get(s['zustand'], s['zustand']), SCHRITT_ART.get(s['zustand'], 'idle'))),
             t('td', [[l, ' '] for l in ls] or '–'), t('td', s.get('grund') or '', class_='leise'))


def _vorschlag_stand(v, p):
    """(Commit, Text zu Tests und Review) für genau den Stand, den der Branch gerade hat."""
    stand = vorschlag.kopf(p, v)
    r = vorschlag.review_stand(p, v, stand) if stand else None
    review = {'bestanden': 'Gegenreview bestanden', 'nacharbeit': 'Gegenreview verlangt Nacharbeit'}.get(r and r['urteil'], 'noch kein Gegenreview')
    return stand, r, ' · '.join(x for x in ({'gruen': 'Tests grün', 'rot': 'Tests rot'}.get(v.get('test')), review) if x)


def _uebernehmen_knopf(v, stand, bestanden):
    if not stand:
        return t('p', f'Branch {v["branch"]} gibt es nicht mehr.', class_='fehler')
    folge = 'Der Merge startet sofort.' if bestanden else 'Das Gegenreview startet; gemergt wird erst, wenn es besteht.'
    return t('div', t('p', folge, class_='leise'), _knopf(f'/traum/vorschlag/{quote(v["id"])}/uebernehmen', 'Übernehmen', t('input', type='hidden', name='gesehen', value=stand),
                                                          bestaetigen=f'Genau den Stand {stand[:7]} übernehmen?', klasse='rc-btn-primary'))


def _verwerfen_knopf(v):
    return _knopf(f'/traum/vorschlag/{quote(v["id"])}/verwerfen', 'Verwerfen', bestaetigen=f'Branch {v["branch"]} und seinen Worktree entfernen, auch ungemergt?')


def _vorschlag(v):
    """Ein Vorschlag mit dem Stand, über den der Inhaber entscheidet."""
    p = Projekt(v['projekt'])
    stand, r, pruefung = _vorschlag_stand(v, p)
    text, art = VORSCHLAG_TEXT[v['zustand']]
    teile = [t('h3', v['titel']), t('p', web_html.status(text, art)),
             t('p', f'{v["projekt"]} · {v["branch"]} · Stand {(stand or v["commit"] or "–")[:7]} · {pruefung}', class_='leise'),
             t('p', v['hinweis'], class_='hinweis') if v.get('hinweis') else None]
    if v.get('bericht'):
        stat = vorschlag.diffstat(p, v, stand) if stand else ''
        teile.append(t('details', t('summary', 'Bericht und Änderungen'), md(v['bericht']), t('pre', stat) if stat else None))
    if v['zustand'] == 'offen':
        teile.append(_uebernehmen_knopf(v, stand, bool(r and r['urteil'] == 'bestanden')))
    return t('article', *teile, _verwerfen_knopf(v), class_='rc-card', id=v['id'])


def vorschlaege_abschnitt():
    w = _w()
    karten = []
    vs = [v for v in vorschlag.alle() if v['zustand'] in ('offen', 'uebernommen')]
    for v in vs:
        try:
            karten.append(_vorschlag(v))
        except (Fehler, OSError) as err:
            karten.append(t('article', t('h3', v['titel']), t('p', f'Fehler: {err}', class_='fehler'), class_='rc-card'))
    return w.abschnitt('Vorschläge', karten or w.leer('Kein Vorschlag wartet.'), anzahl=len(vs), id='vorschlaege')


def _vorschlag_zeile(v):
    text, art = VORSCHLAG_TEXT[v['zustand']]
    return t('li', web_html.status(text, art), ' ', v['titel'], ' ', t('span', f'{v["projekt"]} · {v["branch"]}' + (f' · {v["grund"]}' if v.get('grund') else ''), class_='leise'))


def _traum_block(x, offen=False):
    w = _w()
    kopf = (f'Traum {x["id"]}', ' ', t('span', traum.bilanz(x) if x['zustand'] == 'beendet' else 'läuft', class_='leise'))
    teile = [t('p', f'Start {w.zeit(x["start"])} · Fensterende {w.zeit(x["fenster_ende"])}'
                    + (f' · Ende {w.zeit(x.get("ende"))}: {x.get("grund")}' if x['zustand'] == 'beendet' else '')
                    + ' · ' + ' · '.join(f'{NAMEN[m]} {n} Modellstarts' for m, n in x['zaehler'].items()), class_='leise')]
    b = x.get('bericht') or {}
    if b.get('datei'):
        teile.append(t('p', w.md_link(b['projekt'], b['datei'], text='Traumbericht')))
    elif b.get('fehler'):
        teile.append(t('p', f'Bericht nicht im Repo: {b["fehler"]}', class_='fehler'))
    if traum.plaene_von(x):
        teile.append(t('p', 'Planentwürfe: ', ', '.join(traum.plaene_von(x))))
    vs = vorschlag.von_traum(x['id'])
    if vs:
        teile.append(t('ul', [_vorschlag_zeile(v) for v in vs], class_='liste'))
    if x['schritte']:
        teile.append(t('table', t('thead', t('tr', *[t('th', k) for k in ('Schritt', 'Projekt', 'Zustand', 'Läufe', 'Hinweis')])),
                       t('tbody', [_schritt(s) for s in x['schritte']]), class_='stufen'))
    else:
        teile.append(w.leer('Kein Schritt begonnen.'))
    return t('details', t('summary', *kopf), *teile, open=offen or None)


def traum_seite(request):
    w = _w()
    ts = traum.alle()[::-1]
    return w.antwort('Traum', t('h1', 'Traum'),
                     t('p', 'Von Hand gestartet, höchstens das laufende 5-Stunden-Fenster: Rückblick der Woche, Pflegeprüfung, Ideen ausarbeiten, Code pflegen. '
                            'Normale Arbeit hat Vorrang; gemergt wird nichts ohne dich.', class_='leise'),
                     meldung(request), w.abschnitt('Jetzt', baustein(), _grenzen(), id='jetzt'), vorschlaege_abschnitt(),
                     w.abschnitt('Träume', [_traum_block(x, offen=i == 0) for i, x in enumerate(ts)] or w.leer('Noch kein Traum.'), anzahl=len(ts), id='traeume'),
                     pfad=[('Traum', None)], aktuell='traum')


def _starten():
    with SPERRE:
        return traum.starten(ZUGANG)


async def traum_starten(request):
    await _formular(request)
    try:
        neu = await run_in_threadpool(_starten)
    except Fehler as err:
        return _nicht_moeglich(str(err), '/traum')
    return _weiter('/traum', 'traum-gestartet' if neu else 'traum-laeuft')


def _beenden():
    with SPERRE:
        return traum.beenden()


async def traum_beenden(request):
    await _formular(request)
    return _weiter('/traum', 'traum-beendet' if await run_in_threadpool(_beenden) else 'traum-schon-beendet')


def _vid(request):
    vid = request.path_params['vid']
    if not NAME.fullmatch(vid):
        raise HTTPException(404, 'Vorschlag unbekannt')
    return vid


def _uebernehmen(vid, gesehen):
    with SPERRE:
        return vorschlag.uebernehmen(vid, gesehen, ZUGANG)


async def vorschlag_uebernehmen(request):
    vid, zurueck = _vid(request), '/traum#vorschlaege'
    gesehen = (await _formular(request)).get('gesehen', '')
    try:
        code = await run_in_threadpool(_uebernehmen, vid, gesehen)
    except Fehler as err:
        return _nicht_moeglich(str(err), zurueck)
    return _weiter(zurueck, f'vorschlag-{code}')


def _verwerfen(vid):
    with SPERRE:
        return vorschlag.verwerfen(vid, ZUGANG)


async def vorschlag_verwerfen(request):
    vid, zurueck = _vid(request), '/traum#vorschlaege'
    await _formular(request)
    try:
        neu = await run_in_threadpool(_verwerfen, vid)
    except Fehler as err:
        return _nicht_moeglich(str(err), zurueck)
    return _weiter(zurueck, 'vorschlag-verworfen' if neu else 'vorschlag-schon-verworfen')


def routen():
    post = ['POST']
    return [Route('/traum', traum_seite), Route('/traum/starten', traum_starten, methods=post), Route('/traum/beenden', traum_beenden, methods=post),
            Route('/traum/vorschlag/{vid}/uebernehmen', vorschlag_uebernehmen, methods=post), Route('/traum/vorschlag/{vid}/verwerfen', vorschlag_verwerfen, methods=post)]
