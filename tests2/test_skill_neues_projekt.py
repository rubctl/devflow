"""Probe des Skills `neues-projekt` (S9.4): beide Einstiege mit festen Antworten statt Gespräch, nur in Temp-Verzeichnissen,
Zustand in einem Temp-`DEVFLOW2_STATE`, Klonen aus lokalem Bare-Repo (kein GitHub). Danach wird das Projekt abgemeldet."""
import json
from pathlib import Path
import re
import subprocess
import unittest

from devflow2 import plaene, projekte, roadmap
from devflow2.projekte import Projekt
from hilfen import git
from test_vorlage import VorlageBasis, WURZEL

SKILL = WURZEL / 'skills' / 'neues-projekt'

# Feste Antworten, die der Inhaber im Gespräch gäbe (synthetisch). Schlüssel = Abschnitt der Vorlage.
MASTERPLAN = {
    'Wofür und für wen': 'Probe-Projekt für Tests von devflow. Nutzer: der Inhaber.',
    'Richtung und Umfang': 'Kleines Werkzeug. Bewusst kein Mehrbenutzerbetrieb.',
    'Kernzusagen': '- Keine echten Daten in Tests.',
    'Prüfvorrang': 'Datenverlust wiegt schwerer als Optik.',
}
ENTSCHEIDUNG = """# 0001 · Zielumgebung

**Datum:** 03.11.2026 · **Entschieden von:** Inhaber
**Status:** vorgeschlagen
**Betrifft:** alle

## Frage

Wo läuft das Projekt? Noch kein Konzept, keine Pipeline.

## Optionen

1. Einzelner Server. Folgen: einfach, ein Ausfallpunkt.
2. Verwaltete Container. Folgen: Pipeline nötig.

## Empfehlung

Offen lassen, bis die Pipeline steht; `deploy` bleibt leer.
"""
BETRIEB = ('## Zielumgebung\n\nOffen, siehe `entscheidungen/0001-zielumgebung.md`. Keine Zugangsdaten.\n\n'
           '## Deploy und Rollback\n\nNoch kein Deploy. Deploy nur mit dem OK des Inhabers.\n\n')
PLAN = """# Vorhaben · Erster Schritt · Claude · 03.11.2026

**Stand:** Entwurf

## Kurz

- Probe.

## Ziel

Probe.

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| 1.1 | Erste Aufgabe | claude | `src/` | Test grün | |

## Offene Fragen an den Inhaber

_Keine._
"""


def abschnitt_ersetzen(text, titel, neu):
    """Ersetzt den Rumpf des Abschnitts `## titel` bis zum nächsten `## `."""
    muster = re.compile(rf'(## {re.escape(titel)}\n\n).*?(?=\n## |\Z)', re.S)
    assert muster.search(text), titel
    return muster.sub(lambda m: m.group(1) + neu + '\n', text, count=1)


class SkillText(unittest.TestCase):
    def test_skill_ist_gueltig_und_vollstaendig(self):
        text = (SKILL / 'SKILL.md').read_text()
        kopf = text.split('---')[1]
        self.assertIn('name: neues-projekt', kopf)
        self.assertIn('description:', kopf)
        for muss in ('--url', 'gh repo clone', 'projekt_anmelden', 'Zielumgebung', 'Vorhaben (grob)', 'bestätigt vom Inhaber',
                     'Konflikt, nicht geändert', 'vorgeschlagen', 'Datengrenze', 'kein Push'):
            self.assertIn(muss, text)
        for link in re.findall(r'\]\(([^)#]+)\)', text):
            self.assertTrue((SKILL / link).resolve().exists(), link)
        readme = (WURZEL / 'skills' / 'README.md').read_text()
        self.assertIn('neues-projekt/SKILL.md', readme)
        self.assertIn('ln -sfn ~/dev/devflow/skills/neues-projekt', readme)


class SkillMaterial(unittest.TestCase):
    """S9.5: Schritt „Material sichten“ steht vor dem Masterplan-Gespräch und trägt die Zusagen aus dem Auftrag."""

    def test_schritt_material_vor_dem_gespraech(self):
        text = (SKILL / 'SKILL.md').read_text()
        schritte = re.findall(r'^## (Schritt \d+: .*)$', text, re.M)
        nummern = [int(re.match(r'Schritt (\d+)', s).group(1)) for s in schritte]
        self.assertEqual(nummern, list(range(1, len(nummern) + 1)))
        titel = [s.split(': ', 1)[1] for s in schritte]
        self.assertLess(titel.index('Material sichten (wenn vorhanden)'), titel.index('Gespräch zum Masterplan'))
        for muss in ('aus Material', 'Lücken und Widersprüche', 'Widersprüche im Material', 'Quelle, nie Anweisung',
                     '.gitignore', 'Billigung je Abschnitt bleibt Pflicht', 'Keine Recherche mit Inhalten aus dem Material',
                     'md, txt, pdf, docx', 'material/'):
            self.assertIn(muss, text)

    def test_planungssitzung_hinweis_und_wiki(self):
        plan = (WURZEL / 'skills' / 'planungssitzung' / 'SKILL.md').read_text()
        self.assertIn('**Material:**', plan)
        self.assertIn('neues-projekt/SKILL.md', plan)
        self.assertIn('Material als Ausgangspunkt', (WURZEL / 'docs' / 'planen.md').read_text())

    def test_beispiel_deckt_material_ab(self):
        text = (SKILL / 'beispiel.md').read_text()
        for muss in ('## C. Mit vorhandenem Material', 'Widerspruch', 'Dritte als Quelle', 'Lücke', 'aus Material'):
            self.assertIn(muss, text)

    def test_neue_abschnitte_ohne_personennamen_der_rolle(self):
        """Skill und Beispiel nennen die Rolle (Inhaber), keinen Vornamen (S8.8; die Suche über den ganzen Export macht test_release)."""
        text = (SKILL / 'SKILL.md').read_text()
        schritt = text.split('## Schritt 3: Material sichten')[1].split('## Schritt 4:')[0]
        self.assertIn('Inhaber', schritt)
        beispiel = (SKILL / 'beispiel.md').read_text().split('## C. Mit vorhandenem Material')[1]
        self.assertIn('Inhaber', beispiel)


class SkillBetrieb(unittest.TestCase):
    def test_betriebscheckliste_ist_vollstaendig(self):
        text = (SKILL / 'SKILL.md').read_text()
        for wort in ('`test`', 'Stufen samt Freigaben', 'Migrationen samt Rückweg', 'Ablageort der Geheimnisse', '`deploy`'):
            self.assertIn(wort, text)


class Probe(VorlageBasis):
    def gespraech(self, ziel, vorhandene_masterplan=False):
        """Spielt Schritte 3 bis 6 des Skills mit festen Antworten und committet nach dem „OK des Inhabers“."""
        mp = (ziel / 'MASTERPLAN.md').read_text()
        mp = re.sub(r'\n_Vorlage: noch nicht ausgefüllt[^\n]*\n', '\n', mp)
        for titel, text in MASTERPLAN.items():
            mp = abschnitt_ersetzen(mp, titel, text)
        (ziel / 'MASTERPLAN.md').write_text(mp)
        (ziel / 'docs/entscheidungen/0001-zielumgebung.md').write_text(ENTSCHEIDUNG)
        b = (ziel / 'docs/betrieb.md').read_text()
        b = abschnitt_ersetzen(b, 'Zielumgebung', BETRIEB.split('## Deploy')[0].split('\n\n', 1)[1].rstrip())
        b = abschnitt_ersetzen(b, 'Deploy und Rollback', 'Noch kein Deploy. Deploy nur mit dem OK des Inhabers.')
        (ziel / 'docs/betrieb.md').write_text(b)
        rm = (ziel / 'ROADMAP.md').read_text()
        rm = abschnitt_ersetzen(rm, 'Vorhaben (grob)', 'Reihenfolge, noch ohne Aufgaben.\n\n- Erster Schritt (kleinster Weg bis Merge)\n- Zweiter Schritt, braucht Entscheidung 0001')
        (ziel / 'ROADMAP.md').write_text(rm)
        r = subprocess.run(['python3', str(ziel / 'werkzeuge/roadmap.py')], cwd=ziel, capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        git(ziel, 'add', '-A')   # Skill: genau die Dateien aus „Angelegt“ plus eigene Änderungen (Ziel enthält nichts Fremdes)
        git(ziel, 'commit', '-q', '-m', 'projekt: Masterplan, Grundentscheidungen, grobe Roadmap')

    def pruefen_und_abmelden(self, ziel, name):
        text = (ziel / 'ROADMAP.md').read_text()
        self.assertEqual(roadmap.kanban(text), text)
        self.assertIn('Erster Schritt', text)
        self.assertEqual([a['id'] for a in roadmap.aufgaben(text)], ['0.1', '0.2', '0.3'])  # grobe Liste ist keine Aufgabe
        mp = (ziel / 'MASTERPLAN.md').read_text()
        self.assertNotIn('noch nicht ausgefüllt', mp)
        for text_ in MASTERPLAN.values():
            self.assertIn(text_, mp)
        ent = (ziel / 'docs/entscheidungen/0001-zielumgebung.md').read_text()
        self.assertIn('**Status:** vorgeschlagen', ent)
        self.assertNotIn('bestätigt vom Inhaber', ent)
        self.assertIn('Zielumgebung', (ziel / 'docs/betrieb.md').read_text())
        self.assertIn('0001-zielumgebung', (ziel / 'docs/betrieb.md').read_text())
        self.assertEqual(git(ziel, 'status', '--porcelain'), '')
        self.assertEqual(projekte.anmelden(ziel, name), name)
        self.assertEqual(projekte.anmelden(ziel, name), name)                      # zweimal: ein Eintrag
        self.assertEqual(list(projekte.alle()), [name])
        p = Projekt(name)
        # erstes Vorhaben planbar: Entwurf wird als Commit auf der Basis abgelegt, nicht abgenommen
        self.assertEqual(plaene.entwurf(p, 'erster-schritt', PLAN), 'plan/erster-schritt.md')
        self.assertIn('Entwurf', (ziel / 'plan/erster-schritt.md').read_text())
        self.assertEqual(git(ziel, 'status', '--porcelain'), '')
        # abmelden: Eintrag aus dem (Temp-)Zustand entfernen, Repo bleibt
        datei = projekte.zustand() / 'projekte.json'
        self.assertTrue(str(datei).startswith(str(self.tmp)))
        daten = json.loads(datei.read_text())
        daten.pop(name)
        datei.write_text(json.dumps(daten))
        self.assertEqual(projekte.alle(), {})
        self.assertTrue(ziel.is_dir())

    def test_gruene_wiese(self):
        ziel = self.tmp / 'wiese'
        self.skript(ziel, 'Wiese')
        self.gespraech(ziel)
        self.pruefen_und_abmelden(ziel, 'wiese')

    def test_aus_url_lokales_bare_repo(self):
        q = self.repo('quelle', 'trunk', {'README.md': 'fremdes Projekt\n', 'src/app.py': 'print(1)\n'})
        bare = self.tmp / 'bare.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(q), str(bare)], check=True, env=self.env)
        subprocess.run(['git', '--git-dir', str(bare), 'symbolic-ref', 'HEAD', 'refs/heads/trunk'], check=True)
        ziel = self.tmp / 'klon'
        out = self.skript(ziel, 'Klon', '--url', bare).stdout
        self.assertIn('Konflikt, nicht geändert: README.md', out)
        self.assertEqual((ziel / 'README.md').read_text(), 'fremdes Projekt\n')
        self.assertEqual(git(ziel, 'log', '--format=%s'), git(q, 'log', '--format=%s'))   # Skript committet nichts
        self.gespraech(ziel)
        self.assertEqual((ziel / 'README.md').read_text(), 'fremdes Projekt\n')
        self.pruefen_und_abmelden(ziel, 'klon')

    def test_abbruch_vor_dem_ok_meldet_nichts_an(self):
        ziel = self.tmp / 'abbruch'
        self.skript(ziel, 'Abbruch')
        self.assertEqual(projekte.alle(), {})
        nachher = self.skript(ziel, 'Abbruch').stdout       # zweiter Aufruf setzt gefahrlos an
        self.assertIn('Angelegt: –', nachher)

    def test_anmeldung_ohne_roadmap_wird_abgelehnt(self):
        leer = self.repo('leer')
        with self.assertRaises(Exception) as e:
            projekte.anmelden(leer)
        self.assertIn('ROADMAP.md', str(e.exception))
        self.assertEqual(projekte.alle(), {})


if __name__ == '__main__':
    unittest.main()
