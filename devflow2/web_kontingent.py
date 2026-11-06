"""Kontingent im Dashboard (S3.2): Kopfzeile auf jeder Seite und Bausteine der Seite `/kontingent`.

Die Zahlen kommen aus `kontingent.py`. „unbekannt“ steht, wo nichts gemessen ist oder die Messung überholt ist;
„veraltet“, wo sie älter als zwei Stunden ist. Hochgerechnet wird nicht. Dargestellt wird als Fortschrittsbalken (S4.3).
"""
import time

from . import kennzahlen, kontingent
from .web_html import status, t

WER = {'claude': 'Claude', 'sol': 'Sol'}
VOLL = 80


def uhr(ts, jetzt=None):
    """`HH:MM` binnen 24 h um jetzt, sonst `TT.MM. HH:MM`; ohne Zeit ein Strich."""
    if not ts:
        return '–'
    jetzt = time.time() if jetzt is None else jetzt
    return time.strftime('%H:%M' if abs(ts - jetzt) < 24 * 3600 else '%d.%m. %H:%M', time.localtime(ts))


def prozent(f):
    return 'unbekannt' if f['prozent'] is None else f'{round(f["prozent"])} %'


def art(f):
    """▲ ab 80 % (auch bei veralteter Messung), ○ ohne Wert oder veraltet darunter, sonst ●."""
    if f['prozent'] is None:
        return 'idle'
    if f['prozent'] >= VOLL:
        return 'warn'
    return 'idle' if f['zustand'] == 'veraltet' else 'ok'


def wert(f):
    """Status-Baustein: Symbol und Wort, bei veralteter Messung mit Zusatz."""
    text = prozent(f) + (' (veraltet)' if f['zustand'] == 'veraltet' else '')
    return status(text, art(f))


def zusatz(f, jetzt, stand=True):
    teile = []
    if f['frei'] and f['zustand'] != 'unbekannt':
        teile.append(f'frei {uhr(f["frei"], jetzt)}')
    if stand and f['stand']:
        teile.append(f'Stand {uhr(f["stand"], jetzt)}')
    return ' · '.join(teile)


def wortlaut(titel, f, jetzt):
    """Text für Screenreader: Fenster, Prozent verbraucht, Freigabe, Hinweis."""
    if f['prozent'] is None:
        return f'{titel}: unbekannt ({f["hinweis"]})'
    teile = [f'{titel}: {round(f["prozent"])} % verbraucht']
    if f['frei']:
        teile.append(f'frei {uhr(f["frei"], jetzt)}')
    if f['hinweis']:
        teile.append(f['hinweis'])
    return ', '.join(teile)


def balken(wer, name, f, jetzt, schmal=False):
    """Ein Fenster als Fortschrittsbalken: Füllung = verbraucht, daneben Prozent (▲ ab 80 %) und Freigabezeit.

    Der Balken ist ein `progressbar` mit Wert und Text; ohne Messung hat er keinen Wert. Veraltete Messungen sind blass
    und tragen den Hinweis. Die Füllung kommt aus dem Wert von `<progress>`, nie aus einem Inline-Stil (CSP).
    """
    titel = f'{WER[wer]} {kontingent.NAMEN[name]}'
    text = wortlaut(titel, f, jetzt)
    if f['prozent'] is None:
        spur = t('span', class_='k-spur', role='progressbar', aria_label=titel, aria_valuemin=0, aria_valuemax=100, aria_valuetext=text)
    else:
        p = round(f['prozent'])
        spur = t('progress', f'{p} %', value=p, max=100, role='progressbar', aria_label=titel, aria_valuenow=p, aria_valuetext=text, class_='k-spur')
    klassen = ['k-balken', f'k-{f["zustand"]}', 'k-schmal' if schmal else None, 'k-hoch' if art(f) == 'warn' else None]
    nebenher = zusatz(f, jetzt, stand=not schmal)
    return t('div', t('span', t('span', WER[wer], class_='k-wer'), ' ', t('span', kontingent.NAMEN[name], class_='k-fenster'), class_='k-etikett'),
             spur, t('span', wert(f), class_='k-wert'), t('span', nebenher or None, class_='k-zusatz leise'),
             t('span', f['hinweis'], class_='k-hinweis leise') if f['hinweis'] and not schmal else None,
             class_=' '.join(k for k in klassen if k), data_kontingent=f'{wer}-{name}')


def balken_gruppe(jetzt=None, schmal=False):
    """Alle vier Fenster; wirft nie (bei defekter Quelle steht „unbekannt“)."""
    jetzt = time.time() if jetzt is None else jetzt
    try:
        daten = kontingent.stand(jetzt)
        return [balken(wer, name, f, jetzt, schmal) for wer in WER for name, f in daten[wer].items()]
    except Exception:  # die Anzeige darf keine Seite abbrechen
        leer = {'zustand': 'unbekannt', 'prozent': None, 'frei': None, 'stand': None, 'hinweis': 'Messung nicht lesbar'}
        return [balken(wer, name, leer, jetzt, schmal) for wer in WER for name in kontingent.NAMEN]


def kopfzeile(jetzt=None):
    """Schmale Balken auf jeder Seite; Link auf `/kontingent`. Wirft nie."""
    return t('div', t('a', 'Kontingent', href='/kontingent', class_='k-link'), balken_gruppe(jetzt, schmal=True), class_='kontingent', role='group', aria_label='Kontingent')


def fenster_karte(wer, daten, jetzt):
    return t('div', t('h3', WER[wer]), t('div', [balken(wer, name, f, jetzt) for name, f in daten.items()], class_='k-liste'), class_='rc-card')


def geld(k):
    return '–' if k is None else f'{k:,.2f} $'.replace(',', ' ').replace('.', ',')


def kosten_aufgaben(je_aufgabe, aufgabe_link):
    """Tabelle Kosten je Aufgabe; `aufgabe_link(projekt, aufgabe)` liefert den Link."""
    zeilen = []
    for (projekt, aufgabe), z in sorted(je_aufgabe.items()):
        teilweise = z['bekannt'] < z['laeufe']
        zeilen.append(t('tr', t('td', projekt), t('td', aufgabe_link(projekt, aufgabe) if aufgabe else t('span', 'ohne Aufgabe', class_='leise')),
                        t('td', z['laeufe']),
                        t('td', geld(z['summe'] if z['bekannt'] else None),
                          t('span', f' ({z["bekannt"]} von {z["laeufe"]} Läufen mit Kosten)', class_='leise') if teilweise and z['bekannt'] else None)))
    summe = sum(z['summe'] for z in je_aufgabe.values())
    kopf = t('thead', t('tr', [t('th', x) for x in ('Projekt', 'Aufgabe', 'Läufe', 'Kosten')]))
    fuss = t('tfoot', t('tr', t('th', 'Summe', colspan=3), t('th', geld(summe if any(z['bekannt'] for z in je_aufgabe.values()) else None))))
    return t('div', t('table', kopf, t('tbody', zeilen), fuss, class_='kosten'), class_='breit')


def kosten_laeufe(je_lauf, lauf_link, lauf_name, zeit, zustand):
    kopf = t('thead', t('tr', [t('th', x) for x in ('Lauf', 'Aufgabe', 'Zeit', 'Zustand', 'Kosten')]))
    zeilen = [t('tr', t('td', lauf_link(e['lauf'], lauf_name(e['lauf']))), t('td', e['lauf'].get('aufgabe') or '–'),
                t('td', zeit(e['lauf'].get('angelegt'))), t('td', zustand(e['lauf'])), t('td', geld(e['kosten']))) for e in je_lauf]
    return t('div', t('table', kopf, t('tbody', zeilen), class_='kosten'), class_='breit')


GRUPPEN_NAMEN = {'altbestand': 'Altbestand (ohne K-Kriterien)', 'kriterien': 'Mit K-Kriterien'}


def _anteil(n, m):
    return '–' if not m else f'{round(100 * n / m)} % ({n} von {m})'


def _dauer(s):
    if s is None:
        return '–'
    m = round(s / 60)
    return f'{m // 60} h {m % 60:02d} min' if m >= 60 else f'{m} min'


def _zahl(x):
    return f'{x:.1f}'.replace('.', ',')


def nacharbeit(daten):
    """Abschnitt „Nacharbeit“: je Kennzahl eine Zeile, Altbestand neben Aufgaben mit K-Kriterien (`kennzahlen.stand()`)."""
    def zeile(name, f):
        return t('tr', t('th', name, scope='row'), *[t('td', f(daten[g])) for g in kennzahlen.GRUPPEN])
    def befunde(d):
        return ', '.join(f'{k} {n}' for k, n in sorted(d['befunde'].items(), key=lambda x: (x[0] == kennzahlen.NICHT_ZUGEORDNET, x[0]))) or '–'
    def ursachen(d):
        return ', '.join(f'{u} {n}' for u, n in d['ursachen'].items()) if d['runden'] else '–'
    zeilen = [
        zeile('Aufgaben', lambda d: d['aufgaben']),
        zeile('Mit Nacharbeit nach Review', lambda d: _anteil(d['mit_nacharbeit'], d['bewertet'])),
        zeile('Nacharbeit wegen roter Tests', lambda d: _anteil(d['mit_tests'], d['aufgaben'])),
        zeile('Runden je Aufgabe', lambda d: _zahl(d['runden'] / d['aufgaben']) if d['aufgaben'] else '–'),
        zeile('Runden nach Ursache', ursachen),
        zeile('Befunde nach Kategorie', befunde),
        zeile('Aktive Zeit je Aufgabe', lambda d: _dauer(d['zeit'] / d['mit_zeit']) if d['mit_zeit'] else '–'),
    ]
    kopf = t('thead', t('tr', t('th', ''), *[t('th', GRUPPEN_NAMEN[g]) for g in kennzahlen.GRUPPEN]))
    return t('div', t('table', kopf, t('tbody', zeilen), class_='kosten'), class_='breit')
