"""Seite „Pflege“ (S5.1, S5.2): Ergebnis der letzten Pflegeprüfung je Projekt, Stand des Pflegeplans, Knöpfe „Jetzt prüfen“ und „Pflegeplan widerrufen“.

Die Seite liest nur das gespeicherte Ergebnis (`pflege.letzter`) und die Läufe; geprüft wird im Lauf der Art `pflege`, den der
Knopf oder der Wochentimer des Dashboard-Dienstes startet. Geschrieben wird nur über `pflege.starten` und `pflegeplan.widerrufen`.
"""
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from . import Fehler, laeufe, pflege, pflegeplan, projekte
from . import web_html
from .projekte import Projekt
from .web_aktion import SPERRE, _formular, _knopf, _nicht_moeglich, _projekt, _weiter, meldung
from .web_html import t

SCHWERE_ART = {'kritisch': 'error', 'hoch': 'error', 'mittel': 'warn', 'niedrig': 'idle', 'unbekannt': 'warn'}


def _w():
    from . import web  # erst beim Aufruf: web importiert dieses Modul
    return web


def _lauf_zeile(name):
    ls = pflege.pflege_laeufe(name)
    if not ls:
        return t('p', 'Noch nie geprüft. Der Wochentimer startet die erste Prüfung bald, oder du startest sie hier.', class_='leise')
    l = ls[-1]
    w = _w()
    wann = f'{w.zeit(l.get("start") or l["angelegt"])}'
    return t('p', w.zustand(l), ' ', w.lauf_link(l, 'Lauf'), ' ', t('span', wann, class_='leise'),
             t('div', (l.get('fehler') or '')[-200:], class_='fehler') if l['zustand'] == 'fehler' else None)


def _fund(f):
    return t('li', web_html.status(f['schwere'], SCHWERE_ART.get(f['schwere'], 'idle')), ' ', t('strong', f['paket']), ' ', f['version'] or '', ' ',
             t('span', ' · '.join(x for x in (', '.join(f['ids']), 'behoben in ' + ', '.join(f['behoben']) if f['behoben'] else 'kein Fix bekannt',
                                              ', '.join(f['quellen'])) if x), class_='leise'))


def _veraltet(v):
    art = 'warn' if v['sprung'] == 'major' else 'idle'
    return t('li', web_html.status(v['sprung'], art), ' ', t('strong', v['paket']), f' {v["aktuell"]} → {v["neu"]}')


def _plan_zeile(p):
    """Stand des Pflegeplans; solange er gilt, mit dem Knopf für den Widerruf."""
    zustand, grund = pflegeplan.stand(p)
    art = {'aktiv': 'ok', 'fehlt': 'idle'}.get(zustand, 'warn')
    knopf = None
    if zustand in ('aktiv', 'ungültig'):
        knopf = _knopf('/pflege/widerrufen', 'Pflegeplan widerrufen', t('input', type='hidden', name='projekt', value=p.name), bestaetigen='Pflegeplan widerrufen?')
    return [t('p', web_html.status(f'Pflegeplan {zustand}', art), ' ', t('span', grund, class_='leise')), knopf]


def projekt_block(name):
    w = _w()
    e = pflege.letzter(name)
    try:
        p = Projekt(name)
        cfg = pflege.konfig(p)
    except Fehler as err:
        return w.abschnitt(name, t('p', str(err), class_='fehler'), id=f'pflege-{name}')
    kopf = [*_plan_zeile(p), _lauf_zeile(name)]
    if not cfg['aktiv']:
        kopf = [t('p', web_html.status('ausgeschaltet', 'idle'), ' In `devflow.toml` steht `aktiv = false`.', class_='leise')]
    elif not cfg['pruefungen']:
        kopf.append(t('p', 'Keine Paketdateien erkannt: nichts zu prüfen.', class_='leise'))
    else:
        kopf.append(t('p', 'Prüfungen: ' + ', '.join(cfg['pruefungen']), class_='leise'))
    knopf = _knopf('/pflege/starten', 'Jetzt prüfen', t('input', type='hidden', name='projekt', value=name)) if cfg['aktiv'] else None
    if not e:
        return w.abschnitt(name, *kopf, knopf, id=f'pflege-{name}')
    bericht = pflege.bericht_pfad(e)
    teile = [t('p', f'Stand {e["tag"]}, Commit {e["commit"]} · ', w.md_link(name, bericht, text='Bericht'), class_='leise')]
    if e['nicht_geprueft']:
        teile.append(t('div', web_html.status('nicht geprüft', 'warn'),
                       t('ul', [t('li', t('strong', n['pruefung']), ': ', n['grund']) for n in e['nicht_geprueft']], class_='liste')))
    teile.append(t('h3', f'Lücken ({len(e["funde"])})'))
    teile.append(t('ul', [_fund(f) for f in e['funde']], class_='liste') if e['funde']
                 else w.leer('Keine bekannten Lücken.' if e['geprueft'] else 'Nichts geprüft.'))
    teile.append(t('h3', f'Veraltet ({len(e["veraltet"])})'))
    teile.append(t('ul', [_veraltet(v) for v in e['veraltet'][:30]], class_='liste') if e['veraltet'] else w.leer('Nichts veraltet.' if e['geprueft'] else 'Nichts geprüft.'))
    if len(e['veraltet']) > 30:
        teile.append(w.leer(f'… und {len(e["veraltet"]) - 30} weitere im Bericht.'))
    teile += [t('p', h, class_='leise') for h in e.get('hinweise') or ()]
    return w.abschnitt(name, *kopf, knopf, *teile, id=f'pflege-{name}', anzahl=len(e['funde']))


def pflege_seite(request):
    """Pflege eines Projekts (`/p/<name>/pflege`)."""
    w = _w()
    name = _projekt(request.path_params['projekt']).name
    try:
        block = projekt_block(name)
    except Fehler as err:
        block = t('p', f'{name}: {err}', class_='fehler')
    return w.antwort(f'Pflege {name}', t('h1', f'Pflege · {name}'),
                     t('p', 'Sicherheitslücken und veraltete Pakete, wöchentlich und auf Knopfdruck, ohne KI. Die Werkzeuge fragen öffentliche Datenbanken '
                            '(OSV, PyPI, npm) nur lesend ab und senden Paketnamen und Versionen, keinen Code.', class_='leise'),
                     meldung(request), block, pfad=[(name, f'/p/{quote(name)}'), ('Pflege', None)], projekt=name, unter='pflege')


def _starten(name):
    with SPERRE:
        return pflege.starten(Projekt(name))


async def pflege_starten(request):
    form = await _formular(request)
    name = form.get('projekt', '')
    p = _projekt(name)
    zurueck = f'/p/{quote(p.name)}/pflege#pflege-{quote(p.name)}'
    try:
        lauf = await run_in_threadpool(_starten, p.name)
    except Fehler as err:
        return _nicht_moeglich(str(err), f'/p/{quote(p.name)}/pflege')
    return _weiter(zurueck, 'pflege-gestartet' if lauf else 'pflege-laeuft')


def _widerrufen(name):
    with SPERRE:
        return pflegeplan.widerrufen(Projekt(name))


async def pflege_widerrufen(request):
    form = await _formular(request)
    p = _projekt(form.get('projekt', ''))
    try:
        neu = await run_in_threadpool(_widerrufen, p.name)
    except Fehler as err:
        return _nicht_moeglich(str(err), f'/p/{quote(p.name)}/pflege')
    return _weiter(f'/p/{quote(p.name)}/pflege#pflege-{quote(p.name)}', 'pflege-widerrufen' if neu else 'pflege-schon-widerrufen')


def routen():
    return [Route('/p/{projekt}/pflege', pflege_seite), Route('/pflege/starten', pflege_starten, methods=['POST']),
            Route('/pflege/widerrufen', pflege_widerrufen, methods=['POST'])]
