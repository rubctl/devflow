#!/usr/bin/env bash
# Testläufe nacheinander statt parallel (der Server wird sonst unter Last laut).
#
#   ops/testlauf.sh <befehl …>   # wartet, bis kein anderer Testlauf läuft, dann mit nice/ionice
#   ops/testlauf.sh --status     # zeigt den laufenden Testlauf (Exit 0) oder „frei“ (Exit 1)
#
# Beispiel devflow:
#   ops/testlauf.sh env PYTHONPATH=.:tests2 python -B -m unittest discover -s tests2
#
# Eine Sperre für alle Sessions desselben Benutzers (Claude, Sol, Hand); Datei TESTLAUF_LOCK. Wartezeit höchstens
# TESTLAUF_WARTEN Sekunden (Standard 1800), danach Exit 75. Der Befehl selbst erbt die
# Sperre nicht, damit zurückgelassene Hintergrundprozesse sie nicht festhalten.
set -euo pipefail

runtime=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
[[ -d $runtime && -w $runtime ]] || runtime=/tmp
LOCK=${TESTLAUF_LOCK:-$runtime/testlauf.lock}
WARTEN=${TESTLAUF_WARTEN:-1800}

exec 9<>"$LOCK"

if [[ ${1:-} == --status ]]; then
    if flock -n 9; then
        echo "frei"
        exit 1
    fi
    cat "$LOCK"
    exit 0
fi

if [[ $# -eq 0 ]]; then
    echo "Aufruf: ops/testlauf.sh <befehl …> | --status" >&2
    exit 64
fi

if ! flock -n 9; then
    echo "testlauf: warte auf laufenden Testlauf (höchstens ${WARTEN}s):" >&2
    sed 's/^/  /' "$LOCK" >&2 || true
    if ! flock -w "$WARTEN" 9; then
        echo "testlauf: nach ${WARTEN}s immer noch belegt, abgebrochen" >&2
        exit 75
    fi
fi

printf 'pid %s seit %s\nin  %s\ncmd %s\n' "$$" "$(date '+%F %T')" "$PWD" "$*" >"$LOCK"

set +e
nice -n 10 ionice -c2 -n7 "$@" 9>&-
rc=$?
set -e
: >"$LOCK"
exit "$rc"
