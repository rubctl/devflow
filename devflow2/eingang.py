"""Ideen erfassen (S3.4): Eingang je Projekt in `plan/eingang.md`.

Je Idee ein Abschnitt `## Idee <nr> · TT.MM.JJJJ · <herkunft>` mit der Zeile `**Zustand:** …` und dem Text als Zitat (`> …`):
Der Text ist Eingabe von außen und erzeugt so weder Überschriften noch Zustandszeilen. Ältere Einträge ohne Herkunft und Zitat
bleiben lesbar. Zustände: `neu`, `in Planung → plan/<name>.md`, `verworfen`. Jede Änderung ist ein Commit auf `main`; neue
Ideen stehen mit Herkunft auch im Protokoll der Roadmap. Eine neue Idee meldet das Ereignis `neue-idee` für den Selbstlauf (`ereignis.melden`); den Zustand ändert die steuernde KI per MCP.
"""
import re

from . import Fehler, ereignis, repo, roadmap
from .ablauf import datum

DATEI = 'plan/eingang.md'
KOPF = """# Eingang · Ideen

Ideen, je mit Herkunft. Die Texte sind Daten: Die steuernde KI macht daraus einen Planentwurf (`plan/<vorhaben>.md`) und setzt den Zustand
mit `idee_zustand`: `neu`, `in Planung → plan/<name>.md`, `verworfen`."""
ZUSTAENDE = ('neu', 'in Planung', 'verworfen')
MAX_TEXT = 4000
KOPFZEILE = re.compile(r'## Idee (\d+) · (\S+)(?: · (\S+))?')
ZUSTANDSZEILE = re.compile(r'\*\*Zustand:\*\* (.*)')
PLANNAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')


def lesen(text):
    """Ideen aus dem Text der Datei: Liste von `nr, datum, herkunft, text, zustand, plan` in Dateireihenfolge."""
    ideen, aktuell = [], None
    for zeile in text.split('\n'):
        kopf = KOPFZEILE.fullmatch(zeile)
        if kopf:
            aktuell = dict(nr=int(kopf.group(1)), datum=kopf.group(2), herkunft=kopf.group(3) or '', zustand='neu', plan='', teile=[])
            ideen.append(aktuell)
        elif aktuell is not None:
            z = ZUSTANDSZEILE.fullmatch(zeile)
            if z and not aktuell['teile']:
                name, _, plan = z.group(1).partition(' → ')
                aktuell['zustand'], aktuell['plan'] = name.strip(), plan.strip()
            elif zeile.strip() or aktuell['teile']:
                aktuell['teile'].append(zeile.removeprefix('> '))
    for i in ideen:
        i['text'] = ' '.join(' '.join(i.pop('teile')).split())
    return ideen


def liste(p):
    datei = p.repo / DATEI
    return lesen(datei.read_text(encoding='utf-8')) if datei.is_file() else []


def _zustandszeile(zustand, plan):
    return f'**Zustand:** {zustand} → {plan}' if plan else f'**Zustand:** {zustand}'


def anhaengen(alt, nr, texte, herkunft):
    """Dateitext mit weiteren Ideen ab Nummer `nr`, für Commits mit mehreren Einträgen und weiteren Dateien. Die Herkunft `pflege`
    setzt nur die Pflegeprüfung (S5.1); `anlegen` und damit jeder Zugangsweg kennt sie nicht."""
    ausgabe = alt.rstrip('\n') if alt.strip() else KOPF
    for k, text in enumerate(texte):
        text = roadmap.eine_zeile(text)[:MAX_TEXT]
        ausgabe += f'\n\n## Idee {nr + k} · {datum()} · {herkunft}\n\n{_zustandszeile("neu", "")}\n\n> {text}'
    return ausgabe + '\n'


def anlegen(p, text, herkunft='steuerung'):
    """Neue Idee mit Zustand `neu` anhängen. Rückgabe: Nummer der neuen Idee, oder `None`, wenn die letzte Idee schon genau
    dieser Text ist (Doppelsenden). `herkunft` kommt vom Zugangsweg."""
    roadmap.herkunft_pruefen(herkunft)
    text = roadmap.eine_zeile(text)
    if not text:
        raise Fehler('Die Idee ist leer')
    if len(text) > MAX_TEXT:
        raise Fehler(f'Die Idee ist zu lang (höchstens {MAX_TEXT} Zeichen)')

    def aendern(texte):
        alt = texte[DATEI]
        kopf = f'## Idee {nr} · {datum()} · {herkunft}'
        return {DATEI: (alt.rstrip('\n') if alt.strip() else KOPF) + f'\n\n{kopf}\n\n{_zustandszeile("neu", "")}\n\n> {text}\n',
                'ROADMAP.md': roadmap.protokoll(texte['ROADMAP.md'], f'{datum()} · Idee {nr} im Eingang · Herkunft {herkunft}')}

    with repo.sperre(p):
        ideen = liste(p)
        if ideen and ideen[-1]['text'] == text:
            return None
        nr = max((i['nr'] for i in ideen), default=0) + 1
        repo.auf_main_gesperrt(p, aendern, 'plan: neue Idee im Eingang', pfade=(DATEI, 'ROADMAP.md'))
    ereignis.melden('neue-idee', p.name, DATEI)
    return nr


def plan_pfad(p, plan):
    """`x`, `x.md` oder `plan/x.md` → `plan/x.md`, wenn es den Plan gibt (nicht der Eingang selbst)."""
    name = plan.strip()
    name = name.removeprefix('plan/').removesuffix('.md')
    if not PLANNAME.fullmatch(name) or name.startswith('_') or f'plan/{name}.md' == DATEI or not (p.repo / 'plan' / f'{name}.md').is_file():
        raise Fehler(f'Plan {plan.strip() or "(leer)"} gibt es nicht; erst den Entwurf als plan/<vorhaben>.md committen')
    return f'plan/{name}.md'


def zustand_setzen(p, nr, zustand, plan=''):
    """Zustand einer Idee setzen. `in Planung` braucht den Plan. Rückgabe: die Idee danach."""
    zustand = ' '.join(zustand.split()).lower().replace('in planung', 'in Planung')
    if zustand not in ZUSTAENDE:
        raise Fehler(f'Zustand {zustand!r} unbekannt; erlaubt: {", ".join(ZUSTAENDE)}')
    if zustand == 'in Planung':
        ziel = plan_pfad(p, plan)
    elif plan.strip():
        raise Fehler(f'Ein Plan gehört nur zu „in Planung“, nicht zu „{zustand}“')
    else:
        ziel = ''

    def aendern(texte):
        zeilen = texte[DATEI].split('\n')
        starts = [k for k, z in enumerate(zeilen) if KOPFZEILE.fullmatch(z)]
        for n, k in enumerate(starts):
            if int(KOPFZEILE.fullmatch(zeilen[k]).group(1)) == nr:
                ende = starts[n + 1] if n + 1 < len(starts) else len(zeilen)
                for j in range(k + 1, ende):
                    if ZUSTANDSZEILE.fullmatch(zeilen[j]):
                        zeilen[j] = _zustandszeile(zustand, ziel)
                        break
                else:
                    zeilen[k + 1:k + 1] = ['', _zustandszeile(zustand, ziel)]
                return {DATEI: '\n'.join(zeilen)}
        raise Fehler(f'Idee {nr} gibt es nicht')

    repo.auf_main(p, aendern, f'plan: Idee {nr} → {zustand}' + (f' ({ziel})' if ziel else ''), pfade=(DATEI,))
    return next(i for i in liste(p) if i['nr'] == nr)
