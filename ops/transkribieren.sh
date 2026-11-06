#!/usr/bin/env bash
# Gespräche in Text verwandeln (S13.2): Audio lokal mit faster-whisper, Teams-Exporte (.vtt, .docx) mit Sprechern.
#   ops/transkribieren.sh [<datei oder ordner>] [--rollen <toml>] [--modell <name>] [--material <ordner>] [--datum JJJJ-MM-TT] [--offline] [--threads N]
# Standard: material/neu/ (relativ zum aktuellen Verzeichnis, also im Projekt aufrufen) → material/interviews/<datum>-<nr>/ mit transkript.md
# und quelle.<ext>. Audio braucht die eigene Umgebung aus `ops/installieren.sh --mit-transkription`; .vtt und .docx gehen auch ohne.
# Netz nur zum einmaligen Laden des Modells; ist es da, läuft alles offline. Siehe docs/discovery.md.
set -euo pipefail

hier=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
quelle=$(dirname "$hier")
home=${DEVFLOW2_HOME:-$HOME/.local/share/devflow2}

python=${DEVFLOW2_TRANSKRIPTION_PYTHON:-}
if [ -z "$python" ]; then
    for kandidat in "$home/transkription/.venv/bin/python" "$home/aktiv/.venv/bin/python" "$home/venv/bin/python"; do
        if [ -x "$kandidat" ]; then python=$kandidat; break; fi
    done
fi
[ -n "$python" ] || python=$(command -v python3 || true)
if [ -z "$python" ] || ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo "Kein Python 3.11 gefunden. Einrichten: ops/installieren.sh --mit-transkription (legt $home/transkription/.venv an)" >&2
    exit 1
fi
if [ "$python" != "$home/transkription/.venv/bin/python" ] && [ -z "${DEVFLOW2_TRANSKRIPTION_PYTHON:-}" ]; then
    echo "Hinweis: Die Umgebung für Audio fehlt ($home/transkription/.venv); .vtt und .docx gehen, Audio nicht. Einrichten: ops/installieren.sh --mit-transkription" >&2
fi
PYTHONPATH="$quelle${PYTHONPATH:+:$PYTHONPATH}" exec "$python" -B -m devflow2.transkript "$@"
