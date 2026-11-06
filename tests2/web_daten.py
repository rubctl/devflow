"""Testprojekt für das Dashboard: abgeschlossene, fehlgeschlagene und laufende Attrappen-Läufe, eine offene Frage.

Stand nach `aufbauen()`:
- A1 (Claude): Entwicklung fertig mit Commit auf `claude-a1` (Bericht mit `<script>`), Review Sol mit Urteil
  „nacharbeit“, danach Frage F1 an den Inhaber → „Braucht dich“.
- A3 (Claude): Entwicklung fehlgeschlagen (leeres Drehbuch) → „blockiert“; Details mit relativem Link auf `docs/ziel.md`.
- A2 (Sol): Entwicklung läuft (schläft), Live-Log wächst nur, wenn ein Test Zeilen anhängt.
"""
from devflow2 import ablauf, laeufe

from hilfen import Fall, git

SKRIPT = '<script>alert(1)</script>'
BERICHT = f"""# A1 · Wetter-Kachel · Claude

**Branch/Commit:** `claude-a1` · **Status:** Review

## Was

Gebaut mit {SKRIPT} im Text und [böser Link](javascript:alert(1)) neben [gutem Link](https://example.org).

Siehe [Review](A1-review-sol.md), [Plan](../plan/wetter.md#aufgaben), [Wiki](/docs/ziel.md) und [raus](../../x.md).

| Spalte | Wert |
|---|---|
| a | `<b>` |
"""


def aufbauen():
    """Neues Testprojekt `demo` mit Läufen; gibt (Fall, {name: lauf}) zurück. Aufräumen mit `fall.tearDown()`."""
    f = Fall()
    f.setUp()
    try:
        p = f.p
        (f.repo / 'docs').mkdir()
        (f.repo / 'docs' / 'ziel.md').write_text('# Ziel\n\nNur auf main.\n')
        git(f.repo, 'add', 'docs/ziel.md')
        git(f.repo, 'commit', '-q', '-m', 'docs: Ziel')
        ablauf.plan_uebernehmen(p, 'plan/wetter.md')
        f.drehbuch(claude=[{'tue': 'commit', 'dateien': {'app/wetter.py': 'TEMP = 12\n', 'berichte/A1.md': BERICHT},
                            'ergebnis': f'Gebaut {SKRIPT}'}],
                   codex=[f.nacharbeit(f'Grenzfall {SKRIPT}'), {'tue': 'schlafen', 's': 600}])
        fertig = ablauf.entwickeln(p, 'A1', automatisch=False)
        f.warten(lambda: laeufe.laden(fertig['id'])['zustand'] == 'fertig', text='A1 entwickelt')
        review = f.review_fertig(automatisch=False)
        ablauf.aufgabe_anlegen(p, 'A3', 'Kaputter Lauf', details='[Wiki](docs/ziel.md)')
        kaputt = ablauf.entwickeln(p, 'A3', automatisch=False)
        f.warten(lambda: laeufe.laden(kaputt['id'])['zustand'] == 'fehler', text='A3 Fehler')
        ablauf.frage_stellen(p, f'Celsius oder Fahrenheit? {SKRIPT}', ['Celsius', 'Fahrenheit'], 'Celsius', 'A1')
        laufend = ablauf.entwickeln(p, 'A2', automatisch=False)
        f.warten(lambda: laeufe.laden(laufend['id'])['zustand'] == 'laeuft' and len(f.aufrufe('codex')) == 2,
                 text='A2 läuft')
    except BaseException:
        f.tearDown()
        raise
    return f, {'fertig': fertig['id'], 'review': review['id'], 'kaputt': kaputt['id'], 'laufend': laufend['id']}


def zeile_anhaengen(lid, text):
    """Ereignis ans Log eines Laufs hängen, wie es eine CLI schreiben würde."""
    import json
    with open(laeufe.verzeichnis(lid) / 'log.jsonl', 'a', encoding='utf-8') as log:
        log.write(json.dumps({'type': 'schritt', 'text': text}, ensure_ascii=False) + '\n')
