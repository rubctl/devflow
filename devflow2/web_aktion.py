"""Handlungen im Dashboard (S2.3, S3.3, S3.4, S3.5): Frage beantworten, Lauf anhalten und fortsetzen, alles anhalten und fortsetzen,
Plan abnehmen, Änderung wünschen, Idee anlegen und Selbstlauf schalten.

Geschrieben wird nur über `ablauf.antwort_eintragen`, `ablauf.anhalten`, `ablauf.fortsetzen`, `plaene.abnehmen`, `plaene.rueckmeldung`, `eingang.anlegen` und `selbstlauf.schalten`. Jede Handlung ist ein
POST (Formular), der zusätzlich zur Login-Kopfzeile eine passende `Origin` braucht (`Herkunft`). Nach Erfolg geht es
per 303 zurück auf die Seite, die dann eine kurze Bestätigung zeigt (`?meldung=<code>`). Die Handlungen sind
wiederholbar: zweimal Senden ergibt eine Antwort, zweimal Anhalten oder Fortsetzen ändert nichts.
"""
import re
import threading
from urllib.parse import parse_qs, quote, urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from . import Fehler, ablauf, eingang, ereignis, instanz, laeufe, plaene, roadmap, selbstlauf
from . import inhaber as _i
from . import projekte
from .projekte import Projekt
from .web_html import seite, status, t

ZUGANG = 'dashboard'  # Herkunft jeder Handlung hier; das Dashboard erreicht nur den Login des Inhabers (`web.Zugang`)
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')
ANTWORT_ZEILE = _i.Muster(lambda: r'\*\*Antwort(?: ' + _i.kennung_muster() + r')? \([^)]*\):\*\* (.*)')
MAX_FORM = 32_000
MAX_ANTWORT = 4000
SPERRE = threading.Lock()  # ein Dienstprozess: Prüfen und Handeln laufen nacheinander

MELDUNG = {
    'antwort': 'Antwort ist in der Roadmap eingetragen.',
    'schon': 'Diese Frage war schon beantwortet; es wurde nichts geändert.',
    'angehalten': 'Lauf angehalten. Mit „Fortsetzen“ geht er in derselben Sitzung weiter.',
    'nicht-aktiv': 'Der Lauf war schon nicht mehr aktiv; es wurde nichts geändert.',
    'fortgesetzt': 'Lauf fortgesetzt.',
    'laeuft': 'Der Lauf läuft schon; es wurde nichts geändert.',
    'alles-angehalten': 'Alles angehalten: laufende Läufe sind gestoppt, neue starten erst nach „Alles fortsetzen“.',
    'alles-fortgesetzt': 'Neue Läufe sind wieder erlaubt. Angehaltene Läufe setzt du einzeln fort.',
    'plan-abgenommen': 'Plan abgenommen: Stand und Stufen stehen im Plan, der Selbstlauf bekommt das Ereignis.',
    'ohne-ereignis': 'Gespeichert, aber das Ereignis für den Selbstlauf ließ sich nicht schreiben; die steuernde KI muss es selbst aufgreifen.',
    'idee': 'Idee ist im Eingang gespeichert (plan/eingang.md); die steuernde KI macht daraus einen Planentwurf.',
    'idee-schon': 'Diese Idee war schon der letzte Eintrag im Eingang; es wurde nichts geändert.',
    'idee-ohne-ereignis': 'Idee gespeichert, aber das Ereignis für den Selbstlauf ließ sich nicht schreiben; die steuernde KI muss sie selbst aufgreifen.',
    'rueckmeldung-schon': 'Dieser Änderungswunsch stand schon im Plan; es wurde nichts geändert.',
    'pflege-gestartet': 'Pflegeprüfung gestartet; das Ergebnis erscheint hier, sobald der Lauf fertig ist.',
    'pflege-laeuft': 'Für dieses Projekt läuft schon eine Pflegeprüfung; es wurde nichts geändert.',
    'pflege-widerrufen': 'Pflegeplan widerrufen: Es entstehen keine Routine-Updates mehr, offene Pflegeaufgaben werden nicht gemergt.',
    'pflege-schon-widerrufen': 'Der Pflegeplan war schon widerrufen; es wurde nichts geändert.',
    'traum-gestartet': 'Traum gestartet: Der erste Schritt beginnt gleich. Nichts davon wird ohne dich gemergt.',
    'traum-laeuft': 'Es läuft schon ein Traum; es wurde nichts geändert.',
    'traum-beendet': 'Traum wird beendet: Der laufende Schritt läuft zu Ende, danach startet keiner mehr.',
    'traum-schon-beendet': 'Es läuft kein Traum mehr, oder er endet schon; es wurde nichts geändert.',
    'vorschlag-merge': 'Übernommen: Das Gegenreview ist bestanden, der Merge läuft.',
    'vorschlag-review': 'Übernommen: Das Gegenreview läuft. Gemergt wird erst, wenn es besteht und der Branch auf diesem Stand bleibt.',
    'vorschlag-verworfen': 'Vorschlag verworfen: Branch und Worktree sind weg, Bericht und Entscheidung bleiben.',
    'vorschlag-schon-verworfen': 'Dieser Vorschlag war schon verworfen; es wurde nichts geändert.',
    'selbstlauf-an': 'Selbstlauf ist an: Ereignisse wecken jetzt die steuernde KI. Schalte ihn aus, solange du aus einer Sitzung steuerst.',
    'selbstlauf-aus': 'Selbstlauf ist aus: Es startet kein Steuerlauf mehr. Laufende Läufe und ihre Kette laufen weiter.',
}


# --- Schutz -------------------------------------------------------------------------------------

class Herkunft:
    """Schreibende Anfragen (alles außer GET, HEAD, OPTIONS) brauchen eine `Origin`, die zum Host passt, sonst 403.

    Erlaubt ist die Herkunft der Seite selbst (Host bzw. `X-Forwarded-Host` von `tailscale serve`) und, falls gesetzt,
    jede Herkunft aus `web.origin` der Instanz (`instanz.toml`, bzw. `DEVFLOW2_WEB_ORIGIN` kommagetrennt). Ohne Proxy
    (`weiterleitung=False`, Login-Art `keine`) zählt `X-Forwarded-Host` nicht.
    """

    def __init__(self, app, erlaubt=None, weiterleitung=True):
        self.app, self.weiterleitung = app, weiterleitung
        roh = ','.join(instanz.web()['origin']) if erlaubt is None else erlaubt
        self.erlaubt = {o.strip().rstrip('/').lower() for o in roh.split(',') if o.strip()}

    def passt(self, kopf):
        herkunft = kopf.get('origin')
        if not herkunft or kopf.get('sec-fetch-site', 'same-origin') not in ('same-origin', 'none'):
            return False
        teile = urlsplit(herkunft)
        if teile.scheme not in ('http', 'https') or not teile.netloc or teile.path not in ('', '/') or teile.query or teile.fragment:
            return False
        if herkunft.rstrip('/').lower() in self.erlaubt:
            return True
        hosts = {h.lower() for h in (kopf.get('host'), kopf.get('x-forwarded-host') if self.weiterleitung else None) if h}
        return teile.netloc.lower() in hosts

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['method'] not in ('GET', 'HEAD', 'OPTIONS') and not self.passt(Headers(scope=scope)):
            antwort = PlainTextResponse('Fremde Herkunft', status_code=403, headers={'cache-control': 'no-store'})
            return await antwort(scope, receive, send)
        await self.app(scope, receive, send)


# --- Bausteine für die Seiten -------------------------------------------------------------------

def meldung(request):
    """Kurze Bestätigung nach einer Handlung (nur feste Texte, nie Inhalt aus der Adresse)."""
    art = request.query_params.get('meldung', '')
    text = (f'Änderungswunsch steht im Plan unter „Rückmeldung {_i.kennung()}“, der Selbstlauf bekommt das Ereignis.' if art == 'rueckmeldung'
            else MELDUNG.get(art))
    return t('p', text, class_='hinweis', role='status') if text else None


def _knopf(url, text, *felder, bestaetigen=None, klasse=None):
    return t('form', *felder, t('button', text, type='submit', class_='rc-btn' + (f' {klasse}' if klasse else '')), method='post', action=url, class_='aktion',
             data_bestaetigen=bestaetigen)


def antwort_formular(projekt, frage, primaer=False):
    """`primaer`: höchstens ein Formular je Ansicht trägt den Primär-Button."""
    url = f'/p/{quote(projekt)}/frage/{frage["nr"]}/antwort'
    return t('form', t('label', f'Antwort auf F{frage["nr"]}', t('textarea', class_='rc-input', name='antwort', rows=3, required=True, maxlength=MAX_ANTWORT)),
             t('button', 'Antwort senden', type='submit', class_='rc-btn rc-btn-primary' if primaer else 'rc-btn'), method='post', action=url, class_='aktion antwort')


def lauf_knoepfe(l, fehler=False):
    """„Anhalten“ für aktive, „Fortsetzen“ für angehaltene (und, mit `fehler`, fehlgeschlagene) Läufe."""
    basis = f'/lauf/{quote(l["id"])}'
    if l['zustand'] in laeufe.AKTIV:
        return _knopf(f'{basis}/anhalten', 'Anhalten', bestaetigen='Diesen Lauf anhalten?')
    if l.get('traum'):
        return None  # Traumläufe werden nicht fortgesetzt
    if l['zustand'] == 'angehalten' or (fehler and l['zustand'] == 'fehler'):
        if laeufe.angehalten():
            return t('p', 'Fortsetzen geht erst nach „Alles fortsetzen“.', class_='leise')
        return _knopf(f'{basis}/fortsetzen', 'Fortsetzen')
    return None


def alles_knoepfe():
    if laeufe.angehalten():
        return _knopf('/alles/fortsetzen', 'Alles fortsetzen')
    return _knopf('/alles/anhalten', 'Alles anhalten', bestaetigen='Alle laufenden Läufe stoppen und neue sperren?')


def selbstlauf_baustein():
    """Schalter und Stand auf der Lage. Der Text erklärt, warum gerade nichts startet."""
    s = selbstlauf.stand()
    zahlen = f'Budget heute: {s["heute"]} von {s["budget"]} Steuerläufen · {s["ereignisse"]} Ereignisse warten'
    grund = None
    if s['an'] and laeufe.angehalten():
        grund = 'Angehalten: Es startet kein Steuerlauf, bis fortgesetzt wird.'
    elif s['an'] and s['traum']:
        grund = 'Ein Traum läuft: Es startet kein Steuerlauf, bis er endet.'
    elif s['an'] and s['ereignisse'] and s['heute'] >= s['budget']:
        grund = 'Tagesbudget verbraucht: Die Ereignisse warten bis morgen.'
    knopf = (_knopf('/selbstlauf/aus', 'Selbstlauf ausschalten') if s['an']
             else _knopf('/selbstlauf/an', 'Selbstlauf einschalten', bestaetigen='Die steuernde KI handelt dann ohne offene Sitzung. Einschalten?'))
    return t('div', t('p', status('Selbstlauf an' if s['an'] else 'Selbstlauf aus', 'ok' if s['an'] else 'idle')), t('p', zahlen, class_='leise'),
             t('p', grund, class_='hinweis') if grund else None, knopf)


def plan_formulare(p, plan):
    """Abnehmen (Stufe je Aufgabe) und Änderung wünschen für einen noch nicht abgenommenen Plan."""
    basis = f'/p/{quote(p.name)}/plan/{quote(plan["name"])}'
    stufen = [''] + [s for s in projekte.STUFEN if s in p.cfg['stufen']] + sorted(set(p.cfg['stufen']) - set(projekte.STUFEN))
    zeilen = [t('tr', t('td', t('label', a['id'], for_=f'stufe-{a["id"]}')), t('td', a['aufgabe']),
                t('td', t('select', [t('option', s or f'Projekt ({p.cfg["stufe"] or "Standard"})', value=s, selected=(s == a['stufe'])) for s in stufen],
                          name=f'stufe.{a["id"]}', id=f'stufe-{a["id"]}', class_='rc-input'))) for a in plan['aufgaben']]
    abnehmen = t('form', t('table', t('thead', t('tr', t('th', 'ID'), t('th', 'Aufgabe'), t('th', 'Stufe'))), t('tbody', zeilen), class_='stufen'),
                 t('input', type='hidden', name='gesehen', value=plan['pruefsumme']),
                 t('button', 'Abnehmen', type='submit', class_='rc-btn rc-btn-primary'),
                 method='post', action=f'{basis}/abnehmen', class_='aktion', data_bestaetigen='Plan mit diesen Stufen abnehmen?')
    wunsch = t('form', t('label', 'Änderung wünschen', t('textarea', class_='rc-input', name='wunsch', rows=4, required=True, maxlength=MAX_ANTWORT)),
               t('button', 'Änderung wünschen', type='submit', class_='rc-btn'), method='post', action=f'{basis}/aenderung', class_='aktion antwort')
    return [abnehmen, wunsch]


# --- Handlungen ---------------------------------------------------------------------------------

async def _formular(request):
    roh = b''
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > MAX_FORM:
            raise HTTPException(413, 'Zu groß')
    if roh and request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/x-www-form-urlencoded':
        raise HTTPException(415, 'Erwartet ein Formular')
    return {k: v[0] for k, v in parse_qs(roh.decode('utf-8', 'replace'), keep_blank_values=True).items()}


def _weiter(ziel, code):
    pfad, _, anker = ziel.partition('#')
    return RedirectResponse(f'{pfad}?meldung={code}' + (f'#{anker}' if anker else ''), status_code=303)


def _nicht_moeglich(text, zurueck, status_code=409):
    return HTMLResponse(seite('Nicht möglich', t('h1', 'Nicht möglich'), t('p', text, class_='fehler'),
                              t('p', t('a', 'Zurück', href=zurueck))), status_code=status_code)


def _projekt(name):
    try:
        if NAME.fullmatch(name):
            return Projekt(name)
    except Fehler:
        pass
    raise HTTPException(404, 'Projekt unbekannt')


def _lauf(lid):
    try:
        if NAME.fullmatch(lid):
            return laeufe.verwaist_pruefen(laeufe.laden(lid))
    except (Fehler, ValueError):
        pass
    raise HTTPException(404, 'Lauf unbekannt')


def _frage_ziel(p, bezug):
    """Seite, auf der die Frage steht: die Aufgabe, falls es sie gibt, sonst das Projekt."""
    try:
        roadmap.finde(p.roadmap.read_text(encoding='utf-8'), bezug)
    except (Fehler, OSError):
        return f'/p/{quote(p.name)}#fragen'
    return f'/p/{quote(p.name)}/a/{quote(bezug)}#fragen'


def _beantworten(p, nr, text):
    """(Code, Bezug). Eine Frage, die schon genau diese Antwort trägt, gilt als erledigt (Doppelklick, zweimal Senden)."""
    with SPERRE:
        try:
            return 'antwort', ablauf.antwort_eintragen(p, nr, text, ZUGANG)['bezug']
        except Fehler:
            fragen = roadmap.fragen(p.roadmap.read_text(encoding='utf-8'), nur_offen=False)
            frage = next((f for f in fragen if f['nr'] == nr), None)
            if frage is None or frage['offen']:
                raise
            treffer = [m.group(1) for m in map(ANTWORT_ZEILE.fullmatch, frage['text'].split('\n')) if m]
            return ('antwort' if treffer == [text] else 'schon'), frage['bezug']


async def frage_beantworten(request):
    p = _projekt(request.path_params['projekt'])
    nr = request.path_params['nr']
    zurueck = f'/p/{quote(p.name)}#fragen'
    text = roadmap.eine_zeile((await _formular(request)).get('antwort', ''))
    if not text:
        return _nicht_moeglich('Die Antwort ist leer.', zurueck, 400)
    if len(text) > MAX_ANTWORT:
        return _nicht_moeglich(f'Die Antwort ist zu lang (höchstens {MAX_ANTWORT} Zeichen).', zurueck, 400)
    try:
        code, bezug = await run_in_threadpool(_beantworten, p, nr, text)
    except Fehler as err:
        status_code = 404 if 'gibt es nicht' in str(err) else 409
        return _nicht_moeglich(str(err), zurueck, status_code)
    return _weiter(_frage_ziel(p, bezug), code)


def _lauf_anhalten(lid):
    with SPERRE:
        if _lauf(lid)['zustand'] not in laeufe.AKTIV:
            return 'nicht-aktiv'
        ablauf.anhalten(lid, ZUGANG)
        return 'angehalten'


def _lauf_fortsetzen(lid):
    with SPERRE:
        if _lauf(lid)['zustand'] in laeufe.AKTIV:
            return 'laeuft'
        ablauf.fortsetzen(lid, ZUGANG)
        return 'fortgesetzt'


async def lauf_handlung(request):
    lid = request.path_params['lid']
    await _formular(request)
    tun = _lauf_anhalten if request.url.path.endswith('/anhalten') else _lauf_fortsetzen
    zurueck = f'/lauf/{quote(lid)}'
    try:
        code = await run_in_threadpool(tun, lid)
    except Fehler as err:
        return _nicht_moeglich(str(err), zurueck)
    return _weiter(zurueck, code)


def _alles(anhalten):
    with SPERRE:
        ablauf.anhalten('alles', ZUGANG) if anhalten else ablauf.fortsetzen('alles', ZUGANG)
    return 'alles-angehalten' if anhalten else 'alles-fortgesetzt'


async def alles_handlung(request):
    await _formular(request)
    anhalten = request.url.path.endswith('/anhalten')
    try:
        code = await run_in_threadpool(_alles, anhalten)
    except Fehler as err:
        return _nicht_moeglich(str(err), '/')
    return _weiter('/', code)


def _schalten(wert):
    with SPERRE:
        selbstlauf.schalten(wert)


async def selbstlauf_handlung(request):
    await _formular(request)
    wert = request.url.path.endswith('/an')
    await run_in_threadpool(_schalten, wert)
    return _weiter('/', 'selbstlauf-an' if wert else 'selbstlauf-aus')


def _plan_ziel(p, name):
    return f'/p/{quote(p.name)}/plan/{quote(name)}'


def _plan_fehler(err, zurueck):
    text = str(err)
    return _nicht_moeglich(text, zurueck, 404 if 'gibt es nicht' in text else 400 if text.startswith('Stufe') else 409)


def _abnehmen(p, name, stufen, gesehen):
    with SPERRE:
        try:
            plaene.abnehmen(p, name, stufen, gesehen)
        except ereignis.EreignisFehler:
            return 'ohne-ereignis'
        return 'plan-abgenommen'


async def plan_abnehmen(request):
    p, name = _projekt(request.path_params['projekt']), request.path_params['plan']
    zurueck = _plan_ziel(p, name)
    form = await _formular(request)
    stufen = {k[len('stufe.'):]: v.strip().lower() for k, v in form.items() if k.startswith('stufe.')}
    try:
        code = await run_in_threadpool(_abnehmen, p, name, stufen, form.get('gesehen', ''))
    except Fehler as err:
        return _plan_fehler(err, zurueck)
    return _weiter(zurueck, code)


def _rueckmeldung(p, name, wunsch):
    with SPERRE:
        try:
            return 'rueckmeldung' if plaene.rueckmeldung(p, name, wunsch) else 'rueckmeldung-schon'
        except ereignis.EreignisFehler:
            return 'ohne-ereignis'


async def plan_rueckmeldung(request):
    p, name = _projekt(request.path_params['projekt']), request.path_params['plan']
    zurueck = _plan_ziel(p, name)
    wunsch = roadmap.eine_zeile((await _formular(request)).get('wunsch', ''))
    if not wunsch:
        return _nicht_moeglich('Der Änderungswunsch ist leer.', zurueck, 400)
    if len(wunsch) > MAX_ANTWORT:
        return _nicht_moeglich(f'Der Änderungswunsch ist zu lang (höchstens {MAX_ANTWORT} Zeichen).', zurueck, 400)
    try:
        code = await run_in_threadpool(_rueckmeldung, p, name, wunsch)
    except Fehler as err:
        return _plan_fehler(err, zurueck)
    return _weiter(zurueck, code)


def _idee(p, text):
    with SPERRE:
        try:
            return 'idee' if eingang.anlegen(p, text, ZUGANG) is not None else 'idee-schon'
        except ereignis.EreignisFehler:
            return 'idee-ohne-ereignis'


async def idee_anlegen(request):
    p = _projekt(request.path_params['projekt'])
    zurueck = f'/p/{quote(p.name)}#ideen'
    text = roadmap.eine_zeile((await _formular(request)).get('idee', ''))
    if not text:
        return _nicht_moeglich('Die Idee ist leer.', zurueck, 400)
    if len(text) > eingang.MAX_TEXT:
        return _nicht_moeglich(f'Die Idee ist zu lang (höchstens {eingang.MAX_TEXT} Zeichen).', zurueck, 400)
    try:
        code = await run_in_threadpool(_idee, p, text)
    except Fehler as err:
        return _nicht_moeglich(str(err), zurueck)
    return _weiter(zurueck, code)


def routen():
    post = ['POST']
    return [
        Route('/p/{projekt}/frage/{nr:int}/antwort', frage_beantworten, methods=post),
        Route('/p/{projekt}/idee', idee_anlegen, methods=post),
        Route('/p/{projekt}/plan/{plan}/abnehmen', plan_abnehmen, methods=post),
        Route('/p/{projekt}/plan/{plan}/aenderung', plan_rueckmeldung, methods=post),
        Route('/lauf/{lid}/anhalten', lauf_handlung, methods=post),
        Route('/lauf/{lid}/fortsetzen', lauf_handlung, methods=post),
        Route('/alles/anhalten', alles_handlung, methods=post),
        Route('/alles/fortsetzen', alles_handlung, methods=post),
        Route('/selbstlauf/an', selbstlauf_handlung, methods=post),
        Route('/selbstlauf/aus', selbstlauf_handlung, methods=post),
    ]
