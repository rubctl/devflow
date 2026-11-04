# Vorhaben · Titel · Autor · Datum

**Stand:** Entwurf

Nach der Abnahme durch den Inhaber wird diese Zeile genau so ersetzt: `**Stand:** abgenommen vom Inhaber am TT.MM.JJJJ`. Erst dann übernimmt devflow den Plan (`plan_uebernehmen`).

## Ziel

Was am Ende anders ist, aus Sicht des Inhabers bzw. der Nutzer.

## Nicht Teil davon

- …

## Masterplan und Grundentscheidungen

Wenn das Projekt einen `MASTERPLAN.md` hat: Welche Kernzusagen berührt das Vorhaben, welche bestätigten Entscheidungen gelten, und braucht es eine neue Grundentscheidung? Eine neue entsteht hier, vor den Aufgaben, mit Optionen, Folgen und Empfehlung unter `docs/entscheidungen/` (Status `vorgeschlagen`, bestätigen kann nur der Inhaber). Ohne Masterplan: Abschnitt streichen.

## Fachliche Abnahme

Pflicht bei Projekten mit Fachlogik, sonst streichen: fachliche Abläufe, Regeln und prüfbare Beispiele (Eingabe → erwartetes Ergebnis) mit synthetischen Testdaten, keine echten Kunden- oder Firmendaten. Masterplan und Grundentscheidungen geben Orientierung, beweisen aber keine fachliche Richtigkeit.

| Ablauf oder Regel | Beispiel (synthetisch) | Erwartet |
|---|---|---|
| … | … | … |

## Aufgaben

Feste Spalten, devflow liest diese Tabelle. Reihenfolge = Reihenfolge der Umsetzung. Entwickler: `claude` (Standard) oder `sol`. Bereich: Dateien und Ordner, mit denen der Lauf einsteigt. Stufe (legt der Inhaber bei der Abnahme fest): `leicht` (Sonnet, wenig Effort: UI, Doku, kleine Funktionen), `normal` (Sonnet, hoher Effort) oder `schwer` (Opus: Sicherheit, Datenwege, devflow selbst); leer = Stufe des Projekts. Berührt eine Aufgabe eine Kernzusage des Masterplans, beginnt ihr Abnahmekriterium mit `Kernzusage „…“:` und nennt den Test dazu; die Stufe richtet sich danach (Kernzusagen eher `schwer`). Das Review stuft einen Befund gegen eine Kernzusage mindestens als P2 ein.

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| 1.1 | … | claude | … | … | normal |

## Offene Fragen an den Inhaber

Frage, Optionen mit Folgen, Empfehlung. Sie werden auch in der Roadmap unter „Offene Fragen an den Inhaber“ gelistet.

## Risiken

- …

## Zweitmeinung

Kurz das Urteil des anderen Modells und was daraus übernommen wurde.
