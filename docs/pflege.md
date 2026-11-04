# Pflege

Sicherheitslücken und veraltete Pakete der Projekte, regelmäßig und auf Knopfdruck, **ohne KI** (S5.1), dazu [Routine-Updates](#routine-updates) unter einem Pflegeplan (S5.2). Plan: `plan/s5.md`. Das Dreaming (S5.3, S5.4) baut darauf auf und ist noch nicht da.

## Ablauf

Ein Lauf der Art `pflege` (intern, kein Modell, zählt nicht gegen `DEVFLOW2_MAX`) prüft den **committeten Stand des Basisbranchs** in einer Kopie (`git archive` in ein Temp-Verzeichnis, danach gelöscht), nie im laufenden Dienst und nie in den Arbeitsdateien. Ignorierte Dateien wie `.env` sind deshalb nicht dabei. Die Werkzeuge bekommen eine knappe Umgebung (kein `DEVFLOW2_*`, keine Schlüssel).

| Prüfung | Werkzeug | Was |
|---|---|---|
| `pip-audit` | `pip-audit --path … -f json` | Lücken in einer Umgebung, die `uv` in der Kopie aus den Anforderungen baut (nur Wheels, kein Build-Code aus dem Netz) |
| `pip-outdated` | `uv pip list --outdated` | veraltete Python-Pakete |
| `npm-audit` | `npm audit --json --package-lock-only` | Lücken laut Lockdatei |
| `npm-outdated` | `npm ci --ignore-scripts`, `npm outdated --json` | veraltete Node-Pakete |
| `osv-scanner` | `osv-scanner scan source --format json -L …` | Lücken in Lockdateien, wenn weder Python noch Node erkannt wird |

Die Werkzeuge fragen öffentliche Datenbanken (OSV, PyPI, npm) nur lesend ab und senden Paketnamen und Versionen, keinen Code. `pip-audit` nennt keine Schwere; sie wird je Lücke bei `api.osv.dev` nachgeschlagen (höchstens 60 Anfragen je Lauf). Fehlt sie, steht dort `unbekannt`.

**Werkzeuge einrichten:** `ops/pflege-werkzeuge.sh` (wiederholbar, `--pruefen` zeigt nur, was fehlt). `pip-audit 2.10.1` mit allen Abhängigkeiten fest per Hash (`ops/pflege-pip-audit.txt`, `--require-hashes`) in einer eigenen Umgebung `~/.local/share/devflow2/pflege-venv` (das venv des Dienstes bleibt unberührt), verlinkt nach `~/.local/bin/pip-audit`; `osv-scanner v2.7.0` als einzelne Datei nach `~/.local/bin`, SHA-256 wird vor dem Einbau geprüft. `uv` und `npm` müssen im `PATH` oder in `~/.local/bin` liegen. Gesucht wird in dieser Reihenfolge: `DEVFLOW2_PFLEGE_<NAME>`, Ordner des Interpreters, `~/.local/bin`, `PATH`.

## Einordnung

- **Fund:** eine Lücke in einem Paket. Mehrere Kennungen derselben Lücke (CVE, GHSA, PYSEC …) und mehrere Werkzeuge ergeben **einen** Fund (gleiches Paket und Ökosystem, mindestens eine gemeinsame Kennung); die Schwere ist die höchste gemeldete.
- **Schwere:** `kritisch`, `hoch`, `mittel`, `niedrig`, `unbekannt` aus GHSA-Schwere, CVSS-Wert (3.x wird gerechnet) oder dem Wort des Werkzeugs.
- **Veraltet:** Sprung `major`, `minor`, `patch` oder `unklar` von der aktuellen zur neuesten Version.
- **Nicht geprüft:** Fehlt ein Werkzeug, scheitert es, ist seine Ausgabe nicht lesbar, läuft es in das Zeitlimit (20 min) oder lässt sich die Umgebung nicht bauen, steht die Prüfung mit Grund unter „nicht geprüft“. Der Lauf bleibt `fertig`; das ist kein Fehler. Ein Lauf scheitert nur, wenn `[pflege]` unlesbar ist, die Kopie nicht möglich ist oder der Commit von Bericht und Eingang nicht klappt (das Ergebnis liegt dann trotzdem in `pflege/<projekt>.json`).

## Ergebnis

- **Bericht** `berichte/pflege-JJJJ-MM-TT.md` im Projekt, ein Commit auf `main` (zusammen mit den neuen Eingangseinträgen und einer Protokollzeile). Am selben Tag wird der Bericht überschrieben; ist er unverändert, gibt es keinen Commit.
- **Seite „Pflege“** (`/p/<projekt>/pflege`, Unterleiste des Projekts): je Projekt Lauf, Stand mit Link zum Bericht, nicht Geprüftes, Lücken, Veraltetes und der Knopf „Jetzt prüfen“. Gelesen wird `~/.local/state/devflow2/pflege/<projekt>.json` (das letzte Ergebnis).
- **Eingang:** je neuer Lücke ein Eintrag mit Herkunft `pflege` (`Pflege-Lücke (<paket>) <kennungen>: …`). Er enthält nur Paket, Version, Kennungen, Schwere und Fix-Version, nie Freitext aus den Datenbanken. Eine bekannte Lücke (gleiches Paket, gemeinsame Kennung) ergibt keinen zweiten Eintrag, auch nicht, wenn der erste `verworfen` ist. Höchstens 25 neue Einträge je Lauf, die schwersten zuerst; der Bericht nennt alle. Die Einträge wecken **nicht** den Selbstlauf (kein Ereignis `neue-idee`). Nur `pflege.py` setzt die Herkunft `pflege`; über Dashboard und MCP ist sie nicht erreichbar.
- **Push:** Eine hohe oder kritische Lücke im letzten Ergebnis ist ein Ereignis `pflege:<projekt>:<paket>-<kennung>`. Neue Ereignisse ergeben einen Hinweis „Pflege: Sicherheitslücke gefunden“ (mehrere zusammengefasst). Dieselbe Lücke meldet sich nicht wieder.

## Start

- **Wöchentlich:** Eine Hintergrundschleife des Dashboard-Dienstes (`pflege.schleife`, Takt 1 h, `DEVFLOW2_PFLEGE_TAKT`; erste Prüfung 2 min nach dem Start, `DEVFLOW2_PFLEGE_ANLAUF`) startet für jedes Projekt, dessen letzter Pflegelauf sieben Tage her ist (nach einem Fehler schon nach einem Tag). Ohne früheren Lauf ist ein Projekt sofort fällig. Bei „Alles anhalten“ startet nichts.
- **Knopfdruck:** „Jetzt prüfen“ auf `/p/<projekt>/pflege` (`POST /pflege/starten`, Feld `projekt`, wie alle Handlungen mit `Origin`-Prüfung). Läuft für das Projekt schon eine Prüfung, entsteht keine zweite (Doppelklick, Timer und Knopf teilen eine Sperre).
- **Traum:** Im [Traum](traum.md#reihenfolge) läuft die Prüfung als zweiter Schritt. Sie schreibt Bericht und Eingang wie immer, löst aber keine Update-Kette aus: `pflege-routine` entfällt, `pflege_aufgabe` lehnt ein Ergebnis aus dem Traum ab, und die Wochenprüfung bleibt trotzdem fällig. Am Fensterende beendet der Lauf das laufende Prüfwerkzeug selbst und endet als `traum-beendet`, ohne Ergebnis.

## Einstellungen

`[pflege]` in `devflow.toml` ist optional; devflow erkennt Standardwerte aus den Dateien im Basisbranch. Unbekannte Schlüssel und falsche Typen sind ein Fehler, damit Tippfehler auffallen.

| Schlüssel | Bedeutung | Erkannt |
|---|---|---|
| `aktiv` | `false` schaltet die Pflege für das Projekt aus | `true` |
| `pruefungen` | Liste aus den Namen oben | Python → `pip-audit`, `pip-outdated`; Node → `npm-audit`, `npm-outdated`; sonst mit Lockdatei → `osv-scanner`; sonst leer („nichts zu prüfen“) |
| `umgebung` | Pfad einer Python-Umgebung; ihre installierten Pakete werden geprüft (für Projekte, deren Anforderungen nicht im Repo stehen) | leer |
| `python_quellen` | Anforderungsdateien (`requirements*.txt`, `pyproject.toml` mit `dependencies`); liegt neben der `pyproject.toml` eine `uv.lock`, gilt deren festgelegter Stand (`uv export --frozen --no-dev`), nicht die Bereiche aus der `pyproject.toml` | alle im Basisbranch |
| `node_verzeichnisse` | Ordner mit `package.json` (und `package-lock.json`, sonst „nicht geprüft“); ein Workspace-Paket ohne eigene Lockdatei nutzt die nächste weiter oben, geprüft wird einmal dort | alle im Basisbranch |
| `lockdateien` | Lockdateien für `osv-scanner` | bekannte Namen im Basisbranch |

Beispiel devflow selbst (die Pakete liegen nur im venv, nicht im Repo): `umgebung = "~/.local/share/devflow2/venv"` und `python_quellen = []`.

## Routine-Updates

Patch und Minor laufen unter einem einmal abgenommenen Pflegeplan je Projekt ohne Einzelabnahme durch die volle Kette (Entwicklung, Tests, Gegenreview, Merge, Deploy). Durchgesetzt wird das in `devflow2/pflegeplan.py`, nicht im Prompt.

### Pflegeplan

`plan/pflege.md` im Projekt, Vorlage `plan/_pflege-vorlage.md`. Der Plan hat keine Aufgabentabelle; er wird im Dashboard wie jeder Plan abgenommen, aber nicht übernommen. Gültig ist nur der committete Stand des Basisbranchs. Eine nicht committete Änderung an der Datei sperrt, denn sie kann ein Widerruf sein.

| Abschnitt | Feld | Prüfung |
|---|---|---|
| Kopf | `**Stand:** abgenommen vom Inhaber am …`, `**Pflege:** aktiv` | die erste Stand-Zeile zählt; jede Zeile `**Pflege:** widerrufen` sperrt, andere Werte auch |
| Umfang | `**Pakete:**`, `**Dateien:**` | `alle` oder Namen; nur Manifeste und Lockdateien, relative Pfade |
| Sprünge | `**Erlaubt:**` | nur `patch`, `minor`; `major` macht den Plan ungültig |
| Tests | `**Befehl:**` | Pflicht; ersetzt für Pflegeaufgaben `test` aus `devflow.toml` |
| Deploy | `**Ziel:**` | muss `deploy` aus `devflow.toml` entsprechen, `kein` für keinen |
| Rückweg | Text | Pflicht; `git revert` reicht nicht, wenn sich die Laufzeitumgebung geändert hat, deshalb installiert der Deploybefehl aus der Lockdatei |
| Bekannte Brüche und Migrationen | ``- `paket`: Grund`` | diese Pakete gehen immer zum Inhaber; ändert ein Update sie transitiv, gibt es keinen Merge, sondern eine Frage |

**Widerruf:** Zeile `**Pflege:** widerrufen` committen oder Knopf „Pflegeplan widerrufen“ auf `/p/<projekt>/pflege`. Danach entsteht keine Aufgabe, keine offene startet, keine wird gemergt, und nach einem schon laufenden Merge folgt statt des automatischen Deploys eine Frage. Rückgängig macht das nur der Inhaber von Hand; `plan_entwurf` überschreibt keinen abgenommenen Plan.

### Werkzeug `pflege_aufgabe`

Nur damit legt die steuernde KI Update-Aufgaben an. `aufgabe_anlegen` fehlt in ihrem Werkzeugsatz und lehnt Kennungen `P.<datum>.<n>` auch sonst ab; eine solche Zeile ohne Freigabe startet nicht und wird nicht gemergt. Die Aufgabe entsteht aus dem letzten Prüfergebnis (höchstens 14 Tage alt), nicht aus Angaben der KI. `pakete` wählt aus, `bedenken` (Paket → Grund) schickt ein Paket zum Inhaber; beides macht den Umfang nur enger, unbekannte Namen lehnen den Aufruf ab.

| Kandidat (Reihenfolge der Prüfung) | Ergebnis |
|---|---|
| nicht im Paketumfang | keine Aufgabe, unter `ausserhalb` genannt; ausdrücklich gewählt: Aufruf abgelehnt |
| bekannter Bruch, Migration, Bedenken | Entwurf mit Frage |
| keine Fix-Version, Major oder unklar | Entwurf mit Frage |
| Sprung im Plan nicht erlaubt | keine Aufgabe wie oben |
| sonst | Aufgabe `P.<JJJJ-MM-TT>.<n>`, Status `bereit`, je Aufruf eine für alle passenden Pakete |

- **Vorsichtige Einordnung:** `patch` und `minor` gelten nur für klare, steigende Versionen mit bis zu drei Zahlen. Vorabversionen, Rückschritte, Minor unter `0.x` und jeder Sprung unter `0.0.x` sind `unklar`.
- **Lücken:** Ziel ist die kleinste Fix-Version über der aktuellen. Mehrere Kopien in verschiedenen Versionen sind unklar.
- **Entwurf:** `plan/pflege-<datum>-<n>.md` mit Tabelle der Pakete und Gründe, Aufgabe `U.<datum>.<n>`, dazu eine Frage ohne Aufgabenbezug. Nach Abnahme läuft er wie jeder Plan.
- **Keine Doppelten:** Ein Paket in einer offenen Aufgabe ergibt nichts Neues. Nach `erledigt` oder `entfällt` zählt erst eine höhere Version wieder; ein gemeldeter Entwurf gilt je Paket und Zielversion einmal. Stand: `pflege/<projekt>-updates.json`.
- **Selbstlauf:** Ein Pflegelauf meldet `pflege-routine` nur bei neuer Arbeit unter gültigem Plan, die Abnahme des Pflegeplans ebenfalls. Der Steuerlauf ruft dann `pflege_aufgabe` und `entwickeln(automatisch=True)` auf.

### Kette

- **Entwicklung:** Der Auftrag nennt Pakete, Versionen und erlaubte Dateien; Review und Nacharbeit bekommen denselben Text. Es gilt die Stufe des Projekts.
- **Tests:** Der Befehl läuft in einer frischen Kopie von `HEAD` (`git archive`) mit eigenem `HOME` und knapper Umgebung, also ohne ignorierte Dateien, ohne `DEVFLOW2_*` und nicht in der Umgebung des Dienstes. Das gilt auch für die Tests nach einem Rebase.
- **Prüfung vor dem Merge:** `mergen` und der Merge-Lauf (nach dem Rebase) vergleichen den Diff gegen die Basis mit der Freigabe. Eine Abweichung ergibt eine Frage statt eines Merges:
  - geänderte Dateien nur aus dem Umfang oder die eigenen `berichte/<ID>.md` und `berichte/<ID>-*.md`; Dateien nicht angelegt, gelöscht oder mit neuem Modus
  - Manifeste: außerhalb der Versionsangaben unverändert (keine Skripte, keine Quellen), keine neuen Pakete, Versionen nur für Pakete der Aufgabe. Die Anforderung wird gelesen, nicht als Text durchsucht: Name, Extras, Operatoren und Marker bleiben, eine geänderte Version ist genau die Zielversion und steht nicht an einer oberen Grenze. URLs, Git, Aliase, Tags und Vorabversionen gelten als nicht prüfbar. Muss sich eine obere Grenze mitbewegen, kommt eine Frage.
  - Lockdateien (`package-lock.json`, `uv.lock`, `poetry.lock`, `Cargo.lock`, Pins in `requirements*.txt`): jede neue Version im Paketumfang und ein erlaubter Sprung, jede weggefallene mit erlaubtem Ziel, bei gleicher Version unveränderte Quelle und Prüfsumme; neue Abhängigkeiten nur bei Paketumfang `alle` oder wenn sie dort genannt sind; außerhalb der Pakete (z. B. `requires-python`) keine Änderung
  - Abhängigkeitskanten aus `package-lock.json`, `uv.lock`, `poetry.lock` und `Cargo.lock`: Jede Kante (Eltern, Paket) behält oder wechselt ihre Version nur mit erlaubtem Sprung, und zwar je Stelle des Elternpakets (npm: Pfad, TOML-Lockdateien: Elternversion). So fällt auch ein Wechsel auf eine schon vorhandene Version auf, etwa wenn npm eine verschachtelte 1.0.0 durch die 2.0.0 weiter oben ersetzt oder nur eine von mehreren Kopien desselben Elternpakets wechselt. Stellen, die nur auf einer Seite vorkommen (umgezogen, neue Elternversion, auf eine vorhandene Stelle zusammengeführt), lassen sich nicht zuordnen und werden konservativ geprüft: Eine entfallene Stelle zählt gegen alle neuen Versionen der Kante, eine neue gegen alle alten, auch wenn auf der anderen Seite keine Stelle dazukommt. Passt eine Kombination nicht, kommt eine Frage. Das gilt gleich für npm- und TOML-Lockdateien und ist bewusst keine Auflösungsanalyse: Trägt eine Kante zwei Major-Versionen, löst auch ein Umzug oder eine neue Elternversion ohne echten Wechsel eine Frage aus. Lieber eine Frage zu viel als ein unbemerkter Major-Wechsel.
  - kein Paket unter „Bekannte Brüche und Migrationen“ ändert sich, auch nicht transitiv
  - jedes Paket der Aufgabe erreicht seine Zielversion
  - was sich nicht lesen lässt (andere Lockdateien, kaputter Inhalt), gilt als Abweichung
- **Antwort:** Eine Antwort hebt die Prüfung nicht auf. Der Weg ist Nacharbeit, Aufgabe `entfällt` oder ein eigener Plan.
