"""devflow 2: Werkzeug für Code, gesteuert von einer KI per MCP, überwacht vom Inhaber.

Stand liegt als md im Projekt-Repo (ROADMAP.md, plan/, berichte/, docs/), Läufe als Dateien unter
~/.local/state/devflow2/.
"""
import os
from pathlib import Path


class Fehler(Exception):
    """Erwarteter Fehler mit Meldung für die steuernde KI."""


def schreiben(pfad, text):
    """Atomar schreiben, damit Leser nie eine halbe Datei sehen."""
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(f'.{pfad.name}.{os.getpid()}.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, pfad)
