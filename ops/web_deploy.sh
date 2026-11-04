#!/usr/bin/env bash
# Deploy des Dashboards: Dienst neu starten, dann /health prüfen. Schlägt fehl (Exit 1), wenn /health nicht antwortet.
# Läuft als `deploy` aus devflow.toml im Haupt-Checkout; Einrichtung einmalig mit ops/web_einrichten.sh.
set -uo pipefail

hier=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
systemctl_=${DEVFLOW2_SYSTEMCTL:-systemctl}
# Adresse, Port und Login-Art kommen aus instanz.toml (siehe docs/betrieb.md); DEVFLOW2_WEB_PORT und DEVFLOW2_WEB_LOGIN gehen vor
einstellung() { PYTHONPATH="$hier/.." "${DEVFLOW2_PYTHON:-python3}" -B -m devflow2 instanz "$1"; }
warten=${DEVFLOW2_WEB_WARTEN:-30}   # Sekunden bis zur ersten Antwort (Frist, ungefähr; Curl-Zeit zählt mit)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

fehler() { echo "FEHLER: $*" >&2; "$systemctl_" --user status --no-pager -n 15 devflow2-web.service >&2 2>&1; exit 1; }

"$systemctl_" --user restart devflow2-web.service || fehler "Neustart von devflow2-web fehlgeschlagen (Einheit eingerichtet? ops/web_einrichten.sh)"

port=$(einstellung web.port) && adresse=$(einstellung web.adresse) && art=$(einstellung web.login) && login=$(einstellung web.login_name) \
    || fehler "instanz.toml nicht lesbar (python3 -m devflow2 instanz zeigt den Grund)"
[[ $adresse == *:* ]] && adresse="[$adresse]"
kopf=()
[[ $art == tailscale ]] && kopf=(-H "Tailscale-User-Login: $login")

ende=$((SECONDS + warten))
while ((SECONDS < ende)); do
    rest=$((ende - SECONDS))
    antwort=$(curl -fsS --max-time $((rest < 3 ? rest : 3)) "${kopf[@]}" "http://$adresse:$port/health" 2>/dev/null) || antwort=
    if [[ $antwort == *'"ok"'*true* ]]; then
        echo "devflow2-web neu gestartet, /health antwortet: $antwort"
        exit 0
    fi
    sleep 1
done
fehler "/health antwortet nicht innerhalb von ${warten}s (Port $port)"
