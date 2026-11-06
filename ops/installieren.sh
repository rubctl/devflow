#!/usr/bin/env bash
# Installiert devflow 2 auf einem Arbeitsserver (Ubuntu): Python 3.11 über uv, venv, Benutzerdienst fürs Dashboard.
# Aufruf aus einem Checkout eines Releases (git clone --branch v2.N https://github.com/rubctl/devflow.git):
#   ops/installieren.sh [--version v2.N] [--ohne-dienst] [--ohne-claude] [--mit-transkription]
# Wiederholbar: prüft zuerst alle Voraussetzungen, ändert dann nur, was fehlt oder abweicht. Läuft ohne root; der eine
# root-Schritt (loginctl enable-linger) wird am Ende genannt. Anleitung: docs/installation.md.
#
# Aufbau unter DEVFLOW2_HOME (Standard ~/.local/share/devflow2): versionen/<v2.N>/ (Code und .venv), aktiv → versionen/<v2.N>.
# Neue Fassungen holt danach `python -m devflow2 aktualisieren`.
# --mit-transkription (Schritt 6b) richtet faster-whisper in einer eigenen uv-Umgebung $DEVFLOW2_HOME/transkription/.venv ein (für ops/transkribieren.sh).
# Schritt 6 richtet Claude Code ein (MCP-Server `devflow` im Scope user, Skills nach ~/.claude/skills/); beides zeigt auf `aktiv` und
# überlebt damit jedes Update. Vorhandenes, anders Eingerichtetes wird gemeldet, nie überschrieben.
set -euo pipefail

hier=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
quelle=$(dirname "$hier")
home=${DEVFLOW2_HOME:-$HOME/.local/share/devflow2}
konfig=${DEVFLOW2_CONFIG:-$HOME/.config/devflow2}
einheiten=${DEVFLOW2_UNIT_DIR:-$HOME/.config/systemd/user}
systemctl_=${DEVFLOW2_SYSTEMCTL:-systemctl}
loginctl_=${DEVFLOW2_LOGINCTL:-loginctl}
os_release=${DEVFLOW2_OS_RELEASE:-/etc/os-release}
version=""
dienst=1
claude_einrichten=1
transkription=0
while [ $# -gt 0 ]; do
    case $1 in
        --version) version=${2:?--version braucht einen Wert}; shift 2 ;;
        --ohne-dienst) dienst=0; shift ;;
        --ohne-claude) claude_einrichten=0; shift ;;
        --mit-transkription) transkription=1; shift ;;
        *) echo "Aufruf: ops/installieren.sh [--version v2.N] [--ohne-dienst] [--ohne-claude] [--mit-transkription]" >&2; exit 64 ;;
    esac
done
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
cli() { # cli <name> <umgebungsvariable>: wie devflow2/laeufe.py sie sucht
    local var=$2
    if [ -n "${!var:-}" ]; then echo "${!var}"; else command -v "$1" 2>/dev/null || echo "$HOME/.local/bin/$1"; fi
}

# --- 1. Voraussetzungen: alles prüfen, alles melden, erst dann etwas ändern -----------------------------------------
probleme=()
(. "$os_release" 2>/dev/null && [ "${ID:-}" = ubuntu ]) || probleme+=("Kein Ubuntu (${os_release}). devflow ist nur auf Ubuntu erprobt.")
command -v uv >/dev/null || probleme+=("uv fehlt (Python 3.11 kommt darüber): https://docs.astral.sh/uv/getting-started/installation/")
command -v git >/dev/null || probleme+=("git fehlt: sudo apt install git")
if command -v node >/dev/null; then
    [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 18 ] || probleme+=("Node ist zu alt (mindestens 18, claude und codex brauchen es)")
else
    probleme+=("Node fehlt (claude und codex brauchen es)")
fi
claude=$(cli claude DEVFLOW2_CLAUDE)
codex=$(cli codex DEVFLOW2_CODEX)
if [ ! -x "$claude" ]; then
    probleme+=("claude fehlt (Claude Code installieren, dann 'claude' starten und anmelden)")
elif ! "$claude" auth status >/dev/null 2>&1; then
    probleme+=("claude ist nicht angemeldet: 'claude auth login' (oder 'claude' starten und /login)")
fi
if [ ! -x "$codex" ]; then
    probleme+=("codex fehlt (Codex CLI installieren, dann 'codex login')")
elif ! "$codex" login status >/dev/null 2>&1; then
    probleme+=("codex ist nicht angemeldet: 'codex login'")
fi
if [ ! -f "$quelle/requirements.txt" ] || [ ! -d "$quelle/devflow2" ]; then
    probleme+=("$quelle ist kein devflow-Release (requirements.txt oder devflow2/ fehlt)")
fi

if [ -z "$version" ]; then
    version=$(git -C "$quelle" describe --tags --exact-match 2>/dev/null || true)
fi
[[ $version =~ ^v2\.[0-9]+$ ]] || probleme+=("Fassung unbekannt: der Checkout steht auf keinem Tag v2.N. Mit --version v2.N angeben oder 'git clone --branch v2.N' nutzen.")

# Dashboard-Port: aus den Einstellungen des Checkouts (ohne instanz.toml gilt das Arbeitsprofil, das gleich angelegt würde).
# Läuft der Dienst schon (zweiter Aufruf), ist der Port natürlich belegt.
if [ "$dienst" = 1 ] && command -v uv >/dev/null && [ -d "$quelle/devflow2" ] && ! "$systemctl_" --user is-active --quiet "${DEVFLOW2_EINHEIT:-devflow2-web.service}"; then
    pruef=$konfig
    if [ ! -e "$konfig/instanz.toml" ]; then
        pruef=$(mktemp -d)
        printf 'profil = "arbeit"\n' > "$pruef/instanz.toml"
    fi
    vorab() { PYTHONPATH="$quelle" DEVFLOW2_CONFIG="$pruef" uv run --no-project --python "$(cat "$quelle/.python-version" 2>/dev/null || echo 3.11)" \
                  python -B -m devflow2 instanz "$1" 2>/dev/null; }
    port0=$(vorab web.port || true)
    if [ -n "$port0" ] && ss -ltn "sport = :$port0" 2>/dev/null | grep -q LISTEN; then
        probleme+=("Port $port0 ist belegt. Anderen Port in $konfig/instanz.toml ([web] port = …) eintragen und erneut aufrufen.")
    fi
    [ "$pruef" = "$konfig" ] || rm -rf "$pruef"
fi

if [ ${#probleme[@]} -gt 0 ]; then
    echo "Voraussetzungen nicht erfüllt, nichts wurde geändert:" >&2
    printf '  - %s\n' "${probleme[@]}" >&2
    exit 1
fi
echo "Voraussetzungen ok (Ubuntu, uv, git, Node, claude und codex angemeldet), Fassung $version"

# --- 2. Verzeichnis und venv -----------------------------------------------------------------------------------------
ziel="$home/versionen/$version"
mkdir -p "$home/versionen"
if [ -f "$ziel/.fertig" ]; then
    echo "$version liegt schon fertig in $ziel"
else
    if [ "$(realpath "$quelle")" != "$(realpath -m "$ziel")" ]; then  # nicht, wenn der Aufruf schon aus dem Zielverzeichnis kommt
        rm -rf "$ziel"
        mkdir -p "$ziel"
        trap 'rm -rf "$ziel"' ERR  # eine halbe Fassung bleibt nie liegen
        tar --exclude=.git --exclude=.venv --exclude=__pycache__ -C "$quelle" -cf - . | tar -C "$ziel" -xf -
    fi
    py=$(cat "$ziel/.python-version" 2>/dev/null || echo 3.11)
    uv venv --clear --python "$py" "$ziel/.venv"
    uv pip install --python "$ziel/.venv/bin/python" -r "$ziel/requirements.txt"
    echo "$version" > "$ziel/.fertig"
    trap - ERR
    echo "$version installiert in $ziel"
fi
python="$ziel/.venv/bin/python"

# --- 3. Einstellungen: Arbeitsprofil, falls es noch keine instanz.toml gibt --------------------------------------------
mkdir -p "$konfig"
if [ ! -e "$konfig/instanz.toml" ]; then
    printf 'profil = "arbeit"\n' > "$konfig/instanz.toml"
    echo "$konfig/instanz.toml angelegt (profil = \"arbeit\": nur 127.0.0.1, ohne Tailscale)"
fi
einstellung() { PYTHONPATH="$ziel" DEVFLOW2_CONFIG="$konfig" "$python" -B -m devflow2 instanz "$1"; }
port=$(einstellung web.port)
adresse=$(einstellung web.adresse)
einheit=${DEVFLOW2_EINHEIT:-devflow2-web.service}

# --- 4. aktive Fassung ---------------------------------------------------------------------------------------------------
aktiv="$home/aktiv"
if [ -L "$aktiv" ] && [ "$(readlink -f "$aktiv")" != "$(readlink -f "$ziel")" ]; then
    echo "Aktiv ist schon $(basename "$(readlink -f "$aktiv")"); $version bleibt unbenutzt liegen. Wechsel nur über: python -m devflow2 aktualisieren"
elif [ ! -L "$aktiv" ]; then
    ln -sfn "$ziel" "$aktiv"
    echo "aktiv → $version"
fi

# --- 5. Dienst -------------------------------------------------------------------------------------------------------------
if [ "$dienst" = 1 ]; then
    if ! "$systemctl_" --user is-active --quiet "$einheit"; then  # läuft er schon (zweiter Aufruf), ist der Port natürlich belegt
        if ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then
            echo "Port $port auf $adresse ist belegt. Anderen Port in $konfig/instanz.toml ([web] port = …) eintragen und erneut aufrufen." >&2
            exit 1
        fi
    fi
    mkdir -p "$einheiten"
    neu=$(mktemp)
    cat > "$neu" <<EOF
[Unit]
Description=devflow 2 Dashboard (nur $adresse:$port)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$home/aktiv
Environment=PYTHONPATH=$home/aktiv
Environment=HOME=$HOME
Environment=DEVFLOW2_HOME=$home
Environment=DEVFLOW2_EINHEIT=$einheit
Environment=DEVFLOW2_CONFIG=$konfig
ExecStart=$home/aktiv/.venv/bin/python -B -m devflow2 web
Restart=always
RestartSec=5
# Läufe, die der Dienst startet (setsid), überleben Neustart und Stopp
KillMode=process
MemoryHigh=512M
MemoryMax=1G

[Install]
WantedBy=default.target
EOF
    if ! cmp -s "$neu" "$einheiten/$einheit"; then
        install -m 0644 "$neu" "$einheiten/$einheit"
        "$systemctl_" --user daemon-reload
        echo "Einheit $einheit installiert"
    fi
    rm -f "$neu"
    "$systemctl_" --user enable "$einheit"
    if "$systemctl_" --user is-active --quiet "$einheit"; then
        echo "$einheit läuft schon; Neustart nur über 'python -m devflow2 aktualisieren' oder systemctl --user restart"
    else
        "$systemctl_" --user start "$einheit"
        echo "$einheit gestartet"
    fi
fi

# --- 6. Claude Code: MCP-Server und Skills (nur mit aktiver Fassung; Fremdes bleibt unberührt) ----------------------------
if [ "$claude_einrichten" = 1 ]; then
    echo
    if [ ! -d "$aktiv" ] || [ ! -x "$aktiv/.venv/bin/python" ]; then
        echo "Claude: kein 'aktiv' mit venv unter $home, MCP-Server und Skills bleiben unberührt"
    else
        mcp_befehl="$aktiv/.venv/bin/python"
        if vorhanden=$("$claude" mcp get devflow 2>/dev/null); then
            if grep -qF -- "$mcp_befehl" <<<"$vorhanden" && grep -qF -- "PYTHONPATH=$aktiv" <<<"$vorhanden"; then
                echo "Claude: MCP-Server 'devflow' zeigt schon auf $aktiv"
            else
                echo "Claude: MCP-Server 'devflow' ist anders eingerichtet und bleibt unverändert (Ansehen: claude mcp get devflow;"
                echo "        auf diese Instanz umstellen: claude mcp remove devflow -s user, dann diesen Aufruf wiederholen)"
            fi
        else
            umgebung=(-e "PYTHONPATH=$aktiv")
            [ -z "${DEVFLOW2_HOME:-}" ] || umgebung+=(-e "DEVFLOW2_HOME=$DEVFLOW2_HOME")
            [ -z "${DEVFLOW2_CONFIG:-}" ] || umgebung+=(-e "DEVFLOW2_CONFIG=$DEVFLOW2_CONFIG")
            if "$claude" mcp add devflow -s user "${umgebung[@]}" -- "$mcp_befehl" -B -m devflow2 mcp >/dev/null 2>&1; then
                echo "Claude: MCP-Server 'devflow' (Scope user) auf $aktiv eingerichtet; laufende Claude-Sitzungen mit /mcp neu verbinden"
            else
                echo "Claude: MCP-Server 'devflow' ließ sich nicht einrichten. Von Hand: claude mcp add devflow -s user -e PYTHONPATH=$aktiv -- $mcp_befehl -B -m devflow2 mcp" >&2
            fi
        fi
        skills="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
        for skill in planungssitzung neues-projekt discovery; do
            quelle_skill="$aktiv/skills/$skill"
            [ -f "$quelle_skill/SKILL.md" ] || { echo "Claude: Skill $skill fehlt in $aktiv/skills, übersprungen" >&2; continue; }
            ziel_skill="$skills/$skill"
            if [ -L "$ziel_skill" ] && [ "$(readlink "$ziel_skill")" = "$quelle_skill" ]; then
                echo "Claude: Skill $skill ist schon verlinkt"
            elif [ -e "$ziel_skill" ] || [ -L "$ziel_skill" ]; then
                echo "Claude: Skill $skill existiert schon in $skills (anderer Ursprung) und bleibt unverändert"
            else
                mkdir -p "$skills"
                ln -s "$quelle_skill" "$ziel_skill"
                echo "Claude: Skill $skill verlinkt ($ziel_skill → $quelle_skill)"
            fi
        done
    fi
fi

# --- 6b. Transkription: faster-whisper in eigener Umgebung (nur mit --mit-transkription) ---------------------------------------
tpython="$home/transkription/.venv/bin/python"
transkription_fehler=""
if [ "$transkription" = 1 ]; then
    echo
    if "$tpython" -c 'import faster_whisper' >/dev/null 2>&1; then
        echo "Transkription: faster-whisper liegt schon in $home/transkription/.venv"
    elif uv venv --clear --python "$(cat "$ziel/.python-version" 2>/dev/null || echo 3.11)" "$home/transkription/.venv" \
            && uv pip install --python "$tpython" faster-whisper; then
        echo "Transkription: faster-whisper eingerichtet in $home/transkription/.venv (das Modell wird beim ersten Audio einmalig geladen, siehe docs/discovery.md)"
    else
        rm -rf "$home/transkription/.venv"
        transkription_fehler="faster-whisper ließ sich nicht installieren (Netz? Platz?). Alles andere ist fertig; zum Wiederholen: ops/installieren.sh --mit-transkription"
        echo "Transkription: $transkription_fehler" >&2
    fi
elif ! "$tpython" -c 'import faster_whisper' >/dev/null 2>&1; then
    echo
    echo "Transkription von Audio ist nicht eingerichtet (faster-whisper fehlt). Bei Bedarf: ops/installieren.sh --mit-transkription"
fi

# --- 7. Was noch von Hand zu tun ist --------------------------------------------------------------------------------------
echo
echo "Fertig. Noch zu tun:"
if [ "$("$loginctl_" show-user "$(id -un)" -p Linger --value 2>/dev/null || echo no)" != yes ]; then
    echo "  1. EINMAL als Administrator, damit der Dienst nach einem Neustart ohne Anmeldung läuft:"
    echo "       sudo loginctl enable-linger $(id -un)"
else
    echo "  1. Linger ist schon eingeschaltet (kein root-Schritt nötig)."
fi
echo "  2. Dashboard vom eigenen Rechner aus: Tunnel öffnen und im Browser http://localhost:$port aufrufen:"
echo "       ssh -N -L $port:$adresse:$port $(id -un)@<dieser-server>"
echo "  3. Projekte anmelden und Pläne abnehmen wie in docs/installation.md beschrieben."
[ -z "$transkription_fehler" ] || exit 1
