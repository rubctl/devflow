# devflow 2 · Werkzeug für Code, gesteuert von einer KI per MCP

**Stand:** S1 Kern (DF2.1). devflow 1 ist eingefroren (Tag `devflow-1`) und liegt bis zur Umstellung daneben im Paket `devflow/`.

## Idee

- **Planen:** Der Inhaber plant mit Claude in Claude Code. Der abgenommene Plan liegt als `plan/<vorhaben>.md` im Projekt.
- **Umsetzen:** Claude steuert die Umsetzung über die MCP-Werkzeuge von devflow. Standardmäßig ist die Rollenverteilung so:
  - Claude entwickelt als Hintergrundlauf (`claude -p`).
  - Sol reviewt (`codex exec`).
- **Kette:** Mit `automatisch=True` läuft je Aufgabe:
  1. entwickeln
  2. testen
  3. Review
  4. bei Befunden Nacharbeit, dann wieder testen und Review; höchstens zwei Runden
  5. mergen
  6. deployen, falls ein Deploybefehl hinterlegt ist
- **Rückfragen:** Was der Inhaber entscheiden muss, wird eine offene Frage in `ROADMAP.md`, und die Aufgabe geht auf `Braucht dich`.
- **Stand:** Den Stand gibt es nur als md im Repo (Vorlage `vorlage/projekt` in devflow). devflow schreibt den Status als kleine Commits auf den Basisbranch (`basis`, Standard `main`); Branches ändern `ROADMAP.md` nie.
- **Läufe:** Läufe haben dieselben Rechte wie die Sitzungen von Claude und Sol. Sie laufen unter dem Benutzer, der devflow betreibt, mit der eigenen CLI-Konfiguration. devflow legt keine eigene Sandbox und keine Hashbindung an und verlangt keine Schlüssel. Gilt nur für Code, nicht für Daten; die harten Regeln stehen in `AGENTS.md` des Projekts.

## Einrichtung

```bash
uv venv --python 3.11 ~/.local/share/devflow2/venv
uv pip install --python ~/.local/share/devflow2/venv/bin/python -r requirements.txt
claude mcp add devflow -s user -e PYTHONPATH=$HOME/dev/devflow -- \
  $HOME/.local/share/devflow2/venv/bin/python -B -m devflow2 mcp
```

**Projekt anmelden:**
1. Haupt-Checkout mit `ROADMAP.md` aus der Vorlage: `ops/neues-projekt.py <verzeichnis> [Name]` legt ein neues Repo an oder ergänzt ein vorhandenes, `--url <URL>` klont zuerst (siehe [Betrieb](docs/betrieb.md#neues-projekt-anlegen)).
2. Anmelden über das MCP-Werkzeug `projekt_anmelden` oder mit `python -m devflow2 anmelden <pfad> [name]`.
3. Einstellungen in `devflow.toml` im Repo:

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `test` | Testbefehl, läuft über `ops/testlauf.sh` | leer |
| `deploy` | Befehl im Haupt-Checkout nach dem Merge | leer = kein Deploy |
| `entwickler` | `claude` oder `sol` | `claude` |
| `reviewer` | `claude` oder `sol` | `sol` |
| `basis` | Basisbranch: Status, Pläne und Berichte werden dort committet, Aufgaben zweigen davon ab und werden dorthin gemergt; muss im Repo existieren (prüft die Anmeldung) | `main` |
| `stufe` | Stufe des Projekts, wenn die Planzeile keine nennt: `leicht`, `normal`, `schwer` | leer = CLI-Standard |
| `[stufen.<name>]` | Modell und Effort je Stufe: `claude_modell`, `claude_effort`, `sol_modell`, `sol_effort` | leicht: Sonnet/medium, Sol medium · normal: Sonnet/high, Sol high · schwer: Opus/high, Sol high |
| `claude_modell` | Modell für Claude-Läufe ohne Stufe (`--model`) | leer = CLI-Standard |
| `karte_auslassen` | Pfadpräfixe, die in der Repo-Karte der Aufträge fehlen | leer |
| `selbstlauf_budget` | Steuerläufe pro Tag im [Selbstlauf](docs/architektur.md#selbstlauf), über alle Projekte; es gilt der kleinste Wert | 30 |
| `[traum_grenze]` | je Modell (`claude`, `sol`): Prozent des 5-Stunden-Fensters, ab denen ein [Traum](docs/traum.md#grenzen) kein Modell mehr startet; es gilt der kleinste Wert | je 90 |
| `[traum_reserve]` | je Modell: Prozent der Woche, die der Traum übrig lässt; es gilt der größte Wert | je 15 |
| `[pflege]` | Pflegeprüfung auf Sicherheitslücken und veraltete Pakete, Standardwerte werden erkannt ([Pflege](docs/pflege.md#einstellungen)) | erkannt |
| `[stufen.steuern]` | Modell und Effort der Steuerläufe: `claude_modell`, `claude_effort`; im Plan nicht wählbar | Sonnet/medium |

## Werkzeuge (MCP-Server `devflow`, stdio)

| Gruppe | Werkzeuge |
|---|---|
| Lesen | `lage`, `aufgaben`, `aufgabe`, `lauf` |
| Stand | `projekt_anmelden`, `plan_entwurf`, `plan_uebernehmen`, `aufgabe_anlegen`, `pflege_aufgabe` ([Routine-Updates](docs/pflege.md#routine-updates)), `status_setzen`, `frage_stellen`, `antwort_eintragen`, `protokoll` |
| Ideen | `eingang` (Ideen aus `plan/eingang.md`), `idee_zustand` (`neu`, `in Planung` mit Plan, `verworfen`) |
| Arbeit | `entwickeln`, `testen`, `review_anfordern`, `nacharbeit`, `mergen`, `deployen`, `meinung_anfordern` |
| Aufsicht | `anhalten` (je Lauf oder `alles`), `fortsetzen` (nur je Lauf; `alles` und Halte aus dem Dashboard gehen nur im Dashboard) |

`python -m devflow2 mcp <satz>` bietet nur die Werkzeuge eines Satzes an (`steuerung`, `planung`, `ideen`); das nutzt der Selbstlauf. Ohne Satz gibt es alle Werkzeuge. Die Grenzen, die der Code durchsetzt, stehen in [`docs/architektur.md`](docs/architektur.md#grenzen-im-code).

Regeln, die devflow selbst prüft:

- **Plan übernehmen:** nur, wenn der Plan committet ist und die Zeile `**Stand:** abgenommen vom Inhaber am …` trägt.
- **Pflegeaufgaben:** `P.<datum>.<n>` entstehen nur über `pflege_aufgabe` unter einem gültigen Pflegeplan; vor dem Merge prüft devflow den Diff gegen den Umfang ([Routine-Updates](docs/pflege.md#routine-updates)).
- **Vor jedem Review:** Rebase auf einen festgehaltenen Basiscommit, wenn `main` fachlich weiter ist, dann Tests (Lauf `vorbereiten`). Ein Konflikt wird sofort abgebrochen, die Konfliktpfade gehen an die Nacharbeit ([Merge-Tor](docs/architektur.md#merge-tor-s142)).
- **Mergen:** nur nach einem bestandenen Review auf genau dem vorbereiteten und getesteten Integrationsstand. Der Merge läuft in wiederholbaren Schritten; jeder gilt erst mit Nachweis im Git-Stand als erledigt:
  1. **Vorprüfung:** Alle Tore neu, auch beim Fortsetzen (offene Fragen, Vorschlagsfreigabe, Masterplan, Pflegeumfang). Ist der Statuscommit auf `main` überhaupt möglich (keine fremde Änderung an `ROADMAP.md`)?
  2. **Neues Review bei fachlich neuem `main`:** Hat `main` seit dem Review andere Commits als devflows Statuscommits bekommen, gibt es keinen Merge; die Kette geht zurück zu Vorbereitung, Tests und Review.
  3. **Rebase über Statuscommits:** ohne neues Review; der neue Stand darf sich vom reviewten nur in `ROADMAP.md` unterscheiden.
  4. **Merge:** mit `--ff-only`; der SHA wird sofort gesichert.
  5. **Status:** `erledigt`, geprüft am committeten `ROADMAP.md`. Scheitert der Commit, nimmt devflow die eigene Änderung zurück.
  6. **Aufräumen:** Worktree und Branch ohne Force entfernen; was nicht geht, wird ein Hinweis.
  7. **Push:** zum Upstream, ohne Upstream zu `origin`. Ein Fehler lässt den Lauf fehlschlagen, sodass kein Deploy folgt. `fortsetzen(lauf_id)` holt ihn nach Behebung nach.
- **Review:**
  - Das Review läuft in einer losgelösten Kopie genau des übergebenen Commits.
  - Das Urteil kommt als JSON nach festem Schema. „Bestanden“ mit einem P1/P2-Befund gilt als Nacharbeit.
  - devflow committet den Bericht als `berichte/<ID>-review-<name>.md` auf den Branch.
- **Nacharbeit:** Runden nach Review, roten Tests oder Hinweisen der steuernden KI starten in einer frischen Sitzung mit Aufgabe, Zusatzauftrag vom Start, früheren Hinweisen, Fragen an den Inhaber samt Antworten, Bericht und Befunden; die alte Sitzung trüge den ganzen Verlauf mit und machte jeden Schritt teuer. Kurzes Nachhaken (nicht committet, ROADMAP geändert) setzt die Sitzung fort. Nach zwei Runden stellt devflow eine Frage an den Inhaber.
- **Schwere im Review:** P1 echter Schaden, P2 Abnahmekriterium verfehlt, Fehler bei normaler Nutzung oder Projektregel verletzt, alles andere P3 (nur Bericht, keine Nacharbeit). Nachprüfungen prüfen die alten Befunde auf dem Diff seit der letzten Runde; neue Befunde außerhalb davon nur bei P1, im Nacharbeitsdiff auch P2.
- **Gegenreview:** Entwickler und Reviewer sind immer verschiedene Modelle, auch bei ausdrücklich angegebenem Reviewer. Der Merge erkennt nur ein solches Gegenreview an.
- **Antworten:** `antwort_eintragen` setzt die Aufgabe wieder auf `bereit`, sobald keine Frage zu ihr mehr offen ist. Solange eine offen ist, startet für die Aufgabe nichts. Die steuernde KI gibt die Antwort danach mit `nacharbeit(hinweise=…)` an den Entwickler weiter.
- **Stufe und Kontext:** Der Plan legt je Aufgabe eine Stufe fest (optionale Spalte `Stufe`), daraus folgen Modell und Effort für Entwicklung und Review. Aufträge nennen den Bereich aus dem Plan als Einstieg und enthalten eine kleine Repo-Karte (`devflow2/karte.py`: Dateien mit Klassen und Funktionen, höchstens 5000 Zeichen); Läufe sollen Ausgaben knapp halten, weil jede Ausgabe in allen weiteren Schritten mitgelesen wird. Nachprüfungen bekommen den Diff seit der letzten Runde.
- **Kontingent:** Meldet die CLI das Ende des Kontingents, wartet der Lauf bis zur Freigabe und macht in derselben Sitzung weiter.
- **Parallelität:** Höchstens `DEVFLOW2_MAX` Modellläufe gleichzeitig, Standard 1 (Kontingent).
- **Prozesse:** Jeder Befehl läuft in einer eigenen Prozessgruppe.
  - Zeitlimit (`DEVFLOW2_ZEITLIMIT`, Standard 4 h), Laufende und `anhalten` beenden die ganze Gruppe und warten ihr Ende ab.
  - Die Sitzungskennung wird gesichert, sobald die CLI sie meldet.
- **Fortsetzen:** `fortsetzen(lauf_id)` nimmt einen angehaltenen oder fehlgeschlagenen Lauf in derselben Sitzung wieder auf.
  - Fehlt die Review-Kopie, legt devflow sie auf dem geprüften Commit neu an.
  - War die Arbeit schon fertig und nur der Abschluss scheiterte, wird nur der Abschluss nachgeholt, z. B. Status und Aufräumen nach einem Merge.
- **Halt:** Bei globalem Halt lehnen alle Werkzeuge den Start ab, bevor sie etwas ändern. Aufheben geht nur im Dashboard.
- **Ungültiges Review-JSON:** devflow fragt einmal in derselben Sitzung nach dem Schema nach; erst danach gilt der Lauf als Fehler.

## Dashboard

`python -m devflow2 web` startet die Oberfläche auf `127.0.0.1:8478` (Port `DEVFLOW2_WEB_PORT`). Sie zeigt die Lage über alle Projekte (oben, was beim Inhaber liegt), Kanban, Aufgaben, Läufe mit Live-Log und Urteil, Berichte, Diffs und md-Dateien. Zugang nur mit der Kopfzeile `Tailscale-User-Login` (Login `DEVFLOW2_WEB_LOGIN`, Standard `inhaber@github`), sonst 403, auch für `/health`. Im Betrieb läuft es als Benutzerdienst `devflow2-web` hinter `tailscale serve` auf `https://server.example.net:8472`; `deploy` in `devflow.toml` startet ihn neu und prüft `/health` (`ops/web_einrichten.sh`, `ops/web_deploy.sh`, Rückweg in [`docs/betrieb.md`](docs/betrieb.md#dashboard-dienst)). **Kurzfassung (S14.1):** Pläne (`## Kurz`), Berichte, Reviews und Läufe beginnen mit höchstens drei Zeilen; das Dashboard zeigt sie zuerst ([`docs/dashboard.md`](docs/dashboard.md#kurzfassung-s141)). **Braucht dich (S14.3)** ist eine Entscheidungsliste: je Eintrag eine Zeile mit Überschrift und Knopf (Übernehmen, Abnehmen, Empfehlung annehmen, Fortsetzen), Details per Klick; ein Klick wirkt sofort und ist an den angezeigten Stand gebunden. Handlungen (nur POST mit passender `Origin`): Frage beantworten (landet über `antwort_eintragen` als Commit in der Roadmap), Lauf anhalten und fortsetzen, alles anhalten und fortsetzen. Einzelheiten: [`docs/dashboard.md`](docs/dashboard.md).

## Dateien

| Ort | Inhalt |
|---|---|
| `~/.local/state/devflow2/projekte.json` | angemeldete Projekte |
| `~/.local/state/devflow2/laeufe/<id>/` | je Lauf `lauf.json` (Zustand: wartet, laeuft, kontingent, abschluss, fertig, fehler, angehalten), `log.jsonl` (Ereignisse der CLI), `stderr.log`, `ergebnis.txt` (vollständige letzte Antwort) |
| `~/.local/state/devflow2/angehalten` | gesetzt = globales Anhalten |

Jeder Lauf ist ein eigener Prozess (`python -m devflow2 lauf <id>`, per `setsid -f` losgelöst). Er läuft also weiter, wenn die steuernde Sitzung endet.

## Tests

```bash
cd ~/dev/devflow-<branch>
PYTHONPATH=.:tests2 ~/dev/werkzeuge/ops/testlauf.sh ~/.local/share/devflow2/venv/bin/python -B -m unittest discover -s tests2
```

Die Tests nutzen nachgebaute CLIs (`tests2/fake_cli.py`) und laufen ohne Netz und ohne Kontingent.
