#!/usr/bin/env bash
# Richtet die Prüfwerkzeuge der Pflegeprüfung (S5.1) ein: pip-audit und osv-scanner.
# Wiederholbar: ändert nur, was fehlt oder abweicht. Beide Werkzeuge fragen öffentliche Datenbanken (OSV, PyPI) nur lesend ab
# und senden Paketnamen und Versionen, keinen Code. Rückbau: ~/.local/share/devflow2/pflege-venv, ~/.local/bin/pip-audit und
# ~/.local/bin/osv-scanner löschen.
#
# - pip-audit: eigene Umgebung ~/.local/share/devflow2/pflege-venv (nicht das venv des Dienstes, damit dessen Pakete unberührt
#   bleiben), Version und alle Abhängigkeiten fest mit Hashes aus ops/pflege-pip-audit.txt (`--require-hashes`)
# - osv-scanner: eine einzelne Datei nach ~/.local/bin, Version fest, SHA-256 wird vor dem Einbau geprüft
# Aufruf: ops/pflege-werkzeuge.sh [--pruefen]   (--pruefen installiert nichts und zeigt nur, was fehlt)
set -euo pipefail

hier=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bin=${DEVFLOW2_PFLEGE_BIN:-$HOME/.local/bin}
venv=${DEVFLOW2_PFLEGE_VENV:-$HOME/.local/share/devflow2/pflege-venv}
uv_=${DEVFLOW2_UV:-uv}

PIP_AUDIT_VERSION=2.10.1
OSV_VERSION=v2.7.0
OSV_SHA256=160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b   # osv-scanner_linux_amd64, aus osv-scanner_SHA256SUMS des Release
OSV_URL=${DEVFLOW2_OSV_URL:-https://github.com/google/osv-scanner/releases/download/$OSV_VERSION/osv-scanner_linux_amd64}

nur_pruefen=0
[ "${1:-}" = --pruefen ] && nur_pruefen=1

fehlt=0
pip_audit_ok() { [ -x "$venv/bin/pip-audit" ] && [ "$("$venv/bin/pip-audit" --version 2>/dev/null | awk '{print $NF}')" = "$PIP_AUDIT_VERSION" ] && [ -L "$bin/pip-audit" ]; }
osv_ok() { [ -x "$bin/osv-scanner" ] && [ "$(sha256sum "$bin/osv-scanner" | cut -d' ' -f1)" = "$OSV_SHA256" ]; }

if pip_audit_ok; then
    echo "pip-audit $PIP_AUDIT_VERSION: vorhanden"
elif [ $nur_pruefen = 1 ]; then
    echo "pip-audit $PIP_AUDIT_VERSION: fehlt"; fehlt=1
else
    command -v "$uv_" >/dev/null || { echo "uv fehlt (${uv_}); erst uv installieren" >&2; exit 1; }
    "$uv_" venv -q --python 3.11 "$venv"
    "$uv_" pip install -q --python "$venv/bin/python" --require-hashes --only-binary :all: -r "$hier/pflege-pip-audit.txt"
    mkdir -p "$bin"
    ln -sfn "$venv/bin/pip-audit" "$bin/pip-audit"
    echo "pip-audit $PIP_AUDIT_VERSION: eingerichtet"
fi

if osv_ok; then
    echo "osv-scanner $OSV_VERSION: vorhanden"
elif [ $nur_pruefen = 1 ]; then
    echo "osv-scanner $OSV_VERSION: fehlt"; fehlt=1
else
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL --max-time 300 -o "$tmp/osv-scanner" "$OSV_URL"
    if [ "$(sha256sum "$tmp/osv-scanner" | cut -d' ' -f1)" != "$OSV_SHA256" ]; then
        echo "osv-scanner: Prüfsumme stimmt nicht, nichts eingebaut" >&2
        exit 1
    fi
    mkdir -p "$bin"
    install -m 0755 "$tmp/osv-scanner" "$bin/osv-scanner"
    echo "osv-scanner $OSV_VERSION: eingerichtet"
fi

exit $fehlt
