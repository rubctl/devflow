# Masterplan und Grundentscheidungen

Jedes Projekt kann einen kurzen Masterplan und festgehaltene Grundentscheidungen haben. Jeder Lauf kennt sie, Reviews richten ihr Gewicht danach. Ohne diese Dateien läuft alles wie bisher.

## Vier Ebenen

| Ebene | Frage | Ort | Wer entscheidet |
|---|---|---|---|
| Masterplan | Warum, für wen, was muss stimmen | `MASTERPLAN.md` im Wurzelverzeichnis | Inhaber |
| Grundentscheidungen | Womit und wohin: Sprache, Datenbank, Betrieb, Schnittstellen, Grenzen | `docs/entscheidungen/NNNN-*.md`, Überblick in `docs/architektur.md`, Betrieb in `docs/betrieb.md` | Inhaber, auf Vorschlag mit Optionen und Empfehlung |
| Plan | Wie je Vorhaben, welche Aufgaben, welche fachlichen Beispiele | `plan/<vorhaben>.md` | Inhaber nimmt ab |
| Lauf | Details innerhalb dieser Grenzen | Code, Bericht | Modell |

## Masterplan

So kurz wie möglich, ohne technische Details: Wofür und für wen · Richtung und Umfang (was es werden soll, was bewusst nicht) · **Kernzusagen** (was immer stimmen muss) · **Prüfvorrang** (welche Fehler und Tests schwerer wiegen). Grenze etwa eine Seite: Mehr als 70 Zeilen oder 5000 Zeichen zeigt die Lage als Hinweis „Masterplan zu lang“ (`grundlage.GRENZE_ZEILEN`, `GRENZE_ZEICHEN`); der Auftrag enthält ihn trotzdem ganz.

Nur der Inhaber ändert ihn, im Gespräch oder von Hand, direkt auf der Basis (`main`). Gelesen wird immer der committete Stand der Basis, nie ein Branch und nie das Arbeitsverzeichnis.

## Grundentscheidungen

Eine Grundentscheidung ist, was später teuer umzukehren ist: Sprache und Rahmenwerk, Datenbank und Datenmodell im Kern, Betrieb und Zielumgebung, Anmeldung und Rechte, Schnittstellen nach außen, neue externe Dienste. Vorlage: [`entscheidungen/_vorlage.md`](entscheidungen/_vorlage.md).

Im Kopf der Datei (vor der ersten `##`-Überschrift) stehen zwei Zeilen:

- `**Status:**` `vorgeschlagen`, `bestätigt vom Inhaber` (optional ` am TT.MM.JJJJ`) oder `ersetzt durch NNNN`. **Bindend ist nur `bestätigt vom Inhaber`.** Eine fehlende oder andere Angabe bindet nicht. Die Zeile setzt nur der Inhaber.
- `**Betrifft:**` Stichwörter oder Pfade, kommagetrennt, oder `alle`. Sie wählen aus, welche Entscheidungen für eine Aufgabe vollständig im Auftrag stehen (Treffer im Titel, Bereich oder Abnahmekriterium der Aufgabe). Fehlt die Zeile, gilt die Entscheidung allgemein: vollständig in jedem Auftrag, auch in Planen, Steuern und Traum. Bei der Auswahl zählt auch der Zusatzauftrag der Entwicklung (Entwickeln, Nacharbeit, Review).

Vorgeschlagen wird mit Optionen, Folgen und Empfehlung, bei wichtigen Punkten mit Zweitmeinung von Sol. Ein Lauf entscheidet so etwas nicht selbst: Braucht er eine neue Grundentscheidung oder würde er eine bestätigte brechen, stellt er eine Frage (letzte Antwort beginnt mit „FRAGE AN DEN INHABER:“, daraus wird eine Frage in der Roadmap). Das Review prüft das und meldet es als P2. Ein Vorschlag als Datei darf ein Lauf anlegen, bestätigen nicht.

## Wirkung in den Läufen

Entwickeln, Nacharbeit, Review, Zweitmeinung, Planen (Selbstlauf, Traum-Ideen), Steuern und Traum (Rückblick, Code, Vorschläge) bekommen die Vorgaben im Auftrag, nicht über eine Leseanweisung:

- der Masterplan ganz,
- der Überblick (Abschnitt „Überblick“ in `docs/architektur.md`, höchstens 1500 Zeichen),
- die bestätigten Entscheidungen, die zur Aufgabe passen, vollständig; die übrigen bestätigten nur mit Titel und Pfad. Läufe ohne Aufgabe (Planen, Steuern, Traum) bekommen vollständig nur die ohne Betrifft-Zeile und die mit `Betrifft: alle`. Der Rückblick über alle Projekte bekommt nur die Masterpläne. Die Traumläufe (Rückblick, Ideen, Code, Vorschlag) halten den bestätigten Stand wie alle anderen Läufe fest.
- eine Regel für die Rolle (Entwickler fragen bei neuer oder gebrochener Grundentscheidung; das Review prüft es; Pläne nennen die berührte Kernzusage im Abnahmekriterium).

`vorgeschlagen` und `ersetzt` stehen nie im Auftrag.

**Schwere im Review** (`auftraege.schwere`): Mit Masterplan ist ein Befund gegen eine Kernzusage mindestens P2, auch als Randfall; das hat Vorrang vor „Im Zweifel P3“. Mit bestätigten Entscheidungen ist eine neue oder gebrochene Grundentscheidung ohne Frage ein P2. Im **Nachreview** gilt „neue Befunde anderswo nur bei P1“ weiter, außer für diese beiden Fälle: Sie werden auch dort gemeldet und lösen Nacharbeit aus (P2 ergibt immer „nacharbeit“). Ohne Masterplan und ohne bestätigte Entscheidung bleibt der Text unverändert.

## Merge-Sperre

Ein Branch, dessen Diff gegen seinen Abzweig `MASTERPLAN.md` oder eine bestätigte Entscheidung berührt (ändern, löschen, bestätigen, zurücknehmen), wird nicht gemergt: Statt des Merges kommt eine Frage an den Inhaber, die Aufgabe steht auf „Braucht dich“. Geprüft wird vor dem Merge und im Mergelauf nach dem Rebase, sodass ein Rebase die Sperre nicht umgeht. Übernimmt der Inhaber dieselbe Änderung selbst auf `main`, bringt der Branch nach dem Rebase nichts Eigenes mehr mit, und die Sperre entfällt. Entscheidungsdateien mit Status `vorgeschlagen` darf ein Branch anlegen und ändern.

## Bestätigter Stand und Prüfung vor dem Merge

Jeder Lauf hält fest, welcher bestätigte Stand galt (`grundlage_stand` im Lauf: Prüfsumme von Masterplan und jeder bestätigten Entscheidung). Vor dem Merge wird der Stand der Basis mit dem des letzten Reviews verglichen. Hat sich Masterplan oder eine bestätigte Entscheidung geändert (auch neu bestätigt, ersetzt oder gelöscht), muss die Auswirkung geprüft werden:

- Mit `automatisch=True` (Kette, Standard von `mergen`) startet devflow ein weiteres Review des Branchs, dessen Auftrag die geänderten Dateien nennt („Prüfung vor dem Merge“). Besteht es, folgt der Merge.
- Ohne `automatisch` lehnt `mergen` ab und nennt `review_anfordern`.
- Ein Review ohne gemerkten Stand (vor S9.1) gilt als unverändert.

Eine eigene Versionsverwaltung braucht es nicht: Git und das Statusfeld reichen.

## Hinweis in der Lage

`lage()` (MCP) und die Projektkarte der Lage-Seite im Dashboard zeigen „kein Masterplan“, wenn `MASTERPLAN.md` auf der Basis fehlt, und „Masterplan zu lang …“ über der Grenze. Läuft alles sonst wie bisher.
