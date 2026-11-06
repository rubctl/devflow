"""Kurzfassung (S14.1): höchstens drei Zeilen bzw. Punkte, bei einem Urteil steht es in der ersten Zeile.

Pläne tragen sie als Abschnitt `## Kurz`, Berichte, Reviews und Läufe als erste Zeile `**Kurz:**`. Alte Texte ohne Kurzfassung
fallen auf die ersten zwei Sätze des Fließtexts zurück (Überschriften, Metadatenzeilen, Tabellen und Listen übersprungen).
"""
import re

MAX_ZEILEN = 3
ABSCHNITT = re.compile(r'^##[ \t]+Kurz[ \t]*#*[ \t]*$', re.M)
MARKE = re.compile(r'^\*\*Kurz:\*\*[ \t]*(.*)$')
UEBERSCHRIFT = re.compile(r'^#{1,6}\s')
ZAUN = re.compile(r'^\s*(```|~~~)')
PUNKT = re.compile(r'^\s*([-*+]|\d{1,9}[.)])\s+')
METADATEN = re.compile(r'^\s*(\*\*[^*]+:\*\*|\*\*[^*]+\*\*\s*:|(Stand|Branch|Commit|Datum|Autor|Wer|Status|Lauf|Aufgabe|Plan|Reviewer)\s*:\s)')
# „**Branch/Commit:** …“, „**Stand:** …“, auch unformatiert („Stand: Commit …“)
SATZENDE = re.compile(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ„"*`(\[])')
MAX_ZEICHEN = 400


def _zeilen(text):
    return str(text).replace('\r\n', '\n').replace('\t', '    ').split('\n')


def abschnitt(text):
    """Zeilen des Abschnitts `## Kurz` (ohne Leerzeilen am Rand); None, wenn es ihn nicht gibt. Codeblöcke zählen nicht."""
    zaun, out = False, None
    for z in _zeilen(text):
        if ZAUN.match(z):
            zaun = not zaun
        if out is None:
            if not zaun and ABSCHNITT.match(z):
                out = []
        elif not zaun and UEBERSCHRIFT.match(z):
            break
        elif z.strip():
            out.append(z.rstrip())
    return out


def pruefen(text):
    """Fehlertext, wenn ein Planentwurf keine gültige Kurzfassung hat, sonst None."""
    z = abschnitt(text)
    if z is None or not z:
        return 'erwartet einen nicht leeren Abschnitt „## Kurz“ direkt unter der Stand-Zeile (plan/_vorlage.md)'
    if len(z) > MAX_ZEILEN:
        return f'„## Kurz“ hat {len(z)} Zeilen; erlaubt sind höchstens {MAX_ZEILEN} Zeilen bzw. Punkte'
    return None


def _marke(zeilen):
    """`**Kurz:**`-Zeile samt bis zu zwei Folgezeilen, nur vor dem ersten Abschnitt (`##`) und außerhalb von Codeblöcken."""
    zaun = False
    for i, z in enumerate(zeilen):
        if ZAUN.match(z):
            zaun = not zaun
        elif zaun:
            continue
        elif re.match(r'^#{2,6}\s', z):
            return None
        elif m := MARKE.match(z.strip()):
            out = [m.group(1).strip()] if m.group(1).strip() else []
            for w in zeilen[i + 1:]:
                if not w.strip() or UEBERSCHRIFT.match(w) or len(out) >= MAX_ZEILEN:
                    break
                out.append(w.strip())
            return out or None
    return None


def _saetze(zeilen, n=2):
    """Erste `n` Sätze des Fließtexts; Überschriften, Metadatenzeilen, Tabellen, Zitate, Code und Listen werden übersprungen."""
    zaun, absatz, ersatz = False, [], []
    for z in zeilen:
        s = z.strip()
        if ZAUN.match(z):
            zaun = not zaun
            continue
        if zaun or not s:
            if absatz and (zaun or len(SATZENDE.split(' '.join(absatz))) >= n):
                break  # ein zweiter Absatz ergänzt, wenn der erste nur einen Satz hat
            continue
        if UEBERSCHRIFT.match(s) or s.startswith(('|', '>', '<', '---', '***', '___')) or METADATEN.match(s) or '**Stand:** abgenommen' in s:
            if absatz:
                break
            continue
        if PUNKT.match(s):
            if not absatz and len(ersatz) < 1:
                ersatz.append(PUNKT.sub('', s))  # nur, wenn gar kein Fließtext kommt
            if absatz:
                break
            continue
        absatz.append(s)
    text = ' '.join(absatz) or (ersatz[0] if ersatz else '')
    if not text:
        return ''
    teile = SATZENDE.split(text)
    return ' '.join(teile[:n])[:MAX_ZEICHEN].rstrip()


def aus_text(text):
    """Kurzfassung eines Textes als Markdown: ausdrücklich geschriebene, sonst die ersten zwei Sätze. Leer, wenn nichts da ist."""
    text = str(text or '')
    z = abschnitt(text)
    if z:
        return '\n'.join(z[:MAX_ZEILEN])
    zeilen = _zeilen(text)
    z = _marke(zeilen)
    if z:
        return '\n'.join(z)
    return _saetze(zeilen)


def ausdruecklich(text):
    """True, wenn der Text eine geschriebene Kurzfassung hat (nicht nur den Rückfall)."""
    text = str(text or '')
    return bool(abschnitt(text)) or bool(_marke(_zeilen(text)))
