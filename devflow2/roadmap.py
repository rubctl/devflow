"""ROADMAP.md und Pläne lesen und ändern: Aufgabentabellen, Kanban unter „Aktuell“, offene Fragen, Protokoll.

Format wie vorlage/projekt in devflow; das Kanban entspricht dort werkzeuge/roadmap.py. Alle Funktionen
arbeiten auf Text und geben neuen Text zurück; Schreiben und Committen macht repo.py.
"""
import re

from . import Fehler
from . import inhaber as _i

START, END = '<!-- aktuell:start -->', '<!-- aktuell:end -->'
SPALTEN = ['Braucht dich', 'in Arbeit', 'Review', 'bereit', 'Plan', 'blockiert']
STATUS = SPALTEN + ['offen', 'erledigt', 'entfällt']
KOPF = '| ID | Aufgabe | Wer | Status | Datum | Details |\n|---|---|---|---|---|---|'
PLANKOPF = '| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium |'
PLANKOPF_STUFE = PLANKOPF + ' Stufe |'
ABNAHME = _i.Muster(lambda: r'^\*\*Stand:\*\*\s*abgenommen ' + _i.von_muster() + r' am\s+(\S+)', re.M)
STAND = re.compile(r'^\*\*Stand:\*\*(.*)$', re.M)


def rueckmeldung_kopf():
    """Überschrift des Abschnitts mit Änderungswünschen, so geschrieben, wie die Instanz den Inhaber nennt."""
    return f'## Rückmeldung {_i.kennung()}'


def _rueckmeldung_koepfe():
    return [f'## Rückmeldung {n}' for n in _i.namen()]


def hat_rueckmeldung(text):
    return any(k in _rueckmeldung_koepfe() for k in text.split('\n'))


def offen_marker():
    return f'**Antwort {_i.kennung()}:** _offen_'


def _offen_marker():
    return [f'**Antwort {n}:** _offen_' for n in _i.namen()]
HERKUNFT = ('dashboard', 'steuerung', 'nutzer')  # setzt devflow aus dem Zugangsweg, nie aus dem Text
AKTUELL = re.compile(rf'^{re.escape(START)}$.*?^{re.escape(END)}$', re.M | re.S)
ZEILENENDE = re.compile(r'\r\n?|[\x0b\x0c\x1c-\x1e\x85\u2028\u2029]')
STEUERZEICHEN = re.compile(r'[\x00-\x08\x0e-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff\ud800-\udfff]')


def sauber(text):
    """Freitext ohne Steuerzeichen und unsichtbare Richtungswechsel; jedes Zeilenende wird zu `\\n`."""
    return STEUERZEICHEN.sub('', ZEILENENDE.sub('\n', str(text))).strip()


def eine_zeile(text):
    return ' '.join(sauber(text).split())


def zelle(text):
    return eine_zeile(text).replace('|', '/')


def kapsel(kennung, text):
    """Freitext als Zeilen hinter einer festen Kennung: Eine Zeile bleibt dahinter, mehrere werden ein Zitatblock.

    So beginnt keine Zeile mit Text von außen, und alles, was die Struktur liest (Überschriften, Marker, Tabellen, Status),
    sieht sie nicht. Der Zitatblock ist normales Markdown; die Anzeige bleibt lesbar.
    """
    zeilen = sauber(text).split('\n')
    if len(zeilen) == 1:
        return [f'{kennung} {zeilen[0]}'.rstrip()]
    return [kennung, ''] + [f'> {z}'.rstrip() for z in zeilen]


def herkunft_pruefen(herkunft):
    if herkunft not in HERKUNFT:
        raise Fehler(f'Herkunft {herkunft!r} unbekannt, erlaubt: {", ".join(HERKUNFT)}')
    return herkunft


def _zellen(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def zeilen(text):
    """(Zeilenindex, Zellen) aller Aufgaben in Tabellen mit Kopf `| ID |`."""
    in_table = False
    for i, line in enumerate(text.split('\n')):
        if line.startswith('| ID |'):
            in_table = True
        elif not line.startswith('|'):
            in_table = False
        elif in_table and not line.startswith('|---'):
            cells = _zellen(line)
            if len(cells) != 6:
                raise Fehler(f'ROADMAP Zeile {i + 1}: {len(cells)} statt 6 Spalten')
            yield i, cells


def aufgaben(text):
    return [dict(id=c[0], aufgabe=c[1], wer=c[2], status=c[3], datum=c[4], details=c[5]) for _, c in zeilen(text)]


def finde(text, aufgabe):
    for i, cells in zeilen(text):
        if cells[0] == aufgabe:
            return i, cells
    raise Fehler(f'Aufgabe {aufgabe} steht nicht in ROADMAP.md')


def _teile(details):
    if details.startswith('Stand: '):
        return '', details[len('Stand: '):]
    frei, _, stand = details.partition(' · Stand: ')
    return frei, stand


def setze(text, aufgabe, tag, status=None, wer=None, details=None, stand=None):
    """Zeile ändern. `details` ersetzt den freien Teil, `stand` den von devflow gepflegten Teil „Stand: …“."""
    if status is not None and status not in STATUS:
        raise Fehler(f'Status {status!r} unbekannt, erlaubt: {", ".join(STATUS)}')
    i, c = finde(text, aufgabe)
    if status is not None and status != c[3]:
        c[3], c[4] = status, tag
    if wer is not None:
        c[2] = zelle(wer)
    frei, alt = _teile(c[5])
    frei = zelle(details) if details is not None else frei
    alt = zelle(stand) if stand is not None else alt
    c[5] = ' · '.join(x for x in (frei, f'Stand: {alt}' if alt else '') if x)
    lines = text.split('\n')
    lines[i] = '| ' + ' | '.join(c) + ' |'
    return '\n'.join(lines)


def _abschnitt(lines, titel, ebene=None):
    """`titel`: ein Titel oder mehrere zulässige (der erste steht in der Fehlermeldung)."""
    titel = [titel] if isinstance(titel, str) else titel
    for i, line in enumerate(lines):
        if line.startswith('#') and line.lstrip('#').strip() in titel and (ebene is None or line.startswith(ebene + ' ')):
            return i
    raise Fehler(f'Abschnitt „{titel[0]}“ fehlt in ROADMAP.md')


def anlegen(text, aufgabe, titel, tag, wer='', status='offen', details='', abschnitt=None):
    """Neue Zeile am Ende der ersten Aufgabentabelle unter `abschnitt` (Standard: „Aufgaben“)."""
    if status not in STATUS:
        raise Fehler(f'Status {status!r} unbekannt')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', aufgabe):
        raise Fehler(f'ID {aufgabe!r} ungeeignet (Buchstaben, Ziffern, Punkt, Bindestrich)')
    if any(c[0] == aufgabe for _, c in zeilen(text)):
        raise Fehler(f'Aufgabe {aufgabe} gibt es schon')
    lines = text.split('\n')
    start = _abschnitt(lines, abschnitt or 'Aufgaben')
    kopf = next((j for j in range(start, len(lines)) if lines[j].startswith('| ID |')), None)
    if kopf is None:
        raise Fehler(f'Keine Aufgabentabelle unter „{abschnitt or "Aufgaben"}“')
    ende = kopf + 2
    while ende < len(lines) and lines[ende].startswith('|'):
        ende += 1
    lines.insert(ende, '| ' + ' | '.join(zelle(x) for x in (aufgabe, titel, wer, status, tag, details)) + ' |')
    return '\n'.join(lines)


def neue_tabelle(text, ueberschrift, rows):
    """Eigene Tabelle (z. B. je übernommenem Plan) vor dem Protokoll."""
    vorhanden = {c[0] for _, c in zeilen(text)}
    doppelt = [r[0] for r in rows if r[0] in vorhanden]
    if doppelt:
        raise Fehler(f'IDs gibt es schon in der Roadmap: {", ".join(doppelt)}')
    block = [f'### {ueberschrift}', '', KOPF] + ['| ' + ' | '.join(zelle(x) for x in r) + ' |' for r in rows] + ['']
    lines = text.split('\n')
    try:
        ziel = _abschnitt(lines, 'Protokoll', '##')
    except Fehler:
        ziel = len(lines)
    lines[ziel:ziel] = block
    return '\n'.join(lines)


def protokoll(text, eintrag):
    lines = text.split('\n')
    h = _abschnitt(lines, 'Protokoll', '##')
    i = h + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i == len(lines):
        lines[h + 1:] = ['', f'- {zelle(eintrag)}', '']
    else:
        lines.insert(i, f'- {zelle(eintrag)}')
    return '\n'.join(lines)


def _fragebloecke(lines):
    for i, line in enumerate(lines):
        m = re.match(r'### F(\d+) · ', line)
        if m:
            j = i + 1
            while j < len(lines) and not lines[j].startswith('#'):
                j += 1
            yield int(m.group(1)), i, j


def frage(text, tag, frage_text, optionen, empfehlung, bezug=''):
    lines = text.split('\n')
    s = _abschnitt(lines, [f'Offene Fragen {a}' for a in _i.an_formen()], '##')
    nr = 1 + max([n for n, _, _ in _fragebloecke(lines)], default=0)
    block = [f'### F{nr} · {tag} · {zelle(bezug) or "allgemein"}', '', *kapsel('**Frage:**', frage_text), '', '**Optionen:**']
    block += [f'{k}. {zelle(o)}' for k, o in enumerate(optionen, 1)] or ['- keine vorgegeben']
    block += ['', *kapsel('**Empfehlung:**', sauber(empfehlung) or 'keine'), '', offen_marker(), '']
    ende = next((j for j in range(s + 1, len(lines)) if lines[j].startswith('## ')), len(lines))
    erste = next((j for j in range(s + 1, ende) if lines[j].startswith('### F')), ende)
    lines[erste:erste] = block
    lines = [l for k, l in enumerate(lines) if not (s < k < ende + len(block) and l.strip() == '_Keine._')]
    return '\n'.join(lines), nr


def fragen(text, nur_offen=True):
    lines = text.split('\n')
    out = []
    for nr, i, j in _fragebloecke(lines):
        offen = any(m in lines[i:j] for m in _offen_marker())  # ganze Zeile: im Freitext zählt der Marker nicht
        if offen or not nur_offen:
            out.append(dict(nr=nr, bezug=lines[i].split(' · ')[-1], offen=offen, text='\n'.join(lines[i:j]).strip()))
    return out


def antwort_name(herkunft):
    """Der Name des Inhabers steht nur dort, wo er selbst geantwortet hat: Das Dashboard erreicht nur sein Login."""
    return f'Antwort {_i.kennung()}' if herkunft == 'dashboard' else 'Antwort'


def antwort(text, nr, tag, antwort_text, herkunft):
    herkunft_pruefen(herkunft)
    if not sauber(antwort_text):
        raise Fehler('Die Antwort ist leer')
    lines = text.split('\n')
    for n, i, j in _fragebloecke(lines):
        if n == nr:
            for k in range(i, j):
                if lines[k] in _offen_marker():
                    lines[k:k + 1] = kapsel(f'**{antwort_name(herkunft)} ({tag}, {herkunft}):**', antwort_text)
                    return '\n'.join(lines), lines[i].split(' · ')[-1]
            raise Fehler(f'Frage F{nr} ist schon beantwortet')
    raise Fehler(f'Frage F{nr} gibt es nicht')


def _titel(task):
    bold = re.match(r'\*\*(.+?)\*\*', task)
    text = (bold.group(1) if bold else task).rstrip(':.')
    return text if len(text) <= 80 else text[:77].rsplit(' ', 1)[0] + ' …'


def kanban(text):
    """Abschnitt „Aktuell“ neu erzeugen; gleiche Ausgabe wie werkzeuge/roadmap.py der Vorlage."""
    if not AKTUELL.search(text):  # nur ganze Zeilen: Marker im Freitext zählen nicht
        raise Fehler('Marker für „Aktuell“ fehlen in ROADMAP.md')
    found = [c for _, c in zeilen(text)]
    for c in found:
        if c[3] not in STATUS:
            raise Fehler(f'Aufgabe {c[0]}: Status {c[3]!r} unbekannt')
    out = [START, '', '_Erzeugt mit `python3 werkzeuge/roadmap.py`; nicht von Hand ändern._']
    for spalte in SPALTEN:
        hits = [c for c in found if c[3] == spalte]
        if not hits:
            out += ['', f'**{spalte}** –']
            continue
        out += ['', f'**{spalte}** ({len(hits)})', '']
        out += [f'- `{c[0]}` {_titel(c[1])} · {c[2] or "–"} · {c[4]}' for c in hits]
    done = sum(c[3] == 'erledigt' for c in found)
    out += ['', f'Offen: {sum(c[3] == "offen" for c in found)} · erledigt: {done}', '', END]
    return AKTUELL.sub(lambda _: '\n'.join(out), text, count=1)


def plan(text):
    """Titel, Abnahmedatum (oder None) und Aufgaben eines Plans aus plan/<vorhaben>.md."""
    titel = next((l[2:].strip() for l in text.split('\n') if l.startswith('# ')), 'Plan')
    m = ABNAHME.search(text)
    rows, in_table = [], False
    for line in text.split('\n'):
        if line.startswith('|') and _zellen(line) in (_zellen(PLANKOPF), _zellen(PLANKOPF_STUFE)):
            in_table, spalten = True, len(_zellen(line))
        elif not line.startswith('|'):
            in_table = False
        elif in_table and not line.startswith('|---'):
            c = _zellen(line)
            if len(c) != spalten:
                raise Fehler(f'Planaufgabe mit {len(c)} statt {spalten} Spalten: {line[:80]}')
            rows.append(dict(id=c[0], aufgabe=c[1], entwickler=c[2].lower(), bereich=c[3], kriterium=c[4],
                             stufe=c[5].lower() if spalten == 6 else ''))
    return dict(titel=titel, abgenommen=m.group(1) if m else None, aufgaben=rows)


def plan_stand(text):
    """Text der ersten Zeile `**Stand:** …` eines Plans (ohne Kennung), None ohne diese Zeile."""
    m = STAND.search(text)
    return m.group(1).strip() if m else None


def plan_abnehmen(text, datum, stufen, aufgaben=True):
    """Plan als vom Inhaber abgenommen markieren: die Zeile `**Stand:**` wie in der Vorlage und die Stufen in der Planspalte `Stufe`.

    `stufen`: Aufgaben-ID → Stufe (leer = Stufe des Projekts); nicht genannte Aufgaben behalten ihre Stufe. Hat die Tabelle
    noch keine Spalte `Stufe`, wird sie angehängt. Fehler: schon abgenommen, keine Stand-Zeile, keine Aufgabentabelle (außer `aufgaben=False`), unbekannte ID.
    """
    if ABNAHME.search(text):
        raise Fehler('Der Plan ist schon abgenommen')
    if not STAND.search(text):
        raise Fehler('Im Plan fehlt die Zeile „**Stand:** …“')
    ids = {a['id'] for a in plan(text)['aufgaben']}
    if aufgaben and not ids:
        raise Fehler(f'Im Plan fehlt die Aufgabentabelle „{PLANKOPF}“')
    if set(stufen) - ids:
        raise Fehler(f'Stufe für unbekannte Aufgabe: {", ".join(sorted(set(stufen) - ids))}')
    aus, in_table, vorhanden = [], False, False
    for line in text.split('\n'):
        zellen = _zellen(line) if line.startswith('|') else None
        if zellen in (_zellen(PLANKOPF), _zellen(PLANKOPF_STUFE)):
            in_table, vorhanden = True, len(zellen) == 6
            line = line if vorhanden else line.rstrip() + ' Stufe |'
        elif zellen is None:
            in_table = False
        elif in_table and line.startswith('|---'):
            line = line if vorhanden else line.rstrip() + '---|'
        elif in_table:
            neu = stufen.get(zellen[0], zellen[5] if vorhanden else '')
            kopf = line.rstrip()[:-1].rsplit('|', 1)[0] if vorhanden else line.rstrip()[:-1].rstrip()
            line = f'{kopf}{"" if vorhanden else " "}| {neu + " " if neu else ""}|'
        aus.append(line)
    return STAND.sub(f'**Stand:** abgenommen {_i.von()} am {datum}', '\n'.join(aus), count=1)


def plan_rueckmeldung(text, datum, wunsch):
    """Änderungswunsch als Eintrag `- **TT.MM.JJJJ:** …` im Abschnitt `## Rückmeldung <Inhaber>` (wird angelegt, sonst ergänzt).
    Derselbe Wunsch als letzter Eintrag ändert nichts."""
    eintrag = f'- **{datum}:** {eine_zeile(wunsch)}'
    zeilen = text.rstrip('\n').split('\n')
    kopf = next((z for z in zeilen if z in _rueckmeldung_koepfe()), None)
    if kopf is None:
        return '\n'.join(zeilen + ['', rueckmeldung_kopf(), '', eintrag]) + '\n'
    i = zeilen.index(kopf) + 1
    while i < len(zeilen) and not zeilen[i].startswith('## '):
        i += 1
    while zeilen[i - 1] == '':
        i -= 1
    if zeilen[i - 1] == eintrag:
        return text
    return '\n'.join(zeilen[:i] + [eintrag] + ([''] if i < len(zeilen) and zeilen[i] else []) + zeilen[i:]) + '\n'


SONSTIGES = 'Sonstiges'
PLANLINK = re.compile(r'\[Plan\]\(([^)\s]+)\)')


def vorhaben(text):
    """Aufgaben je Vorhaben: eine Tabelle unter `### …` ist ein Vorhaben, wenn die Überschrift `[Plan](…)` enthält;
    alle Tabellen ohne solche Überschrift (auch unter `### Wartung`) sammeln sich unter „Sonstiges“ (immer als letztes, nur wenn nicht leer).
    Zählung: `entfällt` zählt weder zu `gesamt` noch zu `erledigt`."""
    gruppen, sonstiges, aktuell = [], [], None
    in_table, ueberschrift = False, None
    for line in text.split('\n'):
        if line.startswith('#'):
            ueberschrift = line.lstrip('#').strip() if line.startswith('### ') else None
            in_table = False
        elif line.startswith('| ID |'):
            in_table = True
            aktuell = sonstiges
            m = PLANLINK.search(ueberschrift) if ueberschrift is not None else None
            if m:
                titel = PLANLINK.sub('', ueberschrift).replace('()', '').strip(' ·()')
                aktuell = []
                gruppen.append(dict(titel=titel or ueberschrift, plan=m.group(1), aufgaben=aktuell))
        elif not line.startswith('|'):
            in_table = False
        elif in_table and not line.startswith('|---'):
            c = _zellen(line)
            if len(c) != 6:
                raise Fehler(f'ROADMAP: {len(c)} statt 6 Spalten: {line[:60]}')
            aktuell.append(dict(id=c[0], aufgabe=c[1], wer=c[2], status=c[3], datum=c[4], details=c[5]))
    if sonstiges:
        gruppen.append(dict(titel=SONSTIGES, plan=None, aufgaben=sonstiges))
    for g in gruppen:
        zaehlbar = [a for a in g['aufgaben'] if a['status'] != 'entfällt']
        g['gesamt'] = len(zaehlbar)
        g['erledigt'] = sum(a['status'] == 'erledigt' for a in zaehlbar)
        g['verteilung'] = {s: sum(a['status'] == s for a in g['aufgaben']) for s in STATUS if any(a['status'] == s for a in g['aufgaben'])}
    return gruppen
