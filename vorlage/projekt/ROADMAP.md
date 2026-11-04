# {{PROJEKT}} · Roadmap

Einzige Quelle für Stand und Aufgaben. Das Kanban unter „Aktuell“ wird aus den Aufgabentabellen erzeugt.

**Spielregeln**

- Tabellen: ID · Aufgabe · Wer · Status · Datum (seit wann der Status gilt, `28.10.`) · Details (Commits, Berichte, Links). Mehrere Tabellen, z. B. je Meilenstein, sind erlaubt.
- Status nur aus dieser Liste: `offen` · `Plan` · `bereit` · `in Arbeit` · `Review` · `Braucht dich` · `blockiert` · `erledigt` · `entfällt`.
- Den Status pflegt devflow mit eigenen Commits auf `{{BASIS}}`; Branches ändern diese Datei nicht. Wer von Hand ändert, ruft danach `python3 werkzeuge/roadmap.py` auf.
- Protokoll: neueste Einträge oben.

## Aktuell

<!-- aktuell:start -->
<!-- aktuell:end -->

## Offene Fragen an den Inhaber

Neueste oben. Format: Frage, Optionen mit Folgen, Empfehlung, dann `**Antwort Inhaber:**`. Beantwortete Fragen wandern nach `docs/entscheidungen/` oder in den Bericht.

_Keine._

## Aufgaben

| ID | Aufgabe | Wer | Status | Datum | Details |
|---|---|---|---|---|---|
| 0.1 | Projekt aus Vorlage angelegt | Claude | erledigt | {{TAG}} | `devflow/vorlage/projekt` |
| 0.2 | `MASTERPLAN.md` mit dem Inhaber ausfüllen, Grundentscheidungen und Betrieb festhalten | | offen | {{TAG}} | |
| 0.3 | README, Start- und Testbefehle in `AGENTS.md` und `devflow.toml` eintragen | | offen | {{TAG}} | |

## Vorhaben (grob)

Vorhaben in sinnvoller Reihenfolge, noch ohne Aufgaben. Die Aufgaben entstehen erst in der Planungssitzung des jeweiligen Vorhabens (`plan/<vorhaben>.md`). Diese Liste ist keine Aufgabentabelle und wird von devflow nicht gelesen.

- _noch keine; entstehen im Gespräch zum Masterplan_

## Protokoll

- {{DATUM}} · Projekt aus Vorlage angelegt.
