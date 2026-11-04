# Betrieb

## Orte

| Was | Wo |
|---|---|
| Code (läuft direkt aus `main`) | `~/dev/devflow` |
| venv | `~/.local/share/devflow2/venv` (Python 3.11, `mcp==1.30.0`) |
| Laufzustand und Logs | `~/.local/state/devflow2/` (darin `push.json`: Geräteabos, 0600; `selbstlauf.json`, `ereignisse.jsonl`) |
| VAPID-Schlüssel für Web-Push | `~/.config/devflow2/vapid.key` (0600, entsteht beim ersten Start des Dienstes; nicht löschen, sonst sind alle Geräteabos ungültig und müssen neu angemeldet werden) |
| MCP-Anmeldung in Claude Code | `claude mcp add devflow -s user …`, siehe [`DEVFLOW2.md`](../DEVFLOW2.md#einrichtung) |

## Starten

Der MCP-Server ist kein Dienst: Claude Code startet ihn je Sitzung (stdio), Läufe starten als eigene Prozesse (`python -m devflow2 lauf <id>`). Das Dashboard dagegen läuft als Benutzerdienst, siehe [Dashboard-Dienst](#dashboard-dienst).

```bash
PYTHONPATH=~/dev/devflow ~/.local/share/devflow2/venv/bin/python -B -m devflow2 lage   # Lage als JSON
PYTHONPATH=~/dev/devflow ~/.local/share/devflow2/venv/bin/python -B -m devflow2 web    # Dashboard auf 127.0.0.1:8478
```

Das venv entsteht aus der festen Paketliste: `uv venv --python 3.11 ~/.local/share/devflow2/venv && uv pip install --python ~/.local/share/devflow2/venv/bin/python -r requirements.txt` (Python 3.11 und Pins: Release).

Der Befehl `web` startet das Dashboard von Hand (nur zum Ausprobieren, sonst läuft der Dienst). Zugang und Seiten: [Dashboard](dashboard.md).

## Einstellungen je Projekt

In `devflow.toml` des Projekts (alle optional, Tabelle in [`DEVFLOW2.md`](../DEVFLOW2.md#einrichtung)): `test`, `deploy`, `entwickler`, `reviewer`, `basis`, `stufe`, `[stufen.<name>]`, `[stufen.steuern]`, `claude_modell`, `karte_auslassen`, `selbstlauf_budget`, `[traum_grenze]`, `[traum_reserve]` (siehe [Traum](traum.md#grenzen)), `[pflege]` (siehe [Pflege](pflege.md)). Gleichzeitige Modellläufe über die Umgebungsvariable `DEVFLOW2_MAX` (Standard 1). Laufende oder angehaltene Läufe behalten Modell und Effort, mit denen sie gestartet wurden.

## Deploy und Rollback

Ein Merge auf `main` wirkt für Läufe ab dem nächsten Start; laufende Läufe behalten ihren Code. Das Dashboard übernimmt den neuen Code erst mit dem Deploy: `deploy = "ops/web_deploy.sh"` in `devflow.toml` startet `devflow2-web` neu und prüft `/health`. Antwortet es nicht innerhalb von 30 s (`DEVFLOW2_WEB_WARTEN`), endet der Deploy mit Fehler (Exit 1, `systemctl status` im Log), und devflow stellt dem Inhaber eine Frage.

**Rückweg:** Commit auf `main` zurücknehmen (`git revert <merge>`), dann erneut deployen (MCP `deployen` oder von Hand `cd ~/dev/devflow && ops/web_deploy.sh`). Läufe laden den zurückgenommenen Stand von selbst. Gilt nicht für den Revert genau des Erstinstallations-Commits von S2.2: Er entfernt `ops/web_deploy.sh`, und `deploy` in `devflow.toml` ist danach leer. Dann nicht deployen, sondern den Dienst mit dem [Rückbau](#dashboard-dienst) unten abbauen (oder `systemctl --user restart devflow2-web` von Hand). Ohne Dashboard (Notbremse) genügt `systemctl --user stop devflow2-web`; Aufgaben und Läufe laufen davon unberührt, weil das Dashboard nur liest.

## Dashboard-Dienst

Benutzerdienst `devflow2-web` (systemd `--user`) startet `python -m devflow2 web` aus `~/dev/devflow` auf `127.0.0.1:8478`, `Restart=always`, `WantedBy=default.target`. Linger ist an, deshalb startet er mit server, ohne dass jemand angemeldet ist. Das Tailnet erreicht ihn über `tailscale serve` auf `https://server.example.net:8472`; die Freigaben auf 443 und 7443 bleiben unberührt.

| Datei | Aufgabe |
|---|---|
| `ops/devflow2-web.service` | Vorlage der Einheit (`%h` statt fester Pfade), wird nach `~/.config/systemd/user/` kopiert |
| `ops/web_einrichten.sh` | einmalige und wiederholbare Einrichtung: Linger, Einheit installieren, `enable`/`start`, bei Login-Art `tailscale` `tailscale serve --bg --https=8472 http://127.0.0.1:8478` (Ports aus `instanz.toml`) |
| `ops/web_deploy.sh` | Neustart und `/health`-Prüfung (mit Login-Kopfzeile bei `web.login = "tailscale"`), Exit 1 bei Ausfall |
| `ops/testlauf.sh` | Testläufe nacheinander statt parallel, gedrosselt (siehe [Instanz-Einstellungen](instanz.md#testläufer)) |

Der Dienst trägt vier Hintergrundschleifen: Web-Push, den [Selbstlauf](architektur.md#selbstlauf), den Wochentimer der [Pflege](pflege.md#start) und den Takt des [Traums](traum.md) (10 s, `DEVFLOW2_TRAUM_TAKT`; startet nie selbst einen Traum). Die Prüfwerkzeuge der Pflege richtet `ops/pflege-werkzeuge.sh` ein (einmal, wiederholbar). Läufe, die er startet (Fortsetzen im Dashboard, Steuerläufe und alles, was sie anstoßen), bekommen je eine eigene Scope-Einheit (`systemd-run --user --scope`), sonst säßen sie unter dessen Speichergrenze (`MemoryMax=1G`) und stürben bei großen Testläufen mit. Geht `systemd-run` nicht, startet der Lauf ohne eigene Einheit. Weil im `PATH` des Dienstes `~/.local/bin` fehlt, ergänzt `laeufe._spawn` den Pfad für alle Läufe.

**Einrichten** (einmal nach dem Merge, auf server, im Haupt-Checkout):

```bash
cd ~/dev/devflow && ops/web_einrichten.sh
```

Wiederholen ist gefahrlos: Der Lauf ändert nur, was fehlt oder abweicht (Einheit nur bei Unterschied, `serve` nur wenn die Freigabe fehlt oder anders zeigt, ein laufender Dienst wird nicht neu gestartet). Ändert sich die Einheit, wird sie neu geladen, aber erst `ops/web_deploy.sh` startet neu.

**Rückbau** der Einrichtung:

```bash
systemctl --user disable --now devflow2-web.service
rm ~/.config/systemd/user/devflow2-web.service && systemctl --user daemon-reload
tailscale serve --https=8472 off
```

Linger bleibt an (andere Dienste brauchen ihn). Eine Firewall bekommt keinen Eintrag für 8478, denn der Dienst hört nur auf Loopback.

## Neues Projekt anlegen

`ops/neues-projekt.py` legt ein Projekt aus `vorlage/projekt` an (Ort der Vorlage: dieses Repo). Die Vorlage bringt `MASTERPLAN.md` (Gerüst, vom Inhaber auszufüllen), `ROADMAP.md` mit Kanban und Abschnitt „Vorhaben (grob)“, `devflow.toml` mit `basis`, `AGENTS.md` und `CLAUDE.md`, `README.md`, `plan/`, `berichte/`, `werkzeuge/roadmap.py` und das Wiki `docs/` mit Architektur, **Betrieb (Zielumgebung, Deploy, Rollback)** und `entscheidungen/`.

```bash
ops/neues-projekt.py ~/dev/mein-projekt [Name]            # grüne Wiese oder vorhandenes lokales Repo
ops/neues-projekt.py ~/dev/mein-projekt --url <URL>       # klonen und ergänzen (GitHub über `gh repo clone`, sonst `git clone`)
```

- **Neues oder leeres Verzeichnis:** Vorlage kopieren, `git init -b main` (oder `--basis`), ein Commit.
- **Vorhandenes Repo oder Klon:** Es werden nur fehlende Dateien ergänzt, nichts wird überschrieben oder committet. Vorhandene Dateien, die von der Vorlage abweichen, nennt das Skript als „Konflikt, nicht geändert“, dazu besondere Fälle: `devflow.toml` ohne passende `basis`, `CLAUDE.md` ohne `@AGENTS.md`, `.gitignore` ohne `.env`/Schlüssel, eine Roadmap, die devflow nicht lesen kann. Ein Klon in ein Verzeichnis mit anderem `origin` wird abgelehnt.
- **Wiederholen** ist gefahrlos: Der zweite Lauf legt nichts mehr an und hält bei einem Repo aus der Vorlage keine Abweichung fest (Name, Datum und Basis in den Platzhaltern zählen nicht). Anmelden (`projekt_anmelden`) ersetzt den Eintrag unter demselben Namen, es entstehen keine Doppelten.
- **`basis`** steht in `devflow.toml` und ist der Branch, auf dem das Repo steht (beim Klon der Standardbranch des Remotes), nicht fest `main`. Sie gilt in der ganzen Kette: Worktrees zweigen davon ab, Review und Nachreview vergleichen damit, der Merge ist ein `--ff-only` darauf, Roadmap-, Plan- und Berichts-Commits liegen dort. `projekt_anmelden` lehnt ein Repo ab, in dem der Basisbranch fehlt. Mit `--basis` lässt sie sich im Skript vorgeben.
- Anmelden und Masterplan ausfüllen übernimmt danach der Skill `neues-projekt` ([Planen](planen.md#neues-projekt)) oder der Inhaber von Hand.
- **Abmelden** gibt es als Werkzeug nicht: Der Eintrag steht in `projekte.json` im Zustandsverzeichnis und wird dort von Hand entfernt; das Repo bleibt. Proben mit Wegwerf-Projekten laufen deshalb nur mit `DEVFLOW2_STATE` auf einem Temp-Verzeichnis (siehe `tests2/test_skill_neues_projekt.py`).

## Prüfen, ob es läuft

- `claude mcp list` zeigt `devflow` als verbunden.
- `python -m devflow2 lage` (siehe oben) listet Projekte und Läufe.
- Dienst: `systemctl --user status devflow2-web`, Log mit `journalctl --user -u devflow2-web -n 50`; aus dem Tailnet `curl https://server.example.net:8472/health` (antwortet `{"ok": true}`; `tailscale serve` setzt die Login-Kopfzeile, außerhalb des Tailnets ist der Port nicht erreichbar).
- Einstellungen der Instanz (`~/.config/devflow2/instanz.toml`, Arbeitsprofil, Testläufer): [Instanz-Einstellungen](instanz.md); die geltenden Werte zeigt `python -m devflow2 instanz`. Ohne die Datei gilt das Verhalten von server.
- Dashboard lokal: `curl -H 'Tailscale-User-Login: inhaber@github' http://127.0.0.1:8478/health` antwortet `{"ok":true}` (ohne Kopfzeile 403).
- Traum: Seite `/traum` im Dashboard oder `traum` in `python -m devflow2 lage`; Zustand in `~/.local/state/devflow2/traeume/`, Vorschläge in `~/.local/state/devflow2/vorschlaege/`; Fehler des Takts im Log des Dienstes.
- Selbstlauf: Schalter und Zahlen auf der Lage im Dashboard oder unter `selbstlauf` in `python -m devflow2 lage`; offene Ereignisse in `~/.local/state/devflow2/ereignisse.jsonl`; Prüffehler im Log des Dienstes.
- Hängt ein Lauf: `lauf.json` und `log.jsonl` unter `~/.local/state/devflow2/laeufe/<id>/` ansehen; anhalten über das MCP-Werkzeug `anhalten`.
