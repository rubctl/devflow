# devflow

Werkzeug für Code, gesteuert von einer KI per MCP: Eine KI (Standard Claude) setzt abgenommene Pläne um, Modelle entwickeln und
reviewen als Hintergrundläufe, ein Mensch überwacht und entscheidet. Der Stand liegt als Markdown im jeweiligen Projekt-Repo
(`ROADMAP.md`, `plan/`, `berichte/`, `docs/`); devflow hält lokal nur den Laufzustand. Mehr in [`DEVFLOW2.md`](DEVFLOW2.md) und im
Wiki unter [`docs/`](docs/README.md).

Dieses Repo ist ein **Release** (Version v2.2): ein Commit mit Tag je Version, nur Code, ohne Entwicklungsverlauf, Pläne und Berichte.

## Voraussetzungen

- Linux, `git`, [`uv`](https://docs.astral.sh/uv/), Python 3.11 (`.python-version`; `uv` holt ihn bei Bedarf)
- für echte Läufe die CLIs `claude` und `codex`, angemeldet; für die Tests nicht nötig (Attrappen)

## Einrichten und prüfen

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=.:tests2 .venv/bin/python -B -m unittest discover -s tests2
```

Die Paketliste `requirements.txt` ist fest (alle Pakete mit Version); `pyproject.toml` nennt nur die direkten Abhängigkeiten.

## Starten

```bash
.venv/bin/python -B -m devflow2 instanz     # geltende Einstellungen (siehe docs/instanz.md)
.venv/bin/python -B -m devflow2 web          # Dashboard auf 127.0.0.1:8478
.venv/bin/python -B -m devflow2 mcp          # MCP-Server für die steuernde KI
```

Das Dashboard bindet nur an `127.0.0.1`; von außen geht es per SSH-Tunnel. Einstellungen je Installation stehen in `instanz.toml`
([`docs/instanz.md`](docs/instanz.md)).

## Lizenz

MIT, siehe [`LICENSE`](LICENSE). Die Schriften unter `devflow2/static/cd/fonts/` stehen unter der SIL Open Font License.
