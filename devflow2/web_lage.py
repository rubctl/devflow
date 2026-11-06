"""Übersicht (S4.3): die Startseite als Leitstand mit vier Blöcken, dazu die Umleitungen der alten Sammelseiten `/roadmap`, `/eingang`, `/pflege` und der Eingang je Projekt.

1. Braucht dich: Entscheidungsliste (S14.3, `web_entscheidung`): je Eintrag eine Zeile mit Knopf.  2. Läuft gerade: aktive Läufe.
3. Projekte: eine Karte je Projekt.  4. Kontingent (Balken), Selbstlauf und Traum.
Leere Blöcke zeigen einen Satz. Ein Projekt, das sich nicht lesen lässt, wird zur Fehlerkarte und bricht die Seite nicht ab.
"""
import re
from urllib.parse import quote

from starlette.responses import RedirectResponse

from . import Fehler, grundlage, kontingent, laeufe, projekte, roadmap, selbstlauf, traum
from . import web_html
from .projekte import Projekt
from .web_aktion import alles_knoepfe, lauf_knoepfe, meldung, selbstlauf_baustein
from .web_entscheidung import braucht_dich, halt_eintrag, liste as entscheidungsliste
from .web_html import t
from .web_kontingent import geld
from .web_traum import baustein as traum_baustein

SICHTBAR = 5  # so viele Einträge zeigt „Braucht dich“ sofort; der Rest steht hinter „weitere“


def _w():
    from . import web  # erst beim Aufruf: web importiert dieses Modul
    return web


def _kurz(text, ab=0):
    """Erste nicht leere Zeile ab Zeile `ab`, ohne Markdown-Auszeichnung am Anfang, gekürzt."""
    zeilen = (re.sub(r'^\*\*[^*]+:\*\*\s*', '', z.strip().lstrip('#> ').strip()) for z in str(text).split('\n')[ab:])  # „**Frage:** “ am Anfang
    zeile = next((z for z in zeilen if z), '')
    return zeile if len(zeile) <= 90 else zeile[:87].rsplit(' ', 1)[0] + ' …'


def lauf_karte(l, kosten):
    w = _w()
    z = kosten.get((l['projekt'], l.get('aufgabe') or ''))
    summe = geld(z['summe'] if z and z['bekannt'] else None)  # Kosten der Aufgabe bisher; Sol meldet keine Dollar, dann steht „–“
    return t('article', t('h3', t('span', l['projekt'], class_='leise'), ' ', w.aufgabe_link(l['projekt'], l['aufgabe']) if l.get('aufgabe') else None, ' ',
                          w.lauf_link(l, 'Lauf')),
             w.zustand(l), t('dl', *[web_html.verbinden(t('dt', k), t('dd', v)) for k, v in (
                 ('Art', w.art_text(l)), ('Modell', w.modell_text(l)), ('seit', f'{w.zeit(l.get("start") or l.get("angelegt"))} ({w.dauer(l) or "–"})'),
                 ('Aufgabenkosten bisher', summe))], class_='zeile'),
             lauf_knoepfe(l), class_='rc-card lauf')


def projekt_karte(name, p, text, auf, fragen, ls):
    w = _w()
    gruppen = [g for g in roadmap.vorhaben(text) if g['gesamt']]
    aktiv = next((g for g in gruppen if g['erledigt'] < g['gesamt']), None)
    teile = [t('h3', t('a', name, href=f'/p/{quote(name)}'))]
    if aktiv:
        n, m = aktiv['erledigt'], aktiv['gesamt']
        naechste = next((a for a in aktiv['aufgaben'] if a['status'] not in ('erledigt', 'entfällt')), None)
        teile += [t('p', t('strong', aktiv['titel']), ' ', t('span', f'{n} von {m} erledigt', class_='leise')),
                  t('progress', f'{n} von {m}', value=n, max=max(m, 1), class_='fortschritt', aria_label=f'{aktiv["titel"]}: {n} von {m} erledigt'),
                  t('p', 'Nächste Aufgabe: ', w.aufgabe_link(name, naechste['id']), ' ', w.kurztitel(naechste['aufgabe']), ' ', w.status(naechste['status'])) if naechste else None]
    else:
        teile.append(w.leer('Kein offenes Vorhaben.'))
    hinweise = grundlage.hinweise(p)
    if hinweise:
        teile.append(t('p', 'Hinweis: ' + '; '.join(hinweise), class_='leise'))
    deploys = [l for l in ls if l['art'] == 'deploy']
    if deploys:
        d = deploys[-1]
        teile.append(t('p', 'Letzter Deploy: ', w.zustand(d), ' ', t('span', w.zeit(d.get('ende') or d.get('angelegt')), class_='leise')))
    else:
        teile.append(t('p', 'Letzter Deploy: noch keiner', class_='leise'))
    aktiv_n = sum(l['zustand'] in laeufe.AKTIV for l in ls)
    teile.append(t('p', t('a', 'Roadmap', href=f'/p/{quote(name)}/roadmap'), ' · ', t('a', 'Eingang', href=f'/p/{quote(name)}/eingang'), ' · ', t('a', 'Pläne', href=f'/p/{quote(name)}/plaene'), ' · ',
                    f'{aktiv_n} {"Lauf" if aktiv_n == 1 else "Läufe"} aktiv · {len(fragen)} {"offene Frage" if len(fragen) == 1 else "offene Fragen"}', class_='leise'))
    return t('article', *teile, class_='rc-card')


def zeile(text, art, *inhalt):
    """Zustandszeile; Details und Knöpfe darunter sind eingeklappt."""
    return t('details', t('summary', web_html.status(text, art)), *inhalt)


def _selbstlauf_zeile():
    an = selbstlauf.stand()['an']
    return ('Selbstlauf an' if an else 'Selbstlauf aus'), ('ok' if an else 'idle')


def _traum_zeile():
    a = traum.aktiv()
    if a:
        return ('Traum endet', 'warn') if a.get('stopp') else ('Traum läuft', 'aktiv')
    return ('Kein Traum möglich', 'idle') if traum.startgrund() else ('Traum bereit', 'idle')


def lage_seite(request):
    w = _w()
    eintraege, laufend, karten = [], [], []
    kosten = kontingent.kosten()[0]
    for name in projekte.alle():
        try:
            p = Projekt(name)
            text = p.roadmap.read_text(encoding='utf-8')
            auf, fragen, ls = roadmap.aufgaben(text), roadmap.fragen(text), laeufe.alle(name)
            eintraege += braucht_dich(name, p, text, auf, fragen, ls)
            laufend += [lauf_karte(l, kosten) for l in ls if l['zustand'] in laeufe.AKTIV]
            karten.append(projekt_karte(name, p, text, auf, fragen, ls))
        except (Fehler, OSError, ValueError) as err:
            karten.append(t('article', t('h3', name), t('p', f'Fehler: {err}', class_='fehler'), class_='rc-card'))
    if laeufe.angehalten():
        eintraege.insert(0, halt_eintrag())
    liste, anzahl = entscheidungsliste(eintraege, SICHTBAR)
    return w.antwort(
        'Übersicht', t('h1', 'Übersicht'), meldung(request),
        w.abschnitt('Braucht dich', [w.leer('Nichts offen: Du musst gerade nichts tun.') if not anzahl else None, liste], anzahl=anzahl, id='braucht-dich'),
        w.abschnitt('Läuft gerade', t('div', laufend, class_='karten') if laufend else w.leer('Gerade läuft nichts.'), anzahl=len(laufend), id='laeuft'),
        w.abschnitt('Projekte', t('div', karten, class_='karten') if karten else w.leer('Noch kein Projekt angemeldet.'), id='projekte'),
        w.abschnitt('Selbstlauf', zeile(*_selbstlauf_zeile(), selbstlauf_baustein(), alles_knoepfe()), id='selbstlauf'),
        w.abschnitt('Traum', zeile(*_traum_zeile(), traum_baustein()), id='traum'),
        aktuell='uebersicht')


def _umleiten(unter):
    """Alte Sammelseiten (`/roadmap`, `/eingang`, `/pflege`): 303 zum Projekt, bei genau einem Projekt auf dessen Unterseite, sonst zur Übersicht."""
    namen = list(projekte.alle())
    return RedirectResponse(f'/p/{quote(namen[0], safe="")}/{unter}' if len(namen) == 1 else '/#projekte', status_code=303)


def roadmap_alt(request):
    return _umleiten('roadmap')


def eingang_alt(request):
    return _umleiten('eingang')


def pflege_alt(request):
    return _umleiten('pflege')


def eingang_seite(request):
    """Eingang eines Projekts (`/p/<name>/eingang`): Ideen mit Textfeld."""
    from .web_eingang import ideen_abschnitt
    w = _w()
    name = w._projekt(request.path_params['projekt']).name
    return w.antwort(f'Eingang {name}', t('h1', f'Eingang · {name}'), meldung(request), ideen_abschnitt(Projekt(name), titel='Ideen', id='ideen'),
                     pfad=[(name, f'/p/{quote(name)}'), ('Eingang', None)], projekt=name, unter='eingang')
