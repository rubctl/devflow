"""Kleine Repo-Karte für Aufträge: Dateien mit ihren Klassen und Funktionen (Idee aus aiders „repo map“).

Spart Suchschritte; jede Zeile davon liest der Lauf in jedem Schritt mit, deshalb hart begrenzt.
"""
import ast
from pathlib import Path
import re
import subprocess

GRENZE = 5000
JS = re.compile(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|function)', re.M)
BINAER = ('.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.woff', '.woff2', '.pdf', '.zip', '.lock')


def _namen(pfad):
    try:
        text = pfad.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    if pfad.suffix == '.py':
        try:
            baum = ast.parse(text)
        except SyntaxError:
            return []
        return [(f'{k.name}(' + ', '.join(f.name for f in k.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))) + ')')
                if isinstance(k, ast.ClassDef) else k.name
                for k in baum.body if isinstance(k, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    if pfad.suffix in ('.js', '.mjs', '.ts'):
        return [a or b for a, b in JS.findall(text)]
    if pfad.suffix == '.md':
        return [next((l[2:].strip() for l in text.split('\n') if l.startswith('# ')), '')][:1]
    return []


def karte(wt, auslassen=(), grenze=GRENZE):
    """Eine Zeile je Datei: `pfad: namen`; Berichte, Binärdateien und `auslassen` (Präfixe) fehlen."""
    try:
        # -z: Namen mit Nicht-ASCII-Zeichen kommen unverändert, nicht in Anführungszeichen mit Oktal-Escapes
        dateien = subprocess.run(['git', 'ls-files', '-z'], cwd=wt, capture_output=True, text=True, encoding='utf-8',
                                 errors='surrogateescape', check=True).stdout.split('\0')
    except (OSError, subprocess.CalledProcessError):
        return ''
    zeilen, laenge = [], 0
    weg = ('berichte/', '.', *(p for p in auslassen if p))  # ein leeres Präfix würde alles ausschließen
    dateien = [d for d in dateien if d and not d.startswith(weg) and not d.lower().endswith(BINAER)]
    for i, name in enumerate(dateien):
        namen = [n for n in _namen(Path(wt) / name) if n]
        zeile = name + (': ' + ', '.join(namen[:15]) + (' …' if len(namen) > 15 else '') if namen else '')
        if laenge + len(zeile) > grenze:
            zeilen.append(f'… und {len(dateien) - i} weitere Dateien (`git ls-files`)')
            break
        zeilen.append(zeile)
        laenge += len(zeile) + 1
    return '\n'.join(zeilen)
