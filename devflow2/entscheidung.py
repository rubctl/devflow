"""Entscheidungsliste (S14.3): die Logik hinter den Knöpfen in „Braucht dich“, ohne HTML.

- **Eindeutige Empfehlung:** Eine offene Frage trägt `**Optionen:**` (nummeriert 1, 2, …) und `**Empfehlung:**`. Der Knopf
  „Empfehlung annehmen“ erscheint nur, wenn die Empfehlung genau eine Option bezeichnet: Sie beginnt mit „Option N“ oder „N.“
  (N gibt es, die Nummern sind lückenlos und ohne Doppelte; Positivliste: dahinter steht nur noch nichts, Satzzeichen oder der Optionstext
  selbst, ganz oder als Anfang; Klammern, Begründungen und Zusätze machen die Empfehlung nicht eindeutig, dann bleibt „Antworten“ in den
  Details), oder sie ist wörtlich der Text genau einer Option. Alles andere (Prosa, „keine“, „siehe Frage“) ist nicht eindeutig.
- **Bindung an den Stand:** `frage_stand` ist die Prüfsumme des ganzen Frageblocks (Frage, Optionen, Empfehlung). Ändert sich
  etwas davon, lehnt die Handlung ab. `lauf_stand` bindet „Fortsetzen“ an Zustand, Ende und Fehlertext des angezeigten Laufs.
- **Fortsetzen:** `fortsetzen_grund` sagt, warum ein Lauf gerade nicht fortgesetzt werden darf (None = zulässig).
"""
import hashlib
import re

from . import Fehler, ablauf, laeufe
from .projekte import Projekt

MARKE_OPTIONEN = '**Optionen:**'
MARKE_EMPFEHLUNG = '**Empfehlung:**'
OPTION = re.compile(r'(\d+)\. (.+)')
NUMMER = re.compile(r'(?:Option\s+(\d+)\b(?!\s*[.,]\d)|(\d+)[.)])(.*)', re.S)


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def frage_stand(frage):
    """Prüfsumme des Frageblocks (`roadmap.fragen`-Eintrag), so wie er angezeigt wurde."""
    return _hash(frage['text'])


def lauf_stand(l):
    return _hash('\n'.join(str(l.get(k) or '') for k in ('id', 'zustand', 'ende', 'fehler', 'login')))


def optionen_und_empfehlung(text):
    """(Optionen {nr: text}, Empfehlungstext) aus dem Frageblock; (None, None), wenn ein Teil fehlt, doppelt vorkommt oder die Nummern nicht 1..n sind."""
    zeilen = text.split('\n')
    kopf = [i for i, z in enumerate(zeilen) if z == MARKE_OPTIONEN]
    empf = [i for i, z in enumerate(zeilen) if z.startswith(MARKE_EMPFEHLUNG)]
    if len(kopf) != 1 or len(empf) != 1 or empf[0] < kopf[0]:
        return None, None
    optionen = {}
    for z in zeilen[kopf[0] + 1:empf[0]]:
        m = OPTION.fullmatch(z.strip()) if z.strip() else None
        if m:
            nr = int(m.group(1))
            if nr in optionen:
                return None, None
            optionen[nr] = m.group(2).strip()
    if not optionen or sorted(optionen) != list(range(1, len(optionen) + 1)):
        return None, None
    rest = zeilen[empf[0]][len(MARKE_EMPFEHLUNG):].strip()
    if not rest:
        rest = ' '.join(z[1:].strip() for z in zeilen[empf[0] + 1:] if z.startswith('>')).strip()
    return optionen, rest


def _nur_diese(rest, option):
    """Positivliste: Hinter der Nummer steht nichts, nur Satzzeichen oder (der Anfang von) dem Optionstext."""
    rest = rest.strip(' \t\n.,:;)–—-').rstrip('.')
    return not rest or option.rstrip('.').casefold().startswith(rest.casefold())


def empfehlung(frage):
    """(Nummer, Optionstext) der eindeutigen Empfehlung einer Frage, sonst None."""
    optionen, rest = optionen_und_empfehlung(frage['text'])
    if not optionen or not rest:
        return None
    m = NUMMER.fullmatch(rest)
    if m:
        nr = int(m.group(1) or m.group(2))
        if nr in optionen and _nur_diese(m.group(3), optionen[nr]):
            return nr, optionen[nr]
        return None
    gleich = [nr for nr, o in optionen.items() if o.rstrip('.').casefold() == rest.rstrip('.').casefold()]
    return (gleich[0], optionen[gleich[0]]) if len(gleich) == 1 else None


def antwort_text(nr, option):
    return f'Empfehlung angenommen: Option {nr}, {option}'


def fortsetzen_grund(l):
    """None, wenn der Lauf sich fortsetzen lässt, sonst der Grund in einem Satz. Nur die Vorab-Prüfung: Die Handlung prüft erneut."""
    if l.get('traum'):
        return laeufe.TRAUM_FORT
    if l['zustand'] in laeufe.AKTIV:
        return 'Der Lauf läuft schon.'
    if laeufe.angehalten():
        return 'Fortsetzen geht erst nach „Alles fortsetzen“.'
    if l['art'] == 'steuern' and any(x['art'] == 'steuern' and x['id'] != l['id'] and x['zustand'] in laeufe.AKTIV for x in laeufe.alle()):
        return 'Es läuft schon ein Steuerlauf.'
    try:
        p = Projekt(l['projekt'])
        if l['art'] == 'deploy':
            ablauf._deploy_gesperrt(p, l.get('aufgabe'))
        elif l.get('aufgabe'):
            ablauf._gesperrt(ablauf.offene_fragen(p, l['aufgabe']))
    except Fehler as err:
        return str(err)
    return None
