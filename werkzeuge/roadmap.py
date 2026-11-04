#!/usr/bin/env python3
"""Erneuert in ROADMAP.md das Kanban unter „Aktuell“ aus den Aufgabentabellen und prüft die Statuswerte.

Aufruf: python3 werkzeuge/roadmap.py [ROADMAP.md]
Nur der Bereich zwischen den Markern wird ersetzt. Ein unbekannter Status bricht mit Zeilennummer ab.
"""
import re
import sys

START, END = '<!-- aktuell:start -->', '<!-- aktuell:end -->'
SPALTEN = ['Braucht dich', 'in Arbeit', 'Review', 'bereit', 'Plan', 'blockiert']
STATUS = SPALTEN + ['offen', 'erledigt', 'entfällt']


def title(task):
    bold = re.match(r'\*\*(.+?)\*\*', task)
    text = (bold.group(1) if bold else task).rstrip(':.')
    return text if len(text) <= 80 else text[:77].rsplit(' ', 1)[0] + ' …'


def rows(text):
    in_table = False
    for nr, line in enumerate(text.split('\n'), 1):
        if line.startswith('| ID |'):
            in_table = True
        elif not line.startswith('|'):
            in_table = False
        elif in_table and not line.startswith('|---'):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) != 6:
                raise SystemExit(f'ROADMAP Zeile {nr}: {len(cells)} statt 6 Spalten')
            if cells[3] not in STATUS:
                raise SystemExit(f'ROADMAP Zeile {nr}: Status „{cells[3]}“ unbekannt, erlaubt: {", ".join(STATUS)}')
            yield cells


def section(text):
    found = list(rows(text))
    out = [START, '', '_Erzeugt mit `python3 werkzeuge/roadmap.py`; nicht von Hand ändern._']
    for spalte in SPALTEN:
        hits = [c for c in found if c[3] == spalte]
        if not hits:
            out += ['', f'**{spalte}** –']
            continue
        out += ['', f'**{spalte}** ({len(hits)})', '']
        out += [f'- `{c[0]}` {title(c[1])} · {c[2] or "–"} · {c[4]}' for c in hits]
    done = sum(c[3] == 'erledigt' for c in found)
    out += ['', f'Offen: {sum(c[3] == "offen" for c in found)} · erledigt: {done}', '', END]
    return '\n'.join(out)


def update(text):
    if START not in text or END not in text:
        raise SystemExit('Marker für „Aktuell“ fehlen')
    head, rest = text.split(START, 1)
    return head + section(text) + rest.split(END, 1)[1]


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'ROADMAP.md'
    with open(path, encoding='utf-8') as f:
        new = update(f.read())
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new)
