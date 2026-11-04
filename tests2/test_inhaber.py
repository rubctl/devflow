"""Der Inhaber als Einstellung (S8.8): Name oder Rolle aus `instanz.toml` in Aufträgen, Fehlermeldungen und Markern.

Die server-Instanz bleibt wortgleich, weil ihr Standard ein Vorname ist; Docs, Skills, Vorlage und Tests nennen nur die Rolle.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

from test_instanz import Basis
from devflow2 import Fehler, ablauf, auftraege, grundlage, inhaber, instanz, mcp_server, roadmap, traum, vorschlag

K = dict(id='X1', aufgabe='Titel', projekt='p', worktree='/tmp/nix', branch='b', basis='main', test='make test', plan=None, auftrag='A',
         vorgaben=['v1'], karte_auslassen=(), cwd='/tmp/c', commit='abc', vorher_commit='def', basis_commit='ghi', titel='T')
G = dict(text='GRUNDTEXT', stand={'MASTERPLAN.md': 'x'})
EREIGNIS = [dict(art='plan-rueckmeldung', bezug='plan/x.md')]


def auftraege_alle():
    """Alle Aufträge an Modelle und die Beschreibungen der MCP-Werkzeuge als ein Text je Stück."""
    k = dict(K, grundlage=G)
    out = {
        'entwickeln': auftraege.entwickeln(k), 'nacharbeit': auftraege.nacharbeit(k, 'grund', 'details'),
        'nacharbeit_frisch': auftraege.nacharbeit(k, 'grund', 'details', frisch=True), 'schwere': auftraege.schwere(G),
        'review': auftraege.review(k, 2, [dict(nr='1', schwere='P2', ort='o', was='w')], 'Sol'),
        'meinung': auftraege.meinung('p', 'plan/x.md', 'Frage?', 'Sol', G), 'planen': auftraege.planen('p', EREIGNIS, G),
        'traum_ideen': auftraege.traum_ideen('p', G), 'traum_code': auftraege.traum_code('p', G), 'traum_vorschlag': auftraege.traum_vorschlag(k),
        'steuern': auftraege.steuern('p', EREIGNIS, G), 'nicht_ohne': auftraege.nicht_ohne_inhaber(), 'frage': auftraege.frage(),
    }
    for rolle in ('entwickeln', 'nacharbeit', 'review', 'meinung', 'planen', 'steuern', 'traum'):
        out['regel_' + rolle] = auftraege._regel(rolle, G)
    out['anleitung'] = mcp_server.anleitung()
    return out


class Formen(Basis):
    def test_standard_ist_ein_name_das_arbeitsprofil_die_rolle(self):
        self.assertTrue(inhaber.name())
        self.assertEqual(instanz.SERVER['inhaber'], inhaber.name())
        self.toml('profil = "arbeit"\n')
        self.assertTrue(inhaber.ist_rolle())
        self.assertEqual((inhaber.nom(), inhaber.akk(), inhaber.dat(), inhaber.von(), inhaber.an(), inhaber.zu()),
                         ('der Inhaber', 'den Inhaber', 'dem Inhaber', 'vom Inhaber', 'an den Inhaber', 'zum Inhaber'))

    def test_name_bleibt_in_jedem_fall_gleich(self):
        self.toml('inhaber = "Anna"\n')
        self.assertFalse(inhaber.ist_rolle())
        self.assertEqual((inhaber.nom(), inhaber.akk(), inhaber.dat(), inhaber.von(), inhaber.an(), inhaber.fuer(), inhaber.bei(), inhaber.zu()),
                         ('Anna', 'Anna', 'Anna', 'von Anna', 'an Anna', 'für Anna', 'bei Anna', 'zu Anna'))
        self.assertEqual((inhaber.besitz('Abnahmezeile', 'die'), inhaber.namens(), inhaber.frage_marker(), inhaber.kennung()),
                         ('Annas Abnahmezeile', 'in Annas Namen', 'FRAGE AN ANNA:', 'Anna'))
        self.toml('inhaber = "Hans"\n')
        self.assertEqual(inhaber.besitz('Wort', 'das'), "Hans' Wort")

    def test_rolle_wird_gebeugt(self):
        self.toml('inhaber = "der Inhaber"\n')
        self.assertEqual((inhaber.nom_gross(), inhaber.von_gross(), inhaber.bei(), inhaber.kennung(), inhaber.frage_marker()),
                         ('Der Inhaber', 'Vom Inhaber', 'beim Inhaber', 'Inhaber', 'FRAGE AN DEN INHABER:'))
        self.assertEqual((inhaber.besitz('Abnahmezeile', 'die'), inhaber.besitz('Antwort', 'der'), inhaber.namens()),
                         ('die Abnahmezeile des Inhabers', 'der Antwort des Inhabers', 'im Namen des Inhabers'))

    def test_leerer_oder_falscher_wert_wird_abgelehnt(self):
        for roh in ('inhaber = ""\n', 'inhaber = 3\n'):
            self.toml(roh)
            with self.assertRaisesRegex(Fehler, 'inhaber'):
                instanz.laden()
        self.toml('inhaber = "Anna"\n')
        self.assertEqual(instanz.wert('inhaber'), 'Anna')


class Auftraege(Basis):
    def test_name_fliesst_nur_ueber_die_formen_in_die_texte(self):
        """Bei einem Namen heißt es überall dieser Name, nie die Rolle: ein anderer Name ergibt denselben Text mit anderem Namen."""
        self.toml('inhaber = "Anna"\n')
        anna = auftraege_alle()
        self.toml('inhaber = "Berta"\n')
        berta = auftraege_alle()
        self.assertEqual(anna.keys(), berta.keys())
        for name, text in anna.items():
            with self.subTest(name):
                self.assertNotIn('nhaber', text.replace('Inhaber-', ''))
                self.assertNotIn('INHABER', text)
                self.assertEqual(berta[name], text.replace('ANNA', 'BERTA').replace('Annas', 'Bertas').replace('Anna', 'Berta'))
        self.assertIn('FRAGE AN ANNA:', anna['entwickeln'])
        self.assertIn('Abnehmen darf nur Anna', anna['steuern'])
        self.assertIn('Nicht ohne Anna:', anna['steuern'])
        self.assertIn('keinen Plan ohne Annas Abnahmezeile übernehmen, keine Antwort in Annas Namen geben', anna['steuern'])

    def test_rolle_ergibt_grammatisch_gebeugte_saetze(self):
        self.toml('inhaber = "Inhaber"\n')
        alle = auftraege_alle()
        text = '\n'.join(alle.values())
        self.assertNotRegex(text, r'(?i)\banna\b')
        for satz in ('FRAGE AN DEN INHABER:', 'Nur wenn etwas wirklich den Inhaber betrifft', 'bestätigt vom Inhaber', 'Nicht ohne den Inhaber:',
                     'Abnehmen darf nur der Inhaber', 'keinen Plan ohne die Abnahmezeile des Inhabers übernehmen, keine Antwort im Namen des Inhabers geben',
                     'Rückmeldung Inhaber', 'Fragen an den Inhaber', 'Punkt für den Inhaber', 'Der Inhaber plant, entscheidet und nimmt ab',
                     'Dies ist ein Traum: Der Inhaber hat ihn', 'hebt nur der Inhaber dort auf'):
            self.assertIn(satz, text)
        for falsch in ('der Inhabers', 'den Inhabers', 'zu dem Inhaber', 'nach die ', 'mit Inhaber', 'an Inhaber', 'von Inhaber', '. der Inhaber', '; Der Inhaber'):
            self.assertNotIn(falsch, text)

    def test_ausfuehrung_erkennt_die_frage_in_beiden_formen(self):
        for roh, marker in (('inhaber = "Anna"\n', 'FRAGE AN ANNA:'), ('inhaber = "Inhaber"\n', 'FRAGE AN DEN INHABER:')):
            self.toml(roh)
            self.assertEqual(auftraege.frage(), marker)
            self.assertIn(f'„{marker}“', auftraege.entwickeln(dict(K, grundlage=None)))

    def test_frage_marker_alle_nennt_eigene_form_und_rolle(self):
        for roh, erwartet in (('inhaber = "Anna"\n', ('FRAGE AN ANNA:', 'FRAGE AN DEN INHABER:')), ('inhaber = "Inhaber"\n', ('FRAGE AN DEN INHABER:',))):
            self.toml(roh)
            self.assertEqual(inhaber.frage_marker_alle(), erwartet)

    def test_mcp_beschreibungen_nennen_den_inhaber(self):
        """Die Beschreibungen entstehen beim Laden des Servers; hier wird die Ersetzung selbst geprüft und, dass keine Platzhalter bleiben."""
        for t in asyncio.run(mcp_server.server.list_tools()):
            self.assertNotRegex(t.description, r'\{(nom|an|zu|besitz_antwort)\}', t.name)
        vorlagen = {n: f.__doc__ for n, f in ((n, getattr(mcp_server, n).__wrapped__) for n in ('plan_entwurf', 'frage_stellen', 'nacharbeit', 'anhalten', 'pflege_aufgabe'))}
        for roh, wort in (('inhaber = "Anna"\n', ['nur Anna:', 'Frage an Anna in', 'nach Annas Antwort', 'bis Anna im', 'zu Anna.']),
                          ('inhaber = "Inhaber"\n', ['nur der Inhaber:', 'Frage an den Inhaber in', 'nach der Antwort des Inhabers', 'bis der Inhaber im', 'zum Inhaber.'])):
            self.toml(roh)
            text = ' '.join(mcp_server._beschreibung(d) for d in vorlagen.values())
            for w in wort:
                self.assertIn(w, text)

    def test_fehlermeldungen_nennen_den_inhaber(self):
        self.toml('inhaber = "Anna"\n')
        self.assertIn('Den globalen Halt hebt nur Anna auf', ablauf.halt_nur_dashboard())
        self.assertIn('bis Anna im Dashboard', vorschlag.nicht_uebernommen('V1'))
        self.assertEqual(traum.inhaber_beendet(), 'Von Anna beendet')
        self.assertIn('nur Anna ändert', grundlage.gesperrt_frage('A1', ['MASTERPLAN.md'])[0])
        self.toml('inhaber = "Inhaber"\n')
        self.assertIn('hebt nur der Inhaber auf', ablauf.halt_nur_dashboard())
        self.assertIn('bis der Inhaber im Dashboard', vorschlag.nicht_uebernommen('V1'))
        self.assertEqual(traum.inhaber_beendet(), 'Vom Inhaber beendet')
        self.assertIn('nur der Inhaber ändert', grundlage.gesperrt_frage('A1', ['MASTERPLAN.md'])[0])


PLAN = '# Plan\n\n**Stand:** Entwurf\n\n| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium |\n|---|---|---|---|---|\n| A1 | Eins | claude | x | y |\n'
ROADMAP = '# R\n\n## Offene Fragen {wen}\n\n_Keine._\n\n## Protokoll\n'


class Marker(Basis):
    """Geschrieben wird in der Form der Instanz; gelesen wird sie und immer auch die Rollenform (Vorlagen und Docs nennen die Rolle)."""

    def test_abnahme_in_beiden_formen(self):
        for roh in ('inhaber = "Anna"\n', 'inhaber = "Inhaber"\n'):
            self.toml(roh)
            neu = roadmap.plan_abnehmen(PLAN, '04.11.2026', {})
            self.assertIn(f'**Stand:** abgenommen {inhaber.von()} am 04.11.2026', neu)
            self.assertEqual(roadmap.plan(neu)['abgenommen'], '04.11.2026')
            self.assertEqual(roadmap.plan(PLAN.replace('Entwurf', 'abgenommen vom Inhaber am 01.01.2026'))['abgenommen'], '01.01.2026')
        self.toml('inhaber = "Anna"\n')
        self.assertEqual(roadmap.plan(PLAN.replace('Entwurf', 'abgenommen von Anna am 01.01.2026'))['abgenommen'], '01.01.2026')
        self.assertIsNone(roadmap.plan(PLAN.replace('Entwurf', 'abgenommen von Sol am 01.01.2026'))['abgenommen'])
        with self.assertRaisesRegex(Fehler, 'schon abgenommen'):
            roadmap.plan_abnehmen(PLAN.replace('Entwurf', 'abgenommen vom Inhaber am 01.01.2026'), '04.11.2026', {})

    def test_bindende_entscheidung_in_beiden_formen(self):
        self.toml('inhaber = "Anna"\n')
        for status in ('bestätigt von Anna', 'bestätigt vom Inhaber am 01.11.2026'):
            self.assertTrue(grundlage.BINDEND.fullmatch(status), status)
        for status in ('bestätigt von Berta', 'bestätigt von Sol', 'vorgeschlagen'):
            self.assertFalse(grundlage.BINDEND.fullmatch(status), status)

    def test_frage_und_antwort_in_beiden_formen(self):
        for roh, wen in (('inhaber = "Anna"\n', 'Anna'), ('inhaber = "Inhaber"\n', 'den Inhaber')):
            for abschnitt in dict.fromkeys((f'an {wen}', 'an den Inhaber')):
                self.toml(roh)
                t, nr = roadmap.frage(ROADMAP.format(wen=abschnitt), '04.11.', 'Wie?', ['a'], 'a', 'A1')
                self.assertIn(roadmap.offen_marker(), t.split('\n'))
                self.assertEqual([f['nr'] for f in roadmap.fragen(t)], [nr])
                t2, bezug = roadmap.antwort(t, nr, '04.11.', 'Ja', 'dashboard')
                self.assertIn(f'**Antwort {inhaber.kennung()} (04.11., dashboard):** Ja', t2)
                self.assertEqual(roadmap.fragen(t2), [])
        # eine Frage mit der Markierung der anderen Form ist ebenfalls offen und lässt sich beantworten
        self.toml('inhaber = "Anna"\n')
        t, nr = roadmap.frage(ROADMAP.format(wen='an Anna'), '04.11.', 'Wie?', [], '', 'A1')
        alt = t.replace(roadmap.offen_marker(), '**Antwort Inhaber:** _offen_')
        self.assertEqual([f['offen'] for f in roadmap.fragen(alt)], [True])
        self.assertIn('**Antwort Anna (', roadmap.antwort(alt, nr, '04.11.', 'Ja', 'dashboard')[0])
        with self.assertRaisesRegex(Fehler, 'Abschnitt „Offene Fragen an Anna“ fehlt'):
            roadmap.frage('# R\n', '04.11.', 'Wie?', [], '')

    def test_rueckmeldung_in_beiden_formen(self):
        self.toml('inhaber = "Anna"\n')
        neu = roadmap.plan_rueckmeldung(PLAN, '04.11.2026', 'Bitte teilen')
        self.assertIn('## Rückmeldung Anna', neu)
        self.assertTrue(roadmap.hat_rueckmeldung(neu))
        alt = PLAN + '\n## Rückmeldung Inhaber\n\n- **01.01.2026:** alt\n'
        self.assertTrue(roadmap.hat_rueckmeldung(alt))
        mit = roadmap.plan_rueckmeldung(alt, '04.11.2026', 'Bitte teilen')
        self.assertEqual(mit.count('## Rückmeldung'), 1)
        self.assertIn('- **04.11.2026:** Bitte teilen', mit)


class Skills(Basis):
    """Beide Skills nennen den Inhaber als Rolle und sagen, wie der eingestellte Name zu ermitteln ist (Abnahme 1)."""
    WURZEL = Path(__file__).resolve().parent.parent

    def befehl(self, skill):
        text = (self.WURZEL / 'skills' / skill / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('**Ansprache:**', text)
        m = re.search(r'python3 -B -c "([^"]+)"', text)
        self.assertIsNotNone(m, skill)
        return m.group(1)

    def ermittelt(self, skill):
        env = dict(os.environ, PYTHONPATH=str(self.WURZEL))
        return subprocess.run([sys.executable, '-B', '-c', self.befehl(skill)], env=env, capture_output=True, text=True, check=True).stdout.strip()

    def test_befehl_liefert_eingestellten_namen_oder_rolle(self):
        for skill in ('planungssitzung', 'neues-projekt'):
            with self.subTest(skill=skill):
                self.toml('inhaber = "Anna"\n')
                self.assertEqual(self.ermittelt(skill), 'Anna')
                self.toml('inhaber = "der Inhaber"\n')
                self.assertTrue(inhaber.ist_rolle(self.ermittelt(skill)))
                self.toml('')
                self.assertEqual(self.ermittelt(skill), inhaber.name())

    def test_skill_nennt_marker_in_beiden_formen(self):
        text = (self.WURZEL / 'skills' / 'planungssitzung' / 'SKILL.md').read_text(encoding='utf-8')
        for form in ('bestätigt vom Inhaber', 'bestätigt von <Name>', 'abgenommen von <Name>'):
            self.assertIn(form, text)


if __name__ == '__main__':
    unittest.main()
