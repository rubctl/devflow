# Beispieldurchlauf (erfunden, gekürzt)

Vorhaben: „Dashboard zeigt eine Wochenübersicht der Läufe mit Export als CSV.“ Gekürzt auf die Stellen, an denen der Skill etwas Besonderes tut. Alle Angaben sind synthetisch.

## 1. Lesen, dann fragen

Planer (nach Lesen von Masterplan, Entscheidungen und `devflow2/web_lage.py`): „Ich habe gelesen, dass Läufe als JSON je Lauf liegen und das Dashboard ohne Datenbank auskommt. Das fragt ich deshalb nicht. Offen ist nur: Wofür brauchst du den Export?“

## 2. Frage mit Empfehlung

> **Frage 1:** Soll der Export im Dashboard als Link erscheinen oder nur als Befehl?
> **Empfehlung:** Link. Grund: Du nutzt das Dashboard am Handy, ein Befehl hilft dort nicht. Folge: eine neue Route, kein neuer Dienst.

Inhaber: „Link, ja.“ → Annahme geht in den Plan.

## 3. Offene Grundentscheidung

Planer: „Der Export soll Kosten je Lauf enthalten. Dafür müssten Laufdaten dauerhaft gespeichert werden, heute werden alte Läufe nach 30 Tagen entfernt. Das ändert das Datenmodell im Kern, also eine Grundentscheidung, die ich nicht selbst treffe.“

Entsteht: `docs/entscheidungen/0007-laufdaten-aufbewahrung.md`, Status `vorgeschlagen`.

| Option | Folge |
|---|---|
| A: 30 Tage bleiben, Export nur für diesen Zeitraum | kein Eingriff, Auswertung begrenzt |
| B: Laufkosten in eine Jahresdatei überführen | neue Datei, eigene Aufräumregel |
| C: SQLite einführen | neue Abhängigkeit, Migration, schwer umkehrbar |

**Empfehlung:** A. Grund: deckt die Wochenübersicht ab, kein Eingriff. Das Vorhaben wird so geschnitten, dass es auch ohne die Antwort des Inhabers auf B oder C planbar ist; die Entscheidung bleibt `vorgeschlagen`, die Aufgabe „Export über 30 Tage hinaus“ kommt nicht in den Plan.

## 4. Begrenzte Recherche

Auslöser: Unklar, ob CSV-Dateien mit Semikolon oder Komma in deutschem Excel sauber öffnen. Das ist zeitabhängig und strittig.

Vorher festgelegt: Frage „Welches Trennzeichen öffnet ohne Importdialog?“, Optionen Komma, Semikolon, Grenze: 3 Suchen, nur Herstellerdokumentation.

Suchanfrage (erlaubt): „Excel CSV Trennzeichen Regionseinstellung Deutsch“. Nicht erlaubt wäre jede Anfrage mit Spaltennamen oder Beispielwerten aus echten Läufen oder Projekten.

Ergebnis in der Entscheidung: Datum, Quelle, Unsicherheit („abhängig von der Systemregion“), Empfehlung: Semikolon und UTF-8 mit Kennzeichen.

## 5. Bewusst vertagt

Inhaber: „Filter nach Projekt später.“ Planer: „Vertagt. Steht als Annahme unter Risiken: *Der Export enthält alle Projekte; ein Filter ist ein eigenes Vorhaben.* Das blockiert nichts.“

## 6. Prüfblickwinkel (Auszug)

- Fachlichkeit: betrifft, Beispiel „3 Läufe am Montag → 3 Zeilen mit Datum, Projekt, Dauer“.
- Betrieb: betrifft nicht, Route im bestehenden Dienst.
- Sicherheit: betrifft, Export nur mit bestehender Anmeldung; Test auf unangemeldeten Aufruf.
- Daten: betrifft, Projektnamen sind kein Geheimnis, Kosten aus der Messung; Test mit synthetischen Läufen.
- Kosten: betrifft nicht, kein Modelllauf nötig.

## 7. Ergebnis

Entwurf `plan/wochenuebersicht.md` mit `**Stand:** Entwurf`, zwei Aufgaben (Wochenübersicht, CSV-Export), offene Frage „Entscheidung 0007 bestätigen?“. Die Zusammenfassung endet mit: „Abnehmen musst du; ich ändere die Zeile nicht.“
