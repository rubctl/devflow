"""Masterplan und Grundentscheidungen eines Projekts: lesen, für Aufträge aufbereiten, Stand festhalten, Merge-Tor.

Gelesen wird der committete Stand der Basis (`devflow.toml: basis`), nie das Arbeitsverzeichnis: Was nicht auf der Basis liegt, gilt noch nicht,
und ein Lauf kann sich seine Vorgaben nicht selbst schreiben. Ohne `MASTERPLAN.md` und ohne bestätigte Entscheidung ist alles leer, und die Aufträge
bleiben, wie sie waren.

- `MASTERPLAN.md` im Wurzelverzeichnis: nur der Inhaber ändert ihn.
- `docs/entscheidungen/NNNN-*.md`: bindend ist nur, was im Kopf (vor der ersten `##`-Überschrift) die Zeile `**Status:** bestätigt vom Inhaber`
  trägt (optional ` am TT.MM.JJJJ`). `vorgeschlagen`, `ersetzt durch NNNN` und jede fehlende oder andere Angabe binden nicht. Die Zeile
  `**Betrifft:** begriff, pfad/, …` grenzt ein, für welche Aufgaben die Entscheidung vollständig in den Auftrag kommt (`alle` = immer; fehlt die Zeile,
  gilt sie für jede Aufgabe, damit nichts Bindendes fehlt).
- `docs/architektur.md`, Abschnitt `## Überblick`: der kurze Überblick.
"""
import hashlib
import re

from . import Fehler
from . import inhaber as _i
from .repo import git

MASTERPLAN = 'MASTERPLAN.md'
ENTSCHEIDUNGEN = 'docs/entscheidungen'
ARCHITEKTUR = 'docs/architektur.md'
GRENZE_ZEILEN = 70  # „etwa eine Seite“
GRENZE_ZEICHEN = 5000
UEBERBLICK_MAX = 1500
NAME = re.compile(r'\d{4}-[^/]+\.md')
STATUS = re.compile(r'^\*\*Status:\*\*[ \t]*(.*?)[ \t]*$', re.M)
BETRIFFT = re.compile(r'^\*\*Betrifft:\*\*[ \t]*(.*?)[ \t]*$', re.M)
BINDEND = _i.Muster(lambda: r'bestätigt ' + _i.von_muster() + r'(?: am \d\d\.\d\d\.\d{4})?')
KOPF_ENDE = re.compile(r'^## ', re.M)


class VorgabenGeaendert(Fehler):
    """Masterplan oder bestätigte Entscheidungen sind nach dem Review anders als zu dessen Beginn. `sachen`: was sich geändert hat."""

    def __init__(self, sachen):
        super().__init__('; '.join(sachen))
        self.sachen = sachen


def _lesen(p, ref, pfad):
    return git(p.repo, 'show', f'{ref}:{pfad}', check=False)


def masterplan(p, ref=None):
    return _lesen(p, ref or p.cfg['basis'], MASTERPLAN)


def entscheidung(datei, text):
    kopf = KOPF_ENDE.split(text, maxsplit=1)[0]
    m, b = STATUS.search(kopf), BETRIFFT.search(kopf)
    status = m.group(1).strip() if m else ''
    titel = next((l[2:].strip() for l in text.split('\n') if l.startswith('# ')), datei)
    return dict(datei=datei, nr=datei.rsplit('/', 1)[-1][:4], titel=titel, status=status, bindend=bool(BINDEND.fullmatch(status)), text=text,
                betrifft=[x.strip().lower() for x in b.group(1).split(',') if x.strip()] if b else None)


def entscheidungen(p, ref=None, nur_bindend=True):
    ref = ref or p.cfg['basis']
    namen = git(p.repo, 'ls-tree', '--name-only', ref, f'{ENTSCHEIDUNGEN}/', check=False).split('\n')
    alle = [entscheidung(n, _lesen(p, ref, n)) for n in sorted(namen) if n and NAME.fullmatch(n.rsplit('/', 1)[-1])]
    return [e for e in alle if e['bindend'] or not nur_bindend]


def _betrifft(e, text):
    """Kommt die Entscheidung vollständig in den Auftrag? Ohne Betrifft-Zeile immer; sonst bei `alle` oder wenn der Aufgabentext einen Begriff
    nennt (ohne Aufgabentext, also bei Planung, Steuern und Traum, nur bei `alle`)."""
    if e['betrifft'] is None:
        return True
    if 'alle' in e['betrifft']:
        return True
    text = (text or '').lower()
    return any(b in text for b in e['betrifft'])


def ueberblick(p, ref=None):
    text = _lesen(p, ref or p.cfg['basis'], ARCHITEKTUR)
    m = re.search(r'^## Überblick[ \t]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    return (m.group(1).strip() if m else '')[:UEBERBLICK_MAX]


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]


def stand(p, ref=None):
    """Bestätigter Stand: Prüfsumme des Masterplans und jeder bindenden Entscheidung, Datei → Prüfsumme."""
    ref = ref or p.cfg['basis']
    out = {MASTERPLAN: _hash(t)} if (t := masterplan(p, ref)) else {}
    out.update({e['datei']: _hash(e['text']) for e in entscheidungen(p, ref)})
    return out


def unterschied(alt, neu):
    """Was sich zwischen zwei Ständen geändert hat, als Zeilen für Frage und Prüfauftrag."""
    sachen = []
    for datei in sorted({*alt, *neu}):
        if datei not in alt:
            sachen.append(f'{datei}: neu' + (' (bestätigt)' if datei != MASTERPLAN else ''))
        elif datei not in neu:
            sachen.append(f'{datei}: entfernt' if datei == MASTERPLAN else f'{datei}: nicht mehr bestätigt (ersetzt, zurückgenommen oder gelöscht)')
        elif alt[datei] != neu[datei]:
            sachen.append(f'{datei}: geändert')
    return sachen


def fuer(p, text=None, ref=None):
    """Grundlage für einen Auftrag: `text` (Block fürs Prompt, leer ohne Vorgaben) und `stand`. `text`-Parameter: Aufgabentext, nach dem die
    Entscheidungen ausgewählt werden; None für Läufe ohne Aufgabe (Planung, Steuern, Traum)."""
    ref = ref or p.cfg['basis']
    mp, ent = masterplan(p, ref), entscheidungen(p, ref)
    st = {MASTERPLAN: _hash(mp)} if mp else {}
    st.update({e['datei']: _hash(e['text']) for e in ent})
    if not mp and not ent:
        return dict(text='', stand=st)
    teile = []
    if mp:
        teile.append(f'Masterplan des Projekts (`{MASTERPLAN}`, verbindlich; nur {_i.nom()} ändert ihn). Kernzusagen sind das, was immer stimmen muss:\n\n{mp}')
    if ü := ueberblick(p, ref):
        teile.append(f'Überblick zur Technik (`{ARCHITEKTUR}`):\n\n{ü}')
    voll = [e for e in ent if _betrifft(e, text)]
    if voll:
        teile.append('Bestätigte Grundentscheidungen, die diese Aufgabe berühren (verbindlich, vollständig):\n\n'
                     + '\n\n'.join(f'--- {e["datei"]} ---\n{e["text"].strip()}' for e in voll))
    rest = [e for e in ent if e not in voll]
    if rest:
        teile.append('Weitere bestätigte Grundentscheidungen (verbindlich; nur die Titel, den Wortlaut bei Bedarf in `docs/entscheidungen/` lesen):\n'
                     + '\n'.join(f'- {e["datei"]}: {e["titel"]}' for e in rest))
    if ent:
        teile.append(f'Nur „bestätigt {_i.von()}“ bindet. Entscheidungen mit Status „vorgeschlagen“ oder „ersetzt“ sind Vorschläge oder Geschichte, '
                     'keine Vorgabe.')
    return dict(text='\n\n'.join(teile), stand=st)


def fuer_rueckblick(namen):
    """Masterplan und Titel der bestätigten Entscheidungen jedes Projekts, das welche hat, für den wöchentlichen Rückblick über alle Projekte.
    Der Stand hält je Projekt dieselben Prüfsummen fest wie `stand` ('<projekt>: <datei>')."""
    from .projekte import Projekt
    teile, st = [], {}
    for name in namen:
        try:
            p = Projekt(name)
            mp, ent = masterplan(p), entscheidungen(p)
        except Fehler:
            continue
        if not mp and not ent:
            continue
        zeilen = [f'### {name}']
        if mp:
            zeilen.append(mp)
        if ent:
            zeilen.append('Bestätigte Grundentscheidungen (verbindlich):\n' + '\n'.join(f'- {e["datei"]}: {e["titel"]}' for e in ent))
        teile.append('\n\n'.join(zeilen))
        st.update({f'{name}: {d}': h for d, h in stand(p).items()})
    return dict(text=('Masterplan und bestätigte Entscheidungen je Projekt (verbindlich; Kernzusagen sind das, was immer stimmen muss):\n\n'
                      + '\n\n'.join(teile)) if teile else '', stand=st)


def hinweise(p):
    """Hinweise für die Lage: kein Masterplan, oder er ist länger als etwa eine Seite."""
    mp = masterplan(p)
    if not mp:
        return ['kein Masterplan']
    zeilen = len(mp.split('\n'))
    if zeilen > GRENZE_ZEILEN or len(mp) > GRENZE_ZEICHEN:
        return [f'Masterplan zu lang ({zeilen} Zeilen, {len(mp)} Zeichen; Grenze etwa {GRENZE_ZEILEN} Zeilen und {GRENZE_ZEICHEN} Zeichen): kürzen']
    return []


def gesperrte_aenderungen(p, branch):
    """Änderungen am Branch gegenüber seinem Abzweig von der Basis, die nur der Inhaber machen darf: `MASTERPLAN.md` und jede Entscheidung, die vor
    oder nach der Änderung bestätigt ist. Vorschläge (nur `vorgeschlagen`) darf ein Lauf anlegen und ändern. Gilt auch nach einem Rebase."""
    basis = p.cfg['basis']
    abzweig = git(p.repo, 'merge-base', basis, branch)
    namen = [n for n in git(p.repo, 'diff', '--name-only', '--no-renames', f'{abzweig}..{branch}').split('\n') if n]
    out = []
    for n in namen:
        if n == MASTERPLAN:
            out.append(f'{n} (nur {_i.nom()} ändert den Masterplan)')
        elif n.rsplit('/', 1)[0] == ENTSCHEIDUNGEN and NAME.fullmatch(n.rsplit('/', 1)[-1]):
            vorher, nachher = entscheidung(n, _lesen(p, abzweig, n)), entscheidung(n, _lesen(p, branch, n))
            if vorher['bindend'] or nachher['bindend']:
                out.append(f'{n} (bestätigen oder ändern einer bestätigten Entscheidung kann nur {_i.nom()})')
    return out


def gesperrt_frage(aufgabe_id, gesperrt):
    return (f'Der Branch von {aufgabe_id} ändert Vorgaben, die nur {_i.nom()} ändert: ' + '; '.join(gesperrt) + '. Er wird deshalb nicht gemergt.',
            ['Änderung aus dem Branch nehmen (Nacharbeit); ich ändere die Vorgabe selbst auf main',
             'Aufgabe zurückstellen'],
            f'Änderung aus dem Branch nehmen; Masterplan und bestätigte Entscheidungen ändert {_i.nom()} selbst und committet sie auf main.')
