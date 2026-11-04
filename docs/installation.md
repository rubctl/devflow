# Installation und Update

So kommt devflow auf einen Arbeitsserver (Mini-PC, Ubuntu). Der Code kommt ausschließlich aus dem öffentlichen Release-Repo `rubctl/devflow` (nur Code, Tags `v2.N`, siehe Release), lesend und ohne Schlüssel. Pläne, Berichte und Verlauf des Entwicklungsrepos gelangen nie dorthin. Das Dashboard lauscht nur auf `127.0.0.1`; Zugriff gibt es per SSH-Tunnel. Kein Container, kein Zertifikat, keine Anmeldung am Dashboard (Profil `arbeit`, siehe [Instanz-Einstellungen](instanz.md)).

## Voraussetzungen

- Ubuntu, ein normaler Benutzer mit eigenem `sudo` (nur für einen Schritt, unten).
- `git`, [`uv`](https://docs.astral.sh/uv/) (liefert Python 3.11, nichts anderes nötig), Node 18 oder neuer.
- `claude` (Claude Code) und `codex` installiert **und angemeldet** (die Abos der Arbeit):

  ```bash
  claude auth login      # oder 'claude' starten und /login
  codex login            # ohne Browser auf dem Server: Anmeldung am eigenen PC, Zugangsdatei kopieren (Hilfe: codex login --help)
  ```
- Ein freier Port für das Dashboard (Standard 8478).

Das Installationsskript prüft das alles zuerst und nennt **alle** Mängel auf einmal; solange etwas fehlt, ändert es nichts.

## Installieren

```bash
git clone --branch v2.N https://github.com/rubctl/devflow.git ~/devflow-start   # v2.N = neuestes Tag
~/devflow-start/ops/installieren.sh
```

Das Skript (`ops/installieren.sh [--version v2.N] [--ohne-dienst] [--ohne-claude]`):

1. prüft Ubuntu, `uv`, `git`, Node, Anmeldung von `claude` und `codex` und dass der Dashboard-Port frei ist (alles vor der ersten Änderung, alle Mängel auf einmal);
2. kopiert den Code nach `~/.local/share/devflow2/versionen/v2.N/` und legt dort das venv an (`uv venv --python 3.11`, Pakete aus `requirements.txt`);
3. schreibt `~/.config/devflow2/instanz.toml` mit `profil = "arbeit"`, falls es noch keine Datei gibt (bestehende bleibt);
4. legt `~/.local/share/devflow2/aktiv` auf diese Fassung;
5. richtet den Benutzerdienst `devflow2-web.service` ein (läuft aus `aktiv`, nicht aus einem Checkout) und startet ihn;
6. richtet Claude Code ein (nächster Abschnitt), außer mit `--ohne-claude`.

Es ist wiederholbar: Ein zweiter Aufruf baut nichts neu, lässt Einstellungen und laufenden Dienst unberührt und meldet, was schon da ist. `~/devflow-start` darf danach gelöscht werden. Verzeichnisse lassen sich über `DEVFLOW2_HOME` (Code), `DEVFLOW2_CONFIG` (Einstellungen) verlegen, der Name des Dienstes über `DEVFLOW2_EINHEIT` (nur für Proben neben einer anderen Instanz; der Dienst bekommt dabei `HOME` mitgegeben, damit er nie auf den Zustand eines anderen Benutzerverzeichnisses zugreift; `aktualisieren` liest dieselbe Variable).

### MCP-Server und Skills für Claude (Schritt 6)

Damit eine Claude-Sitzung (auch Claude Remote) die Instanz steuern kann, braucht Claude Code den MCP-Server `devflow` und die Skills `planungssitzung` und `neues-projekt`. Der Installer richtet beides ein, aber nur, wenn `~/.local/share/devflow2/aktiv` mit venv existiert, und zeigt immer auf `aktiv`, nie auf `versionen/v2.N`. Deshalb gilt es nach `aktualisieren` weiter, weil dort nur `aktiv` umgehängt wird.

- **MCP-Server:** `claude mcp add devflow -s user -e PYTHONPATH=<home>/aktiv -- <home>/aktiv/.venv/bin/python -B -m devflow2 mcp`. Bei gesetztem `DEVFLOW2_HOME` bzw. `DEVFLOW2_CONFIG` wandern beide mit. Der Installer fragt zuerst `claude mcp get devflow`: zeigt der Eintrag schon auf `aktiv`, passiert nichts; ist er **anders** eingerichtet (etwa mit `PYTHONPATH` auf einen Checkout, so auf dem Entwicklungsrechner server), bleibt er unverändert und der Installer meldet es mitsamt dem Weg, ihn umzustellen (`claude mcp remove devflow -s user`, Installer erneut aufrufen).
- **Skills:** Links `~/.claude/skills/<name>` → `<home>/aktiv/skills/<name>` (bei gesetztem `CLAUDE_CONFIG_DIR` dort). Ein vorhandener gleichnamiger Ordner oder Link mit anderem Ziel wird nie ersetzt, nur gemeldet.
- Scheitert das Einrichten, bleibt die Installation gelungen; die Meldung nennt den Befehl für von Hand. Mit `--ohne-claude` bleibt Claude Code unberührt.
- **Nach einem Update** (und nach der Erstinstallation) laufende Claude-Sitzungen neu verbinden (`/mcp` bzw. Sitzung neu starten): Ein laufender MCP-Server behält den alten Code, und neue Skills sieht Claude erst in der nächsten Sitzung.

### Der eine root-Schritt

Damit der Dienst nach einem Neustart ohne angemeldeten Benutzer läuft, einmal als Administrator:

```bash
sudo loginctl enable-linger <benutzer>
```

Das Skript sagt am Ende, ob das noch fehlt. Mehr braucht devflow nicht als root.

## Dashboard öffnen: SSH-Tunnel

Vom Büro-PC aus, in einem eigenen Terminal, das offen bleibt:

```bash
ssh -N -L 8478:127.0.0.1:8478 <benutzer>@<server>
```

Dann im Browser `http://localhost:8478`. Zugangskontrolle ist der SSH-Schlüssel; der Port ist nur auf dem eigenen Rechner offen (Büro-PC sperren, wenn man weg ist). Anderer Port auf dem Server: `[web] port` in `instanz.toml`, im Tunnel beide Zahlen anpassen. Unterwegs steuert man über Claude Remote (Sitzung auf dem Mini-PC); Abnahmen bleiben am Schreibtisch.

## Projekte und erster Lauf

Nach der Installation meldet man Projekte an wie sonst auch (`python -m devflow2 anmelden <pfad>`, aus `~/.local/share/devflow2/aktiv`, oder über die MCP-Werkzeuge der Claude-Sitzung). Die Projektwurzel (`projektwurzel` in `instanz.toml`) bestimmt, wo relative Pfade gelten. Erst wenn `claude` und `codex` angemeldet sind, laufen Läufe durch; ein abgelaufener Login erscheint im Dashboard unter „Braucht dich“.

## Aktualisieren

```bash
cd ~/.local/share/devflow2/aktiv
.venv/bin/python -B -m devflow2 aktualisieren                    # neuestes Release
.venv/bin/python -B -m devflow2 aktualisieren --version v2.N     # eine bestimmte Fassung
.venv/bin/python -B -m devflow2 aktualisieren --zurueck          # zur vorherigen Fassung
.venv/bin/python -B -m devflow2 aktualisieren --quelle <url-oder-pfad> --warten <sekunden>
```

Ablauf, jeder Schritt bricht bei einem Fehler ab und lässt die alte Fassung aktiv:

1. Neuestes Tag `v2.N` in der Quelle lesen (Standard `rubctl/devflow` auf GitHub, überschreibbar mit `--quelle` oder `DEVFLOW2_RELEASE_QUELLE`). Ist es nicht neuer als die aktive Fassung: „Schon aktuell“.
2. **Sperre** setzen: Ab hier bleiben neue Läufe auf `wartet`, bis das Update fertig oder gescheitert ist. In ein **neues Verzeichnis** `versionen/v2.N/` holen, venv bauen, **Format** des Zustands prüfen (unten), alle **Tests** der neuen Fassung laufen lassen (über `ops/testlauf.sh`, mit eigenem `HOME` und Zustand, nichts vom echten System). Rote Tests: Fassung wird entfernt, alte bleibt aktiv.
3. Warten (die Sperre gilt weiter; wartende Läufe beginnen erst nach dem Umschalten und immer mit dem Code von `aktiv`), bis kein Lauf mehr `läuft`, `kontingent` oder `abschluss` ist (Höchstdauer `--warten`, Standard 6 Stunden, danach Abbruch ohne Änderung).
4. Format noch einmal prüfen, `aktiv` auf die neue Fassung legen (`vorherige` zeigt auf die bisherige), Zustand vorwärts umstellen, Dienst neu starten (laufende Läufe überleben), Sperre aufheben. Scheitert die Umstellung oder der Neustart des Dienstes, ist `aktiv` wieder die alte Fassung. Nur wenn der Zustand schon umgestellt ist und die alte Fassung ihn nicht mehr kennt, bleibt die neue aktiv und die Meldung sagt es.

Ältere Fassungen außer `aktiv` und `vorherige` werden entfernt. Laufende Claude-Sitzungen mit dem MCP-Server von devflow behalten den alten Code, bis die Sitzung neu verbunden (`/mcp`) oder neu gestartet wird; die Registrierung selbst zeigt auf `aktiv` und braucht nach dem Update nichts.

## Formatversion der Zustandsdateien

Der Zustand (`~/.local/state/devflow2`) trägt in `format.json` seine Formatnummer; jede Fassung kennt ihre eigene (`devflow2/format.py`). Ein Zustand ohne Marke gilt als Format 1.

- Eine neue Fassung stellt den Zustand nur **vorwärts** um und sichert ihn vorher nach `sicherung/format-<alt>-<zeit>/` (ohne die Läufe).
- Eine Fassung, die ein **älteres** Format kennt als der Zustand, startet nicht (Dienst, MCP-Server, Läufe, Lage), und `aktualisieren --zurueck` bzw. `--version` auf sie wird **abgelehnt**. Wer trotzdem zurück muss, spielt die Sicherung zurück; Änderungen seit der Umstellung gehen dabei verloren.
- Wer das Format ändert, erhöht `FORMAT` und trägt die Umstellung unter `MIGRATIONEN` ein, mit Test.

## Tests

`tests2/test_aktualisieren.py`: Release-Quelle ist ein lokales Repo, `uv`, `systemctl`, `claude` und `codex` sind Attrappen, nichts geht zu GitHub. Geprüft werden rote Tests (alte Fassung bleibt, nichts Halbes liegt herum), Warten auf aktive Läufe, dass in der Zeit kein neuer Lauf beginnt, Rückweg und Format, Zeitablauf beim Warten, Wiederholbarkeit des Installationsskripts und das Melden aller fehlenden Voraussetzungen. Für Schritt 6 ist `claude` eine Attrappe mit eigenem Speicher für `mcp get`/`mcp add` (nie das echte `claude mcp`): Einrichtung und zweiter Aufruf, Verzeichnisse aus der Umgebung, fremde Registrierung und fremde Skills bleiben unberührt, `--ohne-claude`, `aktiv` ohne venv, Scheitern von `mcp add`.

## Probe auf einem Rechner mit laufender Instanz

`ops/probe.sh` (nur im Entwicklungsrepo) spielt die ganze Strecke in einer Wegwerf-Umgebung durch: Release in ein lokales Bare-Repo (nichts nach GitHub), Installation unter eigenem `HOME`, eigener Einheit (`devflow2-probe-<zufall>.service`) und eigenem Port auf `127.0.0.1`, Mini-Projekt mit den Attrappen aus `tests2/fake_cli.py` bis zum Merge, SSH-Tunnel, `aktualisieren` von `v2.N` auf `v2.N+1`. Davor und danach hält es den Stand der echten Instanz fest (Dienst, Einheitsdatei, Port 8478, die drei Verzeichnisse) und vergleicht; am Ende entfernt es alles. Exit 0 nur, wenn alles gelang und die echte Instanz unverändert ist. Ergebnis der ersten Probe: `berichte/S8.5.md`.
