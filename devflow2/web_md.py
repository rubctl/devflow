"""md als HTML: Überschriften, Absätze, Listen (verschachtelt), Tabellen, Code, Zitate, Fett, Kursiv, Links.

Absichtlich klein und streng: HTML im Text wird immer maskiert, Links sind nur `http(s)` oder relativ.
`link(url) -> url | None` löst geprüfte Links auf (z. B. relative Repo-Pfade auf die md-Ansicht); None = nur Text.
"""
import re

from .web_html import Roh, e, t

ZAUN = re.compile(r'^\s*(```|~~~)')
UEBERSCHRIFT = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
PUNKT = re.compile(r'^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$')
TRENNZEILE = re.compile(r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$')
LINIE = re.compile(r'^\s*([-*_])(\s*\1){2,}\s*$')
INLINE = re.compile(r'(`+)(.+?)\1|\[((?:[^\[\]]|\[[^\]]*\])+)\]\(([^()\s]+)(?:\s+"[^"]*")?\)')
SCHEMA = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.-]*):')


def url_ok(url):
    """Nur http(s) oder relativ; Steuerzeichen und Leerraum sperren Tricks wie `java\\tscript:`."""
    if not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    m = SCHEMA.match(url)
    return not m or m.group(1).lower() in ('http', 'https')


def _betonen(roh):
    """Maskierter Text mit Fett und Kursiv."""
    s = str(e(roh))
    s = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'__(?=\S)(.+?)(?<=\S)__', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])', r'<em>\1</em>', s)
    s = re.sub(r'(?<![\w])_(?=\S)(.+?)(?<=\S)_(?![\w])', r'<em>\1</em>', s)
    return s


def inline(text, link=None):
    out, pos = [], 0
    for m in INLINE.finditer(text):
        out.append(_betonen(text[pos:m.start()]))
        if m.group(1):
            out.append(t('code', m.group(2).strip() or m.group(2)))
        elif url_ok(m.group(4)) and (ziel := link(m.group(4)) if link else m.group(4)):
            out.append(t('a', Roh(_betonen(m.group(3))), href=ziel))
        else:
            out.append(_betonen(m.group(3)))
        pos = m.end()
    out.append(_betonen(text[pos:]))
    return Roh(''.join(out))


def _zellen(line):
    s = line.strip()
    s = s[1:] if s.startswith('|') else s
    s = s[:-1] if s.endswith('|') and not s.endswith('\\|') else s
    return [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', s)]


def _anker(text):
    return re.sub(r'[^\w-]', '', text.strip().lower().replace(' ', '-'))


def _blockanfang(lines, i):
    line = lines[i]
    return (ZAUN.match(line) or UEBERSCHRIFT.match(line) or PUNKT.match(line) or line.lstrip().startswith('>')
            or LINIE.match(line) or (line.lstrip().startswith('|') and i + 1 < len(lines) and TRENNZEILE.match(lines[i + 1])))


def _liste(eintraege, link=None):
    """Einträge (Einrückung, nummeriert, Text) als verschachtelte Listen."""
    out, stapel = [], []
    for tief, nummer, text in eintraege:
        while stapel and tief < stapel[-1][0]:
            out.append(f'</li></{stapel.pop()[1]}>')
        name = 'ol' if nummer else 'ul'
        if not stapel or tief > stapel[-1][0]:
            out.append(f'<{name}><li>')
            stapel.append((tief, name))
        elif stapel[-1][1] != name:
            out.append(f'</li></{stapel[-1][1]}><{name}><li>')
            stapel[-1] = (tief, name)
        else:
            out.append('</li><li>')
        out.append(inline(text, link))
    while stapel:
        out.append(f'</li></{stapel.pop()[1]}>')
    return ''.join(out)


def md(text, link=None):
    lines = str(text).replace('\r\n', '\n').replace('\t', '    ').split('\n')
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
        elif m := ZAUN.match(line):
            zaun, code = m.group(1), []
            i += 1
            while i < n and not lines[i].strip().startswith(zaun):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(t('pre', t('code', '\n'.join(code)), class_='code'))
        elif m := UEBERSCHRIFT.match(line):
            ebene = len(m.group(1))
            out.append(t(f'h{ebene}', inline(m.group(2), link), id=_anker(m.group(2)) or None))
            i += 1
        elif line.lstrip().startswith('|') and i + 1 < n and TRENNZEILE.match(lines[i + 1]):
            kopf = _zellen(line)
            i += 2
            rows = []
            while i < n and lines[i].lstrip().startswith('|'):
                rows.append(_zellen(lines[i]))
                i += 1
            out.append(t('div', t('table', t('thead', t('tr', [t('th', inline(c, link)) for c in kopf])),
                                  t('tbody', [t('tr', [t('td', inline(c, link)) for c in r]) for r in rows])), class_='breit'))
        elif PUNKT.match(line):
            eintraege = []
            while i < n:
                m = PUNKT.match(lines[i])
                if m:
                    eintraege.append([len(m.group(1)), m.group(2)[0].isdigit(), m.group(3)])
                    i += 1
                elif lines[i].strip() and lines[i].startswith(' ') and eintraege:
                    eintraege[-1][2] += ' ' + lines[i].strip()
                    i += 1
                elif (not lines[i].strip() and i + 1 < n and (m := PUNKT.match(lines[i + 1]))
                      and (m.group(1) or m.group(2)[0].isdigit() == eintraege[0][1])):
                    i += 1
                else:
                    break
            out.append(Roh(_liste(eintraege, link)))
        elif line.lstrip().startswith('>'):
            zitat = []
            while i < n and lines[i].lstrip().startswith('>'):
                zitat.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            out.append(t('blockquote', md('\n'.join(zitat), link)))
        elif LINIE.match(line):
            out.append(Roh('<hr>'))
            i += 1
        else:
            absatz = [line.strip()]
            i += 1
            while i < n and lines[i].strip() and not _blockanfang(lines, i):
                absatz.append(lines[i].strip())
                i += 1
            out.append(t('p', inline(' '.join(absatz), link)))
    return Roh(''.join(out))


def kurz_kasten(text, link=None):
    """Kurzfassung eines Textes (S14.1) als hervorgehobener Kasten für die Seitenköpfe; None, wenn es nichts zu zeigen gibt.
    `text` ist die Kurzfassung selbst (Markdown), etwa aus `kurz.aus_text` oder am Lauf gespeichert."""
    z = str(text or '').strip()
    if not z:
        return None
    return t('aside', t('strong', 'Kurz', class_='kurz-kopf'), md(z, link), class_='kurz', aria_label='Kurzfassung')
