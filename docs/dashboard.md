# Dashboard

Oberfläche für den Inhaber, vom Handy und vom Rechner. Plan: `plan/dashboard.md` (S2). Stand: lesen (S2.1), Dienst eingerichtet (S2.2), Handlungen (S2.3: Frage beantworten, Lauf und alles anhalten und fortsetzen; S3.3: Pläne abnehmen; S3.4: Ideen erfassen), Handy und Web-Push (S2.4: installierbar, Hinweise je Gerät).

## Starten

```bash
PYTHONPATH=~/dev/devflow ~/.local/share/devflow2/venv/bin/python -B -m devflow2 web
```

Adresse (nur Loopback), Port und Login-Art stehen in `instanz.toml` ([Instanz-Einstellungen](instanz.md)); Standard `127.0.0.1:8478` mit Tailscale-Login. `DEVFLOW2_WEB_PORT` und `DEVFLOW2_WEB_LOGIN` überschreiben Port und Login.

Im Betrieb läuft er als Benutzerdienst `devflow2-web` und ist bei Login-Art `tailscale` über `tailscale serve` im Tailnet erreichbar (Adresse siehe Betrieb) (Einrichtung, Deploy, Rückweg: [Betrieb](betrieb.md#dashboard-dienst)). Lokal zum Ausprobieren:

```bash
curl -H 'Tailscale-User-Login: <login>' http://127.0.0.1:8478/
```

## Zugang

- Bei `web.login = "tailscale"` braucht jede Anfrage, auch `/health`, genau eine Kopfzeile `Tailscale-User-Login` mit dem eingestellten Login. Sonst antwortet der Dienst mit 403, ohne Inhalt.
- Bei `web.login = "keine"` (Arbeitsinstanz) gibt es keine Anmeldung; der Dienst bindet nur an Loopback und antwortet nur, wenn der `Host` `127.0.0.1`, `localhost` oder `[::1]` ist, sonst 403 ([Instanz-Einstellungen](instanz.md#arbeitsprofil)).
- Wer lokal prüft (z. B. `ops/web_deploy.sh`), schickt die Kopfzeile selbst mit: `curl -H 'Tailscale-User-Login: <login>' http://127.0.0.1:8478/health` → `{"ok": true}`.
- Seiten und `/api/…` gibt es nur per GET; andere Methoden ergeben 405.
- Schreibende Anfragen (die Handlungen unten) sind nur als POST möglich und brauchen zusätzlich eine passende `Origin`: Sie muss zu `Host` (bzw. `X-Forwarded-Host` von `tailscale serve`) passen oder in `web.origin` (`instanz.toml`, bzw. `DEVFLOW2_WEB_ORIGIN`) stehen (kommagetrennt, z. B. `https://server.example.net:8472`). Fehlende oder fremde `Origin` und `Sec-Fetch-Site: cross-site` ergeben 403, ohne etwas zu ändern. GET auf eine Handlung ergibt 405.
- Jede Antwort trägt eine strenge Content-Security-Policy (nur eigene Skripte und Stile, keine Inline-Skripte, kein Einbetten), `nosniff`, `Referrer-Policy: same-origin` (der Browser schickt so bei eigenen Formularen eine echte `Origin`; fremden Seiten gibt er keine), `no-store`.

## Seiten

| Pfad | Inhalt |
|---|---|
| `/` | **Übersicht** (Leitstand, siehe unten): Braucht dich (Entscheidungsliste, siehe unten), Läuft gerade, Projekte, Selbstlauf, Traum; ohne erledigte Aufgaben und alte Läufe (S11.3) |
| `/roadmap`, `/eingang`, `/pflege` | **Alte Sammelseiten (S11.1):** antworten mit 303. Bei genau einem Projekt auf dessen Unterseite (`/p/<projekt>/roadmap` usw.), sonst auf die Übersicht (`/#projekte`). Nichts Projektübergreifendes außer der Übersicht |
| `/traum` | **Traum (S5.3, S5.4):** „Träumen“ und „Traum beenden“, Grenzen und Messung je Modell, Vorschläge mit „Übernehmen“ und „Verwerfen“, alle Träume mit Schritten, Läufen und Grund des Endes ([Traum](traum.md)) |
| `/p/<projekt>/pflege` | **Pflege (S5.1, S5.2):** je Projekt Stand des Pflegeplans mit „Pflegeplan widerrufen“, letzte Prüfung auf Sicherheitslücken und veraltete Pakete, nicht Geprüftes, Link zum Bericht und „Jetzt prüfen“ ([Pflege](pflege.md)) |
| `/p/<projekt>/eingang` | **Eingang (S11.1):** Ideen des Projekts mit Textfeld |
| `/p/<projekt>` | **Projektseite (S11.3)**, Reihenfolge: aktuelles Vorhaben (Kurztabelle der offenen Aufgaben), offene Fragen, Läuft gerade, die letzten fünf abgeschlossenen Läufe (alle über das geschlossene „Alle Läufe“), Ideen; „Erledigte Aufgaben“ und „Protokoll“ stehen eingeklappt (`details`, geschlossen) ganz unten |
| `/p/<projekt>/roadmap` | **Roadmap als Tabelle (S4.4, S11.2):** je Vorhaben (je `###`-Tabelle der ROADMAP.md, mit Planlink; Tabellen ohne eigene Überschrift unter „Sonstiges“, diese Karte entfällt ohne Läufe) ein Kopf mit „n von m erledigt“, Fortschrittsbalken und Summen (Läufe, Nacharbeit, Kosten, Dauer) und eine Tabelle mit ID, Kurztitel, Stand (● erledigt, ◐ in Arbeit/Review, ○ bereit/offen, ▲ Braucht dich/blockiert, darunter das Datum), Läuft/Wartet, Stufe und Kosten (Details siehe unten). `entfällt` zählt bei „n von m“ nicht mit. Offene Vorhaben stehen oben, erledigte unten und eingeklappt; innerhalb davon das neueste zuerst (nach Datum in der Überschrift) |
| `/p/<projekt>/a/<id>` | Roadmap-Zeile mit Planzeile, Fragen, Läufe mit Urteil, Kriterientabelle je Reviewrunde (K, erfüllt, Begründung; bei Altbestand ein Hinweis), Befunden mit Kategorie und K-ID und Log, Bericht und Reviews, Diff des Branches gegen die Basis |
| `/p/<projekt>/plaene` | **Pläne des Projekts (S14.3b):** nur die Pläne `plan/*.md` dieses Projekts in Gruppen, jede mit Anzahl in der Überschrift, leere Gruppen entfallen: **Zur Abnahme** (Entwurf; Zeile mit Kurzfassung beim Aufklappen und dem Knopf „Abnehmen“, derselbe wie in „Braucht dich“ mit Stufen und Prüfsumme `gesehen`; danach zurück auf diese Seite), **In Arbeit** (abgenommen, noch nicht jede Planaufgabe in der Roadmap `erledigt` oder `entfällt`), **Nicht lesbar** (Plan mit Fehler) und ganz unten eingeklappt **Erledigt (n)** (abgenommen, alle Aufgaben erledigt oder entfallen; ein abgenommener Plan ohne Aufgaben wie `pflege` zählt hier). Je Gruppe das Neueste zuerst: nach Abnahmedatum, sonst nach dem Datum der Planüberschrift, bei Gleichstand nach Planname absteigend (Zahlen als Zahlen). Unbekanntes Projekt: 404. Der Link „Pläne“ der Projektseite und der Übersichtskarte führt hierher |
| `/plaene` | **Pläne aller Projekte:** je Projekt dieselben Gruppen (mit Link „Nur dieses Projekt“); „Abnehmen“ kommt hierher zurück |
| `/p/<projekt>/plan/<name>` | Planansicht: Stand, Kurzfassung oben (siehe [Kurzfassung](#kurzfassung-s141)), Stufenwahl je Aufgabe, „Abnehmen“ und „Änderung wünschen“ (nur solange der Plan nicht abgenommen ist), darunter der Plan |
| `/kontingent` | **Kontingent:** Balken für Claude und Sol mit Prozent, Freigabe, Stand und Hinweis, Abschnitt „Nacharbeit“ (Kennzahlen, S15.4), Kosten je Aufgabe und je Lauf (die neuesten 100) |
| `/lauf/<id>` | ein Lauf: Kurzfassung oben, Felder, Kriterien (Review), Befunde, Log (200 Zeilen, live), Ergebnis |
| `/p/<projekt>/md/<pfad>` | md-Datei aus dem Repo (Wiki, Pläne, Berichte; bei `berichte/` und `plan/` mit Kurzfassung oben); `?zweig=<branch>` liest vom Aufgabenbranch. Ohne `.md` am Ende: Liste der md-Dateien darunter |
| `/api/lauf/<id>?zeilen=N` | JSON für das Live-Log (`zustand`, dazu `text` und `art` für die Anzeige, `aktiv`, `log`) |
| `/hinweise` | **Hinweise:** Web-Push auf diesem Gerät an- und abschalten, Probehinweis, Liste der angemeldeten Geräte |
| `/manifest.webmanifest`, `/sw.js` | Manifest (installierbar, `standalone`, Kurzbefehle) und Service Worker (Offline-Fassung, Push), beide mit Login wie alles |
| `/health` | Lebenszeichen (mit Login) |

## Übersicht (S4.3)

Die Startseite hat vier Blöcke, jeder mit einem Satz statt einer leeren Liste, wenn nichts da ist („Nichts offen: Du musst gerade nichts tun.“, „Gerade läuft nichts.“):

1. **Braucht dich (S14.3):** eine Entscheidungsliste. Jeder Eintrag ist eine Zeile: Status, Projekt und Überschrift links, der Knopf daneben (auch bei 390 px). Ein Tipp auf die Überschrift (oder Enter/Leertaste) klappt Kurzfassung ([S14.1](#kurzfassung-s141)) und Details auf. Drei Gruppen:
   - **Zu klären** (oben, orange umrandet): Aufgabe auf „Braucht dich“ oder „blockiert“ ohne Frage, abgelaufene Anmeldung (S8.4: fester Hinweis auf `claude` bzw. `codex login`; Logtext oder Token landen nie im Hinweis; „Fortsetzen“ nur, wenn zulässig) und der globale Halt mit „Alles fortsetzen“.
   - **Entscheidungen**, je Art ein Knopf, der sofort wirkt (keine Rückfrage; die Beschriftung und der Hinweis in den Details nennen, was startet):

     | Eintrag | Knopf | Details |
     |---|---|---|
     | [Vorschlag aus dem Traum](traum.md#vorschläge) | „Übernehmen“ (genau der angezeigte Commit; Merge oder Gegenreview, wie auf `/traum`) | Kurzfassung des Berichts, Branch, Stand, Tests und Review, „Verwerfen“ (mit Rückfrage, an den angezeigten Stand gebunden wie „Übernehmen“: steht der Branch inzwischen woanders, wird nichts entfernt) |
     | Plan zur Abnahme | „Abnehmen“ mit den Stufen aus der Plantabelle (der Knopf trägt sie als versteckte Felder `stufe.<ID>`; die Beschriftung nennt sie im Tooltip) | Kurzfassung, Stufenliste, „Änderung wünschen“, Link zum ganzen Plan. Liegt ein Änderungswunsch offen, gibt es keinen Ein-Klick-Knopf; der Plan steht dann unter „Zur Info“ |
     | offene Frage | „Empfehlung annehmen“, **nur** bei eindeutiger Empfehlung (siehe unten); sonst „Antworten“ (klappt die Zeile auf und springt ins Antwortfeld) | Frage, Optionen, Empfehlung, Antwortformular |
     | Lauf fehlgeschlagen oder angehalten | „Fortsetzen“, nur wenn zulässig (kein Traumlauf, kein Halt, keine offene Frage zur Aufgabe, kein zweiter Steuerlauf, Deploy nicht gesperrt); sonst steht der Grund in den Details | Kurzfassung, Fehler, Link zum Lauf |

     Fünf Entscheidungen stehen sofort da, der Rest hinter „n weitere“.
   - **Zur Info** (eingeklappt, zählt nicht in „(n)“): neue Idee (Sprung zu `#ideen`), Push offen, Plan mit Änderungswunsch.

   **Eindeutige Empfehlung:** Die Frage hat nummerierte Optionen (1, 2, …) und eine `**Empfehlung:**`, die mit „Option N“ oder „N.“/„N)“ beginnt (N existiert; **Positivliste:** dahinter steht nur noch nichts, Satzzeichen oder der Optionstext selbst, ganz oder als Anfang, Groß-/Kleinschreibung egal; Klammern, Begründungen („weil“/„da“/„denn“) und jeder Zusatz sind nicht erlaubt, also weder „Option 1 (zusätzlich Dokumentation ergänzen)“ noch „Option 1, weil …“ noch „Option 1 und 2 kombinieren“) oder wörtlich dem Text genau einer Option entspricht. Prosa, „keine“ oder „siehe Frage“ ist nicht eindeutig; dann gibt es nur „Antworten“. Der Knopf trägt die Antwort `Empfehlung angenommen: Option N, <Text>` ein. **Modelle sollen die Empfehlung deshalb nur als „Option N“ schreiben; die Begründung gehört in die Frage** (`frage_stellen`). So wird kein Zusatz stillschweigend verworfen: Was die Positivliste nicht kennt, führt zu „Antworten“.

   **Bindung an den Stand:** Jeder Knopf sendet `gesehen` mit: Prüfsumme des Plans (wie die Planseite), Commit des Vorschlags (wie `/traum`), Prüfsumme des ganzen Frageblocks (Frage, Optionen, Empfehlung) bzw. Zustand, Ende und Fehlertext des Laufs. Hat sich das inzwischen geändert, lehnt devflow mit 409 und „hat sich geändert“ ab. Eine schon beantwortete Frage meldet „schon beantwortet“ und ändert nichts. Die Knöpfe senden an dieselben Routen wie die Formulare auf den Detailseiten (Origin, Halt, Inhaberrechte); das Feld `zurueck=start` bringt danach zurück auf die Übersicht (kein anderes Ziel möglich). Code: `entscheidung.py` (Logik), `web_entscheidung.py` (Anzeige), Routen in `web_aktion.py`.
2. **Läuft gerade:** je aktivem Lauf Aufgabe, Art, Modell, seit (Start und Dauer), Aufgabenkosten bisher (Summe aller Läufe der Aufgabe in US-Dollar laut Claude-Lauflog, also auch bei einem Sol-Lauf die bereits angefallenen Claude-Kosten der Aufgabe; „–“ nur, wenn für die Aufgabe noch nichts gemeldet ist) und „Anhalten“. Das Live-Log steht auf der Laufseite.
3. **Projekte:** je Projekt das aktive Vorhaben (erstes mit offenen Aufgaben) mit Fortschrittsbalken, nächste Aufgabe, ein Hinweis, wenn der [Masterplan](masterplan.md#hinweis-in-der-lage) fehlt oder zu lang ist, letzter Deploy, Zähler und Links.
4. **Selbstlauf** und **Traum** je als Zustandszeile; Details und Knöpfe sind eingeklappt. Darin: Selbstlauf (an/aus, Budget heute, „Alles anhalten“) und Traum (Stand, „Träumen“ oder der Grund, warum es nicht geht, „Traum beenden“).

**Navigation (S11.1):** Obere Leiste (`nav.haupt`): Übersicht · Projekte (Auswahl, klappt auf; Skript in `app.js`) · Traum · Kontingent, dazu „Hinweise“ im Pfad. Am Rechner steht sie oben unter dem Pfad, auf dem Handy (bis 40 rem) als Leiste unten, die aktuelle Seite mit `aria-current`. **Unterleiste je Projekt** (`nav.unter`, unter der oberen Leiste): Projekt · Roadmap · Eingang · Pflege, auf jeder Seite eines Projekts (Projektseite, Aufgabe, Lauf, Dokumente, Plan, Roadmap, Eingang, Pflege); `web_html.seite(…, projekt=, unter=)`. Roadmap, Eingang und Pflege gibt es nur dort.

**Anzeige statt Rohwert.** Die Tabellen in `web.py` (`ZUSTAND`, `URTEIL`, `ART_TEXT`, `MODELL_TEXT`, `STATUS_ART`) setzen Lauf-Zustand, Urteil, Art und Modell in Anzeigetexte um („läuft“, „Nacharbeit nötig“, „Entwicklung“, „Sol“); Rohwerte wie `laeuft` oder `codex` stehen nie in der Oberfläche, auch nicht im Live-Log-Update (der Server liefert `text` und `art`). Neue Lauf-Zustände im Code brauchen einen Eintrag in `ZUSTAND` (ein Test prüft das). Farben: **Grün (`ok` ●) nur für Abgeschlossenes und Gutes:** Roadmap `erledigt`, Lauf `fertig`, Urteil „bestanden“, abgenommener Plan, Kontingent unter 80 %, Selbstlauf an. „in Arbeit“, „Review“ und „läuft“ sind `aktiv` (◐, ohne Farbe); `warn` ▲ gilt für Wartendes, `error` ✕ für Fehler. Symbole sind 17 px groß.

## Kontingent (S3.2, S4.3)

**Nacharbeit (S15.4):** `/kontingent` hat den Abschnitt „Nacharbeit“ (Code `kennzahlen.py`, Darstellung `web_kontingent.nacharbeit`). Zwei Spalten: **Altbestand** und **Mit K-Kriterien**. Zugeordnet wird über die tatsächlich verwendete Auftragsart (trägt ein Lauf der Aufgabe `kriterien_auftrag` – Entwicklungs- oder Nacharbeitsauftrag nannte K-Kriterien – oder `kriterien_soll` – Review-Auftrag nannte K-IDs –, gehört sie zu „Mit K-Kriterien“, auch vor dem Review), nie über ein Datum. Zeilen: Aufgaben; Anteil mit Nacharbeit nach Review (Basis: Aufgaben mit Review-Urteil); Anteil mit Nacharbeit wegen roter Tests; Runden je Aufgabe (zählende Nacharbeitsläufe); Runden nach Ursache (`review`, `tests`, `konflikt`, `hinweise`, bestimmt aus dem Lauf davor); Befunde nach Kategorie (`kriterium`, `rand`, `sonst`, Altbefunde ohne Kategorie als „nicht zugeordnet“); aktive Zeit je Aufgabe (Dauer beendeter Läufe der Arten Entwicklung, Nacharbeit, Vorbereitung, Tests, Review, abzüglich `pause`: Kontingentwartezeit, auch wenn währenddessen angehalten wurde, und die Zeit zwischen Anhalten/Fehler und Fortsetzen). Nur lesend, ohne Hochrechnung.

Auf jeder Seite steht unter dem Pfad eine Kopfzeile mit vier schmalen Balken: Claude und Sol, je 5 h und Woche. **Die Füllung ist der verbrauchte Anteil**; daneben stehen Prozent mit Symbol und die Freigabezeit („frei“). Auf der Übersicht und auf `/kontingent` sind es dieselben Balken in voller Form, zusätzlich mit Zeitpunkt der Messung („Stand“) und Hinweis. Jeder Balken ist ein `role="progressbar"` mit `aria-valuenow` und `aria-valuetext` („Claude 5 h: 85 % verbraucht, frei 14:00“); ohne Messung hat er keinen Wert (`aria-valuetext` „unbekannt“). Die Füllung ist ein `<progress>`; Inline-Stile erlaubt die CSP nicht. Ab 80 % steht ▲ und die Füllung ist gelb; eine veraltete Messung ist blass und trägt „(veraltet)“ und den Hinweis. Die Kopfzeile verlinkt auf `/kontingent`. Gemessen wird nur gelesen, nie hochgerechnet; Code in `devflow2/kontingent.py`, Darstellung in `web_kontingent.py`.

| Quelle | Was gelesen wird |
|---|---|
| Claude | jüngstes `rate_limit_event` (`unifiedWindows.five_hour`/`seven_day`: `utilization`, `resetsAt`) in den 40 zuletzt geänderten Lauflogs unter `laeufe/<id>/log.jsonl`. Die Ereignisse tragen keine Uhrzeit, als Stand gilt die letzte Änderung der Logdatei |
| Sol | letztes `rate_limits` (`primary` = 5 h, `secondary` = Woche, `used_percent`, `resets_at`) im Ende der 20 zuletzt geänderten Dateien `*.jsonl` unter `~/.codex/sessions/` (nach Änderungszeit, nicht nach Tagesordner) (anderer Ort: `DEVFLOW2_CODEX_SITZUNGEN`); nur lesend. Stand ist der Zeitstempel des Ereignisses |
| Kosten | Summe `total_cost_usd` der `result`-Ereignisse im Claude-Lauflog. Sol meldet keine Kosten (Strich) |

Jeder Wert hat einen Zustand: `aktuell` (●), `veraltet` (○, blass, Messung älter als 2 h, Wert wird mit Zusatz gezeigt), `unbekannt` (○: nichts gemessen, Format nicht erkannt oder die Freigabezeit ist verstrichen, das Fenster hat also neu begonnen und der alte Wert ist überholt). Ab 80 % steht ▲ (auch bei veralteter Messung; unter 80 % ist eine veraltete Messung ○). Fehler beim Lesen ergeben `unbekannt`, die Seite fällt nie aus. Ändert sich das Format der codex-Dateien, zeigt die Anzeige „unbekannt“, devflow läuft weiter.

Als „fehlgeschlagen“ zählt ein Lauf nur, solange danach für dieselbe Aufgabe (bzw. dieselbe Art ohne Aufgabe) kein neuerer Lauf gestartet wurde.

## Roadmap-Tabelle (S4.4, S11.2)

Code: `web_roadmap.py` (Seite), `kontingent.aufgaben_bilanz` (Werte aus den Läufen), `roadmap.vorhaben` (Aufgaben je Vorhaben). Sechs Spalten, damit die Seite auf einen Blick lesbar bleibt und auf 390 px ohne seitliches Scrollen auskommt; Läufe, Nacharbeit, Modell und Dauer stehen weiter im Kopf des Vorhabens (Summen) und auf der Aufgabenseite `/p/<projekt>/a/<id>`.

| Spalte | Quelle |
|---|---|
| ID | Aufgabe, Link auf die Aufgabenseite |
| Kurztitel | Text der Roadmap-Zeile vor dem ersten Doppelpunkt (mit Leerraum dahinter; Markdown-Zeichen entfallen); ohne Doppelpunkt der auf 60 Zeichen gekürzte Anfang mit „…“. Der volle Text steht im `title` |
| Stand | Status der Zeile (Symbol und Wort), darunter das Datum |
| Läuft / Wartet | „läuft“ bei einem Lauf im Zustand `laeuft` oder `abschluss`, „wartet“ bei `wartet` oder `kontingent`, jeweils mit der Art des Laufs (Entwicklung, Nacharbeit, Review …); sonst „–“. Läuft und wartet beides, gilt „läuft“ |
| Stufe | Stufe des letzten Entwicklerlaufs, sonst die Stufe in der Planzeile, sonst „–“ |
| Kosten | Summe von `total_cost_usd` der Claude-Läufe in $; Sol meldet keine Kosten und zeigt „–“. Fehlt bei einem Teil der Claude-Läufe das Ergebnis, steht „n von m Claude-Läufen“ darunter |

**Reihenfolge:** Vorhaben mit offenen Aufgaben zuerst (in der Reihenfolge der ROADMAP.md), vollständig erledigte danach und in einem geschlossenen `details`. Auf dem Handy (bis 40 rem) entfallen die Zusatzzeilen (Datum, Art des Laufs, Teilkosten), damit die Tabelle in die Breite passt.

Aufgaben ohne Läufe zeigen „–“ bei Läuft / Wartet und Kosten. **Filter und Sortierung sind Links** (`?status=erledigt`, `?sort=kosten&richtung=ab`), der Server rechnet sie; die Seite braucht kein JavaScript. Ein Klick auf die Spalte sortiert aufsteigend, der zweite absteigend (`aria-sort`, ▲/▼); leere Werte stehen immer am Ende, sortiert wird je Vorhaben. Unbekannte Werte werden ignoriert. Der Filter zählt über alle angezeigten Vorhaben; ein erledigtes Vorhaben klappt auf, wenn der Filter Treffer darin hat. Die Tabelle liegt in einem Kasten (`.tabellenkasten`); bei 390 px scrollt weder der Kasten noch die Seite waagrecht (Browserprobe).

## Handlungen

Alles läuft über Formulare (POST, `application/x-www-form-urlencoded`, höchstens 32 kB) und endet mit einer Weiterleitung (303) auf die Seite, die eine kurze Bestätigung zeigt (`?meldung=<code>`, nur feste Texte). Geschrieben wird ausschließlich über `ablauf.antwort_eintragen`, `ablauf.anhalten` und `ablauf.fortsetzen`, dieselben Wege wie im MCP, sowie über `selbstlauf.schalten`.

| Pfad (POST) | Wirkung | Knopf auf |
|---|---|---|
| `/p/<projekt>/frage/<nr>/antwort` (Feld `antwort`) | Antwort als Commit `roadmap: Antwort Inhaber auf F<nr>` auf `main`, Herkunft `dashboard` im Eintrag und im Protokoll; steht die Aufgabe auf `Braucht dich` und ist keine weitere Frage zu ihr offen, wird sie `bereit` | Aufgabenseite und Projektseite, bei jeder offenen Frage |
| `/p/<projekt>/frage/<nr>/empfehlung` (Felder `gesehen`, `zurueck`) | trägt die eindeutige Empfehlung als Antwort ein (wie `…/antwort`, Herkunft `dashboard`); 409, wenn sich die Frage geändert hat oder keine eindeutige Empfehlung hat; schon beantwortet ändert nichts | Übersicht („Braucht dich“) |
| `/lauf/<id>/anhalten` | beendet den Laufprozess und sichert die Sitzung (Zustand `angehalten`) | Übersicht (laufende Läufe), Aufgabenseite, Laufseite; mit Rückfrage im Browser |
| `/lauf/<id>/fortsetzen` (optional `gesehen`) | nimmt einen angehaltenen (auf der Laufseite und in „Braucht dich“ auch fehlgeschlagenen) Lauf in derselben Sitzung wieder auf; eine offene Frage zur Aufgabe lehnt das mit 409 ab, ebenso ein geänderter Lauf, wenn `gesehen` nicht mehr passt | dort, wo der Lauf angehalten ist |
| `/alles/anhalten`, `/alles/fortsetzen` | globaler Halt: laufende Läufe stoppen, neue sperren, bis fortgesetzt wird. Aufheben geht nur hier, nicht über das MCP | Übersicht; mit Rückfrage im Browser |
| `/traum/starten`, `/traum/beenden` | [Traum](traum.md) starten (409 mit Grund, wenn das nicht geht; ein zweiter Klick ändert nichts) und beenden (der laufende Schritt läuft zu Ende) | Übersicht und `/traum`; mit Rückfrage im Browser |
| `/traum/vorschlag/{id}/uebernehmen` | [Vorschlag](traum.md#vorschläge) für den angezeigten Commit (`gesehen`) übernehmen: Merge bei bestandenem Gegenreview, sonst startet das Review (409, wenn der Branch anders steht) | `/traum` (mit Rückfrage im Browser) und Übersicht (ohne Rückfrage) |
| `/traum/vorschlag/{id}/verwerfen` | genau den registrierten Branch und Worktree entfernen, auch ungemergt; ein zweiter Klick ändert nichts (409, solange ein Lauf aktiv ist oder wenn schon gemergt wurde) | `/traum` und in den Details der Übersicht; mit Rückfrage im Browser |
| `/selbstlauf/an`, `/selbstlauf/aus` | Schalter des [Selbstlaufs](architektur.md#selbstlauf); „aus“ ist der Standard und beendet keinen laufenden Lauf | Übersicht; Einschalten mit Rückfrage im Browser |

- **Herkunft:** Alle Handlungen hier tragen die Herkunft `dashboard`. Im Dashboard angehaltene Läufe lassen sich nur hier fortsetzen (siehe [Grenzen im Code](architektur.md#grenzen-im-code)).
- **Antwort:** Leerraum und Zeilenumbrüche werden zu einem Leerzeichen, Steuerzeichen fallen weg, höchstens 4000 Zeichen, leer ergibt 400. Mehrzeilig ablegen kann nur `antwort_eintragen` im MCP, dann als Zitatblock. Nach der Antwort gibt das Dashboard nichts an den Entwickler weiter: Die Aufgabe steht auf `bereit`, die Steuerung (Claude per MCP oder der Selbstlauf über das Ereignis `antwort`) holt sie dort ab, wie bei einer Antwort über `antwort_eintragen`.
- **Doppelt senden ist harmlos:** `app.js` lässt jedes Formular nur einmal abgehen und sperrt die Knöpfe; serverseitig laufen Prüfen und Handeln hintereinander (eine Sperre im Dienst, dazu die Dateisperre des Repos). Dieselbe Antwort ein zweites Mal gilt als erledigt (gleiche Bestätigung, kein zweiter Commit). Eine **andere** Antwort auf eine schon beantwortete Frage ändert nichts und meldet „Diese Frage war schon beantwortet“. Anhalten eines nicht aktiven Laufs und Fortsetzen eines schon laufenden Laufs melden ebenfalls nur, dass nichts zu tun war.
- **Fortsetzen nach globalem Halt:** Solange alles angehalten ist (in „Braucht dich“ steht dann der Halt als Klärungseintrag oben), fehlen die Knöpfe „Fortsetzen“ je Lauf (der Dienst lehnt sie mit 409 und Erklärung ab). „Alles fortsetzen“ hebt nur die Sperre auf; die angehaltenen Läufe stehen danach in „Braucht dich“ (mit „Fortsetzen“, soweit zulässig) und werden einzeln fortgesetzt.
- **Selbstlauf:** Die Übersicht zeigt den Schalter, „Budget heute: n von m Steuerläufen“, die wartenden Ereignisse und, falls etwas den Start verhindert, den Grund (angehalten, Budget verbraucht). Ein Steuerlauf erscheint wie jeder Lauf unter „Läuft gerade“ und hat eine Laufseite mit Log. Solange Claude aus einer Sitzung steuert, bleibt der Schalter aus.
- Nicht möglich (z. B. Fortsetzen eines fertigen Laufs, Frage gibt es nicht) zeigt eine Seite mit der Meldung von devflow und 409 bzw. 404.

## Pläne abnehmen (S3.3)

Nur der Inhaber nimmt Pläne ab (AGENTS.md „Planen“); das Dashboard ist dafür der Weg ohne Editor. Code: `plaene.py` (Logik), `roadmap.py` (`plan_abnehmen`, `plan_rueckmeldung`), `web_plaene.py` (Seiten), `web_aktion.py` (Formulare und Handlungen), `ereignis.py` (Ereignis).

| Pfad (POST) | Wirkung |
|---|---|
| `/p/<projekt>/plan/<name>/abnehmen` (Felder `stufe.<ID>`, `gesehen`, optional `zurueck`: `start`, `plaene` oder `plaene-alle`, sonst die Planseite; nie ein freies Ziel) | ersetzt die erste Zeile `**Stand:** …` durch `**Stand:** abgenommen vom Inhaber am TT.MM.JJJJ` und schreibt die gewählten Stufen in die Planspalte `Stufe` (fehlt die Spalte, wird sie angehängt). Ein Commit auf `main` nur mit der Plandatei: `plan: plan/<name>.md abgenommen vom Inhaber am TT.MM.JJJJ`. Danach nimmt `plan_uebernehmen` den Plan an |
| `/p/<projekt>/plan/<name>/aenderung` (Feld `wunsch`) | ergänzt den Abschnitt `## Rückmeldung Inhaber` (wird angelegt) um `- **TT.MM.JJJJ:** <Wunsch>`; ein Commit `plan: Änderung gewünscht zu plan/<name>.md`. Der Stand bleibt unverändert |

- **Stufe:** leer = Stufe des Projekts, sonst eine der Stufen aus `devflow.toml` (`leicht`, `normal`, `schwer`, …). Nicht genannte Aufgaben behalten ihre Stufe. Unbekannte Stufe oder Aufgabe ergibt 400.
- **Nur der gesehene Plan:** Das Formular trägt eine Prüfsumme des Plantextes (`gesehen`). Hat sich der Plan seither geändert (oder fehlt die Prüfsumme), ergibt „Abnehmen“ 409 und ändert nichts; die Seite muss neu geladen werden.
- **Einmal:** Ein schon abgenommener Plan lässt sich weder noch einmal abnehmen noch mit Änderungswünschen versehen (409, nichts ändert sich). Derselbe Änderungswunsch zweimal hintereinander (am selben Tag) ergibt einen Eintrag und einen Commit. Hat der Plan nicht committete Änderungen, lehnt `repo.auf_main` ab (409).
- **Ereignis für den Selbstlauf:** Nach jedem Commit steht eine Zeile in `ereignisse.jsonl` im Zustandsverzeichnis (`zeit`, `art` = `plan-abgenommen` oder `plan-rueckmeldung`, `projekt`, `bezug` = Plandatei; nie der Text vom Inhaber). Der [Selbstlauf](architektur.md#selbstlauf) liest und verbraucht sie. Lässt sich die Zeile nicht schreiben, steht der Commit trotzdem, und die Seite sagt es (`?meldung=ohne-ereignis`).
- Schutz wie bei allen Handlungen: POST mit passender `Origin`, Tailscale-Login; GET auf die Handlung ergibt 405, fremde `Origin` 403.

## Ideen erfassen (S3.4)

Der Inhaber schreibt eine Idee auf der Projektseite (Abschnitt „Ideen“, Anker `#ideen`); ein Planungslauf oder die steuernde KI in einer Sitzung macht daraus einen Planentwurf. Code: `eingang.py` (Logik und Ablage), `web_eingang.py` (Abschnitt), `web_aktion.py` (Handlung), `mcp_server.py` (Werkzeuge).

**Ablage:** `plan/eingang.md` im Projekt-Repo, je Idee ein Abschnitt:

```
## Idee 3 · 30.10.2026 · dashboard

**Zustand:** in Planung → plan/wetter.md

> Text der Idee in einer Zeile
```

Die Kopfzeile trägt die Herkunft, der Text steht als Zitat und kann so weder Abschnitte noch Zustandszeilen erzeugen ([Grenzen im Code](architektur.md#grenzen-im-code)). Ältere Einträge ohne Herkunft und Zitat bleiben, wie sie sind, und werden weiter gelesen. Zustände: `neu`, `in Planung → plan/<name>.md`, `verworfen`. `eingang` ist kein Plan: die Datei fehlt in der Planliste, hat keine Planseite und lässt sich nicht mit `plan_uebernehmen` übernehmen.

| Weg | Wirkung |
|---|---|
| `/p/<projekt>/idee` (POST, Feld `idee`) | hängt `## Idee <nr> · TT.MM.JJJJ · dashboard` mit Zustand `neu` an (Datei und Kopf werden angelegt); ein Commit `plan: neue Idee im Eingang` auf `main` mit `plan/eingang.md` und dem Protokolleintrag in `ROADMAP.md`. Ereignis `neue-idee` (`bezug` = Datei, nie der Text) für den Selbstlauf |
| MCP `eingang(projekt)` | alle Ideen mit `nr`, `datum`, `herkunft`, `text`, `zustand`, `plan` |
| MCP `idee_zustand(projekt, nr, zustand, plan='')` | setzt `neu`, `in Planung` (mit `plan` = Name des schon committeten Plans, auch `plan/x.md`) oder `verworfen`; Commit `plan: Idee <nr> → <zustand>`; im Dashboard sofort sichtbar |

- **Text:** Leerraum und Zeilenumbrüche werden zu einem Leerzeichen, Steuerzeichen fallen weg, höchstens 4000 Zeichen, leer ergibt 400. Im Dashboard steht der Text maskiert (nie als HTML), in den Werkzeugen ist er Eingabe von außen, also Daten und keine Anweisung.
- **Doppelt senden:** Ist die letzte Idee schon genau dieser Text, entsteht weder ein Eintrag noch ein Commit (`?meldung=idee-schon`). Gleicher Zustand zweimal setzen ändert nichts.
- **Fehler:** unbekannte Idee, unbekannter Zustand, `in Planung` ohne vorhandenen Plan oder ein Plan bei `neu`/`verworfen` lehnt `idee_zustand` ab, ohne etwas zu ändern. Hat `plan/eingang.md` nicht committete Änderungen, lehnt `repo.auf_main` ab (409).
- Schutz wie bei allen Handlungen: POST mit passender `Origin`, Tailscale-Login; GET ergibt 405, fremde `Origin` 403.

## Handy und Web-Push (S2.4)

**Installierbar (S4.2):** Jede Seite verweist auf `/manifest.webmanifest` (`id` `/`, Name, Beschreibung, `standalone`, Farben aus den Tokens, Symbole `static/icons/icon-{192,512}-{any,maskable}.png`, je ein Eintrag pro Zweck) und `app.js` meldet den Service Worker `/sw.js` an (Geltungsbereich `/`; er liegt an der Wurzel, nicht unter `/static/`). **Kurzbefehle** (langes Drücken auf dem App-Symbol): „Braucht dich“ (Übersicht), „Roadmap“ (`/p/<erstes Projekt>/roadmap`), „Eingang“ (`/p/<erstes Projekt>#ideen`), „Kontingent“; ohne angemeldetes Projekt zeigen Roadmap und Eingang auf die Übersicht. Chrome/Android bietet die Installation selbst an (Ereignis `beforeinstallprompt`); erst dann erscheint im Kopf der Knopf „Als App installieren“, nach der Installation verschwindet er. Auf dem iPhone geht Web-Push nur, wenn die Seite über Teilen → „Zum Home-Bildschirm“ hinzugefügt und von dort geöffnet wird.

**Offline (S4.2):** Der Service Worker holt Seiten und statische Dateien zuerst aus dem Netz und merkt die Antwort (Speicher `devflow-seiten-1`, höchstens 150 Seiten, nicht gemerkt werden Weiterleitungen, Seiten mit `?meldung=` und Nicht-HTML). Ist das Netz nicht erreichbar (Verbindungsfehler, 502/503/504 oder keine Antwort in 8 s), liefert er die zuletzt geladene Fassung mit dem Hinweis „▲ offline, Stand HH:MM“ (Zeit des Ladens); eine nie geladene Seite zeigt „noch nicht gespeichert“ statt eines Browserfehlers. Handlungen sind offline gesperrt: Formulare der gespeicherten Seite sind deaktiviert, und jede Anfrage, die nicht `GET` ist, geht nie aus dem Speicher und wird ohne Netz mit 503 und dem Hinweis „Handlung wurde nicht ausgeführt“ beantwortet (nichts wird vorgemerkt oder später nachgeschickt). Die Live-Log-Abfrage ruht auf einer gespeicherten Seite; kommt die Verbindung zurück, lädt sie sich neu. Die Kopie liegt im Browser des Geräts (mit Inhalten aus dem Dashboard); wer das Gerät abgibt, löscht die Website-Daten.

**Neue Version (S4.2):** Ändert sich `sw.js` oder eine Datei unter `static/` (Schriften ausgenommen), wartet der neue Worker: `/sw.js` trägt am Ende eine Prüfsumme der statischen Dateien, der Browser sieht so jede neue `app.js`/CSS als neue Fassung. Die Seite, die den Worker zum ersten Mal anmeldet, speichert der Worker beim Aktivieren nach, damit auch sie sofort offline verfügbar ist. Die Seite zeigt unten „Neue Version – neu laden“; ein Klick aktiviert ihn und lädt neu. `app.js` fragt beim Zurückkehren in die App nach einer neuen Fassung. Ändert sich das Speicherformat, den Namen des Speichers in `sw.js` hochzählen (alte werden beim Aktivieren gelöscht).

**Android installieren (Kurzanleitung):** 1. In Chrome die Dashboard-Adresse (Tailscale-Name) öffnen, solange das Handy im Tailnet ist. 2. Im Kopf auf „Als App installieren“ tippen und bestätigen (alternativ Menü ⋮ → „App installieren“). 3. Die App liegt nun auf dem Startbildschirm; langes Drücken zeigt die Kurzbefehle. 4. Unter „Hinweise“ in der App „Hinweise erlauben“ wählen, damit Push-Hinweise ankommen. Fehlt der Knopf, ist die App schon installiert, oder Chrome bietet sie (noch) nicht an: Seite einmal neu laden und ein paar Sekunden warten.

**Hinweise je Gerät:** Auf `/hinweise` schaltet jedes Gerät seine eigenen Hinweise an („Hinweise erlauben“: Browser fragt nach Erlaubnis, das Abo geht per `POST /hinweise/abo` an den Dienst) und ab („Hinweise abschalten“ meldet das Abo ab und löscht es im Browser). Die Seite gleicht Browser und Dienst ab (`POST /hinweise/status`): Ist das Browser-Abo da, der Dienst kennt es aber nicht mehr (z. B. in der Geräteliste entfernt), steht dort „nicht mehr angemeldet“ und „Hinweise erlauben“ meldet das Gerät wieder an. „Probehinweis senden“ schickt nur an dieses Gerät. Die Liste zeigt alle angemeldeten Geräte (Name aus dem User-Agent, z. B. „iPhone · Safari“, und seit wann) und kann sie einzeln entfernen, auch wenn das Gerät nicht mehr erreichbar ist. Höchstens 12 Geräte. Alle `POST` unter `/hinweise/…` brauchen wie die Handlungen eine passende `Origin`; JSON höchstens 32 kB.

**Wann ein Hinweis kommt:** Der Dienst prüft alle 30 s (`DEVFLOW2_PUSH_TAKT`, Sekunden) alle Projekte (`push_ereignisse.ereignisse`) auf:

| Art (`kind`) | Ereignis | Text im Service Worker |
|---|---|---|
| `braucht` | Aufgabe steht auf „Braucht dich“, ohne eigene offene Frage | „Eine Aufgabe braucht dich“ / „3 Aufgaben brauchen dich“ |
| `frage` | neue offene Frage (auch die Frage, wegen der eine Aufgabe auf „Braucht dich“ geht: genau ein Hinweis) | „Neue offene Frage“ / „2 neue offene Fragen“ |
| `fehler` | Lauf fehlgeschlagen (Regel wie in „Braucht dich“: kein neuerer Lauf derselben Aufgabe) | „Ein Lauf ist fehlgeschlagen“ / … |
| `budget` | ein Start des Selbstlaufs ist am Tagesbudget gescheitert (einmal je Tag) | „Selbstlauf: Tagesbudget verbraucht“ |
| `traum` | ein Traum ist beendet und sein Bericht steht | „Traum beendet: 0 Vorschläge, 1 Plan“ |
| `test` | nur der Probehinweis | „Probehinweis: so meldet sich devflow“ |

Pro Prüfung geht höchstens ein Hinweis je Art raus, mit der Anzahl der neuen Ereignisse. „Blockiert“ und „angehalten“ lösen nichts aus.

**Genau einmal:** Schon gemeldete Ereignisse stehen in `push.json` (Schlüssel `<art>:<projekt>:<id>`, ohne Texte). Das Ereignis wird vor dem Senden eingetragen, damit auch ein Absturz keinen zweiten Hinweis erzeugt; wer in dem Moment nicht erreichbar ist, bekommt ihn nicht nachgeschickt. Verschwindet ein Ereignis (Frage beantwortet, neuer Lauf) und tritt ein neues auf, gilt das neue. Der erste Start ohne Stand merkt sich nur, was schon da ist, und meldet nichts. Lässt sich ein Projekt gerade nicht lesen, bleiben dessen Einträge stehen.

**Inhalt und Verschlüsselung:** Die Nutzlast ist `{"count": n, "kind": "<art>", "url": "/"}`, bei `traum` zusätzlich `"zahlen": [Vorschläge, Pläne]`, verschlüsselt nach RFC 8291 (`aes128gcm`, immer gleich lang) und mit VAPID nach RFC 8292 signiert (`devflow2/push_protokoll.py`, übernommen aus `devflow/push_protocol.py`). Weder Projekt- noch Aufgabentexte noch Namen gehen an die Push-Dienste. Erlaubt sind nur die Endpunkte `fcm.googleapis.com`, `updates.push.services.mozilla.com` und `web.push.apple.com` (https, ohne Port und Query); andere Abos lehnt der Dienst mit 400 ab. Der Push-Dienst antwortet auf einen Versand mit 404/410, wenn das Abo abgelaufen ist; dann wird das Gerät entfernt. Andere Fehler (Netz, 5xx) werden ohne Endpunkt im Log vermerkt und das Abo bleibt.

**Schlüssel und Daten:** Der VAPID-Schlüssel (`vapid.key`, 32 Byte base64url) entsteht beim ersten Start des Dienstes unter `~/.config/devflow2/` (`DEVFLOW2_CONFIG`) mit 0600 (Verzeichnis 0700); eine zu offene Datei wird auf 0600 gesetzt, eine unbrauchbare nie ersetzt (Fehlermeldung statt neuem Schlüssel, weil sonst alle Abos ungültig würden). Die Abos liegen in `~/.local/state/devflow2/push.json` (0600), nie im Repo, in Logs oder Antworten. Der VAPID-Kontakt ist der erste `https://`-Eintrag aus `DEVFLOW2_WEB_ORIGIN`, sonst `https://server.example.net:8472`.

**Erstes Gerät einrichten (Inhaber):** `/hinweise` auf dem Handy öffnen (iPhone: vorher zum Home-Bildschirm hinzufügen), „Hinweise erlauben“ tippen, dann „Probehinweis senden“.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `devflow2/web.py` | Starlette-App, Zugangsprüfung (ASGI-Middleware `Zugang`), Seiten, `main()` für Uvicorn |
| `devflow2/web_aktion.py` | Handlungen (POST-Routen), Schutz `Herkunft` (ASGI-Middleware), Formulare und Bestätigung für die Seiten |
| `devflow2/web_html.py` | HTML-Bausteine: `t()` maskiert jeden Text und jedes Attribut, nur `Roh` gilt als fertiges HTML |
| `devflow2/web_md.py` | kleine md-Darstellung: Überschriften, Absätze, Listen (verschachtelt), Tabellen, Code, Zitate, Fett, Kursiv, Links |
| `devflow2/web_traum.py` | Seite `/traum`, Baustein für die Übersicht, Knöpfe „Träumen“, „Traum beenden“, „Übernehmen“ und „Verwerfen“ |
| `devflow2/web_push.py` | Routen für Manifest, Service Worker, `/hinweise` und Geräte an-/abmelden |
| `devflow2/push.py` | VAPID-Schlüssel, Abos (`push.json`), Versand, Prüfung auf neue Ereignisse, Hintergrundschleife des Dienstes |
| `devflow2/push_ereignisse.py` | was neu beim Inhaber liegt (nur Art und Kennung), `offene_fehler` |
| `devflow2/push_protokoll.py` | RFC 8291 (Verschlüsselung) und RFC 8292 (VAPID), ohne IO |
| `devflow2/static/` | `cd/` (CD-Paket: Tokens, Schriften Inter und JetBrains Mono, Signet; unverändert aus dem CD-Paket des Auftraggebers), `style.css` (Layout, eine Spalte auf dem Handy, nur Token-Variablen), `app.js` (Live-Log, Rückfrage, Einmal-Senden der Formulare, Hinweise an/aus, Installieren, neue Version, Offline-Sperre), `sw.js` (Service Worker: Offline-Fassung, Push), `icon-*.png` |

- **Daten:** Gelesen wird über `roadmap` (Aufgaben, Fragen), `laeufe` (`alle`, `laden`, `log_text`, `ergebnis`), `ablauf.plan_zeile` und lesendes `git` (`ls-tree`, `cat-file`, `diff`). md-Dateien kommen aus dem committeten Stand eines lokalen Branches, nie aus dem Arbeitsverzeichnis; nur `.md`, nur was Git an diesem Branch kennt.
- **Maskierung:** Inhalte aus Repos und Logs schreiben Modelle. HTML darin erscheint immer als Text; Links in md sind nur `http(s)` oder relativ, Steuerzeichen und Leerraum in Links sperren sie.
- **Relative Links** (`web.verweise`) werden gegen den Repo-Pfad der Datei aufgelöst und zeigen auf die md-Ansicht: in md-Dateien gegen deren Ordner, in Roadmap-Inhalten (Aufgabentabelle, Fragen, Protokoll) gegen `ROADMAP.md`, in Planzeilen gegen die Plandatei. `/pfad` gilt ab Repo-Wurzel, `#anker` bleibt. Wird eine Datei von einem Aufgabenbranch gezeigt, behalten ihre Links `?zweig=…`. Was aus dem Repo hinausführt, bleibt Text.
- **Verwaiste Läufe:** Auch das Live-Log und die Laufseite prüfen über `laeufe.verwaist_pruefen`, ob der Prozess noch lebt (dieselbe Regel wie `laeufe.alle`, 30 s Anlauffrist). Ein abgestürzter Lauf wird so zu `fehler`, und die Anzeige hört auf zu fragen.
- **Live-Log:** `app.js` fragt für jedes `<pre data-lauf>` (Lauf- und Aufgabenseite) alle 2 s `/api/lauf/<id>` und setzt den Text über `textContent`. Ist der Lauf zu Ende, hört es auf und zeigt den neuen Zustand (Text und Art vom Server).
- **Grenzen:** Diffs werden nach 300 kB gekürzt, md-Dateien nach 1 MB.

## Tests

- `tests2/test_web.py`: jede Seite am Testprojekt aus `tests2/web_daten.py` (abgeschlossene, fehlgeschlagene und laufende Attrappen-Läufe, offene Frage), Zugang, Maskierung, md-Darstellung.
- `tests2/test_web_lage.py`: Übersicht (vier Blöcke in Reihenfolge, Sprünge, leere Blöcke, Einklappen ab dem sechsten Eintrag, defektes Projekt; S14.3: Entscheidungsliste mit je einem Test für Plan, Frage, Vorschlag und Lauf, veralteter Stand → 409, Doppelklick, Halt, Herkunft, Planzeile per Knopf = Planseite), obere Leiste und Unterleiste, Umleitungen der alten Pfade, Zuordnung aller Lauf-Zustände, Arten, Modelle und Urteile, keine Rohwerte im sichtbaren Text, Grün nur an `erledigt`/`ok`, Kontingentbalken bei 0, 50, 79, 80, 100 %, „unbekannt“ und veraltet (`role=progressbar`, Wert, Text, ▲).
- `tests2/test_web_aktion.py`: Antwort (Commit, `bereit`, einzeilig, doppelt und gleichzeitig, schon beantwortet, ungültig), Anhalten und Fortsetzen eines Attrappen-Laufs, alles anhalten und fortsetzen, Schutz (fremde oder fehlende `Origin`, GET, fehlender Login → ohne Änderung).
- `tests2/test_push.py`: ganzer Testvektor aus RFC 8291 Anhang A, Rundreise der Nutzlast (unabhängig entschlüsselt, gleich lang), VAPID-Signatur, Endpunktprüfung, Schlüssel (0600, bleibt, kaputt wird nicht ersetzt), mit Attrappe des Push-Dienstes: neue Frage → genau ein Hinweis ohne Texte, kein zweiter für dasselbe Ereignis, Braucht dich, Lauffehler, Anzahl, 404/410 entfernt, Netzfehler/500 behalten das Abo; Routen (Manifest, Service Worker, Anmelden, Abmelden, Probe nur an ein Gerät, Entfernen, ungültige oder fremde Anfragen, Gerätegrenze, Hintergrundschleife).
- `tests2/test_selbstlauf.py`: Steuerlauf mit `claude`-Attrappe (ein Laufende, drei Ereignisse währenddessen, Budget, Schalter, Halt, Aufruf mit MCP und Werkzeugliste), Ereignisse der Kette, Auftrag, `plan_entwurf`, Schalter im Dashboard.
- `tests2/test_traum.py`: Traum mit Attrappen ([Traum](traum.md)): Start und Grenzen, Reihenfolge, Fensterwechsel, Kontingentpause, Neustart, Vorrang, Halt, Doppelklick, Bericht, Seite.
- `tests2/test_vorschlag.py`: Vorschläge: Register, Sperre für Merge und Deploy auf allen Wegen, Schreibziele, Übernehmen, Verwerfen, Pflegefunde, Knöpfe.
- `tests2/test_web_browser.py` mit `tests2/browser/web.cjs`: headless Chromium bei 390 px auf allen Seiten ohne waagrechtes Scrollen, auf der Übersicht „Läuft gerade“ ohne Scrollen sichtbar (bei offener Frage) und Navigation unten, Live-Log wächst ohne Neuladen. Dazu `tests2/browser/liste.cjs` (S14.3: Entscheidungsliste bei 390 px, Knopf neben der Überschrift, Aufklappen per Tastatur und Tippen, Doppelklick nur einmal) und `tests2/browser/app.cjs` (PWA: Manifest ohne Fehler und installierbar, Service Worker aktiv, Seite offline mit „▲ offline, Stand HH:MM“, nie geladene Seite mit Hinweis, Formulare gesperrt, POST offline 503, Installationsknopf, „Neue Version – neu laden“; der Server wird dafür wirklich gestoppt) und `tests2/test_pwa.py` (Manifest, Kurzbefehle, Knopf, Skript-Bausteine). Dazu `tests2/browser/aktion.cjs`: Antwort senden und „Alles anhalten“ per echtem Klick (der Browser schickt die `Origin` selbst). Läuft, wenn Node und `playwright-core` da sind (auf server der Fall), sonst übersprungen.

## Design (CD)

- **Tokens statt Farben:** Alle Farben, Schriften und Abstände kommen aus `static/cd/cd.css`. Außerhalb von `static/cd/` und `static/logo/` steht kein Farbwert (Test `tests2/test_web_cd.py`); `theme-color` und Manifest lesen den Grundton aus den Tokens (`web_html.grund()`).
- **Dark ist Standard**, Light folgt der Systemeinstellung (`prefers-color-scheme`).
- **Bausteine:** Kopf mit Signet und Wortmarke `devflow`; Abschnittsköpfe als Kicker (`rc-kicker`); Karten `rc-card`; Status mit Symbol und Wort: `●` ok (grün), `◐` aktiv, `○` ruhig, `▲` Warnung, `✕` Fehler (`web_html.status`, Zuordnung in `web.py`: `STATUS_ART`, `ZUSTAND`, `URTEIL`; das Live-Update bekommt Text und Art vom Server).
- **Primär-Button** (orange) höchstens einmal je Ansicht: die erste offene Antwort bzw. „Hinweise erlauben“. Alle anderen Knöpfe sind `rc-btn`.
- **Handy (390 px):** Kanban einspaltig bis zweispaltig, Aufgabentabelle als Karten, andere Tabellen scrollen nur im eigenen Rahmen. Die Browserprobe prüft Dark und Light, kein waagrechtes Scrollen, geladene Schriften und dass keine Anfrage an fremde Hosts geht.

## Logo und Icons

Signet C (Doppelpfeil `»` mit orangem Cursor-Block in der Kachel mit Eselsohr) liegt als SVG unter `static/logo/`: `devflow-signet.svg` (Kopf, Favicon), `devflow-icon-any.svg` (Kachel randnah), `devflow-icon-maskable.svg` (Vollfläche, Signet im Kreis mit 80 % Durchmesser als Schutzzone), `devflow-badge.svg` (einfarbig, Alpha-Maske für das Push-Badge). Hier und in `static/cd/` sind Farbwerte erlaubt. Der Kopf zeigt Signet und Wortmarke `devflow` (Mono 700, Cursor-Block); wo weniger als 80 px zur Verfügung stehen (Favicon, App-Icon, Badge), erscheint nur das Signet.

Die PNG unter `static/icons/` (192/512 je `any` und `maskable`, `badge-96.png`, `favicon-16/32/48.png`) und `static/favicon.ico` entstehen aus den SVG mit `node ops/icons_erzeugen.cjs <playwright-core>`; geändert wird nur das SVG, dann neu erzeugen und einchecken. Test: `tests2/test_logo.py`.

## Kurzfassung (S14.1)

Der Inhaber liest vor allem auf dem Handy, deshalb steht bei Plänen, Berichten und Läufen **zuerst eine Kurzfassung**. Einheitliche Regel: **höchstens drei Zeilen bzw. Punkte; steht ein Urteil darin, steht es in der ersten Zeile.** Das Modul `devflow2/kurz.py` kennt sie, `web_md.kurz_kasten` zeigt sie als hervorgehobenen Kasten (`aside.kurz`, Signalbalken links) direkt unter der Überschrift.

| Wo | Form | Anzeige |
|---|---|---|
| Plan (`plan/<name>.md`) | Abschnitt `## Kurz` unter der Stand-Zeile (Vorlage `plan/_vorlage.md`). `plan_entwurf` lehnt neue Entwürfe ohne ihn oder mit mehr als drei Zeilen ab; der Pflegeplan `plan/pflege.md` ist ausgenommen, abgenommene und ältere Pläne bleiben gültig | Planseite, md-Ansicht |
| Entwicklerbericht, Reviewbericht, Zweitmeinung, Pflege- und Traumbericht, Antwort eines Laufs | erste Zeile `**Kurz:** …` (bis zu zwei Folgezeilen), von den Aufträgen in `auftraege.py` verlangt; Pflege- und Traumbericht schreibt devflow selbst | md-Ansicht (`berichte/…`), Laufseite |
| Review-Lauf | Feld `kurz` im Review-JSON (`REVIEW_SCHEMA`); `ablauf.urteil` liest es, `review_sichern` speichert es am Lauf (auch im Lagebild/MCP) | Laufseite, Laufkarte auf der Aufgabenseite |

**Rückfall:** Fehlt die Kurzfassung (alter Plan, alter Bericht, Lauf ohne `**Kurz:**`), zeigt die Seite die ersten zwei Sätze des Fließtexts (höchstens 400 Zeichen); Überschriften, Metadatenzeilen (`**Stand:** …`), Tabellen, Zitate, Code und Listen werden übersprungen, ein Review-Lauf ohne `kurz` nimmt die ersten Sätze seines Berichts. Gibt es gar keinen Fließtext, entfällt der Kasten. Eine `**Kurz:**`-Marke zählt nur vor dem ersten Abschnitt (`##`) und außerhalb von Codeblöcken. Die Kurzfassung steht immer als Text im HTML (maskiert, nur Fett/Kursiv/Code/Links), nie als Skript.
