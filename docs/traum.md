# Traum

Der Inhaber drückt im Dashboard „Träumen“, zum Beispiel nach Feierabend. devflow steckt dann den Rest des laufenden 5-Stunden-Fensters in Arbeit, die sonst liegen bliebe. Nichts davon wird ohne den Inhaber gemergt. Am Ende liegt ein Traumbericht bereit, und ein Push-Hinweis meldet „Traum beendet: 3 Vorschläge, 1 Plan“.

Code: `devflow2/traum.py`, Vorschläge `devflow2/vorschlag.py`, Oberfläche `devflow2/web_traum.py` (Seite `/traum`, Baustein auf der Übersicht). Plan: `plan/s5.md`.

## Start

Ein Traum startet nur über den Knopf „Träumen“ (`POST /traum/starten`, Herkunft `dashboard`). Es gibt keinen Zeitplan und kein MCP-Werkzeug; die Hintergrundschleife setzt nur einen schon laufenden Traum fort.

Der Knopf fehlt mit Grund, und der Dienst lehnt mit 409 ab, wenn:

- `anhalten alles` gilt oder kein Projekt angemeldet ist,
- ein normaler Lauf oder Steuerlauf aktiv ist (Pflegeläufe des Wochentimers zählen nicht),
- der Selbstlauf gerade starten würde (er ist an, Ereignisse liegen, Budget ist frei),
- für Claude auch nach dem Messaufruf keine aktuelle Messung mit bekanntem Fensterende da ist,
- `traum_grenze` oder `traum_reserve` von Claude schon erreicht ist,
- es in diesem Fenster schon einen Traum gab (Abweichung bis 10 Minuten gilt als dasselbe Fenster).

**Frische Messung (S5.5):** Es zählt der Stand aus [`kontingent.py`](dashboard.md#kontingent-s32): jünger als 2 Stunden, Fenster noch nicht vorbei. Claude meldet das Kontingent nur in Läufen, Sol in den codex-Sitzungsdateien. Ist die Messung veraltet oder fehlt sie, holt devflow sie beim Klick auf „Träumen“ mit einem Messaufruf je Modell (`messung.py`): kürzeste Eingabe („Antworte nur mit: ok“), Claude mit `haiku`/`low`, ohne Werkzeuge und MCP, Sol mit `codex exec` im Lesemodus, beide in einem leeren Temp-Verzeichnis statt im Repo. Der Aufruf ist ein eigener kurzer Lauf der Art `messung` („Kontingentmessung“ in der Laufliste), zählt nicht als normale Arbeit, nimmt keinen Laufplatz und löst kein Ereignis aus. Danach gilt die normale Prüfung (Grenze, Reserve) unverändert. Das Dashboard zeigt „Träumen“ auch bei veralteter Messung; gemessen wird erst beim Klick, und nur, wenn nichts anderes gegen den Start spricht (Halt, laufende Arbeit). Scheitert der Claude-Aufruf oder liefert er keine Grenzwerte, lehnt der Start ab: „Claude: keine frische Messung, der Messaufruf ist gescheitert (…)“. Bei Sol entfällt in dem Fall nur dessen Teil des Traums. Im Traum wird vor einem Schritt ebenfalls erst gemessen, wenn die Messung des Modells veraltet ist; ein gescheiterter Messaufruf wird in demselben Traum nicht wiederholt. Geschätzt wird weiterhin nichts.

## Eine Steuerung

Traum und Selbstlauf teilen sich `selbstlauf.startsperre`. Solange ein Traum aktiv ist, startet kein Steuerlauf, auch nicht, wenn er nur noch seinen letzten Schritt abwartet. Die Ereignisse warten und werden danach abgearbeitet.

Ein zweiter Klick und ein Neustart des Dienstes erzeugen keinen zweiten Traum: Der Zustand liegt in `traeume/<id>.json`, die Zähler bleiben.

**Normale Arbeit hat Vorrang:** Abnahme, Änderungswunsch, Antwort, ein neuer normaler Lauf (Nacharbeit, Entwicklung, Deploy …) und das Fortsetzen eines Laufs beenden den Traum nach dem laufenden Schritt. Erkannt wird das an den Ereignissen `plan-abgenommen`, `plan-rueckmeldung` und `antwort` seit dem Start und an Läufen ohne Traumkennung. Neue Ideen und Pflegeereignisse beenden keinen Traum.

## Grenzen

| Grenze | Wo | Folge |
|---|---|---|
| **Fensterende** | beim Start als `fenster_ende` gespeichert, je Traumlauf als `bis` | Der Lauf beendet die CLI und die Prüfwerkzeuge der Pflegeprüfung selbst samt Prozessgruppe, auch ohne Dienst. Danach startet er nichts mehr und schreibt kein Ergebnis |
| **Kein Fensterwechsel** | Traumläufe kennen keine Kontingentpause und kein Fortsetzen (`laeufe.fortsetzen` lehnt ab, im Dashboard fehlt der Knopf) | Endzustand `traum-beendet`, angezeigt als „Traum beendet“. Meldet die Messung ein deutlich späteres Fensterende, gilt das Fenster ebenfalls als gewechselt |
| **Neustart** | Der Takt liest den gespeicherten Traum | Vor dem Fensterende geht es weiter, ein toter Lauf zählt als gescheitert; danach endet der Traum |
| `traum_grenze` | vor jedem Modellstart, je Modell | ab diesem Wert im 5-Stunden-Fenster kein Start mehr (Standard 90 %) |
| `traum_reserve` | vor jedem Modellstart, je Modell | bleibt von der Woche höchstens so viel übrig, kein Start mehr (Standard 15 % verbleibend) |

Die Grenzen prüft `traum.tor` im Laufprozess unmittelbar vor jedem Start der CLI, die Takte prüfen sie vorher schon einmal. Eine Wiederholung passiert dasselbe Tor und zählt in `zaehler`. Die Messung ist verzögert: Die Grenze ist ein Richtwert, kein exakter Deckel.

Greift eine Grenze bei Claude, endet der Traum. Bei Sol entfällt nur die Zweitmeinung; Sols 5-Stunden-Fenster ist ein eigenes, die Laufzeit begrenzt trotzdem das Fenster von Claude.

Einstellen in `devflow.toml`, einzelne Modelle dürfen fehlen. Bei mehreren Projekten gilt der strengste Wert.

```toml
[traum_grenze]
claude = 80
sol = 90

[traum_reserve]
claude = 20
```

## Reihenfolge

Billig vor teuer. Jeder Schritt startet erst, wenn der vorige fertig ist; es läuft immer nur einer.

| Schritt | Modell | Was |
|---|---|---|
| Rückblick der Woche | Sonnet, nur lesend | von devflow gezählte Läufe, Kosten, Nacharbeit, Fehler; Bericht mit Vorschlägen für devflow selbst |
| Pflegeprüfung | keines | je Projekt ein Lauf `pflege`. Funde landen wie immer im Eingang, lösen im Traum aber keine Update-Kette aus |
| Ideen ausarbeiten | Sonnet, Werkzeugsatz `ideen` | je Projekt mit neuen Ideen (ohne Pflegefunde) ein Planungslauf. Wie `planung`, aber ohne `frage_stellen`: Entwürfe stehen von selbst unter „Braucht dich“ |
| Zweitmeinung | Sol | je neuem Entwurf, wenn Sol Kontingent hat |
| Code pflegen | nach Stufe des Projekts, nur lesend | je Projekt höchstens fünf lohnende Punkte als Bericht, daraus höchstens drei Themen für Vorschläge (Codeblock `json` am Ende der Antwort) |
| Vorschlag bauen | nach Stufe des Projekts, nur Read, Grep, Glob, Edit, Write | je Thema ein Lauf auf eigenem Branch, siehe [Vorschläge](#vorschläge) |
| Vorschlag testen | keines | Testbefehl aus `devflow.toml` im Worktree des Vorschlags, wenn einer hinterlegt ist |
| Vorschlag prüfen | Sol | Gegenreview, wenn Sol Kontingent hat und die Tests nicht rot sind |

Rückblick und Traumbericht gehören ins Projekt `devflow`, ohne dieses ins erste angemeldete. Scheitert ein Lauf, bekommt der Schritt einen zweiten Versuch; scheitert auch der, geht der Traum mit dem nächsten Schritt weiter.

Traumläufe tragen `traum` und `bis`, haben die Art `traum` (Zweitmeinung `meinung`, Prüfung `pflege`, Vorschläge `entwickeln`, `test` und `review` mit dem Feld `vorschlag`), melden keine Ereignisse und keine Fehler-Hinweise; was passiert ist, steht im Traumbericht. Sie zählen gegen `DEVFLOW2_MAX`.

## Vorschläge

Traumbericht und Berichte der Vorschläge beginnen mit `**Kurz:**` (höchstens drei Zeilen, [Kurzfassung](dashboard.md#kurzfassung-s141)); in „Braucht dich“ steht ein Vorschlag als Zeile mit dem Knopf „Übernehmen“, die Kurzfassung klappt per Klick auf ([Entscheidungsliste](dashboard.md#übersicht-s43)).

Änderungen aus dem Traum kommen als Vorschlag zum Inhaber: ein eigener Branch mit Bericht, der bis zu „Übernehmen“ weder gemergt noch deployt werden kann. Es gibt höchstens drei je Projekt und Traum.

**Register:** `vorschlaege/<id>.json` im Zustandsverzeichnis. Der Eintrag entsteht, bevor es den Branch gibt, und bleibt für immer, auch nach „Verwerfen“. Die ID ist `traum-<traum-id>-<thema>`, der Branch `traum/<traum-id>-<thema>`, der Worktree `<repo>-traum-<traum-id>-<thema>`. Ein Thema kommt je Traum nur einmal vor, auch über Projekte hinweg.

| Zustand | Bedeutung |
|---|---|
| `entsteht` | Der Traum baut, testet oder prüft noch |
| `offen` | Steht unter „Braucht dich“ und auf `/traum`. Alle fertigen Vorschläge erscheinen beim Ende des Traums, auch ohne Review |
| `uebernommen` | Der Inhaber hat dem Stand zugestimmt, die normale Prüfung läuft |
| `gemergt` | auf `main` |
| `verworfen` | Branch und Worktree sind weg, Bericht und Entscheidung bleiben |
| `gescheitert` | Es entstand kein Vorschlag, der Grund steht im Traumbericht |

**Sperre:** `vorschlag.merge_tor` steht in `ablauf._merge_pruefen`, durch das jeder Merge muss: Werkzeug `mergen`, Folgeschritt nach einem Review, Fortsetzen eines Mergelaufs, auch wenn der Lauf schon angelegt ist. `vorschlag.deploy_sperre` steht in `ablauf._deploy_gesperrt`, also vor `deployen`, dem Deploy nach dem Merge und dem Fortsetzen. Geprüft wird das Register, nicht der Auftrag:

- Ein Vorschlag ohne Freigabe wird nicht gemergt, ein verworfener nie.
- Ein Branch `traum/…` ohne Eintrag im Register und eine Aufgabe `traum-…` ohne Eintrag sind ebenfalls gesperrt. `aufgabe_anlegen` lehnt solche IDs ab.
- Ein normaler Branch, der einen Stand eines ungefreigegebenen Vorschlags enthält, wird nicht gemergt. Enthält `main` so einen Stand (Merge von Hand), gibt es keinen Deploy. Das gilt auch nach „Verwerfen“. Als Stand zählt die ganze Herkunft: der Baucommit (`bau` im Register, er ändert sich nie), der aktuelle `commit` und devflows Rebases. Ein Branch, der vor dem Reviewbericht abgezweigt wurde, kommt also nicht durch.

Ereignisse wecken nichts: Traumläufe melden keine. Es gibt kein MCP-Werkzeug für Vorschläge; `lage()` nennt sie nur.

**Schreibziele im Traum:**

| Ziel | Wer |
|---|---|
| eigener Branch und Worktree | Baulauf (Edit, Write), Commit durch devflow |
| `berichte/traum-*` | devflow: Traumbericht, Berichte der Vorschläge, Reviews |
| `plan/` als Entwurf, Eingang | Planungslauf über `plan_entwurf` und `idee_zustand`, Pflegeprüfung über den Eingang |
| `berichte/pflege-*`, `berichte/<plan>-meinung-sol.md` | devflow selbst, wie außerhalb des Traums; der Pflegebericht erneuert dabei das Kanban in `ROADMAP.md` |

Der Baulauf hat keine Shell, kein Git und kein MCP. Schreiben darf er nur im eigenen Worktree: Er läuft mit `--permission-mode dontAsk` und `--allowedTools 'Read,Grep,Glob,Edit(./**)'`, denn im Modus `auto` ginge Write auch nach außen (am CLI geprüft). Nach dem Lauf prüft devflow zusätzlich: kein eigener Commit, `ROADMAP.md` unverändert, Haupt-Checkouts aller Projekte wie vor dem Lauf, mindestens eine Änderung. Sonst wird der Vorschlag `gescheitert`, sein Branch und Worktree werden entfernt. Dateien, die ein Lauf trotzdem außerhalb seines Worktrees geschrieben hat, bleiben liegen: Sie gehören nicht devflow, der Grund steht im Traumbericht. Bearbeitet der Inhaber während des Baulaufs den Haupt-Checkout, scheitert der Vorschlag ebenfalls; das ist Absicht, im Zweifel gibt es keinen Vorschlag.

**Übernehmen** (`POST /traum/vorschlag/<id>/uebernehmen`, nur Dashboard): Das Formular trägt den angezeigten Commit. Steht der Branch anders, passiert nichts. Sonst merkt sich devflow die Freigabe, legt die Aufgabe `traum-…` in der Roadmap an und macht weiter:

- **Bestandenes Gegenreview auf genau diesem Stand:** Merge wie immer (Rebase, Tests, falls `main` sich geändert hat, `--ff-only`), danach der Deploy aus `devflow.toml`.
- **Sonst:** Das Gegenreview startet, gemergt wird erst danach, mit der normalen Kette einschließlich Nacharbeit. Fehlte Sol im Traum das Kontingent, ist das der normale Weg.

Erlaubt ist beim Merge nur der freigegebene Commit, dazu der Reviewbericht, den devflow selbst darauf committet, und devflows Rebases davon (vor dem Review und beim Merge über Statuscommits, siehe [Merge-Tor](architektur.md#merge-tor-s142)). Ist `main` seit dem Traum-Review fachlich weiter, startet „Übernehmen“ statt des Merges Vorbereitung und neues Review. Hat sich der Branch sonst geändert, etwa durch Nacharbeit, verfällt die Freigabe: Der Vorschlag ist wieder `offen`, die Aufgabe steht auf „Braucht dich“, und der Inhaber stimmt dem neuen Stand zu oder verwirft ihn.

**Verwerfen** (`POST /traum/vorschlag/<id>/verwerfen`, nur Dashboard): Entfernt genau den registrierten Branch und Worktree, ausdrücklich auch ungemergt, mit `--force` und `-D`. Das ist die einzige Ausnahme von „ohne Force“. Nicht entfernt wird, was nur so heißt: Der Branch muss den registrierten Stand enthalten, der Worktree muss in `git worktree list` auf diesem Branch stehen. Was nicht passt, bleibt stehen, und ein Hinweis nennt es. Solange ein Lauf aktiv ist oder wenn schon gemergt wurde, lehnt devflow ab. Der Bericht und die Entscheidung landen in `berichte/<id>.md` auf `main` und im Protokoll, eine angelegte Aufgabe geht auf `entfällt`.

Scheitert ein Baulauf zweimal oder endet der Traum mitten im Bau, wird der Vorschlag `gescheitert`, und das Ende des Traums entfernt Branch und Worktree mit derselben Prüfung wie „Verwerfen“. Das betrifft nur Stände, die devflow nie committet hat.

**Pflegefunde** aus dem Traum lösen keine Update-Kette aus: kein Ereignis `pflege-routine`, und `pflege_aufgabe` lehnt ab, solange die letzte Prüfung aus einem Traum stammt. Die Wochenprüfung läuft unabhängig davon weiter; eine Prüfung im Traum verschiebt sie nicht.

## Bedienung

- **Traum beenden** (`POST /traum/beenden`): Der laufende Schritt läuft zu Ende, danach startet keiner mehr.
- **Alles anhalten** erfasst auch den Traum: Der laufende Schritt stoppt sofort, der Traum endet, und „Alles fortsetzen“ weckt ihn nicht wieder.
- **Anhalten** eines einzelnen Traumlaufs auf der Laufseite beendet ihn endgültig. Bei Claude endet damit der Traum, bei Sol nur der Schritt.
- Scheitert die Steuerung selbst, endet der Traum, damit er die Sperre nicht behält.

## Ergebnis

- **Seite „Traum“:** Grenzen und Messung je Modell, offene Vorschläge mit Stand, Tests, Review, Bericht, Diffstat und den Knöpfen, alle Träume mit Schritten, Läufen, Hinweisen, Modellstarts und Grund des Endes.
- **„Braucht dich“:** je offenem Vorschlag ein Eintrag mit Sprung auf die Seite „Traum“.
- **Traumbericht:** `berichte/traum-<id>.md` als Commit auf `main` im Heimatprojekt, mit Schritten, Vorschlägen, Rückblick und Code-Punkten. Lässt er sich nicht committen, steht der Grund auf der Seite.
- **Push:** Art `traum` nach dem Bericht, genau einmal. Die Nutzlast trägt nur die Anzahl der Vorschläge und Pläne, der Text entsteht im Service Worker.
