#!/usr/bin/env bash
# Richtet das devflow2-Dashboard ein: Benutzerdienst, Linger und, bei login = "tailscale" in instanz.toml, tailscale serve.
# Wiederholbar: ändert nur, was fehlt oder abweicht. Rückbau steht in docs/betrieb.md.
set -euo pipefail

hier=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
systemctl_=${DEVFLOW2_SYSTEMCTL:-systemctl}
loginctl_=${DEVFLOW2_LOGINCTL:-loginctl}
tailscale_=${DEVFLOW2_TAILSCALE:-tailscale}
einheiten=${DEVFLOW2_UNIT_DIR:-$HOME/.config/systemd/user}
einstellung() { PYTHONPATH="$hier/.." "${DEVFLOW2_PYTHON:-python3}" -B -m devflow2 instanz "$1"; }
lokal=$(einstellung web.port)          # Port des Dienstes, nur Loopback
art=$(einstellung web.login)           # tailscale oder keine
extern=$(einstellung web.tailscale_port)  # Port im Tailnet (HTTPS), nur bei tailscale
adresse=$(einstellung web.adresse)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

# 1. Linger: der Benutzerdienst startet mit dem Rechner, ohne dass jemand angemeldet ist
if [ "$("$loginctl_" show-user "$(id -un)" -p Linger --value)" != yes ]; then
    "$loginctl_" enable-linger "$(id -un)"
    echo "Linger eingeschaltet"
fi

# 2. Einheit installieren (nur bei Änderung neu laden), aktivieren und starten
mkdir -p "$einheiten"
if ! cmp -s "$hier/devflow2-web.service" "$einheiten/devflow2-web.service"; then
    install -m 0644 "$hier/devflow2-web.service" "$einheiten/devflow2-web.service"
    "$systemctl_" --user daemon-reload
    echo "Einheit devflow2-web.service installiert"
fi
"$systemctl_" --user enable devflow2-web.service
if "$systemctl_" --user is-active --quiet devflow2-web.service; then
    echo "devflow2-web läuft schon; Neustart nur über ops/web_deploy.sh"
else
    "$systemctl_" --user start devflow2-web.service
fi

# 3. tailscale serve: nur Port $extern, andere Freigaben bleiben unberührt (nicht bei login = "keine")
if [ "$art" != tailscale ]; then
    echo "login = $art: kein tailscale serve; das Dashboard ist nur auf $adresse:$lokal erreichbar (SSH-Tunnel, docs/betrieb.md)"
    exit 0
fi
proxy_adresse=$adresse
[[ $adresse == *:* ]] && proxy_adresse="[$adresse]"
ziel="http://$proxy_adresse:$lokal"
# (in der Ausgabe steht die Adresse in einer Zeile, der Proxy in der nächsten)
if "$tailscale_" serve status 2>/dev/null | grep -A1 -E ":$extern( |\$)" | grep -qF "proxy $ziel"; then
    echo "tailscale serve auf $extern zeigt schon auf $ziel"
else
    "$tailscale_" serve --bg --https="$extern" "$ziel"
fi
"$tailscale_" serve status
