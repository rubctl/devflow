"""Roadmap als Tabelle (S4.4): je Vorhaben eine Tabelle mit Läufen, Nacharbeit, Kosten und Dauer aus den Läufen.

Filter nach Status und Sortierung sind Links mit Abfrageparametern (`?status=…&sort=…&richtung=ab`), der Server rechnet sie.
Ohne JavaScript bleibt die Seite also voll lesbar. Texte aus der Roadmap gehen durch `t()` und werden maskiert.
"""
import re
from urllib.parse import urlencode

from . import Fehler, kontingent, laeufe, roadmap
from .projekte import NAMEN
from .web_html import t
from .web_kontingent import geld
from .web_md import inline

# (Schlüssel, Kopf, rechtsbündig)
SPALTEN = [('id', 'ID', False), ('aufgabe', 'Kurztitel', False), ('status', 'Stand', False), ('laeuft', 'Läuft / Wartet', False),
           ('stufe', 'Stufe', False), ('kosten', 'Kosten', True)]
LEER = '–'


def _w():
    from . import web  # erst beim Aufruf: web importiert dieses Modul
    return web


def dauer_text(s):
    if s is None:
        return LEER
    s = int(s)
    return f'{s // 3600} h {s % 3600 // 60:02d} min' if s >= 3600 else f'{s // 60} min' if s >= 60 else f'{s} s'


def kurztitel(text, grenze=60):
    """Text vor dem ersten Doppelpunkt (mit Leerraum dahinter); ohne Doppelpunkt der gekürzte Anfang. Markdown-Zeichen entfallen."""
    text = ' '.join(re.sub(r'[*`_]|\[([^\]]*)\]\([^)]*\)', lambda m: m.group(1) or '', text).split())
    m = re.search(r':(\s|$)', text)
    if m and m.start():
        text = text[:m.start()]
    return text if len(text) <= grenze else text[:grenze - 2].rsplit(' ', 1)[0].rstrip(' ,;') + ' …'


def _modell(wer):
    if not wer:
        return None
    rest = '/'.join(x for x in (wer['cli_modell'], wer['cli_effort']) if x)
    return ' '.join(x for x in (NAMEN.get(wer['modell'], wer['modell']), rest) if x)


def _plan_stufen(p):
    """{Aufgabe: Stufe} aus den Plänen des Projekts; ein kaputter Plan wird übergangen."""
    out = {}
    for datei in sorted((p.repo / 'plan').glob('*.md')):
        if datei.name.startswith('_'):
            continue
        try:
            for z in roadmap.plan(datei.read_text(encoding='utf-8'))['aufgaben']:
                if z['stufe']:
                    out[z['id']] = z['stufe']
        except (Fehler, OSError, ValueError):
            continue
    return out


LAUF_ART = {'entwickeln': 'Entwicklung', 'nacharbeit': 'Nacharbeit', 'vorbereiten': 'Vorbereitung', 'review': 'Review', 'meinung': 'Zweitmeinung', 'mergen': 'Merge',
            'deploy': 'Deploy', 'test': 'Test'}


def _aktive(projekt):
    """{Aufgabe: (läuft?, Art, Modell, Lauf-ID)} für Aufgaben mit aktivem Lauf; ein laufender Lauf geht einem wartenden vor."""
    out = {}
    for l in laeufe.alle(projekt):
        if l.get('zustand') not in laeufe.AKTIV or not l.get('aufgabe'):
            continue
        laeuft = l['zustand'] in ('laeuft', 'abschluss')
        art = LAUF_ART.get(l.get('art'), l.get('art') or 'Lauf')
        if l['zustand'] == 'kontingent':
            art += ' (Kontingent)'
        if l['aufgabe'] not in out or (laeuft and not out[l['aufgabe']][0]):
            out[l['aufgabe']] = (laeuft, art, NAMEN.get(l.get('modell'), l.get('modell') or ''), l['id'])
    return out


def zeilen(p, gruppen, jetzt=None):
    """Alle Aufgaben der Vorhaben mit den Werten der Tabelle; gleiche Reihenfolge, jede Aufgabe ergänzt um `werte`."""
    bilanz = kontingent.aufgaben_bilanz(p.name, jetzt)
    stufen = _plan_stufen(p)
    aktiv = _aktive(p.name)
    for g in gruppen:
        for a in g['aufgaben']:
            z = bilanz.get(a['id'])
            kosten = z['kosten'] if z and z['bekannt'] else None
            a['werte'] = dict(
                id=a['id'], aufgabe=a['aufgabe'], laeuft=aktiv.get(a['id']), status=a['status'], stand=a['datum'],
                stufe=(z['stufe'] if z else '') or stufen.get(a['id']) or '',
                entwickler=_modell(z and z['entwickler']), reviewer=_modell(z and z['reviewer']),
                laeufe=z['laeufe'] if z else None, runden=z['runden'] if z else None, kosten=kosten, dauer=z['dauer'] if z else None,
                teilweise=(z['claude'] - z['bekannt'], z['claude']) if z and kosten is not None and z['bekannt'] < z['claude'] else None)
    return gruppen


def summen(g):
    """Summen über alle Aufgaben eines Vorhabens (entfällt eingeschlossen: auch dafür liefen Läufe)."""
    w = [a['werte'] for a in g['aufgaben']]
    kosten = [x['kosten'] for x in w if x['kosten'] is not None]
    dauer = [x['dauer'] for x in w if x['dauer'] is not None]
    return dict(laeufe=sum(x['laeufe'] or 0 for x in w), runden=sum(x['runden'] or 0 for x in w),
                kosten=sum(kosten) if kosten else None, dauer=sum(dauer) if dauer else None)


def _natuerlich(text):
    return [(0, int(x), '') if x.isdigit() else (1, 0, x.lower()) for x in re.split(r'(\d+)', text) if x]


def _stand_schluessel(text):
    m = re.match(r'(\d+)\.(\d+)\.(\d+)?', text)
    return (int(m.group(3) or 0), int(m.group(2)), int(m.group(1))) if m else None


def _schluessel(spalte, w):
    """Sortierwert oder None (leer: steht in beiden Richtungen am Ende)."""
    wert = {'id': lambda: _natuerlich(w['id']), 'aufgabe': lambda: w['aufgabe'].lower(),
            'status': lambda: roadmap.STATUS.index(w['status']) if w['status'] in roadmap.STATUS else len(roadmap.STATUS),
            'stufe': lambda: w['stufe'] or None, 'laeuft': lambda: (not w['laeuft'][0], w['laeuft'][1]) if w['laeuft'] else None,
            'kosten': lambda: w['kosten']}[spalte]()
    return wert if wert not in ('', None) else None


def sortiert(aufgaben, spalte, absteigend):
    if spalte not in {s[0] for s in SPALTEN}:
        return list(aufgaben)
    mit = [a for a in aufgaben if _schluessel(spalte, a['werte']) is not None]
    ohne = [a for a in aufgaben if _schluessel(spalte, a['werte']) is None]
    return sorted(mit, key=lambda a: _schluessel(spalte, a['werte']), reverse=absteigend) + ohne


def _url(basis, status=None, sort=None, absteigend=False):
    abfrage = {k: v for k, v in (('status', status), ('sort', sort), ('richtung', 'ab' if sort and absteigend else None)) if v}
    return basis + ('?' + urlencode(abfrage) if abfrage else '')


def _kopf(basis, status, sort, absteigend, spalte, text, rechts):
    jetzt = sort == spalte
    link = t('a', text, (' ▼' if absteigend else ' ▲') if jetzt else None, href=_url(basis, status, spalte, jetzt and not absteigend))
    aria = ('descending' if absteigend else 'ascending') if jetzt else None
    return t('th', link, scope='col', aria_sort=aria, class_='zahl' if rechts else None)


def _zelle(w, spalte, a, name, rm):
    web = _w()
    if spalte == 'id':
        return t('th', web.aufgabe_link(name, a['id']), scope='row', class_='id')
    if spalte == 'aufgabe':
        return t('td', kurztitel(a['aufgabe']), class_='titel', title=a['aufgabe'])
    if spalte == 'status':
        return t('td', web.symbol_status(a['status']), t('div', w['stand'], class_='leise') if w['stand'] else None)
    if spalte == 'laeuft':
        if not w['laeuft']:
            return t('td', LEER)
        return t('td', t('strong', 'läuft') if w['laeuft'][0] else 'wartet', t('div', t('a', w['laeuft'][1], href=f'/lauf/{w["laeuft"][3]}'), f' · {w["laeuft"][2]}' if w['laeuft'][2] else None, class_='leise'), data_laeuft='1' if w['laeuft'][0] else '0')
    if spalte == 'kosten':
        return t('td', geld(w['kosten']) if w['kosten'] is not None else LEER,
                 t('div', f'{w["teilweise"][1] - w["teilweise"][0]} von {w["teilweise"][1]} Claude-Läufen', class_='leise') if w['teilweise'] else None,
                 class_='zahl')
    return t('td', w[spalte] or LEER)


def tabelle(name, g, basis, status, sort, absteigend, rm):
    kopf = t('thead', t('tr', [_kopf(basis, status, sort, absteigend, s, text, rechts) for s, text, rechts in SPALTEN]))
    auswahl = [a for a in g['aufgaben'] if not status or a['status'] == status]
    if not auswahl:
        return t('p', 'Keine Aufgaben mit diesem Status.' if status else 'Keine Aufgaben.', class_='leise')
    zeilen_ = [t('tr', [_zelle(a['werte'], s, a, name, rm) for s, _, _ in SPALTEN]) for a in sortiert(auswahl, sort, absteigend)]
    return t('div', t('table', kopf, t('tbody', zeilen_), class_='roadmap-tabelle'), class_='tabellenkasten', tabindex='0',
             role='region', aria_label=f'Aufgaben: {g["titel"]}')


def vorhaben_block(name, g, nr, basis, status, sort, absteigend, rm):
    web = _w()
    n, m, s = g['erledigt'], g['gesamt'], summen(g)
    kosten = f'{geld(s["kosten"])} Claude' if s['kosten'] is not None else f'Kosten {LEER}'
    kopf = t('summary', t('span', g['titel'], class_='v-titel'), ' ', t('span', f'{n} von {m} erledigt', class_='leise'),
             t('progress', f'{n} von {m}', value=n, max=max(m, 1), class_='fortschritt'),
             t('span', f'{s["laeufe"] or LEER} {"Lauf" if s["laeufe"] == 1 else "Läufe"} · Nacharbeit {s["runden"] if s["laeufe"] else LEER} · {kosten} · Dauer {dauer_text(s["dauer"])}',
               class_='leise summen'))
    plan = t('p', web.md_link(name, g['plan'], text='Plan'), class_='leise') if g['plan'] else None
    fertig = m > 0 and n == m
    treffer = bool(status) and any(a['status'] == status for a in g['aufgaben'])
    return t('details', kopf, plan, tabelle(name, g, basis, status, sort, absteigend, rm), id=f'{name}-v{nr}',
             open=not fertig or treffer, class_='rc-card vorhaben', data_fertig='1' if fertig else None)


def filter_leiste(basis, gruppen, status, sort, absteigend):
    web = _w()
    alle = [a for g in gruppen for a in g['aufgaben']]
    punkte = [t('li', t('a', f'Alle {len(alle)}', href=_url(basis, None, sort, absteigend), aria_current='true' if not status else None))]
    for s in roadmap.STATUS:
        k = sum(a['status'] == s for a in alle)
        if k:
            punkte.append(t('li', t('a', web.symbol_status(s), f' {k}', href=_url(basis, s, sort, absteigend), aria_current='true' if s == status else None)))
    return t('ul', punkte, class_='filter', aria_label='Nach Status filtern')


def parameter(request):
    """(Status, Spalte, absteigend) aus der Abfrage; Unbekanntes wird ignoriert."""
    q = request.query_params
    status = q.get('status') if q.get('status') in roadmap.STATUS else None
    sort = q.get('sort') if q.get('sort') in {s[0] for s in SPALTEN} else None
    return status, sort, bool(sort) and q.get('richtung') == 'ab'


def _datum(titel):
    """Datum `TT.MM.JJJJ` aus der Vorhabenüberschrift als sortierbares Tupel, sonst None."""
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', titel)
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def neueste_zuerst(gruppen):
    """Vorhaben nach Datum der Überschrift absteigend (ohne Datum und bei Gleichstand: später in der Datei zuerst); offene vor erledigten
    (stabil); „Sonstiges“ ohne Plan bleibt am Ende seines Blocks."""
    nummeriert = list(enumerate(gruppen))
    nummeriert.sort(key=lambda x: (_datum(x[1]['titel']) or (0, 0, 0), x[0]), reverse=True)
    return sorted((g for _, g in nummeriert),
                  key=lambda g: (g['gesamt'] > 0 and g['erledigt'] == g['gesamt'], not g['plan']))


def _sonstiges_zeigen(g):
    """Die Karte „Sonstiges“ entfällt, wenn es dafür keine Läufe gibt."""
    return summen(g)['laeufe'] > 0


def projekt_teil(p, text, basis, status, sort, absteigend):
    """Vorhabenblöcke und Filterleiste eines Projekts; Fehler in der Roadmap wirft `Fehler`."""
    web = _w()
    gruppen = zeilen(p, roadmap.vorhaben(text))
    rm = web.verweise(p.name, 'ROADMAP.md')
    gruppen = [g for g in gruppen if g['plan'] or _sonstiges_zeigen(g)]
    gruppen = neueste_zuerst(gruppen)
    bloecke = [vorhaben_block(p.name, g, i, basis, status, sort, absteigend, rm) for i, g in enumerate(gruppen)]
    return gruppen, bloecke
