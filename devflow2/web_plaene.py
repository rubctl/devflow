"""Seiten zu den Plänen (S3.3, S14.3b): Pläne je Projekt gruppiert und die Planansicht mit Stufenwahl und Knöpfen.

Gruppen in dieser Reihenfolge: **Zur Abnahme** (Entwurf, wartet auf den Inhaber; mit „Abnehmen“ wie in „Braucht dich“), **In Arbeit** (abgenommen,
noch offene Aufgaben), **Nicht lesbar** (Plan mit Fehler) und eingeklappt **Erledigt** (abgenommen, alle Aufgaben in der Roadmap erledigt oder entfallen).
Leere Gruppen entfallen. Innerhalb einer Gruppe steht das Neueste oben: nach Abnahmedatum, sonst nach dem Datum in der Planüberschrift, bei
Gleichstand nach Planname (Zahlen als Zahlen, absteigend).
"""
import re
from datetime import datetime
from urllib.parse import quote

from starlette.exceptions import HTTPException

from . import Fehler, plaene, projekte, roadmap
from . import web_html
from .web_aktion import meldung, plan_formulare
from .web_entscheidung import eintrag, plan_eintrag, _link
from .web_html import t
from .web_md import kurz_kasten, md


def _w():
    from . import web  # erst beim Aufruf: web importiert dieses Modul
    return web


def stand_baustein(pl):
    """Stand als Status-Baustein: abgenommen ●, Rückmeldung offen ▲, sonst Entwurf ○."""
    if pl['abgenommen']:
        return web_html.status(f'abgenommen am {pl["abgenommen"]}', 'ok')
    if pl['rueckmeldung']:
        return web_html.status('Änderung gewünscht', 'warn')
    return web_html.status(pl['stand'], 'idle')


GRUPPEN = ('Zur Abnahme', 'In Arbeit', 'Nicht lesbar', 'Erledigt')
DATUM = re.compile(r'\b(\d{2})\.(\d{2})\.(\d{4})\b')
FERTIG = ('erledigt', 'entfällt')


def _datum(pl):
    """Abnahmedatum, sonst das erste Datum der Überschrift, als sortierbarer Wert; ohne Datum 0."""
    m = DATUM.fullmatch(pl.get('abgenommen') or '') or DATUM.search(pl.get('titel') or '')
    if not m:
        return 0
    try:
        return datetime(int(m[3]), int(m[2]), int(m[1])).toordinal()
    except ValueError:
        return 0


def _natuerlich(name):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', name)]


def _neueste_zuerst(liste):
    """Neueste zuerst; bei gleichem Datum der Planname absteigend (Zahlen als Zahlen)."""
    liste = sorted(liste, key=lambda pl: _natuerlich(pl['name']), reverse=True)
    return sorted(liste, key=_datum, reverse=True)  # stabil: gleiches Datum behält die Namensreihenfolge


def gruppe_von(pl, status):
    """Gruppe eines Plans. `status`: {Aufgaben-ID: Status} aus der Roadmap. Erledigt heißt: abgenommen und jede Planaufgabe erledigt oder entfallen
    (ein abgenommener Plan ohne Aufgaben wie der Pflegeplan hat nichts offen)."""
    if 'fehler' in pl:
        return 'Nicht lesbar'
    if not pl['abgenommen']:
        return 'Zur Abnahme'
    return 'Erledigt' if all(status.get(a['id']) in FERTIG for a in pl['aufgaben']) else 'In Arbeit'


def _status(p):
    """{Aufgaben-ID: Status} der Roadmap des Projekts; ist sie nicht lesbar, ist nichts erledigt."""
    try:
        return {a['id']: a['status'] for a in roadmap.aufgaben(p.roadmap.read_text(encoding='utf-8'))}
    except (Fehler, OSError, ValueError):
        return {}


def gruppieren(liste, status):
    """{Gruppe: [Plan, …]} in der Reihenfolge von `GRUPPEN`, nur nicht leere Gruppen, je Gruppe das Neueste zuerst."""
    aus = {g: [] for g in GRUPPEN}
    for pl in liste:
        aus[gruppe_von(pl, status)].append(pl)
    return {g: _neueste_zuerst(pls) for g, pls in aus.items() if pls}


def _offen(pl, status):
    erledigt = sum(status.get(a['id']) in FERTIG for a in pl['aufgaben'])
    return f'{pl["uebernommen"]} von {len(pl["aufgaben"])} Aufgaben in der Roadmap, {erledigt} erledigt'


def _plan_zeile(name, p, pl, gruppe, zurueck, status):
    """Eine Zeile als `li`: Überschrift (klappt Kurzfassung und Details auf), bei „Zur Abnahme“ der Knopf. Gleiche Handlung wie in „Braucht dich“."""
    ziel = f'/p/{quote(name)}/plan/{quote(pl["name"])}'
    if gruppe == 'Nicht lesbar':
        return t('li', t('a', pl['name'], href=ziel), ' ', t('span', pl['fehler'], class_='fehler'))
    if gruppe == 'Zur Abnahme':
        return plan_eintrag(name, p, pl, zurueck=zurueck, projekt=False)['li']
    return eintrag('plan', None, f'abgenommen am {pl["abgenommen"]}', 'ok', pl['titel'], kurz_kasten(pl['kurz']),
                   t('p', f'{pl["name"]}.md · {_offen(pl, status)}', class_='leise'), _link('Ganzen Plan lesen', ziel))['li']


def _gruppen_html(name, p, liste, zurueck, kopf):
    """Die Gruppen eines Projekts. `kopf(titel, anzahl, *inhalt)` baut den Abschnitt (Seite je Projekt: Abschnitt, globale Seite: Unterüberschrift)."""
    status = _status(p)
    aus = []
    for gruppe, pls in gruppieren(liste, status).items():
        zeilen = t('ul', [_plan_zeile(name, p, pl, gruppe, zurueck, status) for pl in pls], class_='liste entscheidungen', aria_label=gruppe)
        if gruppe == 'Erledigt':
            aus.append(t('details', t('summary', f'Erledigt ({len(pls)})'), zeilen, class_='erledigt', id='erledigt'))
        else:
            aus.append(kopf(gruppe, len(pls), zeilen))
    return aus or [_w().leer('Keine Pläne.')]


def plaene_seite(request):
    """Alle Projekte, je Projekt die Gruppen."""
    w = _w()
    gruppen = []
    for name in projekte.alle():
        try:
            p = w._projekt(name)
            liste = plaene.liste(p)
        except HTTPException as err:
            gruppen.append(w.abschnitt(name, t('p', err.detail, class_='fehler')))
            continue
        inhalt = _gruppen_html(name, p, liste, 'plaene-alle', lambda titel, n, zeilen: t('div', t('h3', f'{titel} ({n})'), zeilen, class_='plangruppe'))
        gruppen.append(w.abschnitt(name, t('p', t('a', 'Nur dieses Projekt', href=f'/p/{quote(name)}/plaene'), class_='leise'), *inhalt, anzahl=len(liste)))
    return w.antwort('Pläne', t('h1', 'Pläne'), meldung(request), gruppen or w.leer('Kein Projekt angemeldet.'), pfad=[('Pläne', None)])


def projekt_plaene_seite(request):
    """Nur die Pläne eines Projekts; unbekanntes Projekt: 404 (wie jede Projektseite)."""
    w = _w()
    name = request.path_params['projekt']
    p = w._projekt(name)
    try:
        liste = plaene.liste(p)
    except OSError as err:
        raise HTTPException(404, f'Pläne nicht lesbar: {err}') from None
    inhalt = _gruppen_html(name, p, liste, 'plaene', lambda titel, n, zeilen: w.abschnitt(titel, zeilen, anzahl=n))
    return w.antwort(f'Pläne · {name}', t('h1', 'Pläne'), meldung(request), *inhalt,
                     pfad=[(name, f'/p/{quote(name)}'), ('Pläne', None)], projekt=name, unter='projekt')


def kriterien_abschnitt(pl):
    """Je Aufgabe die erkannten Kriterien (K-Nummer und Text) als Liste, vor und nach der Abnahme. Pläne ohne K-Nummern: Altbestand, mit dem
    Kriterium im Wortlaut; eine einzelne Aufgabe ohne K in einem Plan mit K-Nummern bekommt den Hinweis „keine nummerierten Kriterien“."""
    if not pl['aufgaben']:
        return None
    zeilen = []
    for a in pl['aufgaben']:
        if a['kriterien']:
            inhalt = t('ul', [t('li', t('strong', f'K{n}'), ' ', text) for n, text in a['kriterien']], class_='liste kriterien')
        else:
            inhalt = t('p', a['kriterium'] or 'ohne Kriterium', class_='leise')
        zeilen.append(t('li', t('strong', a['id']), ' ', a['aufgabe'],
                        t('span', 'Altbestand' if pl['altbestand'] else 'keine nummerierten Kriterien', class_='leise') if not a['kriterien'] else None, inhalt))
    kopf = t('p', 'Altbestand: Plan ohne nummerierte Kriterien (vor S15.1), das Kriterium steht im Wortlaut.', class_='leise') if pl['altbestand'] else None
    return _w().abschnitt('Kriterien', kopf, t('ul', zeilen, class_='liste'), id='kriterien')


def plan_seite(request):
    w = _w()
    name, plan = request.path_params['projekt'], request.path_params['plan']
    p = w._projekt(name)
    try:
        pl = plaene.info(p, plan)
    except (Fehler, OSError) as err:
        raise HTTPException(404, str(err)) from None
    datei = f'plan/{plan}.md'
    kopf = t('p', stand_baustein(pl), ' ', w.md_link(name, datei, text=datei), class_='leise')
    if pl['abgenommen']:
        stufen = t('ul', [t('li', t('strong', a['id']), ' ', a['aufgabe'], ' ', t('span', a['stufe'] or f'Projekt ({p.cfg["stufe"] or "Standard"})', class_='leise'))
                          for a in pl['aufgaben']], class_='liste')
        handeln = [w.abschnitt('Stufen', stufen)]
    else:
        handeln = [w.abschnitt('Abnehmen', t('p', 'Stufe je Aufgabe: leicht = Sonnet, wenig Aufwand; normal = Sonnet, hoher Aufwand; schwer = Opus.', class_='leise'),
                               *plan_formulare(p, pl), id='abnehmen')]
    return w.antwort(pl['titel'], t('h1', pl['titel']), meldung(request), kurz_kasten(pl['kurz'], w.verweise(name, datei)), kopf,
                     *handeln, kriterien_abschnitt(pl),
                     w.abschnitt('Plan', t('article', md(pl['text'], w.verweise(name, datei)), class_='md')),
                     pfad=[(name, f'/p/{quote(name)}'), ('Pläne', f'/p/{quote(name)}/plaene'), (plan, None)], projekt=name, unter='projekt')
