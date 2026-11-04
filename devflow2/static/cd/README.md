# CD-Paket für Web-Oberflächen

Quelle: Design System (Claude-Artefakt, Terrain × Signal, Stand 30.10.2026). Hier liegt die Fassung, die Projekte ohne Netz und ohne Build-Schritt einbinden.

| Datei | Inhalt |
|---|---|
| `cd.css` | Schriften (`@font-face`), Tokens als CSS-Variablen (Dark Standard, Light über `prefers-color-scheme` oder `data-theme="light"`), Bausteine `rc-*` |
| `tokens.json` | Tokens mit Verwendungshinweisen (Farben, Typo, Abstände, Radien, Schatten) |
| `bausteine.css` | Klassen `rc-btn`, `rc-input`, `rc-card`, `rc-status`, `rc-kicker`, `rc-prompt`, `rc-measure`, `rc-wordmark`/`rc-cursor` |
| `erzeugen.py` | erzeugt `cd.css` aus `tokens.json` und `bausteine.css` |
| `fonts/` | Inter 400/600/700, JetBrains Mono 300/400/700 (latin, woff2, OFL, Lizenzen daneben) |
| `logo/` | Signet (dunkle Kachel) und Einfarbfassung |
| `terrain/` | Höhenlinien-Hintergrund 1920×1080, Dark und Light |

## Einbinden

Ordner nach `static/cd/` des Projekts kopieren (ohne `erzeugen.py`, `tokens.json`, `bausteine.css`), dann `<link rel="stylesheet" href="/static/cd/cd.css">` und Favicon `logo/cd-signet.svg`. Keine Google Fonts und keine CDNs: die Seiten laufen nur im Tailnet und laden nichts von außen.

## Kurzregeln (Auszug aus dem CD)

- Dark ist das Primär-Theme, Light folgt der Systemeinstellung.
- `signal` (Orange): Primäraktion (höchstens eine pro Ansicht), Links, Fokus, Prompt. `signal-mark` nur für Formen.
- `terrain` (Grün) nur für Zustände (`● ok`), nie für Aktionen. `warn`/`error` immer mit Symbol und Wort.
- Inter für Text, JetBrains Mono für Wortmarke, Labels/Kicker (Versalien, Sperrung 0,2 em), Messwerte, Code.
- Wortmarke bzw. Produktname klein in Mono 700 mit orangem Cursor-Block; unter 80 px nur das Signet.
- 8er-Raster (`space-1` 4 … `space-8` 96), Radien 4/8/10/14, keine Schatten außer `shadow-pop`, keine Verläufe, keine Emoji, keine Ausrufezeichen.
- Kontrast: Text ≥ 4,5:1 in beiden Themes (die Tokens sind darauf ausgelegt).
