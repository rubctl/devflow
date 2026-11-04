"""Seiten zu den Plänen (S3.3): Liste aller Pläne mit Stand je Projekt und die Planansicht mit Stufenwahl und Knöpfen."""
from urllib.parse import quote

from starlette.exceptions import HTTPException

from . import Fehler, plaene, projekte
from . import web_html
from .web_aktion import meldung, plan_formulare
from .web_html import t
from .web_md import md


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


def _zeile(name, pl):
    ziel = f'/p/{quote(name)}/plan/{quote(pl["name"])}'
    if 'fehler' in pl:
        return t('li', t('a', pl['name'], href=ziel), ' ', t('span', pl['fehler'], class_='fehler'))
    ueber = f'{pl["uebernommen"]} von {len(pl["aufgaben"])} Aufgaben in der Roadmap' if pl['abgenommen'] else f'{len(pl["aufgaben"])} Aufgaben'
    return t('li', stand_baustein(pl), ' ', t('a', pl['titel'], href=ziel), ' ', t('span', f'{pl["name"]}.md · {ueber}', class_='leise'))


def plaene_seite(request):
    w = _w()
    gruppen = []
    for name in projekte.alle():
        try:
            liste = plaene.liste(w._projekt(name))
        except HTTPException as err:
            gruppen.append(w.abschnitt(name, t('p', err.detail, class_='fehler')))
            continue
        gruppen.append(w.abschnitt(name, t('ul', [_zeile(name, pl) for pl in liste], class_='liste') if liste else w.leer('Keine Pläne.'),
                                   anzahl=len(liste)))
    return w.antwort('Pläne', t('h1', 'Pläne'), meldung(request), gruppen or w.leer('Kein Projekt angemeldet.'), pfad=[('Pläne', None)])


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
    return w.antwort(pl['titel'], t('h1', pl['titel']), meldung(request), kopf,
                     *handeln,
                     w.abschnitt('Plan', t('article', md(pl['text'], w.verweise(name, datei)), class_='md')),
                     pfad=[(name, f'/p/{quote(name)}'), ('Pläne', '/plaene'), (plan, None)], projekt=name, unter='projekt')
