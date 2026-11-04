# Skills

Skills für Claude Code, die zu devflow gehören. Sie liegen im Repo, damit sie versioniert und mit dem Release (S8) auf die Arbeitsinstanz kommen.

| Skill | Zweck |
|---|---|
| [`planungssitzung`](planungssitzung/SKILL.md) | Ein Vorhaben mit dem Inhaber planen: Fragen mit Empfehlung, Grundentscheidungen, begrenzte Recherche, Entwurf |
| [`neues-projekt`](neues-projekt/SKILL.md) | Neues Projekt anlegen (grüne Wiese oder GitHub-URL), mit dem Inhaber Masterplan (auf Wunsch aus vorhandenem Material), Grundentscheidungen mit Betrieb und grobe Roadmap erarbeiten, anmelden |

## Einrichtung

Auf einer Arbeitsinstanz, die mit `ops/installieren.sh` eingerichtet wurde, verlinkt der Installer beide Skills selbst (nach `~/.claude/skills/`, Ziel `~/.local/share/devflow2/aktiv/skills/…`, siehe [Installation](../docs/installation.md#mcp-server-und-skills-für-claude-schritt-6)); gleichnamige fremde Skills lässt er unberührt. Von Hand, etwa auf dem Entwicklungsrechner:

Claude Code liest persönliche Skills aus `~/.claude/skills/<name>/SKILL.md`. Einmal je Rechner, wiederholbar:

```bash
mkdir -p ~/.claude/skills
ln -sfn ~/dev/devflow/skills/planungssitzung ~/.claude/skills/planungssitzung
ln -sfn ~/dev/devflow/skills/neues-projekt ~/.claude/skills/neues-projekt
```

Der Link zeigt auf den Checkout von `main`; ein gemergter Stand gilt damit ab der nächsten Sitzung. Prüfen: In Claude Code `/planungssitzung` bzw. `/neues-projekt` eintippen, der Skill erscheint in der Liste. Ohne Link (oder bei einem Rechner ohne den Checkout) den Ordner des Skills nach `~/.claude/skills/` kopieren.

## Benutzen

1. Claude Code im Projekt starten, für das geplant wird, mit Opus (`/model`).
2. `/planungssitzung <Vorhaben in einem Satz>`.
3. Fragen beantworten (oder „vertagen“), am Ende den Entwurf lesen, Abnahme von Hand: `**Stand:** abgenommen vom Inhaber am TT.MM.JJJJ` setzen, committen, dann `plan_uebernehmen`.

Die Sitzung läuft aus deinem Kontingent, nicht aus dem Budget des Selbstlaufs.

### Neues Projekt

1. Claude Code im Checkout von devflow starten (das Skript liegt unter `ops/`), mit Opus.
2. `/neues-projekt <Name>` und den Einstieg wählen: grüne Wiese oder vorhandenes Repo per URL.
3. Fragen beantworten; am Ende die Zusammenfassung lesen und das OK geben. Danach ist das Projekt committet und angemeldet. Die Entscheidungsvorschläge bestätigst du selbst, dann `/planungssitzung <erstes Vorhaben>` im neuen Projekt.
