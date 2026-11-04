"""Kleine HTML-Bausteine für das Dashboard: alles, was kein `Roh` ist, wird maskiert.

Inhalte stammen aus Repos und Logs, die Modelle schreiben. Deshalb entsteht HTML nur über `t()`; Text und
Attributwerte werden dabei immer maskiert. `Roh` markiert schon fertiges, sicheres HTML.
"""
import html
import re
from pathlib import Path

LEER = {'meta', 'link', 'br', 'hr', 'img', 'input'}
CD = Path(__file__).with_name('static') / 'cd'
SIGNET = '/static/logo/devflow-signet.svg'
GLYPHEN = {'ok': '●', 'aktiv': '◐', 'idle': '○', 'warn': '▲', 'error': '✕'}
NAVIGATION = [('uebersicht', 'Übersicht', '/'), ('traum', 'Traum', '/traum'), ('kontingent', 'Kontingent', '/kontingent')]
# Unterleiste je Projekt (S11.1): Roadmap, Eingang und Pflege gibt es nur hier, nie projektübergreifend.
UNTER = [('projekt', 'Projekt', ''), ('roadmap', 'Roadmap', '/roadmap'), ('eingang', 'Eingang', '/eingang'), ('pflege', 'Pflege', '/pflege')]


def grund():
    """Hintergrundfarben (dunkel, hell) aus den Tokens des CD-Pakets, damit außerhalb von `static/cd/` kein Farbwert steht."""
    werte = re.findall(r'--ground:\s*(#[0-9a-fA-F]{3,8})\s*;', (CD / 'cd.css').read_text(encoding='utf-8'))
    return werte[0], werte[-1]


class Roh(str):
    """Fertiges HTML, wird nicht noch einmal maskiert."""


def e(x):
    return x if isinstance(x, Roh) else Roh(html.escape(str(x), quote=True))


def _inhalt(teile):
    for x in teile:
        if x is None or x is False:
            continue
        if isinstance(x, (list, tuple)):
            yield from _inhalt(x)
        else:
            yield e(x)


def t(name, /, *inhalt, **attr):
    """`t('a', 'Text', href='/x', class_='k')`; None/False lassen ein Attribut weg, True setzt es ohne Wert."""
    teile = [name]
    for k, v in attr.items():
        if v is None or v is False:
            continue
        k = k.rstrip('_').replace('_', '-')
        teile.append(k if v is True else f'{k}="{e(v)}"')
    kopf = '<' + ' '.join(teile) + '>'
    if name in LEER:
        return Roh(kopf)
    return Roh(kopf + ''.join(_inhalt(inhalt)) + f'</{name}>')


def verbinden(*teile):
    return Roh(''.join(_inhalt(teile)))


def status(text, art, **attr):
    """Status-Baustein des CD: Symbol und Wort (`ok` ●, `aktiv` ◐, `idle` ○, `warn` ▲, `error` ✕), nie Farbe allein. Grün ist nur `ok`."""
    return t('span', t('span', GLYPHEN[art], class_='rc-status-glyph', aria_hidden='true'), text, class_=f'rc-status rc-status-{art}', **attr)


def kicker(text, anzahl=None, **attr):
    """Abschnittskopf als Kicker (Versalien, Mono, Balken)."""
    return t('h2', t('span', class_='rc-kicker-bar'), text, t('span', f'({anzahl})', class_='rc-kicker-num') if anzahl is not None else None,
             class_='rc-kicker', **attr)


def _projektwahl(aktuell):
    from urllib.parse import quote
    from . import projekte
    try:
        namen = projekte.alle()
    except Exception:  # die Leiste darf die Seite nie verhindern
        namen = []
    return t('div', t('button', 'Projekte', type='button', aria_expanded='false', aria_controls='projektwahl-liste', class_='projektwahl-knopf'),
             t('ul', [t('li', t('a', n, href=f'/p/{quote(n, safe="")}', aria_current='page' if n == aktuell else None)) for n in namen], id='projektwahl-liste', hidden=True),
             class_='projektwahl') if namen else None


def navigation(aktuell=None, projekt=None):
    """Hauptnavigation: auf dem Handy unten als Leiste, am Rechner oben (nur CSS). Übersicht, Projektwahl, Traum, Kontingent."""
    links = [t('a', text, href=link, aria_current='page' if schluessel == aktuell else None) for schluessel, text, link in NAVIGATION]
    return t('nav', links[0], _projektwahl(projekt), links[1:], class_='haupt', aria_label='Hauptnavigation')


def unterleiste(projekt, unter=None):
    """Unterleiste eines Projekts: Projekt, Roadmap, Eingang, Pflege. `unter`: Schlüssel in `UNTER`."""
    from urllib.parse import quote
    basis = f'/p/{quote(projekt, safe="")}'
    return t('nav', [t('a', text, href=basis + link, aria_current='page' if schluessel == unter else None) for schluessel, text, link in UNTER],
             class_='unter', aria_label=f'Projekt {projekt}')


def seite(titel, *inhalt, pfad=(), aktuell=None, projekt=None, unter=None):
    """Ganze Seite. `pfad`: Liste von (Text, Link) für die Brotkrumen; der letzte Eintrag ohne Link. `aktuell`: Schlüssel in `NAVIGATION`. `projekt`: zeigt die Unterleiste dieses Projekts, `unter` markiert den Eintrag."""
    from .web_kontingent import kopfzeile  # erst hier: web_kontingent braucht die Bausteine dieses Moduls
    dunkel, hell = grund()
    krumen = [t('a', t('img', src=SIGNET, alt='', width=28, height=28, class_='rc-signet'),
                t('span', 'devflow', t('span', class_='rc-cursor'), class_='rc-wordmark'), href='/', class_='marke', aria_label='devflow')]
    for text, link in pfad:
        krumen += [Roh('<span class="trenner">/</span>'), t('a', text, href=link) if link else t('span', text)]
    return Roh('<!doctype html>' + t(
        'html',
        t('head', t('meta', charset='utf-8'), t('meta', name='viewport', content='width=device-width, initial-scale=1'),
          t('meta', name='color-scheme', content='light dark'), t('title', f'{titel} · devflow'),
          t('meta', name='theme-color', content=dunkel, media='(prefers-color-scheme: dark)'),
          t('meta', name='theme-color', content=hell, media='(prefers-color-scheme: light)'), t('link', rel='icon', href=SIGNET, type='image/svg+xml'),
          t('link', rel='icon', href='/static/favicon.ico', sizes='16x16 32x32 48x48'),
          t('link', rel='manifest', href='/manifest.webmanifest'),
          t('link', rel='apple-touch-icon', href='/static/icons/icon-192-maskable.png'),
          t('link', rel='stylesheet', href='/static/cd/cd.css'),
          t('link', rel='stylesheet', href='/static/style.css'), t('script', src='/static/app.js', defer=True)),
        t('body', t('header', t('nav', krumen, t('a', 'Hinweise', href='/hinweise', class_='rechts'),
                                  t('button', 'Als App installieren', type='button', id='app-installieren', hidden=True, class_='rc-btn'), class_='pfad', aria_label='Pfad'), navigation(aktuell, projekt), unterleiste(projekt, unter) if projekt else None, kopfzeile(), class_='kopf'), t('main', *inhalt)),
        lang='de'))
