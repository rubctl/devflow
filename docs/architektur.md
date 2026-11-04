# Architektur

## Überblick

devflow 2 ist ein Werkzeug für Code: Eine KI (Standard Claude) steuert über MCP die Umsetzung abgenommener Pläne, Modelle entwickeln und reviewen als Hintergrundläufe, der Inhaber überwacht. Der Stand liegt als md im jeweiligen Projekt-Repo; devflow hält lokal nur den Laufzustand. Bedienung und Regeln im Detail: [`DEVFLOW2.md`](../DEVFLOW2.md).

devflow 1 (Paket `devflow/`, Tag `devflow-1`) ist eingefroren und liegt bis zur Umstellung daneben.

## Bausteine

| Baustein | Ort im Repo | Aufgabe |
|---|---|---|
| MCP-Server | `devflow2/mcp_server.py` | Werkzeuge für die steuernde KI (stdio) |
| Ideeneingang | `devflow2/eingang.py`, `web_eingang.py` | Ideen in `plan/eingang.md`, Textfeld auf der Projektseite, MCP `eingang` und `idee_zustand` ([Dashboard](dashboard.md#ideen-erfassen-s34)) |
| Selbstlauf | `devflow2/selbstlauf.py`, `ereignis.py` | Ereignisse sammeln, Steuerlauf `steuern` starten (einer zur Zeit, Tagesbudget, Schalter); Beschreibung unter [Selbstlauf](#selbstlauf) |
| Ablauf | `devflow2/ablauf.py` | Kette entwickeln → testen → Review → Nacharbeit → mergen → deployen, Fragen an den Inhaber |
| Läufe | `devflow2/laeufe.py` | CLI-Aufrufe (`claude -p`, `codex exec`, Shell) als eigene Prozesse mit Log, Zeitlimit, Kontingentpause |
| Roadmap | `devflow2/roadmap.py` | `ROADMAP.md` lesen und schreiben: Aufgaben, Kanban, Fragen, Protokoll, Pläne |
| Repo | `devflow2/repo.py` | Git: Statuscommits auf `main`, Sperre, Push ([Push von main](#push-von-main)) |
| Projekte | `devflow2/projekte.py` | angemeldete Projekte, `devflow.toml` |
| Dashboard | `devflow2/web.py`, `web_aktion.py`, `web_push.py`, `web_kontingent.py`, `web_roadmap.py`, `web_html.py`, `web_md.py`, `static/` | Oberfläche für den Inhaber (`python -m devflow2 web`): lesen, Fragen beantworten, Läufe anhalten und fortsetzen, siehe [Dashboard](dashboard.md) |
| Kontingent | `devflow2/kontingent.py` | Auslastung von Claude und Sol aus Lauflogs und codex-Sitzungsdateien (nur lesend), Kosten je Aufgabe und Lauf, Läufe/Runden/Dauer je Aufgabe für die Roadmap-Tabelle ([Dashboard](dashboard.md#kontingent-s32)) |
| Web-Push | `devflow2/push.py`, `push_ereignisse.py`, `push_protokoll.py` | Hinweise aufs Handy bei „Braucht dich“, neuer Frage, fehlgeschlagenem Lauf ([Dashboard](dashboard.md#handy-und-web-push-s24)) |
| Pflege | `devflow2/pflege.py`, `web_pflege.py` | Lauf `pflege` ohne KI: Lücken und veraltete Pakete in einer Kopie prüfen, Bericht, Eingang, Push; Wochentimer im Dienst ([Pflege](pflege.md)) |
| Traum | `devflow2/traum.py`, `web_traum.py` | Dreaming von Hand: Startsperre mit dem Selbstlauf, gespeichertes Fensterende, Grenzen vor jedem Modellstart, Traumarten, Traumbericht ([Traum](traum.md)) |
| Vorschläge | `devflow2/vorschlag.py` | Register der Traum-Vorschläge, Sperre für Merge und Deploy, Schreibziele, „Übernehmen“ und „Verwerfen“ ([Traum](traum.md#vorschläge)) |
| Routine-Updates | `devflow2/pflegeplan.py` | Pflegeplan prüfen, `pflege_aufgabe`, Entwurf und Frage bei Großem, Diff-Prüfung vor dem Merge ([Pflege](pflege.md#routine-updates)) |
| Aufträge | `devflow2/auftraege.py` | Texte für Entwicklung, Nacharbeit, Review (JSON-Schema, Schwere P1–P3, an Kernzusagen gebunden), Meinung, Planen, Steuerlauf, Traum; Masterplan und Entscheidungen stehen im Auftrag |
| Masterplan | `devflow2/grundlage.py` | `MASTERPLAN.md` und bestätigte Entscheidungen von der Basis lesen, für Aufträge aufbereiten, Stand je Lauf festhalten, Merge-Sperre, Hinweise für die Lage ([Masterplan](masterplan.md)) |
| Repo-Karte | `devflow2/karte.py` | Dateien mit Klassen und Funktionen für den Einstieg der Läufe, höchstens 5000 Zeichen |
| Tests | `tests2/` | Abläufe mit Attrappen statt echter CLIs |

## Push von main

Jeder Commit von `repo.auf_main` / `auf_main_gesperrt` (Roadmap, Berichte, Planentwürfe, Ideen, Zweitmeinungen) merkt den Push vor (`.git/devflow2-push-offen`, nur mit Remote). Beim Verlassen der Projektsperre (`repo.sperre`, bei `auf_main` also gleich danach) schiebt `repo.nachschieben` den Basisbranch nach, außerhalb der Sperre und mit Zeitlimit (60 s); mehrere Commits fallen in einen Push zusammen. Scheitert er (Remote nicht erreichbar, Remote weiter, Zugang abgelaufen), bleibt der Commit bestehen, der Vermerk samt Fehlertext bleibt, und der nächste Commit versucht es erneut. Solange ein Fehler vermerkt ist, steht „Push offen“ unter „Braucht dich“. Es gibt nie `--force`. Der Push nach dem Merge (`ablauf.py`) nutzt dieselbe Funktion `repo.push` und löscht den Vermerk bei Erfolg; ein zweiter Push ohne neue Commits ist wirkungslos. Ohne Remote ändert sich nichts.

## Selbstlauf

Nach Abnahme, Antwort, Laufende oder Kontingentfreigabe weckt devflow die steuernde KI, ohne dass eine Claude-Sitzung offen ist. Sie macht den nächsten Schritt, bis eine Frage an den Inhaber ansteht.

- **Ereignisse:** Zeilen in `ereignisse.jsonl` im Zustandsverzeichnis, nur Art, Projekt und Bezug, nie Eingangstexte.

  | Art | Bezug | Wer meldet |
  |---|---|---|
  | `plan-abgenommen`, `plan-rueckmeldung` | Plandatei | `plaene.py` |
  | `neue-idee` | `plan/eingang.md` | `eingang.py` |
  | `antwort` | Frage (`F3`) | `ablauf.antwort_eintragen` |
  | `lauf-ende` | Lauf | `laeufe.ausfuehren`, wenn ein Lauf `fertig` oder `fehler` endet und für seine Aufgabe kein Folgelauf aktiv ist |
  | `pflege-routine` | Datum oder Pflegeplan | `pflegeplan.nach_pruefung` bei neuer Arbeit unter gültigem Plan, `plaene.abnehmen` für `plan/pflege.md` |
  | `kontingent-frei` | Lauf | `laeufe.py`, wenn die Pause eines Laufs endet |

  Die Kette mit `automatisch=True` weckt also erst an ihrem Ende, nicht bei jedem Schritt. Angehaltene Läufe und Steuerläufe melden nichts; ein abgestürzter Laufprozess auch nicht, er erscheint nur als Lauffehler in Dashboard und Push.
- **Start:** Die Hintergrundschleife des Dashboard-Dienstes ruft alle 10 s `selbstlauf.pruefen` auf (`DEVFLOW2_SELBSTLAUF_TAKT`). Ohne Dienst startet kein Steuerlauf. Es läuft höchstens ein Steuerlauf zur Zeit über alle Projekte. Er übernimmt die offenen Ereignisse seiner Art im Projekt mit dem ältesten Ereignis; dieses Ereignis bestimmt auch die Art (Planungslauf oder Steuerlauf, siehe **Rechte**). Was übrig bleibt oder währenddessen dazukommt, ergibt nach seinem Ende genau einen weiteren. Ein fehlgeschlagener Steuerlauf holt seine Ereignisse nicht zurück (sonst drohte eine Schleife); der Lauffehler erscheint im Dashboard und als Push, und der nächste Steuerlauf sieht denselben Stand über `lage()`.
- **Halt:** Es startet nichts, wenn der Schalter aus ist (Standard), `anhalten alles` gilt, ein [Traum](traum.md) aktiv ist (gemeinsame Startsperre) oder das Tagesbudget verbraucht ist; die Ereignisse warten dann. „Aus“ beendet keinen Lauf; das tut „Alles anhalten“, das auch einen laufenden Steuerlauf stoppt.
- **Budget:** `selbstlauf_budget` (30 Steuerläufe pro Tag) in `devflow.toml`. Gezählt wird über alle Projekte seit Mitternacht, auch fehlgeschlagene Läufe; bei verschiedenen Werten gilt der kleinste, damit die vom Inhaber freigegebene Summe nicht überschritten wird. Scheitert ein Start am Budget, kommt ein Web-Push-Hinweis (einer je Tag). Am nächsten Tag geht es von selbst weiter.
- **Der Steuerlauf:** ein Lauf der Art `steuern` mit Log im Dashboard, ohne Aufgabe, im Haupt-Checkout des Projekts. Modell und Effort aus `[stufen.steuern]` in `devflow.toml` (Standard Sonnet/medium; im Plan nicht wählbar). Zeitlimit 30 Minuten. Er zählt nicht gegen `DEVFLOW2_MAX`, weil er sonst mit dem Standardwert 1 Stunden auf einen Entwicklerlauf warten würde. In der Probe vom 30.10. brauchte ein Steuerlauf bis 7 s und 0,02–0,06 $ Gegenwert.
- **Rechte:** `claude -p` mit eigener MCP-Konfiguration je Satz (`selbstlauf-steuerung-mcp.json`, `selbstlauf-planung-mcp.json`, nur devflow, `--strict-mcp-config`), eingebaut nur `Read`, `Grep` und `Glob`, keine Shell, kein Schreibzugriff. Es gibt zwei Werkzeugsätze (`selbstlauf.SAETZE`):
  - **Planungslauf** (`PLANUNG`) für `neue-idee` und `plan-rueckmeldung`: `lage`, `aufgaben`, `aufgabe`, `lauf`, `eingang`, `plan_entwurf`, `idee_zustand` und `frage_stellen` (nur ohne Aufgabe). Auftrag `auftraege.planen`. Diese Ereignisse teilen sich nie einen Lauf mit anderen.
  - **Steuerlauf** (`WERKZEUGE`) für alles andere. Es fehlen `antwort_eintragen`, `status_setzen`, `aufgabe_anlegen`, `projekt_anmelden`, `anhalten` und die Planungswerkzeuge `eingang`, `plan_entwurf`, `idee_zustand`.
- **Regeln:** Der Auftrag (`auftraege.steuern`) enthält „Nicht ohne den Inhaber“ aus `AGENTS.md`, ein Test hält beide gleich. Abnehmen darf nur der Inhaber; `plan_entwurf` lehnt deshalb zusätzlich jede Abnahmezeile ab und überschreibt keinen abgenommenen Plan.
- **Doppelte Steuerung:** Steuert Claude aus einer offenen Sitzung, muss der Selbstlauf **aus** sein, sonst handeln zwei KIs an denselben Aufgaben. Deshalb ist er nach der Installation aus; `lage()` zeigt seinen Stand unter `selbstlauf`.
- **Rückweg:** Schalter aus. Jede Handlung ist ein Lauf oder Commit, zurück geht es mit `git revert`.

## Grenzen im Code

Was hier steht, setzt devflow selbst durch; es hängt nicht am Prompt der steuernden KI. Nicht abgedeckt sind ein kompromittierter Entwicklerlauf oder Host: Läufe haben die Rechte des Benutzers, der devflow betreibt.

- **Texte von außen ändern keine Struktur** (`roadmap.kapsel`, `roadmap.sauber`). Antworten, Fragetexte und Empfehlungen stehen hinter einer festen Kennung, mehrzeilig als Zitatblock (`> …`); Ideen stehen immer als Zitat. Steuerzeichen, fremde Zeilentrenner und unsichtbare Richtungswechsel fallen weg. Damit beginnt keine Zeile mit Text von außen: `### F99`, der Offen-Marker, Status- und Tabellenzeilen, `## Idee …` und `**Zustand:** …` bleiben Text. Marker zählen nur als ganze Zeile, auch die für „Aktuell“. Einzeilige Felder (Zellen, Protokoll, Änderungswünsche) werden zu einer Zeile. Ältere Einträge bleiben unverändert und lesbar.
- **Herkunft kommt vom Zugang** (`roadmap.HERKUNFT`: `dashboard`, `steuerung`, `nutzer`). `web_aktion.py` setzt fest `dashboard`, `mcp_server.py` fest `steuerung`; kein Werkzeug nimmt sie als Parameter, und aus Text wird sie nie gelesen. Ohne Angabe gilt in `ablauf.py` `steuerung`, das Recht ohne Vorrang. „Antwort Inhaber“ steht nur bei `dashboard`, dessen Login nur der Inhaber erreicht.

  | Was | Wo die Herkunft steht |
  |---|---|
  | Antwort | Kennung im Frageblock (`**Antwort Inhaber (31.10., dashboard):**`, `**Antwort (31.10., steuerung):**`) und Protokoll |
  | Idee | Kopfzeile in `plan/eingang.md`, MCP `eingang`, Dashboard und Protokoll |
  | Halt eines Laufs | `halt_herkunft` im Lauf und Protokoll (scheitert das Protokoll, gilt der Halt trotzdem) |
  | Start und Anhalten eines Laufs | `lauf.json` ändern beide nur unter der Sperre `lauf.lock` im Laufverzeichnis (`laeufe.sperre`); ein Lauf, der beim Start schon angehalten ist oder dem der globale Halt begegnet, beginnt keine Arbeit und bleibt `angehalten` (Traumlauf: `traum-beendet`). Ein Einzelhalt legt vor der Sperre die Datei `halt.anfrage` an (nur während `anhalten`); der Start prüft sie nach dem Eintragen der PID und beginnt dann keine Modellarbeit |
  | Globaler Halt | Datei `angehalten`, `lage()['halt']`; Protokoll der Projekte, deren Läufe er gestoppt hat |

- **Offene Frage sperrt ihre Aufgabe** (`ablauf.offene_fragen`). Maßgeblich ist die offene Frage, nicht der Status: `status_setzen` hebt die Sperre nicht auf, nur die Antwort auf die letzte offene Frage. Gesperrt sind `entwickeln`, `nacharbeit`, `testen`, `review_anfordern`, `mergen` und `fortsetzen` eines Laufs der Aufgabe, im MCP wie im Dashboard. Der Merge prüft innerhalb der Repo-Sperre noch einmal. Fragen ohne Aufgabenbezug sperren nichts.
  - **Deploy:** Deployt wird ganz `main`. Die Kette sperrt die eigene Frage und jede zu einer schon gemergten Aufgabe; das Werkzeug `deployen` kennt keine Aufgabe und sperrt deshalb bei jeder Frage mit Aufgabenbezug.
  - **Kette:** Trifft ein Abschluss auf die Sperre, endet sie ohne Fehler mit dem Protokolleintrag „Kette wartet“, die Aufgabe bleibt `Braucht dich`. Nach der Antwort holt die Steuerung sie ab (Ereignis `antwort`).
  - **Grenze:** Ein Lauf, der schon läuft, wenn die Frage kommt, läuft zu Ende; erst der nächste Schritt ist gesperrt.
- **Fortsetzen ist begrenzt** (`ablauf.fortsetzen`). Das MCP nimmt nur Lauf-IDs: `fortsetzen("alles")` lehnt es ab, auch in einer offenen Sitzung, auch wenn der Halt aus der Steuerung kam. Läufe mit `halt_herkunft` `dashboard` setzt nur das Dashboard fort. Das gilt auch für Läufe, die „Alles anhalten“ im Dashboard gestoppt hat. Die Aufhebung im Dashboard löscht die Herkunft.
- **Neue Ideen führen nur zu Planentwürfen.** Der Planungslauf hat keine ausführenden Werkzeuge. Das gilt doppelt: `--allowedTools` begrenzt die CLI, und `devflow2 mcp <satz>` bietet nur den Satz an, der Rest existiert in diesem Server nicht (`mcp_server.begrenzen`). Der Steuerlauf hat umgekehrt keinen Zugriff auf `eingang`, `plan_entwurf` und `idee_zustand`; lesen könnte er `plan/eingang.md` über `Read`, sein Auftrag führt ihn nicht dorthin. Abnehmen bleibt dem Inhaber (`plan_entwurf` lehnt Abnahmezeilen ab, `plan_uebernehmen` verlangt sie).
- **Aufträge nennen Eingangstexte als Daten** (`auftraege.DATEN`), keine Anweisungen und keine Freigabe. Das ist die zweite Linie, nicht die Grenze.

## Kontext und Kontingent

Teuer ist nicht das erste Lesen, sondern dass jeder Schritt eines Laufs den ganzen bisherigen Verlauf mitliest. Deshalb:

- **Stufe je Aufgabe** (Planspalte `Stufe`, sonst `stufe` des Projekts) → Modell und Effort für Claude und Sol (`Projekt.cli`, Werte in `projekte.STUFEN`, änderbar unter `[stufen.<name>]` in `devflow.toml`).
- **Einstieg statt Suche:** Aufträge nennen den Bereich aus dem Plan und enthalten die Repo-Karte; Läufe holen nur knappe Ausgaben in den Verlauf.
- **Frische Nacharbeit:** Runden nach Review, roten Tests oder Hinweisen starten eine neue Sitzung. Mit: Aufgabe, Plan, Zusatzauftrag vom Start, frühere Hinweise der steuernden KI, Fragen an den Inhaber mit Antworten, Befunde. Nur kurzes Nachhaken und Kontingentpausen setzen die Sitzung fort.
- **Review nach Zweck:** P1 echter Schaden, P2 Abnahmekriterium verfehlt, Fehler bei normaler Nutzung oder Projektregel verletzt, sonst P3 ohne Nacharbeit. Nachprüfungen sehen den Diff seit der letzten Runde; neue Befunde außerhalb davon nur bei P1, im Nacharbeitsdiff auch P2.
- **Ein Modelllauf zur Zeit** (`DEVFLOW2_MAX`, Standard 1).
- **Kontingentpause:** Der Lauf wartet bis zur gemeldeten Freigabe (`laeufe.frei_ab`). Nennt die Meldung nur eine Uhrzeit, die weniger als 3 Stunden zurückliegt (auch über Mitternacht), versucht er es in 5 Minuten erneut statt am nächsten Tag; der Anbieter schaltet oft erst kurz nach der genannten Zeit frei.

## Daten

| Was | Wo | Wer schreibt |
|---|---|---|
| Stand, Fragen, Protokoll | `ROADMAP.md` im Projekt-Repo | nur devflow (Statuscommits auf `main`) |
| Pläne, Berichte, Wiki | `plan/`, `berichte/`, `docs/` im Projekt-Repo | Claude Code (Pläne), Läufe (Berichte, Wiki) |
| Angemeldete Projekte | `~/.local/state/devflow2/projekte.json` | devflow |
| Läufe | `~/.local/state/devflow2/laeufe/<id>/` (`lauf.json`, `log.jsonl`, `ergebnis.txt`) | devflow |
| Globaler Halt | `~/.local/state/devflow2/angehalten` | devflow (`anhalten alles`) |
| Pflege | `~/.local/state/devflow2/pflege/<projekt>.json` (letztes Ergebnis), `<projekt>-updates.json` (Freigaben der Aufgaben `P.*`, gemeldete Entwürfe), `start.lock`; Bericht `berichte/pflege-*.md` im Projekt | devflow (Lauf `pflege`, `pflege_aufgabe`) |
| Selbstlauf | `~/.local/state/devflow2/selbstlauf.json` (Schalter, Budgethinweis), `ereignisse.jsonl`, `selbstlauf-mcp.json` | devflow |
| Traum | `~/.local/state/devflow2/traeume/<id>.json` (Fensterende, Schritte, Zähler); Bericht `berichte/traum-<id>.md` im Heimatprojekt | devflow (`traum.py`) |
| Traum-Vorschläge | `~/.local/state/devflow2/vorschlaege/<id>.json` (Branch, Worktree, Commit, Freigabe, Entscheidung), nie gelöscht | devflow (`vorschlag.py`) |
