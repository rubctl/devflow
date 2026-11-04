# {{PROJEKT}} · Arbeitsregeln für Modelle

Gilt für Claude (liest diese Datei über `CLAUDE.md`), Sol/Codex und jedes weitere Modell. Der Inhaber plant, entscheidet und nimmt ab; den Rest machen die Modelle.

## Einstieg

1. [`ROADMAP.md`](ROADMAP.md): Kanban, offene Fragen, Aufgaben, Protokoll.
2. [`docs/README.md`](docs/README.md): Wiki des Projekts.
3. [`plan/`](plan/): das laufende Vorhaben.

## Ablauf je Aufgabe

Mit devflow (Standard) steuert Claude diese Schritte über MCP, und devflow pflegt den Status in `ROADMAP.md` mit eigenen Commits auf `{{BASIS}}`. **Branches ändern `ROADMAP.md` nie.** Ohne devflow gelten dieselben Schritte von Hand.

1. **Beginnen:** Status `in Arbeit`, „Wer“ = dein Name, Datum.
2. **Eigener Branch und Worktree:** Branch `<name>-<ID>`, Worktree `~/dev/{{PROJEKT}}-<branch>`. Nie zwei Bearbeiter im selben Worktree.
3. **Bauen:** Code, Tests und betroffene Wiki-Seiten in `docs/` im selben Commit.
4. **Übergeben:** Bericht `berichte/<ID>.md` (Vorlage `berichte/_vorlage.md`), Status `Review`.
5. **Gegenreview:** Das jeweils andere Modell prüft und schreibt `berichte/<ID>-review-<name>.md` (Vorlage `berichte/_review-vorlage.md`).
6. **Nacharbeit:** Bei Befunden arbeitet der Autor nach, dann folgt eine Nachprüfung. Nach zwei Runden ohne Einigung: Status `Braucht dich` mit offener Frage an den Inhaber.
7. **Abschluss:** Nach bestandenem Review mergt Claude (`git merge --ff-only`), setzt `erledigt`, entfernt Worktree und Branch (ohne Force) und schreibt einen Protokolleintrag.

Von Hand geänderte Roadmap danach mit `python3 werkzeuge/roadmap.py` prüfen (erneuert das Kanban, prüft die Statuswerte).

## Planen

Geplant wird in Claude Code im Gespräch mit dem Inhaber: Plan nach `plan/<vorhaben>.md` (Vorlage `plan/_vorlage.md`), bei Bedarf Zweitmeinung von Sol, Abnahme durch den Inhaber. Erst der abgenommene, committete Plan geht an devflow.

## Entscheidungen

Vier Ebenen, jede Entscheidung hat genau einen Ort: **Masterplan** (`MASTERPLAN.md`: warum, für wen, was immer stimmen muss), **Grundentscheidungen** (`docs/entscheidungen/`: womit und wohin), **Plan** (`plan/<vorhaben>.md`: wie, mit welchen Aufgaben) und **Lauf** (Details innerhalb dieser Grenzen). Beschreibung im Wiki von devflow (`docs/masterplan.md`).

- **Im Lauf** entscheidet das Modell selbst und schreibt die Entscheidung mit Begründung in den Bericht, solange es im Rahmen von Masterplan, bestätigten Grundentscheidungen und Plan bleibt.
- **Was den Inhaber betrifft** (Umfang, Kosten, Daten, Nutzersicht, Sicherheit): als offene Frage in die Roadmap (Frage, Optionen mit Folgen, Empfehlung), Aufgabe auf `Braucht dich`.
- **Grundentscheidung** ist, was später teuer umzukehren ist: Sprache und Rahmenwerk, Datenbank und Datenmodell im Kern, Betrieb und Zielumgebung, Anmeldung und Rechte, Schnittstellen nach außen, neue externe Dienste. Ein Lauf trifft so etwas nie selbst: Braucht er eine neue oder würde er eine bestätigte brechen, stellt er eine Frage (Antwort beginnt mit „FRAGE AN DEN INHABER:“). Das Review prüft das als P2. Als Seite unter `docs/entscheidungen/` (Vorlage dort) mit Status `vorgeschlagen`, `bestätigt vom Inhaber` oder `ersetzt durch NNNN`; **bindend ist nur `bestätigt vom Inhaber`**, und diesen Status setzt nur der Inhaber.
- **Masterplan:** Nur der Inhaber ändert `MASTERPLAN.md`. Ein Branch, der ihn oder eine bestätigte Entscheidung ändert, wird nicht gemergt, sondern ergibt eine Frage. Ein Befund gegen eine Kernzusage ist mindestens P2, auch als Randfall.
- **Beantwortet und dauerhaft wichtig:** als Seite unter `docs/entscheidungen/` (Vorlage dort).

## Wiki

`docs/` ist das Wiki. Ändert sich Verhalten, Aufbau oder Betrieb, wird die passende Seite im selben Commit angepasst. Neue Seiten in `docs/README.md` verlinken.

## Grenzen

- Keine Geheimnisse im Repo, in Berichten oder Logs. `.env` ist ignoriert; Werte nie ausgeben.
- **Nicht ohne den Inhaber:** Daten löschen, Zugangsdaten, alles außerhalb von server, kostenpflichtige Dienste, Deploy in den Echtbetrieb.
- Größere Testläufe auf server nur über `~/dev/werkzeuge/ops/testlauf.sh <befehl …>`.
- Vor `git add` kurz `git status` ansehen und nur eigene Dateien aufnehmen; ein anderes Modell arbeitet evtl. parallel.
- Dokumente auf Deutsch.

## Tests

```bash
# Testbefehl eintragen
```
