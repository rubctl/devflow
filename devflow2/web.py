"""Dashboard (S2.1, S2.3): Oberfläche über alle Projekte, Läufe mit Live-Log, md-Ansicht, wenige Handlungen.

Start: `python -m devflow2 web` (lauscht nur auf Loopback, Adresse und Port aus `instanz.toml`, Standard 127.0.0.1:8478).
Bei `login = "tailscale"` ist der Dienst über `tailscale serve` erreichbar; jede Anfrage, auch `/health`, braucht dann die
Kopfzeile `Tailscale-User-Login` mit dem eingestellten Login, sonst 403. Bei `login = "keine"` (Arbeitsinstanz) gibt es keine
Anmeldung; dafür muss der Host der Anfrage `127.0.0.1`, `localhost` oder `[::1]` sein (Schutz vor DNS-Rebinding), sonst 403.
Gelesen wird über roadmap, laeufe, ablauf und git; geschrieben wird nur in `web_aktion` (Antworten, Anhalten, Fortsetzen;
POST mit passender `Origin`).
"""
import asyncio
import contextlib
import hmac
import os
from pathlib import Path
import posixpath
import re
import subprocess
import time
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import Fehler, ablauf, instanz, kontingent, laeufe, pflege, projekte, push, roadmap, selbstlauf, traum, vorschlag
from .ablauf import plan_zeile
from .projekte import Projekt
from .web_aktion import Herkunft, antwort_formular, lauf_knoepfe, meldung, routen as aktions_routen
from . import web_html
from .web_html import Roh, e, kicker, seite, t, verbinden
from .web_kontingent import fenster_karte, kosten_aufgaben, kosten_laeufe
from .web_push import routen as push_routen
from . import kurz as kurzfassung
from .web_md import SCHEMA, inline, kurz_kasten, md
from .web_eingang import ideen_abschnitt
from . import web_roadmap
from .web_lage import eingang_alt, eingang_seite, lage_seite, pflege_alt, roadmap_alt
from .web_plaene import plaene_seite, plan_seite, projekt_plaene_seite
from .web_pflege import routen as pflege_routen
from .web_traum import routen as traum_routen

STATIC = Path(__file__).with_name('static')
LOKALE_HOSTS = {'127.0.0.1', 'localhost', '[::1]'}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')
ZWEIG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]*')
MDPFAD = re.compile(r'(?:[\w-][\w.-]*/)*(?:[\w-][\w.-]*)?')
MAX_DIFF = 300_000
MAX_MD = 1_000_000
SICHERHEIT = [
    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                 b"connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
    (b'x-content-type-options', b'nosniff'),
    (b'referrer-policy', b'same-origin'),
    (b'cache-control', b'no-store'),
]


# --- Zugang -------------------------------------------------------------------------------------

def _hostname(host):
    """Name aus einer Host-Kopfzeile ohne Port (`127.0.0.1:8478` → `127.0.0.1`, `[::1]:80` → `[::1]`), kleingeschrieben."""
    name = host[:host.find(']') + 1] if host.startswith('[') and ']' in host else host.split(':', 1)[0]
    return name.lower()


class Zugang:
    """Zugang zum Dashboard, je nach Login-Art der Instanz.

    - `login` gesetzt (`tailscale`): jede Anfrage braucht genau eine passende Kopfzeile `Tailscale-User-Login` (Plan: ohne
      Ausnahme, auch /health). `tailscale serve` setzt sie für angemeldete Nutzer des Tailnets; wer lokal prüft, schickt sie selbst mit.
    - `login=None` (`keine`): keine Anmeldung, aber nur Anfragen mit lokalem Host (Schutz vor DNS-Rebinding). Der Dienst bindet ohnehin nur an Loopback.
    """

    def __init__(self, app, login):
        self.app, self.login = app, login.encode() if login is not None else None

    def erlaubt(self, kopf):
        if self.login is None:
            return _hostname(kopf.get('host', '')) in LOKALE_HOSTS
        werte = kopf.getlist('tailscale-user-login')
        return len(werte) == 1 and hmac.compare_digest(werte[0].encode('latin-1'), self.login)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if not self.erlaubt(Headers(scope=scope)):
            antwort = PlainTextResponse('Kein Zugang', status_code=403, headers={'cache-control': 'no-store'})
            return await antwort(scope, receive, send)

        async def senden(nachricht):
            if nachricht['type'] == 'http.response.start':
                vorhanden = {k.lower() for k, _ in nachricht.get('headers', [])}
                nachricht['headers'] = list(nachricht.get('headers', [])) + [h for h in SICHERHEIT if h[0] not in vorhanden]
            await send(nachricht)
        await self.app(scope, receive, senden)


# --- Lesen --------------------------------------------------------------------------------------

def _git(cwd, *args):
    """Lesender Git-Aufruf mit Bytes, tolerant gegenüber beliebigem Inhalt. Fehler → None."""
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True)
    return None if r.returncode else r.stdout


def _zweig_da(p, zweig):
    return bool(zweig and ZWEIG.fullmatch(zweig) and '..' not in zweig
                and _git(p.repo, 'rev-parse', '--verify', '-q', f'refs/heads/{zweig}') is not None)


def _md_dateien(p, ref):
    roh = _git(p.repo, 'ls-tree', '-r', '-z', '--name-only', f'refs/heads/{ref}')
    return sorted(f for f in (roh or b'').decode('utf-8', 'replace').split('\0') if f.endswith('.md'))


def _projekt(name):
    if not NAME.fullmatch(name):
        raise HTTPException(404, 'Projekt unbekannt')
    try:
        return Projekt(name)
    except Fehler as err:
        raise HTTPException(404, str(err)) from None


def _roadmap_text(p):
    try:
        return p.roadmap.read_text(encoding='utf-8')
    except OSError as err:
        raise HTTPException(404, f'ROADMAP.md nicht lesbar: {err}') from None


def _lauf(lid):
    if not NAME.fullmatch(lid):
        raise HTTPException(404, 'Lauf unbekannt')
    try:
        # Wie in laeufe.alle(): ein verwaister Lauf wird als Fehler erkannt, auch wenn nur das Live-Log fragt.
        return laeufe.verwaist_pruefen(laeufe.laden(lid))
    except (Fehler, ValueError):
        raise HTTPException(404, 'Lauf unbekannt') from None


def verweise(projekt, datei, zweig=None):
    """Linkauflösung für md aus `datei` im Repo: relative Pfade → md-Ansicht, Branch bleibt erhalten.

    `#anker` und http(s) bleiben, wie sie sind; `/pfad` gilt ab Repo-Wurzel; was aus dem Repo führt, wird nur Text.
    """
    ordner = posixpath.dirname(datei)

    def ziel(url):
        if url.startswith(('#', '//')) or SCHEMA.match(url):
            return url
        pfad, _, anker = url.partition('#')
        pfad = pfad.split('?', 1)[0]
        if not pfad:
            voll = datei
        else:
            voll = posixpath.normpath(pfad.lstrip('/') if pfad.startswith('/') else posixpath.join(ordner, pfad))
            if voll == '..' or voll.startswith('../'):
                return None
            voll = '' if voll == '.' else voll + ('/' if pfad.endswith('/') else '')
        return (f'/p/{q(projekt)}/md/{q(voll, "/")}' + (f'?zweig={q(zweig)}' if zweig else '')
                + (f'#{q(anker, "-_.~")}' if anker else ''))
    return ziel


def protokoll(text, anzahl=40):
    lines = text.split('\n')
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == '## Protokoll')
    except StopIteration:
        return []
    out = []
    for l in lines[start + 1:]:
        if l.startswith('## '):
            break
        if l.startswith('- '):
            out.append(l[2:])
    return out[:anzahl]


def kurztitel(text):
    fett = re.match(r'\*\*(.+?)\*\*', text)
    text = (fett.group(1) if fett else text).rstrip(':.')
    return text if len(text) <= 90 else text[:87].rsplit(' ', 1)[0] + ' …'


# --- Bausteine ----------------------------------------------------------------------------------

def zeit(ts):
    return time.strftime('%d.%m. %H:%M', time.localtime(ts)) if ts else '–'


def dauer(l):
    if not l.get('start'):
        return ''
    s = int((l.get('ende') or time.time()) - l['start'])
    return f'{s // 3600} h {s % 3600 // 60} min' if s >= 3600 else f'{s // 60} min {s % 60} s'


# Zuordnung Rohwert → Anzeige. Roh (`laeuft`, `entwickeln`, `codex`) steht nie in der Oberfläche. Grün (`ok`) nur für
# Abgeschlossenes: Roadmap `erledigt`, Lauf `fertig`; „in Arbeit“, „Review“ und „läuft“ sind `aktiv` (◐, ohne Farbe).
STATUS_ART = {'Braucht dich': 'warn', 'in Arbeit': 'aktiv', 'Review': 'aktiv', 'blockiert': 'warn', 'erledigt': 'ok'}
ZUSTAND = {'wartet': ('wartet', 'idle'), 'laeuft': ('läuft', 'aktiv'), 'abschluss': ('schließt ab', 'aktiv'), 'fertig': ('fertig', 'ok'),
           'kontingent': ('wartet auf Kontingent', 'warn'), 'angehalten': ('angehalten', 'warn'), 'fehler': ('Fehler', 'error'),
           laeufe.TRAUM_ENDE: ('Traum beendet', 'idle')}
URTEIL = {'bestanden': ('bestanden', 'ok'), 'nacharbeit': ('Nacharbeit nötig', 'warn')}
ART_TEXT = {'entwickeln': 'Entwicklung', 'nacharbeit': 'Nacharbeit', 'vorbereiten': 'Vorbereitung', 'review': 'Review', 'meinung': 'Zweitmeinung', 'mergen': 'Merge',
            'deploy': 'Deploy', 'test': 'Test', 'steuern': 'Steuerlauf', 'pflege': 'Pflegeprüfung', 'traum': 'Traum', 'messung': 'Kontingentmessung'}
MODELL_TEXT = {'claude': 'Claude', 'sol': 'Sol', 'codex': 'Sol', 'shell': 'Shell', 'intern': 'intern'}


def status(s):
    return web_html.status(s or '–', STATUS_ART.get(s, 'idle'))


def zustand_text(z):
    return ZUSTAND.get(z, (z, 'idle'))


def zustand(l):
    text, art = zustand_text(l['zustand'])
    return web_html.status(text, art, data_zustand=l['id'])


def urteil(l):
    if not l.get('urteil'):
        return None
    text, art = URTEIL.get(l['urteil'], (l['urteil'], 'idle'))
    return web_html.status(f'Urteil: {text}', art)


def art_text(l):
    return ART_TEXT.get(l['art'], l['art'])


def modell_text(l):
    return MODELL_TEXT.get(l['modell'], l['modell'])


def lauf_name(l):
    """„Entwicklung · Claude“ für Läufe in Listen und Links."""
    return f'{art_text(l)} · {modell_text(l)}'


def q(x, safe=''):
    return quote(str(x), safe=safe)


def aufgabe_link(projekt, aid, text=None):
    return t('a', text or aid, href=f'/p/{q(projekt)}/a/{q(aid)}')


def md_link(projekt, pfad, zweig=None, text=None):
    return t('a', text or pfad, href=f'/p/{q(projekt)}/md/{q(pfad, "/")}' + (f'?zweig={q(zweig)}' if zweig else ''))


def lauf_link(l, text=None):
    return t('a', text or l['id'], href=f'/lauf/{l["id"]}')


def log_block(l, zeilen=40):
    """Log als <pre>; läuft der Lauf noch, holt app.js neue Zeilen ohne Neuladen."""
    text = '\n'.join(laeufe.log_text(l, zeilen)) or '(noch keine Ausgabe)'
    aktiv = l['zustand'] in laeufe.AKTIV
    return t('pre', text, class_='log', data_lauf=l['id'] if aktiv else None, data_zeilen=zeilen if aktiv else None)


def lauf_kopf(l):
    teile = [art_text(l), modell_text(l)] + ([f'Runde {l["runde"]}'] if l.get('runde') else [])
    return t('div', zustand(l), ' ', t('strong', ' · '.join(teile)), ' ',
             t('span', f'{zeit(l.get("angelegt"))} · {dauer(l)}', class_='leise'),
             ' ', urteil(l),
             class_='laufkopf')


def befunde(l):
    if not l.get('befunde'):
        return None
    return t('ul', [t('li', t('strong', f'{b.get("nr", "")} ({b.get("schwere", "")})'), ' ', t('code', b.get('ort', '')),
                       ': ', b.get('was', '')) for b in l['befunde']], class_='befunde')


def abschnitt(titel, *inhalt, id=None, anzahl=None):
    return t('section', kicker(titel, anzahl), *inhalt, id=id)


def leer(text):
    return t('p', text, class_='leise')


def antwort(titel, *inhalt, pfad=(), status_code=200, aktuell=None, projekt=None, unter=None):
    return HTMLResponse(seite(titel, *inhalt, pfad=pfad, aktuell=aktuell, projekt=projekt, unter=unter), status_code=status_code)


# --- Seiten -------------------------------------------------------------------------------------

def kanban(name, auf):
    spalten = []
    for s in roadmap.SPALTEN:
        hits = [a for a in auf if a['status'] == s]
        karten = [t('li', aufgabe_link(name, a['id']), ' ', kurztitel(a['aufgabe']),
                    t('div', f'{a["wer"] or "–"} · {a["datum"]}', class_='leise')) for a in hits]
        spalten.append(t('div', t('h3', status(s), f' {len(hits)}'), t('ul', karten) if karten else leer('–'), class_='spalte'))
    return t('div', spalten, class_='kanban')


def aufgaben_tabelle(name, auf):
    rm = verweise(name, 'ROADMAP.md')
    kopf = t('tr', [t('th', x) for x in ('ID', 'Aufgabe', 'Wer', 'Status', 'Datum', 'Details')])
    rows = [t('tr', t('td', aufgabe_link(name, a['id'])), t('td', inline(a['aufgabe'], rm)), t('td', a['wer']),
              t('td', status(a['status'])), t('td', a['datum']), t('td', inline(a['details'], rm))) for a in auf]
    return t('div', t('table', t('thead', kopf), t('tbody', rows), class_='aufgaben'), class_='breit')


LETZTE_LAEUFE = 5  # so viele abgeschlossene Läufe zeigt die Projektseite


def aufgaben_kurz(name, auf):
    """Kurztabelle der offenen Aufgaben: ID, Kurztitel, Stand, Details."""
    rm = verweise(name, 'ROADMAP.md')
    kopf = t('tr', [t('th', x) for x in ('ID', 'Aufgabe', 'Stand', 'Details')])
    rows = [t('tr', t('td', aufgabe_link(name, a['id'])), t('td', kurztitel(a['aufgabe'])), t('td', symbol_status(a['status']), t('div', a['datum'], class_='leise')),
              t('td', inline(a['details'], rm))) for a in auf]
    return t('div', t('table', t('thead', kopf), t('tbody', rows), class_='aufgaben'), class_='breit')


def laufliste(ls):
    return t('ul', [t('li', zustand(l), ' ', aufgabe_link(l['projekt'], l['aufgabe']) if l.get('aufgabe') else None, ' ',
                      lauf_link(l, lauf_name(l)), ' ', t('span', zeit(l.get('angelegt')), class_='leise'),
                      ' ', urteil(l))
                 for l in reversed(ls)], class_='liste')


# Symbol je Status auf der Roadmap-Seite: erledigt ●, in Arbeit/Review ◐, offen ○, Braucht dich/blockiert ▲
ROADMAP_SYMBOL = {'erledigt': ('●', 'ok'), 'in Arbeit': ('◐', 'aktiv'), 'Review': ('◐', 'aktiv'), 'Braucht dich': ('▲', 'warn'),
                  'blockiert': ('▲', 'warn')}


def symbol_status(s):
    glyph, art = ROADMAP_SYMBOL.get(s, ('○', 'idle'))
    return t('span', t('span', glyph, class_='rc-status-glyph', aria_hidden='true'), s or '–', class_=f'rc-status rc-status-{art}')


def roadmap_seite(request):
    name = request.path_params['projekt']
    p = _projekt(name)
    text = _roadmap_text(p)
    basis = f'/p/{q(name)}/roadmap'
    status_, sort, ab = web_roadmap.parameter(request)
    pfad = [(name, f'/p/{q(name)}'), ('Roadmap', None)]
    try:
        gruppen, bloecke = web_roadmap.projekt_teil(p, text, basis, status_, sort, ab)
    except Fehler as err:
        return antwort('Roadmap', t('h1', 'Roadmap'), t('p', str(err), class_='fehler'), pfad=pfad, projekt=name, unter='roadmap')
    return antwort(f'Roadmap {name}', t('h1', f'Roadmap · {name}'),
                   t('p', t('a', 'Rohfassung', href=f'/p/{q(name)}/md/ROADMAP.md'), class_='leise'),
                   abschnitt('Aufgaben', web_roadmap.filter_leiste(basis, gruppen, status_, sort, ab) if gruppen else None,
                             bloecke or leer('Keine.'), id='aufgaben'), pfad=pfad, projekt=name, unter='roadmap')


def projekt_seite(request):
    name = request.path_params['projekt']
    p = _projekt(name)
    text = _roadmap_text(p)
    try:
        auf, fragen = roadmap.aufgaben(text), roadmap.fragen(text)
    except Fehler as err:
        return antwort(name, t('h1', name), t('p', str(err), class_='fehler'), pfad=[(name, None)], projekt=name, unter='projekt')
    ls = laeufe.alle(name)
    rm = verweise(name, 'ROADMAP.md')
    laufend = [l for l in ls if l['zustand'] in laeufe.AKTIV]
    fertig = [l for l in ls if l['zustand'] not in laeufe.AKTIV]
    letzte = fertig[-LETZTE_LAEUFE:]
    gruppen = [g for g in roadmap.vorhaben(text) if g['gesamt']]
    aktuell = next((g for g in gruppen if g['erledigt'] < g['gesamt']), None)
    offen = [a for a in auf if a['status'] not in ('erledigt', 'entfällt')]
    erledigt = [a for a in auf if a['status'] in ('erledigt', 'entfällt')]
    return antwort(
        name, t('h1', name), meldung(request), t('p', t('a', 'Pläne', href=f'/p/{name}/plaene'), ' · ', t('a', 'Wiki, Pläne, Berichte', href=f'/p/{name}/md/'), class_='leise'),
        abschnitt('Aktuelles Vorhaben', t('p', t('strong', aktuell['titel']), ' ', t('span', f'{aktuell["erledigt"]} von {aktuell["gesamt"]} erledigt', class_='leise')) if aktuell else None,
                  aufgaben_kurz(name, offen) if offen else leer('Kein offenes Vorhaben.'), id='vorhaben'),
        abschnitt('Offene Fragen', [t('article', md(f['text'], rm), t('p', aufgabe_link(name, f['bezug'], f'zu {f["bezug"]}'))
                                               if f['bezug'] != 'allgemein' else None, antwort_formular(name, f, primaer=(i == 0)), class_='rc-card frage') for i, f in enumerate(fragen)]
                  or leer('Keine.'), id='fragen', anzahl=len(fragen)),
        abschnitt('Läuft gerade', laufliste(laufend) if laufend else leer('Gerade läuft nichts.'), id='laeuft', anzahl=len(laufend)),
        abschnitt('Letzte Läufe', laufliste(letzte) if letzte else leer('Noch keine.'),
                  t('details', t('summary', f'Alle Läufe ({len(fertig)})'), laufliste(fertig), id='alle-laeufe') if len(fertig) > len(letzte) else None, id='laeufe'),
        ideen_abschnitt(p),
        t('details', t('summary', f'Erledigte Aufgaben ({len(erledigt)})'), aufgaben_tabelle(name, erledigt) if erledigt else leer('Keine.'), id='erledigt'),
        t('details', t('summary', 'Protokoll'), t('ul', [t('li', inline(x, rm)) for x in protokoll(text)], class_='protokoll'),
          t('p', t('a', 'ganze Roadmap', href=f'/p/{name}/md/ROADMAP.md'), class_='leise'), id='protokoll'),
        pfad=[(name, None)], projekt=name, unter='projekt')


def _berichte(p, aid, zweige):
    """(Pfad, Branch) der Berichte zu einer Aufgabe; der Aufgabenbranch hat Vorrang vor der Basis."""
    muster = re.compile(rf'berichte/{re.escape(aid)}(?:-[^/]*)?\.md')
    gefunden = {}
    for ref in [p.cfg['basis']] + zweige:
        for f in _md_dateien(p, ref):
            if muster.fullmatch(f):
                gefunden[f] = ref
    return sorted(gefunden.items())


def diff_block(p, zweig):
    basis = p.cfg['basis']
    stat = _git(p.repo, 'diff', '--no-color', '--no-ext-diff', '--stat=100', f'{basis}...refs/heads/{zweig}')
    roh = _git(p.repo, 'diff', '--no-color', '--no-ext-diff', f'{basis}...refs/heads/{zweig}')
    if roh is None:
        return t('p', 'Diff nicht lesbar.', class_='fehler')
    if not roh.strip():
        return leer(f'Branch {zweig} hat noch keine Änderungen gegenüber {basis}.')
    gekuerzt = len(roh) > MAX_DIFF
    zeilen = []
    for z in roh[:MAX_DIFF].decode('utf-8', 'replace').split('\n'):
        art = ('datei' if z.startswith(('diff --git', '+++ ', '--- ', 'index ')) else 'stueck' if z.startswith('@@')
               else 'plus' if z.startswith('+') else 'minus' if z.startswith('-') else None)
        zeilen.append(t('span', z, class_=art) if art else e(z))
    return verbinden(t('p', t('code', f'git diff {basis}...{zweig}')),
                     t('pre', (stat or b'').decode('utf-8', 'replace'), class_='stat'),
                     t('pre', Roh('\n'.join(zeilen)), class_='diff'),
                     t('p', f'Gekürzt auf {MAX_DIFF // 1000} kB.', class_='hinweis') if gekuerzt else None)


def aufgabe_seite(request):
    name, aid = request.path_params['projekt'], request.path_params['aufgabe']
    p = _projekt(name)
    text = _roadmap_text(p)
    try:
        _, c = roadmap.finde(text, aid)
        fragen = [f for f in roadmap.fragen(text, nur_offen=False) if f['bezug'] == aid]
        pz = plan_zeile(p, aid)
    except Fehler as err:
        if vorschlag.ist_vorschlag(aid):  # vor der Übernahme steht ein Vorschlag nicht in der Roadmap
            return RedirectResponse(f'/traum#{q(aid)}', status_code=303)
        raise HTTPException(404, str(err)) from None
    ls = laeufe.alle(name, aid)
    zweige = list(dict.fromkeys(l['branch'] for l in ls if l.get('branch')))
    da = [z for z in zweige if _zweig_da(p, z)]
    rm = verweise(name, 'ROADMAP.md')
    zeile = [('ID', c[0]), ('Aufgabe', inline(c[1], rm)), ('Wer', c[2]), ('Status', status(c[3])), ('Datum', c[4]),
             ('Details', inline(c[5], rm))]
    if pz:
        zeile += [('Plan', md_link(name, pz['datei'])), ('Bereich', inline(pz['bereich'], verweise(name, pz['datei']))),
                  ('Abnahme', inline(pz['kriterium'], verweise(name, pz['datei'])))]
    laufkarten = [t('article', lauf_kopf(l), t('p', lauf_link(l, l['id'])),
                    t('p', l['fehler'], class_='fehler') if l.get('fehler') else None,
                    kurz_kasten(l.get('kurz')), befunde(l),
                    t('p', 'Bericht: ', md_link(name, l['bericht_datei'], l['branch'] if l.get('branch') in da else None))
                    if l.get('bericht_datei') else None,
                    t('details', t('summary', 'Log'), log_block(l, 60), open=l['zustand'] in laeufe.AKTIV),
                    lauf_knoepfe(l), class_='rc-card lauf') for l in reversed(ls)]
    berichte = [t('li', md_link(name, f, ref if ref != p.cfg['basis'] else None),
                  ' ', t('span', f'auf {ref}', class_='leise')) for f, ref in _berichte(p, aid, da)]
    erste = next((f['nr'] for f in fragen if f['offen']), None)
    gemergt = [l for l in ls if l['art'] == 'mergen' and l.get('merge')]
    if da:
        diffs = [diff_block(p, z) for z in da]
    elif gemergt:
        diffs = [leer(f'Gemergt als {gemergt[-1]["merge"][:7]}; der Branch ist entfernt.')]
    elif zweige:
        diffs = [leer(f'Branch {zweige[-1]} gibt es nicht mehr.')]
    else:
        diffs = [leer('Noch kein Branch.')]
    return antwort(
        f'{aid} · {name}', t('h1', aid, ' ', t('span', kurztitel(c[1]), class_='leise')), meldung(request),
        abschnitt('Roadmap', t('dl', [verbinden(t('dt', k), t('dd', v)) for k, v in zeile], class_='zeile')),
        abschnitt('Fragen', [t('article', md(f['text'], rm), antwort_formular(name, f, primaer=(f['nr'] == erste)) if f['offen'] else None, class_='rc-card frage')
                             for f in fragen], id='fragen') if fragen else None,
        abschnitt('Läufe', laufkarten or leer('Noch keine.'), anzahl=len(ls)),
        abschnitt('Bericht und Reviews', t('ul', berichte, class_='liste') if berichte else leer('Noch keine.')),
        abschnitt('Diff des Branches', diffs),
        pfad=[(name, f'/p/{name}'), (aid, None)], projekt=name, unter='projekt')


def lauf_kurz(l):
    """Kurzfassung eines Laufs: am Lauf gespeichert (Review), sonst aus dem Ergebnis (`**Kurz:**`, sonst die ersten zwei Sätze)."""
    if l.get('kurz'):
        return l['kurz']
    text = laeufe.ergebnis(l)
    if l.get('art') == 'review':  # das Ergebnis ist JSON; ältere Läufe haben kein Feld `kurz`
        try:
            u = ablauf.urteil(text)
        except Fehler:
            return ''
        return u['kurz'] or kurzfassung.aus_text(u['bericht'])
    return kurzfassung.aus_text(text)


def lauf_seite(request):
    l = _lauf(request.path_params['lid'])
    anzeige = dict(art=art_text(l), modell=modell_text(l), urteil=URTEIL.get(l.get('urteil'), (l.get('urteil'),))[0])
    namen = dict(projekt='Projekt', aufgabe='Aufgabe', art='Art', modell='Modell', branch='Branch', runde='Runde', urteil='Urteil', rc='Exit-Code',
                 session='Sitzung', merge='Merge')
    felder = [(namen[k], anzeige.get(k, l[k])) for k in namen if l.get(k) is not None]
    felder += [('angelegt', zeit(l.get('angelegt'))), ('Dauer', dauer(l))]
    if l.get('frei_ab'):
        felder.append(('Kontingent frei ab', zeit(l['frei_ab'])))
    ergebnis = laeufe.ergebnis(l)
    pfad = [(l['projekt'], f'/p/{q(l["projekt"])}')] + ([(l['aufgabe'], f'/p/{q(l["projekt"])}/a/{q(l["aufgabe"])}')] if l.get('aufgabe') else [])
    return antwort(
        f'Lauf {l["id"]}', t('h1', 'Lauf ', t('span', l['id'], class_='leise')), meldung(request), kurz_kasten(lauf_kurz(l)), lauf_kopf(l), lauf_knoepfe(l, fehler=True),
        t('p', l['fehler'], class_='fehler') if l.get('fehler') else None,
        t('dl', [verbinden(t('dt', k), t('dd', v)) for k, v in felder], class_='zeile'),
        abschnitt('Befunde', befunde(l)) if l.get('befunde') else None,
        abschnitt('Log', log_block(l, 200)),
        abschnitt('Ergebnis', t('pre', ergebnis[-20000:], class_='log')) if ergebnis.strip() else None,
        pfad=pfad + [('Lauf', None)], projekt=l['projekt'], unter='projekt')


KURZ_PFAD = re.compile(r'(berichte|plan)/(?!_)[^/]+\.md')  # Berichte, Reviews und Pläne bekommen oben die Kurzfassung


def md_seite(request):
    name, pfad = request.path_params['projekt'], request.path_params['pfad']
    p = _projekt(name)
    zweig = request.query_params.get('zweig') or p.cfg['basis']
    if not MDPFAD.fullmatch(pfad) or not _zweig_da(p, zweig):
        raise HTTPException(404, 'Datei unbekannt')
    krumen = [(name, f'/p/{name}'), ('Dokumente', f'/p/{name}/md/')]
    if not pfad.endswith('.md'):
        vorne = pfad.rstrip('/') + '/' if pfad else ''
        dateien = [f for f in _md_dateien(p, zweig) if f.startswith(vorne)]
        if not dateien:
            raise HTTPException(404, 'Keine Dokumente hier')
        gruppen = {}
        for f in dateien:
            gruppen.setdefault(f.rsplit('/', 1)[0] if '/' in f else '', []).append(f)
        return antwort(f'Dokumente · {name}', t('h1', 'Dokumente', ' ', t('span', zweig, class_='leise')),
                       [abschnitt(g or 'Wurzel', t('ul', [t('li', md_link(name, f, zweig if zweig != p.cfg['basis'] else None, f.rsplit('/', 1)[-1]))
                                                         for f in fs], class_='liste')) for g, fs in sorted(gruppen.items())],
                       pfad=krumen[:1] + [('Dokumente', None)], projekt=name, unter='projekt')
    roh = _git(p.repo, 'cat-file', 'blob', f'refs/heads/{zweig}:{pfad}')
    if roh is None:
        raise HTTPException(404, 'Datei unbekannt')
    hinweis = t('p', f'Gekürzt auf {MAX_MD // 1000} kB.', class_='hinweis') if len(roh) > MAX_MD else None
    links = verweise(name, pfad, zweig if zweig != p.cfg['basis'] else None)
    text = roh[:MAX_MD].decode('utf-8', 'replace')
    return antwort(pfad, t('p', t('code', pfad), ' auf ', t('code', zweig), class_='leise'), hinweis,
                   kurz_kasten(kurzfassung.aus_text(text), links) if KURZ_PFAD.match(pfad) else None,
                   t('article', md(text, links), class_='md'),
                   pfad=krumen + [(pfad, None)], projekt=name, unter='projekt')


def kontingent_seite(request):
    jetzt = time.time()
    je_aufgabe, je_lauf = kontingent.kosten()
    return antwort(
        'Kontingent', t('h1', 'Kontingent'),
        t('p', 'Stand der letzten Messung in den Lauflogs von Claude und den Sitzungsdateien von Sol, ohne Hochrechnung. '
               '„unbekannt“: nichts gemessen oder das Fenster hat seitdem neu begonnen.', class_='leise'),
        abschnitt('Fenster', t('div', [fenster_karte(wer, daten, jetzt) for wer, daten in kontingent.stand(jetzt).items()], class_='karten')),
        abschnitt('Kosten je Aufgabe', kosten_aufgaben(je_aufgabe, aufgabe_link) if je_aufgabe else leer('Noch keine Läufe.'),
                  t('p', 'Nur Claude meldet Kosten (US-Dollar laut Lauflog); bei Sol steht ein Strich.', class_='leise'), id='aufgaben'),
        abschnitt('Kosten je Lauf', kosten_laeufe(je_lauf[:100], lauf_link, lauf_name, zeit, zustand) if je_lauf else leer('Noch keine Läufe.'),
                  t('p', f'Die neuesten {len(je_lauf[:100])} von {len(je_lauf)} Läufen.', class_='leise') if len(je_lauf) > 100 else None, id='laeufe'),
        pfad=[('Kontingent', None)], aktuell='kontingent')


def lauf_api(request):
    l = _lauf(request.path_params['lid'])
    try:
        zeilen = max(1, min(400, int(request.query_params.get('zeilen', 40))))
    except ValueError:
        zeilen = 40
    text, art = zustand_text(l['zustand'])
    return JSONResponse({'id': l['id'], 'zustand': l['zustand'], 'text': text, 'art': art, 'aktiv': l['zustand'] in laeufe.AKTIV,
                         'log': laeufe.log_text(l, zeilen)})


def health(request):
    return JSONResponse({'ok': True})


async def nicht_gefunden(request, exc):
    if request.url.path.startswith('/api/'):
        return JSONResponse({'fehler': exc.detail}, status_code=exc.status_code)
    if exc.status_code == 405:
        return PlainTextResponse('Nicht erlaubt', status_code=405)
    return antwort('Nicht gefunden', t('h1', 'Nicht gefunden'), t('p', exc.detail), status_code=exc.status_code)


@contextlib.asynccontextmanager
async def hinweise_laufen(app):
    aufgaben = [asyncio.create_task(push.schleife()), asyncio.create_task(selbstlauf.schleife()), asyncio.create_task(pflege.schleife()),
                asyncio.create_task(traum.schleife())]
    try:
        yield
    finally:
        for aufgabe in aufgaben:
            aufgabe.cancel()


def anwendung(login=None, hinweise=False):
    """`hinweise=True` startet im Dienst die Hintergrundschleifen (Web-Push, Selbstlauf, Pflege, Traum); Tests lassen sie aus."""
    routen = [
        Route('/', lage_seite),
        Route('/health', health),
        Route('/roadmap', roadmap_alt),
        Route('/eingang', eingang_alt),
        Route('/pflege', pflege_alt),
        Route('/p/{projekt}/eingang', eingang_seite),
        Route('/p/{projekt}', projekt_seite),
        Route('/p/{projekt}/roadmap', roadmap_seite),
        Route('/p/{projekt}/a/{aufgabe}', aufgabe_seite),
        Route('/p/{projekt}/md/{pfad:path}', md_seite),
        Route('/plaene', plaene_seite),
        Route('/p/{projekt}/plaene', projekt_plaene_seite),
        Route('/p/{projekt}/plan/{plan}', plan_seite),
        Route('/kontingent', kontingent_seite),
        Route('/lauf/{lid}', lauf_seite),
        Route('/api/lauf/{lid}', lauf_api),
        *aktions_routen(),
        *pflege_routen(),
        *traum_routen(),
        *push_routen(),
        Mount('/static', StaticFiles(directory=STATIC), name='static'),
    ]
    app = Starlette(routes=routen, exception_handlers={HTTPException: nicht_gefunden}, lifespan=hinweise_laufen if hinweise else None)
    cfg = instanz.web()
    if login is None and cfg['login'] == 'keine':
        return Zugang(Herkunft(app, weiterleitung=False), None)
    return Zugang(Herkunft(app), login or cfg['login_name'])


def main():
    import uvicorn
    cfg = instanz.web()  # prüft auch, dass die Adresse Loopback ist
    push.schluessel()  # beim ersten Start anlegen (0600)
    uvicorn.run(anwendung(hinweise=True), host=cfg['adresse'], port=cfg['port'], log_level='warning', access_log=False, proxy_headers=False,
                server_header=False)
