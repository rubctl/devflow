# Instanz-Einstellungen

Alles, was je Installation anders ist, steht in `instanz.toml` (S8.1, Plan: `plan/s8-arbeit.md`). Der Code enthält keine festen Namen, Adressen oder Pfade einer bestimmten Maschine mehr; die Standardwerte der server-Instanz stehen allein in `devflow2/instanz.py`.

## Datei

`~/.config/devflow2/instanz.toml` (Verzeichnis `DEVFLOW2_CONFIG`, Datei `DEVFLOW2_INSTANZ`). Fehlt sie, gilt das Profil `server`, und alles verhält sich wie vorher. Eine mit `DEVFLOW2_INSTANZ` genannte, aber fehlende Datei ist ein Fehler. Unbekannte Schlüssel, falsche Typen und unzulässige Werte lehnt devflow beim Laden ab (kein stilles Ignorieren, denn ein Tippfehler bei `login` wäre eine Sicherheitslücke).

Reihenfolge, spätere gewinnen: Standard, Profil, Datei, Umgebungsvariable.

| Schlüssel | Bedeutung | Profil `server` | Profil `arbeit` |
|---|---|---|---|
| `profil` | Satz von Standardwerten | `server` | `arbeit` |
| `host` | Name der Maschine in den Aufträgen an die Modelle („Größere Läufe auf …“, „alles außerhalb von …“) | `server` | `arbeit` |
| `inhaber` | wer plant, entscheidet und abnimmt, in den Aufträgen an die Modelle, in Fehlermeldungen und in Markern wie `abgenommen von …`: ein Vorname oder die Rolle `Inhaber` | siehe `devflow2/instanz.py` | `Inhaber` |
| `projektwurzel` | relative Pfade bei `projekt_anmelden` und `devflow2 anmelden` gelten hier; leer = aktuelles Verzeichnis | leer | leer |
| `testlaeufer` | Befehl, der vor größeren Testläufen steht (Sperre, `nice`); `""` schaltet ihn ab; Umgebung `DEVFLOW2_TESTLAUF` | `~/dev/werkzeuge/ops/testlauf.sh` | `ops/testlauf.sh` im devflow-Checkout |
| `web.adresse` | Bindung des Dashboards; nur Loopback (`127.0.0.1`, `::1`, `localhost`) | `127.0.0.1` | `127.0.0.1` |
| `web.port` | Port des Dashboards; Umgebung `DEVFLOW2_WEB_PORT` | `8478` | `8478` |
| `web.login` | `tailscale` (Kopfzeile `Tailscale-User-Login`) oder `keine` | `tailscale` | `keine` |
| `web.login_name` | erwarteter Tailscale-Login; Umgebung `DEVFLOW2_WEB_LOGIN` | siehe `devflow2/instanz.py` | – |
| `web.tailscale_port` | Port im Tailnet, nur für `ops/web_einrichten.sh` | `8472` | `0` |
| `web.origin` | zusätzlich erlaubte `Origin`s für POST (Liste); Umgebung `DEVFLOW2_WEB_ORIGIN` kommagetrennt | leer | leer |
| `push.schluessel` | Verzeichnis mit `vapid.key`; Umgebung `DEVFLOW2_CONFIG` | `~/.config/devflow2` | `~/.config/devflow2` |
| `push.kontakt` | VAPID-Kontakt (`https://` oder `mailto:`); eine `https://`-Origin aus `web.origin` geht vor | siehe `devflow2/instanz.py` | `https://localhost` |

**Inhaber:** Der Wert ist ein Vorname oder die Rolle (`Inhaber`, auch `der Inhaber`). Aus ihm bildet `devflow2/inhaber.py` die Formen der Texte (`FRAGE AN ANNA:` bzw. `FRAGE AN DEN INHABER:`, `abgenommen von Anna` bzw. `abgenommen vom Inhaber`, `Antwort Anna` bzw. `Antwort Inhaber`); die Rolle wird gebeugt, ein Name bleibt gleich. Wo devflow solche Marker in Plänen, Entscheidungen und Roadmaps liest, gilt neben der eigenen Form immer auch die Rollenform, weil Docs, Skills und Vorlagen die Rolle nennen. Geschrieben wird in der eigenen Form. Nur im Standard der Instanz, die der Inhaber selbst betreibt, steht ein Vorname; der Export kennt nur die Rolle (siehe Release).

Die geltenden Werte zeigt `python -m devflow2 instanz` (alles als JSON) bzw. `python -m devflow2 instanz web.port` (ein Wert, auch für Skripte).

## Arbeitsprofil

Beispiel für die Arbeitsinstanz (`instanz.toml`):

```toml
profil = "arbeit"
```

- Der Dienst bindet nur an `127.0.0.1`. Andere Adressen, auch `0.0.0.0` und `::`, lehnt devflow ab; eine Bindung an alle Adressen gibt es nicht, auch nicht aus der Standardeinstellung. Von außen geht es per SSH-Tunnel (`ssh -L 8478:127.0.0.1:8478 …`).
- Ohne Anmeldung erreichbar: kein `Tailscale-User-Login` nötig und keiner geprüft. Dafür muss der `Host` der Anfrage `127.0.0.1`, `localhost` oder `[::1]` sein (Port beliebig, wegen des Tunnels); jeder andere Host ergibt 403 (Schutz vor DNS-Rebinding aus einer fremden Webseite im Browser).
- Schreibende Anfragen (POST) brauchen weiter eine `Origin`, die zum `Host` passt; eine fremde `Origin` ergibt 403. `X-Forwarded-Host` zählt hier nicht, denn es gibt keinen Proxy.
- `ops/web_einrichten.sh` richtet bei `login = "keine"` nur Dienst und Linger ein, kein `tailscale serve`; `ops/web_deploy.sh` prüft `/health` ohne Kopfzeile.

## Testläufer

`ops/testlauf.sh <befehl …>` wartet auf einen laufenden Testlauf und startet gedrosselt (`nice`, `ionice`); `--status` zeigt, wer läuft. Die Sperrdatei heißt `testlauf.lock` (Verzeichnis `XDG_RUNTIME_DIR`, `TESTLAUF_LOCK` überschreibt). Auf server bleibt der Standard bei der Kopie in `werkzeuge`, damit alle Projekte dieselbe Sperre teilen; auf der Arbeitsinstanz gilt die Kopie im Checkout.

## Release-Quelle

`devflow2 aktualisieren` liest die Releases aus `https://github.com/rubctl/devflow.git` (Standard in `devflow2/instanz.py`, überschreibbar mit `--quelle` oder `DEVFLOW2_RELEASE_QUELLE`). Einrichtung und Update: [Installation und Update](installation.md).
