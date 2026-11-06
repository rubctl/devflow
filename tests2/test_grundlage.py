"""S9.1: Masterplan und Grundentscheidungen in Aufträgen, Schwere, Merge-Tor, Stand und Lage."""
from pathlib import Path
import unittest
from unittest import mock

from devflow2 import Fehler, ablauf, auftraege, grundlage, laeufe, roadmap, traum, inhaber
from hilfen import Fall, git

MASTERPLAN = """# Masterplan demo

## Wofür
Wetter für Pendler.

## Kernzusagen
- Die Temperatur in der Kachel stimmt immer mit der Quelle überein.

## Prüfvorrang
Fehler in der Anzeige wiegen schwerer als Optik.
"""


def entscheidung(nr, titel, status, betrifft=None, text='Wir nehmen SQLite.'):
    return (f'# {nr} · {titel}\n\n**Datum:** 01.11.2026 · **Entschieden von:** Inhaber\n**Status:** {status}\n'
            + (f'**Betrifft:** {betrifft}\n' if betrifft else '') + f'\n## Entscheidung\n\n{text}\n')


class Vorgaben(Fall):
    def fest(self, dateien, meldung='Vorgaben'):
        """Dateien auf main committen, wie der Inhaber es täte."""
        for name, inhalt in dateien.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(inhalt)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', meldung)

    def alle_vorgaben(self):
        self.fest({
            'MASTERPLAN.md': MASTERPLAN,
            'docs/architektur.md': '# Architektur\n\n## Überblick\n\nEine Flask-App mit SQLite.\n\n## Bausteine\n\nGeheim-Baustein.\n',
            'docs/entscheidungen/0001-wetterquelle.md': entscheidung('0001', 'Wetterquelle', 'bestätigt vom Inhaber am 01.11.2026', 'wetter',
                                                                   'BESTAETIGT-WETTER-TEXT'),
            'docs/entscheidungen/0002-datenbank.md': entscheidung('0002', 'Datenbank', 'bestätigt vom Inhaber', 'datenbank, migrations/',
                                                                'BESTAETIGT-DB-TEXT'),
            'docs/entscheidungen/0003-sprache.md': entscheidung('0003', 'Sprache', 'vorgeschlagen', 'wetter', 'VORGESCHLAGEN-TEXT'),
            'docs/entscheidungen/0004-alt.md': entscheidung('0004', 'Alte Quelle', 'ersetzt durch 0001', 'wetter', 'ERSETZT-TEXT'),
        })

    def uebernehmen(self):
        ablauf.plan_uebernehmen(self.p, 'plan/wetter.md')

    def bis_review(self, dateien=None, review=None):
        """Entwickeln und Review von Hand, Merge offen."""
        self.uebernehmen()
        self.drehbuch(claude=[{'tue': 'commit', **({'dateien': dateien} if dateien else {})}], codex=[review or self.bestanden()])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.ruhig()


class EntscheidungTest(unittest.TestCase):
    def test_nur_bestaetigt_bindet(self):
        e = lambda s: grundlage.entscheidung('docs/entscheidungen/0001-x.md', entscheidung('0001', 'X', s))
        self.assertTrue(e('bestätigt vom Inhaber')['bindend'])
        self.assertTrue(e('bestätigt vom Inhaber am 01.11.2026')['bindend'])
        self.assertTrue(e(f'bestätigt {inhaber.von()}')['bindend'])  # die Form der Instanz gilt auch
        for s in ('vorgeschlagen', 'ersetzt durch 0002', 'nicht bestätigt vom Inhaber', 'bestätigt von Sol', ''):
            self.assertFalse(e(s)['bindend'], s)

    def test_status_nur_im_kopf_und_ohne_zeile_unverbindlich(self):
        im_text = '# 0001 · X\n\n**Status:** vorgeschlagen\n\n## Entscheidung\n\n**Status:** bestätigt vom Inhaber\n'
        self.assertFalse(grundlage.entscheidung('x/0001-x.md', im_text)['bindend'])
        self.assertFalse(grundlage.entscheidung('x/0001-x.md', '# 0001 · X\n\n## Entscheidung\n\n**Status:** bestätigt vom Inhaber\n')['bindend'])
        self.assertFalse(grundlage.entscheidung('x/0001-x.md', '# 0001 · X\n\nOhne Status.\n')['bindend'])


class AuftragsTest(Vorgaben):
    def test_ohne_dateien_nichts_im_auftrag_und_lage_zeigt_hinweis(self):
        g = grundlage.fuer(self.p, 'Wetter')
        self.assertEqual(g, dict(text='', stand={}))
        self.uebernehmen()
        self.drehbuch(claude=[{'tue': 'commit'}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.ruhig()
        for text in (self.aufrufe('claude')[0]['argv'][-1], self.aufrufe('codex')[0]['argv'][-1]):
            self.assertNotIn('Masterplan', text)
            self.assertNotIn('Grundentscheidung', text)
        self.assertIn('Im Zweifel P3', self.aufrufe('codex')[0]['argv'][-1])
        self.assertNotIn('mindestens P2, auch als Randfall', self.aufrufe('codex')[0]['argv'][-1])
        self.assertEqual(ablauf.lage()['projekte']['demo']['hinweise'], ['kein Masterplan'])

    def test_nur_vorgeschlagene_und_ersetzte_entscheidungen_ergeben_nichts(self):
        self.fest({'docs/entscheidungen/0003-sprache.md': entscheidung('0003', 'Sprache', 'vorgeschlagen'),
                   'docs/entscheidungen/0004-alt.md': entscheidung('0004', 'Alt', 'ersetzt durch 0003')})
        self.assertEqual(grundlage.fuer(self.p, 'Wetter'), dict(text='', stand={}))

    def test_masterplan_und_relevante_entscheidungen_in_jedem_auftrag(self):
        self.alle_vorgaben()
        self.uebernehmen()
        self.drehbuch(claude=[{'tue': 'commit'}, {'tue': 'commit', 'dateien': {'n.txt': 'x'}}], codex=[self.bestanden(), {'tue': 'text', 'text': '# Zweitmeinung Sol · plan/wetter.md\n\nTragfähig.'}])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        ablauf.nacharbeit(self.p, 'A1', 'Bitte Randfall', automatisch=False)
        self.ruhig()
        ablauf.review_anfordern(self.p, 'A1', automatisch=False)
        self.ruhig()
        ablauf.meinung_anfordern(self.p, 'plan/wetter.md', 'Passt das?')
        self.ruhig()
        claude, codex = self.aufrufe('claude'), self.aufrufe('codex')
        texte = {'entwickeln': claude[0]['argv'][-1], 'nacharbeit': claude[1]['argv'][-1], 'review': codex[0]['argv'][-1], 'meinung': codex[1]['argv'][-1]}
        for rolle, t in texte.items():
            with self.subTest(rolle):
                self.assertIn('Die Temperatur in der Kachel stimmt immer', t)
                self.assertIn('Eine Flask-App mit SQLite', t)          # Überblick
                self.assertNotIn('Geheim-Baustein', t)                  # nur der Überblick, nicht die ganze Seite
                self.assertIn('BESTAETIGT-WETTER-TEXT', t)              # berührt die Aufgabe „Wetter-Kachel“
                self.assertNotIn('BESTAETIGT-DB-TEXT', t)               # betrifft die Aufgabe nicht: nur der Titel
                self.assertIn('0002-datenbank.md: 0002 · Datenbank', t)
                self.assertNotIn('VORGESCHLAGEN-TEXT', t)
                self.assertNotIn('ERSETZT-TEXT', t)
        self.assertIn(inhaber.frage_marker(), texte['entwickeln'])
        self.assertIn(inhaber.frage_marker(), texte['nacharbeit'])
        self.assertIn('mindestens P2, auch als Randfall', texte['review'])
        self.assertIn('Im Zweifel P3', texte['review'])
        self.assertIn('neue Grundentscheidung', texte['meinung'])

    def test_auftraege_ohne_aufgabe_planen_steuern_traum(self):
        self.alle_vorgaben()
        g = grundlage.fuer(self.p)
        self.assertNotIn('BESTAETIGT-WETTER-TEXT', g['text'])           # ohne Aufgabe nur die Titel
        self.assertIn('0001-wetterquelle.md: 0001 · Wetterquelle', g['text'])
        for t in (auftraege.planen('demo', [dict(art='neue-idee', bezug='x')], g), auftraege.steuern('demo', [dict(art='lauf-ende', bezug='x')], g),
                  auftraege.traum_ideen('demo', g), auftraege.traum_code('demo', g), auftraege.traum_rueckblick('fakten', g),
                  auftraege.traum_vorschlag(dict(projekt='demo', worktree='/w', branch='b', basis='main', titel='T', auftrag='A', grundlage=g))):
            self.assertIn('Die Temperatur in der Kachel stimmt immer', t)
            self.assertIn('0002-datenbank.md: 0002 · Datenbank', t)
            self.assertNotIn('ERSETZT-TEXT', t)
        self.assertIn('Kernzusage', auftraege.planen('demo', [], g))

    def test_auswahl_ohne_betrifft_ist_vollstaendig_und_alle_gilt_immer(self):
        self.fest({'docs/entscheidungen/0001-a.md': entscheidung('0001', 'Ohne', 'bestätigt vom Inhaber', text='OHNE-BETRIFFT'),
                   'docs/entscheidungen/0002-b.md': entscheidung('0002', 'Alle', 'bestätigt vom Inhaber', 'alle', 'ALLE-TEXT')})
        self.assertIn('OHNE-BETRIFFT', grundlage.fuer(self.p, 'irgendwas')['text'])
        ohne_aufgabe = grundlage.fuer(self.p)['text']  # Planen, Steuern, Traum: eine allgemeine Entscheidung gilt auch hier vollständig
        self.assertIn('ALLE-TEXT', ohne_aufgabe)
        self.assertIn('OHNE-BETRIFFT', ohne_aufgabe)

    def test_eingegrenzte_entscheidung_ohne_aufgabentext_nur_als_titel(self):
        self.fest({'docs/entscheidungen/0001-a.md': entscheidung('0001', 'Eng', 'bestätigt vom Inhaber', 'datenbank', 'ENG-TEXT')})
        t = grundlage.fuer(self.p)['text']
        self.assertNotIn('ENG-TEXT', t)
        self.assertIn('0001-a.md: 0001 · Eng', t)

    def test_zusatzauftrag_waehlt_die_entscheidungen_in_entwicklung_und_nacharbeit(self):
        self.fest({'docs/entscheidungen/0001-speicher.md': entscheidung('0001', 'Speicher', 'bestätigt vom Inhaber', 'app/speicher.py', 'SPEICHER-TEXT')})
        self.uebernehmen()
        self.drehbuch(claude=[{'tue': 'commit'}, {'tue': 'commit', 'dateien': {'n.txt': 'x'}}])
        ablauf.entwickeln(self.p, 'A1', auftrag='Ändere app/speicher.py', automatisch=False)
        self.ruhig()
        ablauf.nacharbeit(self.p, 'A1', 'Bitte Randfall', automatisch=False)
        self.ruhig()
        for t in (a['argv'][-1] for a in self.aufrufe('claude')):
            self.assertIn('SPEICHER-TEXT', t)

    def test_kein_zusatzauftrag_dann_nur_der_titel(self):
        self.fest({'docs/entscheidungen/0001-speicher.md': entscheidung('0001', 'Speicher', 'bestätigt vom Inhaber', 'app/speicher.py', 'SPEICHER-TEXT')})
        self.uebernehmen()
        self.drehbuch(claude=[{'tue': 'commit'}])
        ablauf.entwickeln(self.p, 'A1', automatisch=False)
        self.ruhig()
        self.assertNotIn('SPEICHER-TEXT', self.aufrufe('claude')[0]['argv'][-1])

    def test_entscheidung_nur_von_der_basis_nicht_aus_dem_arbeitsverzeichnis(self):
        (self.repo / 'MASTERPLAN.md').write_text(MASTERPLAN)  # nicht committet
        self.assertEqual(grundlage.fuer(self.p, 'x')['text'], '')


class LageTest(Vorgaben):
    def test_hinweise(self):
        self.assertEqual(grundlage.hinweise(self.p), ['kein Masterplan'])
        self.fest({'MASTERPLAN.md': MASTERPLAN})
        self.assertEqual(grundlage.hinweise(self.p), [])
        self.assertNotIn('hinweise', ablauf.lage()['projekte']['demo'])
        self.fest({'MASTERPLAN.md': MASTERPLAN + ''.join(f'- Zeile {i}\n' for i in range(grundlage.GRENZE_ZEILEN))})
        h = grundlage.hinweise(self.p)
        self.assertEqual(len(h), 1)
        self.assertIn('zu lang', h[0])
        self.assertIn('zu lang', ablauf.lage()['projekte']['demo']['hinweise'][0])

    def test_dashboard_zeigt_den_hinweis(self):
        from devflow2 import web_lage
        text = self.roadmap()
        karte = web_lage.projekt_karte('demo', self.p, text, roadmap.aufgaben(text), [], [])
        self.assertIn('kein Masterplan', str(karte))


class MergeTorTest(Vorgaben):
    def test_branch_mit_geaendertem_masterplan_wird_nicht_gemergt_sondern_frage(self):
        self.fest({'MASTERPLAN.md': MASTERPLAN})
        self.bis_review({'MASTERPLAN.md': MASTERPLAN + '- Neue Zusage\n'})
        with self.assertRaises(ablauf.FrageOffen):
            ablauf.mergen(self.p, 'A1')
        self.assertEqual(self.zeile('A1')[3], 'Braucht dich')
        self.assertIn(f'nur {inhaber.nom()} ändert', self.roadmap())
        self.assertIn('MASTERPLAN.md', roadmap.fragen(self.roadmap())[0]['text'])
        self.assertNotIn('Neue Zusage', (self.repo / 'MASTERPLAN.md').read_text())
        self.assertFalse([l for l in laeufe.alle() if l['art'] == 'mergen'])

    def test_auch_nach_rebase_gesperrt_und_ohne_eigene_aenderung_frei(self):
        self.fest({'MASTERPLAN.md': MASTERPLAN})
        self.bis_review({'MASTERPLAN.md': MASTERPLAN + '- Neue Zusage\n'})
        wt = self.tmp / 'demo-claude-a1'
        self.fest({'anderes.txt': 'x'})  # main läuft weiter
        git(wt, 'rebase', '-q', 'main')
        self.assertEqual(len(grundlage.gesperrte_aenderungen(self.p, 'claude-a1')), 1)
        # Der Inhaber übernimmt dieselbe Änderung selbst auf main: der Branch bringt dann nichts Eigenes mehr mit
        self.fest({'MASTERPLAN.md': MASTERPLAN + '- Neue Zusage\n'})
        git(wt, 'rebase', '-q', 'main')
        self.assertEqual(grundlage.gesperrte_aenderungen(self.p, 'claude-a1'), [])

    def test_mergelauf_selbst_prueft_nach_dem_rebase(self):
        self.fest({'MASTERPLAN.md': MASTERPLAN})
        self.bis_review({'MASTERPLAN.md': MASTERPLAN + '- Neue Zusage\n'})
        z = ablauf.zweig(self.p, 'A1')
        l = laeufe.starten(self.p, 'mergen', 'intern', self.p.repo, aufgabe='A1', branch=z['branch'], worktree=z['worktree'],
                           entwickler=z['entwickler'], automatisch=False)
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler')
        self.assertIn('kein Merge', laeufe.laden(l['id'])['fehler'])
        self.assertNotIn('Neue Zusage', (self.repo / 'MASTERPLAN.md').read_text())
        self.assertNotIn('arbeit.txt', git(self.repo, 'ls-files'))
        self.assertEqual(len(roadmap.fragen(self.roadmap())), 1)

    def test_bestaetigte_entscheidung_nur_vom_inhaber_vorschlag_darf_ein_lauf(self):
        self.fest({'docs/entscheidungen/0001-a.md': entscheidung('0001', 'A', 'bestätigt vom Inhaber')})
        self.bis_review({'docs/entscheidungen/0001-a.md': entscheidung('0001', 'A', 'ersetzt durch 0002'),
                         'docs/entscheidungen/0002-b.md': entscheidung('0002', 'B', 'bestätigt vom Inhaber')})
        self.assertEqual(len(grundlage.gesperrte_aenderungen(self.p, 'claude-a1')), 2)
        with self.assertRaises(ablauf.FrageOffen):
            ablauf.mergen(self.p, 'A1')

    def test_vorschlag_einer_entscheidung_geht_durch(self):
        self.bis_review({'docs/entscheidungen/0001-a.md': entscheidung('0001', 'A', 'vorgeschlagen')})
        self.assertEqual(grundlage.gesperrte_aenderungen(self.p, 'claude-a1'), [])
        ablauf.mergen(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')

    def test_ohne_vorgaben_merge_wie_bisher(self):
        self.bis_review()
        ablauf.mergen(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')


class StandTest(Vorgaben):
    def vorgaben_ok(self):
        """Das Vorgabentor lässt durch; danach greift nur, dass main fachlich weiter ist (S14.2: neues Review statt Merge)."""
        with self.assertRaisesRegex(ablauf.NeuPruefen, 'fachlich geändert'):
            ablauf._merge_pruefen(self.p, 'claude-a1', 'claude', 'A1')

    def test_lauf_haelt_den_bestaetigten_stand_fest(self):
        self.alle_vorgaben()
        self.bis_review()
        stand = grundlage.stand(self.p)
        self.assertEqual(set(stand), {'MASTERPLAN.md', 'docs/entscheidungen/0001-wetterquelle.md', 'docs/entscheidungen/0002-datenbank.md'})
        for l in laeufe.alle():
            if l['art'] in ('entwickeln', 'review'):
                self.assertEqual(l['grundlage_stand'], stand)

    def test_geaenderter_masterplan_nach_dem_review_fuehrt_zur_pruefung_vor_dem_merge(self):
        self.alle_vorgaben()
        self.bis_review()
        self.fest({'MASTERPLAN.md': MASTERPLAN + '- Neue Kernzusage: Daten bleiben im Land.\n'})
        with self.assertRaisesRegex(Fehler, 'Vorgaben haben sich seit dem Review geändert.*MASTERPLAN.md: geändert'):
            ablauf.mergen(self.p, 'A1', automatisch=False)
        self.assertFalse([l for l in laeufe.alle() if l['art'] == 'mergen'])
        self.drehbuch(codex=[self.bestanden()])
        ablauf.mergen(self.p, 'A1')   # automatisch: Prüfung statt Merge, danach Merge
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        self.assertEqual([l['art'] for l in laeufe.alle()][-4:], ['review', 'vorbereiten', 'review', 'mergen'])  # main ist fachlich weiter: neu vorbereitet
        pruefung = self.aufrufe('codex')[-1]['argv'][-1]
        self.assertIn('Prüfung vor dem Merge', pruefung)
        self.assertIn('Daten bleiben im Land', pruefung)
        self.assertIn('MASTERPLAN.md: geändert', pruefung)
        self.assertNotIn('Prüfung vor dem Merge', self.aufrufe('codex')[0]['argv'][-1])

    def test_geaenderter_masterplan_waehrend_der_mergelauf_wartet(self):
        """S14.2 M1: Ein unterbrochener Mergelauf, fortgesetzt nach einer Änderung am Masterplan, geht zurück zu Vorbereitung und Prüfung."""
        self.alle_vorgaben()
        self.bis_review()
        (self.repo / 'ROADMAP.md').write_text(self.roadmap() + '\nvon Hand\n')
        l = ablauf.mergen(self.p, 'A1')
        self.warten(lambda: laeufe.laden(l['id'])['zustand'] == 'fehler')
        git(self.repo, 'checkout', '--', 'ROADMAP.md')
        self.fest({'MASTERPLAN.md': MASTERPLAN + '- Neue Kernzusage: Daten bleiben im Land.\n'})
        self.drehbuch(codex=[self.bestanden()])
        ablauf.fortsetzen(l['id'])
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        m = laeufe.laden(l['id'])
        self.assertIn('MASTERPLAN.md: geändert', m['neu_pruefen'])
        self.assertIsNone(m.get('merge'))
        self.assertEqual([x['art'] for x in laeufe.alle()][-4:], ['mergen', 'vorbereiten', 'review', 'mergen'])
        pruefung = self.aufrufe('codex')[-1]['argv'][-1]
        self.assertIn('Prüfung vor dem Merge', pruefung)
        self.assertIn('MASTERPLAN.md: geändert', pruefung)

    def test_neu_bestaetigte_entscheidung_loest_pruefung_aus_vorschlag_nicht(self):
        self.alle_vorgaben()
        self.bis_review()
        self.fest({'docs/entscheidungen/0005-neu.md': entscheidung('0005', 'Neu', 'vorgeschlagen')})
        self.vorgaben_ok()   # Vorschlag bindet nicht
        self.fest({'docs/entscheidungen/0005-neu.md': entscheidung('0005', 'Neu', 'bestätigt vom Inhaber')})
        with self.assertRaisesRegex(Fehler, '0005-neu.md: neu'):
            ablauf._merge_pruefen(self.p, 'claude-a1', 'claude', 'A1')
        self.fest({'docs/entscheidungen/0005-neu.md': entscheidung('0005', 'Neu', 'ersetzt durch 0006')})
        self.vorgaben_ok()   # wieder wie beim Review

    def test_zurueckgenommene_entscheidung_zaehlt_als_aenderung(self):
        self.alle_vorgaben()
        self.bis_review()
        self.fest({'docs/entscheidungen/0002-datenbank.md': entscheidung('0002', 'Datenbank', 'ersetzt durch 0009')})
        with self.assertRaisesRegex(Fehler, 'nicht mehr bestätigt'):
            ablauf._merge_pruefen(self.p, 'claude-a1', 'claude', 'A1')

    def test_review_ohne_gemerkten_stand_gilt_als_unveraendert(self):
        self.bis_review()
        r = [l for l in laeufe.alle() if l['art'] == 'review'][0]
        r.pop('grundlage_stand')
        laeufe.speichern(r)
        self.fest({'MASTERPLAN.md': MASTERPLAN})
        self.vorgaben_ok()


class TraumStandTest(Vorgaben):
    def test_traumlaeufe_halten_den_stand_fest(self):
        self.alle_vorgaben()
        t = dict(id='t1', fenster_ende=9e9)
        gesehen = {}
        with mock.patch.object(laeufe, 'starten', side_effect=lambda p, art, modell, cwd, prompt, **kw: [gesehen.setdefault(kw.get('traum_art'), kw), dict(id='x')][1]):
            for art in ('ideen', 'code', 'rueckblick'):
                traum._lauf_starten(t, dict(art=art, projekt='demo'))
        self.assertEqual(gesehen['ideen']['grundlage_stand'], grundlage.stand(self.p))
        self.assertEqual(gesehen['code']['grundlage_stand'], grundlage.stand(self.p))
        erwartet = {f'demo: {d}': h for d, h in grundlage.stand(self.p).items()}
        self.assertEqual(gesehen['rueckblick']['grundlage_stand'], erwartet)
        self.assertIn('docs/entscheidungen/0001-wetterquelle.md', ' '.join(erwartet))
        self.assertNotIn('0003-sprache', ' '.join(erwartet))

    def test_rueckblick_stand_aendert_sich_mit_bestaetigter_entscheidung(self):
        self.alle_vorgaben()
        vorher = grundlage.fuer_rueckblick(['demo'])
        self.assertIn('0002-datenbank.md: 0002 · Datenbank', vorher['text'])
        self.assertNotIn('BESTAETIGT-DB-TEXT', vorher['text'])
        self.assertEqual(grundlage.fuer_rueckblick(['demo'])['stand'], vorher['stand'])
        self.fest({'docs/entscheidungen/0002-datenbank.md': entscheidung('0002', 'Datenbank', 'bestätigt vom Inhaber', 'datenbank', 'ANDERER-TEXT')})
        nachher = grundlage.fuer_rueckblick(['demo'])['stand']
        self.assertNotEqual(nachher, vorher['stand'])
        self.assertNotEqual(nachher['demo: docs/entscheidungen/0002-datenbank.md'], vorher['stand']['demo: docs/entscheidungen/0002-datenbank.md'])
        self.assertEqual(nachher['demo: MASTERPLAN.md'], vorher['stand']['demo: MASTERPLAN.md'])

    def test_rueckblick_nur_mit_entscheidung_ohne_masterplan(self):
        self.fest({'docs/entscheidungen/0002-datenbank.md': entscheidung('0002', 'Datenbank', 'bestätigt vom Inhaber', 'datenbank', 'X')})
        r = grundlage.fuer_rueckblick(['demo'])
        self.assertEqual(list(r['stand']), ['demo: docs/entscheidungen/0002-datenbank.md'])


class NachreviewTest(Vorgaben):
    def kernverletzung(self):
        return {'tue': 'urteil', 'urteil': {'urteil': 'bestanden', 'bericht': '# Nachreview\n\nKern verletzt.',
                                            'befunde': [{'nr': 'M2', 'schwere': 'P2', 'ort': 'wetter.py:3', 'was': 'Temperatur weicht von der Quelle ab'}]}}

    def test_kernverletzung_im_nachreview_loest_nacharbeit_aus(self):
        self.fest({'MASTERPLAN.md': MASTERPLAN})
        self.drehbuch(claude=[{'tue': 'commit'}, {'tue': 'commit', 'dateien': {'a.txt': 'x'}}, {'tue': 'commit', 'dateien': {'b.txt': 'x'}}],
                      codex=[self.nacharbeit(), self.kernverletzung(), self.bestanden()])
        self.uebernehmen()
        ablauf.entwickeln(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        self.assertEqual([l['art'] for l in laeufe.alle()],
                         ['entwickeln', 'vorbereiten', 'review', 'nacharbeit', 'vorbereiten', 'review', 'nacharbeit', 'vorbereiten', 'review', 'mergen'])
        nachreview = self.aufrufe('codex')[1]['argv'][-1]
        self.assertIn('Nachprüfung', nachreview)
        self.assertIn('Ausnahme mit Vorrang', nachreview)
        self.assertIn('Kernzusage verletzt (mindestens P2)', nachreview)
        self.assertIn('Temperatur weicht von der Quelle ab', self.aufrufe('claude')[2]['argv'][-1])

    def test_neue_grundentscheidung_ist_auch_ohne_bestaetigte_entscheidung_p2(self):
        self.fest({'MASTERPLAN.md': MASTERPLAN})
        g = grundlage.fuer(self.p)
        self.assertEqual(list(g['stand']), ['MASTERPLAN.md'])
        self.assertIn('neue Grundentscheidung selbst oder bricht er eine bestätigte', auftraege.schwere(g))
        self.assertIn('neue oder gebrochene Grundentscheidung (P2)', auftraege._nachreview_ausnahme(g))

    def test_ohne_masterplan_bleibt_die_nachreview_regel(self):
        self.drehbuch(claude=[{'tue': 'commit'}, {'tue': 'commit', 'dateien': {'a.txt': 'x'}}], codex=[self.nacharbeit(), self.bestanden()])
        self.uebernehmen()
        ablauf.entwickeln(self.p, 'A1')
        self.warten(lambda: self.zeile('A1')[3] == 'erledigt')
        self.ruhig()
        nachreview = self.aufrufe('codex')[1]['argv'][-1]
        self.assertIn('Neue Befunde anderswo nur bei P1', nachreview)
        self.assertNotIn('Ausnahme mit Vorrang', nachreview)


class VorlagenTest(unittest.TestCase):
    def test_planvorlage(self):
        vorlage = (Path(__file__).resolve().parents[1] / 'vorlage' / 'projekt' / 'plan' / '_vorlage.md').read_text(encoding='utf-8')
        self.assertIn('## Fachliche Abnahme', vorlage)
        self.assertIn('Kernzusage', vorlage)
        roadmap.plan(vorlage)   # Aufgabentabelle bleibt lesbar

    def test_entscheidungsvorlage_hat_status(self):
        vorlage = (Path(__file__).resolve().parents[1] / 'docs' / 'entscheidungen' / '_vorlage.md').read_text(encoding='utf-8')
        self.assertIn('**Status:**', vorlage)
        self.assertIn('vorgeschlagen', vorlage)
        self.assertIn('**Betrifft:**', vorlage)
        self.assertFalse(grundlage.entscheidung('docs/entscheidungen/0000-vorlage.md', vorlage)['bindend'])


if __name__ == '__main__':
    unittest.main()
