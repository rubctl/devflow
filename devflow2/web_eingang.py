"""Ideen auf der Projektseite (S3.4): Textfeld und Liste mit Zustand."""
from urllib.parse import quote

from . import eingang
from . import web_html
from .web_html import t


def _zustand(idee):
    art = {'neu': 'idle', 'in Planung': 'aktiv', 'verworfen': 'idle'}.get(idee['zustand'], 'idle')
    return web_html.status(idee['zustand'] or 'neu', art)


def _idee(name, idee):
    plan = idee['plan']
    ziel = None
    if plan.startswith('plan/') and plan.endswith('.md') and '/' not in plan[5:]:
        ziel = t('a', plan, href=f'/p/{quote(name)}/plan/{quote(plan[5:-3])}')
    return t('li', _zustand(idee), ' ', t('strong', f'#{idee["nr"]}'), ' ', t('span', ' · '.join(x for x in (idee['datum'], idee['herkunft']) if x), class_='leise'), ' ', ziel, t('p', idee['text']),
             class_='idee')


def formular(name):
    return t('form', t('label', 'Neue Idee', t('textarea', class_='rc-input', name='idee', rows=3, required=True, maxlength=eingang.MAX_TEXT)),
             t('button', 'Idee speichern', type='submit', class_='rc-btn'), method='post', action=f'/p/{quote(name)}/idee', class_='aktion antwort')


def ideen_abschnitt(p, titel='Ideen', id='ideen'):
    from .web import abschnitt, leer  # erst beim Aufruf: web importiert dieses Modul
    try:
        ideen = eingang.liste(p)
    except (OSError, UnicodeDecodeError) as err:
        ideen = []
        fehler = t('p', f'{eingang.DATEI} nicht lesbar: {err}', class_='fehler')
    else:
        fehler = None
    offen = sum(i['zustand'] == 'neu' for i in ideen)
    return abschnitt(titel, formular(p.name), fehler,
                     t('ul', [_idee(p.name, i) for i in reversed(ideen)], class_='liste') if ideen else leer('Noch keine Ideen.'),
                     id=id, anzahl=offen)
