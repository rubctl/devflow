"""Discovery-Katalog (S13.1): Prüfung und Übersicht an synthetischen Katalogen in Temp-Verzeichnissen."""
import contextlib
import datetime
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from devflow2 import Fehler, discovery

WURZEL = Path(__file__).resolve().parent.parent

FAKTOREN = """
[[faktor]]
id = "F-REG-01"
art = "Vorschrift"
name = "Aufbewahrungspflicht für Belege"
quelle = "I-01"
aendert_sich = 2027-01-01

[[faktor]]
id = "F-KUN-01"
art = "Kunde"
name = "Lieferfenster eines Großkunden"
quelle = "I-02"
aendert_sich = "jährlich im Herbst"

[[faktor]]
id = "F-SYS-01"
art = "System"
name = "Altes Warenwirtschaftssystem"
"""


def bereich(bid, name, prozesse, gruppen=('G1',)):
    text = f'[bereich]\nid = "{bid}"\nname = "{name}"\n'
    for g in gruppen:
        text += f'\n[[gruppe]]\nid = "{g}"\nname = "Gruppe {g}"\n'
    for p in prozesse:
        text += '\n[[prozess]]\n' + ''.join(f'{k} = {v}\n' for k, v in p.items())
    return text


def prozess(pid, stand='erfasst', soll='offen', gruppe='G1', **mehr):
    d = {'id': f'"{pid}"', 'name': f'"Prozess {pid}"', 'gruppe': f'"{gruppe}"', 'stand': f'"{stand}"', 'soll': f'"{soll}"'}
    d.update(mehr)
    return d


BESCHREIBUNG = ''.join(f'## {a}\n\ntext\n\n' for a in discovery.ABSCHNITTE)


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wurzel = Path(self.tmp.name)
        (self.wurzel / 'discovery' / 'katalog').mkdir(parents=True)
        self.schreiben('faktoren.toml', FAKTOREN)
        self.schreiben('fragen.md', '# Fragen\n')

    def schreiben(self, name, text):
        pfad = self.wurzel / 'discovery' / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(text, encoding='utf-8')

    def bereich(self, datei, *args, **kw):
        self.schreiben(f'katalog/{datei}.toml', bereich(*args, **kw))

    def pruefen(self):
        k, text, code = discovery.pruefen(self.wurzel)
        return k, text, code


class Pruefen(Basis):
    def test_sauberer_katalog_ist_ok(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', faktoren='["F-REG-01"]')])
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        self.assertIn('OK: keine Fehler', text)

    def test_doppelte_id_in_zwei_dateien_nennt_beide_fundstellen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-03')])
        self.bereich('lager2', 'LAG2', 'Lager 2', [prozess('LAG-WE-03')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('LAG-WE-03', text)
        self.assertIn('discovery/katalog/lager.toml, Zeile', text)
        self.assertIn('discovery/katalog/lager2.toml, Zeile', text)
        self.assertIn('zweimal', text)

    def test_doppelte_id_in_derselben_datei(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-03'), prozess('LAG-WE-03')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('ID „LAG-WE-03“ kommt zweimal vor', text)

    def test_unbekannter_faktor_nennt_prozess_und_faktor(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', faktoren='["F-REG-99"]')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('discovery/katalog/lager.toml · LAG-WE-01', text)
        self.assertIn('F-REG-99', text)

    def test_ungueltiger_stand_nennt_erlaubte_werte(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', stand='fast fertig')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('„fast fertig“ ist nicht erlaubt', text)
        for s in discovery.STAENDE:
            self.assertIn(f'„{s}“', text)

    def test_ungueltiges_soll_und_unbekannte_gruppe(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', soll='vielleicht'), prozess('LAG-WE-02', gruppe='XX')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('Soll „vielleicht“ ist nicht erlaubt', text)
        self.assertIn('Gruppe „XX“', text)

    def test_soll_entfaellt_bei_soll_abgestimmt_ist_gueltig_und_nicht_offen(self):
        self.schreiben('faktoren.toml', '')
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', stand='Soll abgestimmt', soll='entfällt',
                                                        quellen='["I-01"]', beschreibung='"p/LAG-WE-01.md"')])
        self.schreiben('p/LAG-WE-01.md', BESCHREIBUNG)
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        self.assertEqual(k.warnungen(), [], text)
        uebersicht = discovery.stand(self.wurzel)
        kopf, *zeilen = [z for z in uebersicht.split('## Bereich × Soll')[1].split('## Offene')[0].splitlines() if z.startswith('|')]
        self.assertIn('offen | neu | bleibt | ändert sich | entfällt', kopf)
        zelle = dict(zip([c.strip() for c in kopf.strip('|').split('|')], [c.strip() for c in zeilen[-1].strip('|').split('|')]))
        self.assertEqual((zelle['offen'], zelle['entfällt']), ('0', '1'))

    def test_zusammengelegt_mit_pruefung(self):
        self.bereich('lager', 'LAG', 'Lager', [
            prozess('LAG-WE-01', soll='zusammengelegt mit LAG-WE-02'),
            prozess('LAG-WE-02'),
            prozess('LAG-WE-03', soll='zusammengelegt mit LAG-WE-09'),
            prozess('LAG-WE-04', soll='zusammengelegt mit LAG-WE-04'),
            prozess('LAG-WE-05', soll='zusammengelegt mit LAG-WE-01'),
        ])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertNotIn('LAG-WE-01: ', text)
        self.assertIn('„LAG-WE-09“, aber diesen Prozess gibt es nicht', text)
        self.assertIn('nicht mit sich selbst', text)
        self.assertIn('bitte auf das endgültige Ziel verweisen', text)

    def test_beschreibung_erst_bei_bedarf(self):
        self.schreiben('faktoren.toml', '')
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01'),
                                               prozess('LAG-WE-02', stand='Ist vertieft', quellen='["I-01"]')])
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        warnungen = [discovery.Katalog.zeile(m) for m in k.warnungen()]
        self.assertEqual(len(warnungen), 1, text)
        self.assertIn('LAG-WE-02', warnungen[0])
        self.assertIn('keine Beschreibung', warnungen[0])

    def test_beschreibung_fehlende_datei_ist_fehler_und_fehlende_abschnitte_warnung(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', beschreibung='"p/gibts-nicht.md"'),
                                               prozess('LAG-WE-02', beschreibung='"p/kurz.md"'),
                                               prozess('LAG-WE-03', beschreibung='"../../etc/passwd"')])
        self.schreiben('p/kurz.md', '## Ist\n\nnur das\n\n## Soll\n')
        k, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('„p/gibts-nicht.md“ gibt es nicht', text)
        self.assertIn('LAG-WE-02: Beschreibung „p/kurz.md“ hat keine Überschrift für: Lücke Ist → Soll, Äußere Faktoren, Quellen', text)
        self.assertIn('außerhalb von discovery/', text)

    def test_quellen_und_soll_offen_warnungen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', stand='Ist aufgenommen'),
                                               prozess('LAG-WE-02', stand='Soll entworfen', quellen='["I-01"]')])
        k, text, code = self.pruefen()
        self.assertEqual(code, 0)
        self.assertIn('LAG-WE-01: Stand „Ist aufgenommen“, aber keine Quellen', text)
        self.assertIn('LAG-WE-02: Stand „Soll entworfen“, aber Soll steht noch auf „offen“', text)

    def test_einzeiler_ersetzen_beschreibung_bis_soll_abgestimmt(self):
        self.schreiben('faktoren.toml', '')
        self.bereich('lager', 'LAG', 'Lager', [
            prozess('LAG-WE-01', 'Ist vertieft', quellen='["I-01"]', ist_kurz='"Ware wird gezählt."', soll_kurz='"Bleibt so."'),
            prozess('LAG-WE-02', 'Soll abgestimmt', 'neu', quellen='["I-01"]', ist_kurz='"Handliste."', soll_kurz='"Scanner."'),
            prozess('LAG-WE-03', 'Ist vertieft', quellen='["I-01"]', ist_kurz='"Nur das Ist."'),
            prozess('LAG-WE-04', 'Ist vertieft', quellen='["I-01"]', soll_kurz='"Nur das Soll, Soll offen."'),
        ])
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        warnungen = '\n'.join(discovery.Katalog.zeile(m) for m in k.warnungen())
        self.assertNotIn('LAG-WE-01', warnungen)
        self.assertNotIn('LAG-WE-02', warnungen)
        self.assertIn('LAG-WE-03: Stand „Ist vertieft“, aber keine Beschreibung', warnungen)
        self.assertIn('ist_kurz', warnungen)
        self.assertIn('LAG-WE-04: Stand „Ist vertieft“, aber keine Beschreibung', warnungen)
        self.assertEqual(len(k.warnungen()), 2, text)
        self.assertEqual(k.prozesse[0]['ist_kurz'], 'Ware wird gezählt.')

    def test_soll_kurz_allein_genuegt_bei_bleibt_entfaellt_ausserhalb_zusammengelegt(self):
        self.schreiben('faktoren.toml', '')
        ps = [prozess(f'LAG-WE-0{i}', 'Soll abgestimmt', soll, quellen='["I-01"]', soll_kurz='"Begründung."')
              for i, soll in enumerate(('bleibt', 'entfällt', 'außerhalb', 'zusammengelegt mit LAG-WE-01'), 2)]
        ps.append(prozess('LAG-WE-01', 'Soll abgestimmt', 'neu', quellen='["I-01"]', ist_kurz='"a"', soll_kurz='"b"'))
        ps.append(prozess('LAG-WE-09', 'Soll abgestimmt', 'ändert sich', quellen='["I-01"]', soll_kurz='"ohne Ist"'))
        ps.append(prozess('LAG-WE-10', 'Soll abgestimmt', 'entfällt', quellen='["I-01"]'))  # ohne jede Begründung
        self.bereich('lager', 'LAG', 'Lager', ps)
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        betroffen = sorted(m[2] for m in k.warnungen())
        self.assertEqual(betroffen, ['LAG-WE-09', 'LAG-WE-10'], text)

    def test_ab_im_prototyp_bleibt_beschreibung_erwartet_auch_mit_einzeilern(self):
        self.schreiben('faktoren.toml', '')
        self.bereich('lager', 'LAG', 'Lager', [
            prozess(f'LAG-WE-0{i}', stand, 'bleibt', quellen='["I-01"]', ist_kurz='"a"', soll_kurz='"b"')
            for i, stand in enumerate(('im Prototyp', 'freigegeben', 'in Umsetzung', 'umgesetzt'), 1)])
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        self.assertEqual(len(k.warnungen()), 4, text)
        self.assertNotIn('Einzeiler', text)  # kein Hinweis auf einen Weg, der hier nicht gilt

    def test_beschreibungsdatei_ersetzt_einzeiler_weiterhin(self):
        self.schreiben('faktoren.toml', '')
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', 'umgesetzt', 'bleibt', quellen='["I-01"]',
                                                       beschreibung='"p/a.md"')])
        self.schreiben('p/a.md', BESCHREIBUNG)
        self.assertEqual(self.pruefen()[0].meldungen, [])

    def test_einzeiler_muessen_text_sein_und_leere_zaehlen_nicht(self):
        self.schreiben('faktoren.toml', '')
        self.bereich('lager', 'LAG', 'Lager', [
            prozess('LAG-WE-01', 'Ist vertieft', quellen='["I-01"]', ist_kurz='5', soll_kurz='"x"'),
            prozess('LAG-WE-02', 'Ist vertieft', quellen='["I-01"]', ist_kurz='"  "', soll_kurz='"x"')])
        k, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('LAG-WE-01: Feld „ist_kurz“ muss Text sein', text)
        self.assertIn('LAG-WE-02: Stand „Ist vertieft“, aber keine Beschreibung', text)
        self.assertNotIn('unbekanntes Feld', text)

    def test_offene_frage_ohne_adressat_ist_nur_hinweis(self):
        self.schreiben('faktoren.toml', '')
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        self.schreiben('fragen.md', '# Fragen\n- [ ] Wer prüft? (an Lagerleiterin)\n- [ ] Wer zahlt?\n'
                                    '- [x] Erledigt ohne Rolle → ja\n- [ ] Leer? (an )\n')
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        self.assertEqual((len(k.fehler()), len(k.warnungen()), len(k.hinweise())), (0, 0, 2), text)
        self.assertIn('Hinweis · discovery/fragen.md · Zeile 3: offene Frage ohne Adressat', text)
        self.assertIn('Zeile 5', text)
        self.assertNotIn('Zeile 2', text)
        self.assertNotIn('Zeile 4', text)
        self.assertIn('OK: keine Fehler, 0 Warnungen, 2 Hinweise (1 Bereich, 1 Prozess, 0 Faktoren, 3 offene Fragen)', text)

    def test_zusammenfassung_grammatik(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', stand='Ist aufgenommen')])
        self.schreiben('faktoren.toml', '')
        self.assertIn('OK: keine Fehler, 1 Warnung (1 Bereich, 1 Prozess, 0 Faktoren, 0 offene Fragen)', self.pruefen()[1])

    def test_faktor_fehler(self):
        self.schreiben('faktoren.toml', FAKTOREN + '''
[[faktor]]
id = "F-REG-01"
art = "Wetter"
name = "doppelt"
aendert_sich = "2027-13-45"
''')
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', faktoren='["F-REG-01", "F-KUN-01", "F-SYS-01"]')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('Faktor-ID „F-REG-01“ kommt zweimal vor', text)
        self.assertIn('Art „Wetter“ ist nicht erlaubt', text)
        for a in discovery.ARTEN:
            self.assertIn(f'„{a}“', text)
        self.assertIn('kein Datum', text)

    def test_ungenutzter_faktor_ist_warnung(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', faktoren='["F-REG-01"]')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 0)
        self.assertIn('F-KUN-01: wird von keinem Prozess genannt', text)

    def test_kaputtes_toml_und_fehlende_pflichtfelder(self):
        self.schreiben('katalog/kaputt.toml', '[bereich\nid = ')
        self.schreiben('katalog/leer.toml', '[[prozess]]\nname = "x"\n')
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('discovery/katalog/kaputt.toml: kein gültiges TOML', text)
        self.assertIn('Abschnitt [bereich] fehlt', text)
        self.assertIn('Feld „id“ fehlt', text)
        self.assertIn('Feld „stand“ fehlt', text)

    def test_falsche_feldtypen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', faktoren='"F-REG-01"', prioritaet='"dringend"')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('Feld „faktoren“ muss eine Liste von Texten sein', text)
        self.assertIn('Priorität „dringend“', text)

    def test_unbekanntes_feld_und_falscher_praefix_sind_warnungen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('XXX-01', prio='"hoch"')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        self.assertIn('unbekanntes Feld „prio“', text)
        self.assertIn('ID beginnt nicht mit der Bereichs-ID „LAG-“', text)

    def test_fragen_syntax(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        self.schreiben('fragen.md', '- [ ] gut\n- [x] auch gut\n- [] schlecht\n- Fließtext ohne Kästchen\n')
        k, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('discovery/fragen.md · Zeile 3: Frage ohne gültiges Kästchen', text)
        self.assertEqual(k.offene_fragen(), ['gut'])

    def test_fehlende_dateien_sind_warnungen_aber_kein_discovery_ordner_ist_fehler(self):
        (self.wurzel / 'discovery' / 'faktoren.toml').unlink()
        (self.wurzel / 'discovery' / 'fragen.md').unlink()
        k, text, code = self.pruefen()
        self.assertEqual(code, 0)
        self.assertEqual(len(k.warnungen()), 3, text)  # kein Bereich, keine Faktoren, keine Fragen
        with tempfile.TemporaryDirectory() as leer:
            with self.assertRaises(Fehler):
                discovery.pruefen(leer)

    def test_pfad_darf_der_discovery_ordner_selbst_sein(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        self.assertEqual(discovery.pruefen(self.wurzel / 'discovery')[2], 0)

    def test_html_kommentar_in_fragen_zaehlt_nicht_und_zeilen_bleiben_stimmig(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        self.schreiben('fragen.md', '<!--\n- [ ] auskommentiert\n-->\n- [ ] echt\n<!-- - [x] einzeilig -->\n- [] kaputt\n')
        k, text, code = self.pruefen()
        self.assertEqual(k.offene_fragen(), ['echt'])
        self.assertIn('discovery/fragen.md · Zeile 6:', text)

    def test_platzhalter_zusammengelegt_mit_id_ist_fehler(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', soll='zusammengelegt mit <id>')])
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn('discovery/katalog/lager.toml · LAG-WE-01: zusammengelegt mit „<id>“, aber diesen Prozess gibt es nicht.', text)
        s = Uebersicht.zeilen(None, discovery.stand(self.wurzel), 'Bereich × Soll')
        self.assertEqual(s['LAG Lager']['zusammengelegt'], '1')  # fällt nicht mehr aus allen Spalten
        self.assertEqual(s['LAG Lager']['offen'], '0')

    def test_doppelte_id_in_einer_datei_nennt_beide_zeilen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01'), prozess('LAG-WE-02'), prozess('LAG-WE-01')])
        zeilen = (self.wurzel / 'discovery' / 'katalog' / 'lager.toml').read_text(encoding='utf-8').splitlines()
        erste, zweite = [i + 1 for i, z in enumerate(zeilen) if z == 'id = "LAG-WE-01"']
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn(f'kommt zweimal vor: discovery/katalog/lager.toml, Zeile {erste} und discovery/katalog/lager.toml, Zeile {zweite}.', text)

    def test_doppelte_faktor_id_nennt_beide_zeilen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', faktoren='["F-SYS-01"]')])
        self.schreiben('faktoren.toml', FAKTOREN + '\n[[faktor]]\nid = "F-SYS-01"\nart = "System"\nname = "Doppelt"\n')
        zeilen = (self.wurzel / 'discovery' / 'faktoren.toml').read_text(encoding='utf-8').splitlines()
        erste, zweite = [i + 1 for i, z in enumerate(zeilen) if z == 'id = "F-SYS-01"']
        _, text, code = self.pruefen()
        self.assertEqual(code, 1)
        self.assertIn(f'Faktor-ID „F-SYS-01“ kommt zweimal vor: discovery/faktoren.toml, Zeile {erste} und '
                      f'discovery/faktoren.toml, Zeile {zweite}.', text)


class Uebersicht(Basis):
    def katalog_mit_zwölf_prozessen(self):
        self.bereich('lager', 'LAG', 'Lager', [
            prozess('LAG-WE-01', 'erfasst'), prozess('LAG-WE-02', 'erfasst'),
            prozess('LAG-WE-03', 'Ist aufgenommen', quellen='["I-01"]', faktoren='["F-REG-01"]'),
            prozess('LAG-WE-04', 'umgesetzt', 'bleibt', quellen='["I-01"]', faktoren='["F-REG-01", "F-KUN-01"]',
                    beschreibung='"p/a.md"'),
        ])
        self.bereich('einkauf', 'EIN', 'Einkauf', [
            prozess('EIN-BE-01', 'erfasst', 'außerhalb'), prozess('EIN-BE-02', 'Ist vertieft', quellen='["I-02"]'),
            prozess('EIN-BE-03', 'Ist vertieft', quellen='["I-02"]'),
            prozess('EIN-BE-04', 'Soll abgestimmt', 'entfällt', quellen='["I-02"]'),
        ])
        self.bereich('versand', 'VER', 'Versand', [
            prozess('VER-LI-01', 'im Prototyp', 'neu', quellen='["I-03"]'),
            prozess('VER-LI-02', 'freigegeben', 'ändert sich', quellen='["I-03"]'),
            prozess('VER-LI-03', 'in Umsetzung', 'zusammengelegt mit VER-LI-02', quellen='["I-03"]'),
            prozess('VER-LI-04', 'Soll entworfen', 'offen', quellen='["I-03"]'),
        ])
        self.schreiben('p/a.md', BESCHREIBUNG)
        self.schreiben('fragen.md', '''# Fragen
- [ ] LAG-WE-03: Wer prüft die Lieferscheine?
- [ ] EIN-BE-02: Wie läuft die Freigabe bei Abwesenheit?
- [ ] Gilt das für alle Standorte?
- [ ] Kennung VER: Welche Spediteure gibt es?
- [x] LAG-WE-01: schon beantwortet
''')

    def zeilen(self, text, abschnitt):
        teil = text.split(f'## {abschnitt}')[1].split('\n## ')[0]
        zeilen = [[c.strip() for c in z.strip('|').split('|')] for z in teil.splitlines() if z.startswith('|')]
        kopf = zeilen[0]
        return {z[0]: dict(zip(kopf, z)) for z in zeilen[2:]}

    def test_tabelle_bereich_mal_stand(self):
        self.katalog_mit_zwölf_prozessen()
        text = discovery.stand(self.wurzel)
        t = self.zeilen(text, 'Bereich × Stand')
        self.assertEqual(t['LAG Lager']['erfasst'], '2')
        self.assertEqual(t['LAG Lager']['Ist aufgenommen'], '1')
        self.assertEqual(t['LAG Lager']['umgesetzt'], '1')
        self.assertEqual(t['LAG Lager']['Prozesse'], '4')
        self.assertEqual(t['EIN Einkauf']['Ist vertieft'], '2')
        self.assertEqual(t['VER Versand']['in Umsetzung'], '1')
        s = t['**Summe**']
        self.assertEqual(s['Prozesse'], '12')
        self.assertEqual(s['erfasst'], '3')
        self.assertEqual(sum(int(s[x]) for x in discovery.STAENDE), 12)

    def test_offene_fragen_je_bereich(self):
        self.katalog_mit_zwölf_prozessen()
        text = discovery.stand(self.wurzel)
        t = self.zeilen(text, 'Bereich × Stand')
        self.assertEqual((t['LAG Lager']['Fragen offen'], t['EIN Einkauf']['Fragen offen'], t['VER Versand']['Fragen offen']),
                         ('1', '1', '1'))
        self.assertEqual(t['(ohne Bereich)']['Fragen offen'], '1')
        self.assertEqual(t['**Summe**']['Fragen offen'], '4')
        self.assertIn('## Offene Fragen (4)', text)
        self.assertNotIn('schon beantwortet', text)

    def test_soll_tabelle_zaehlt_entfaellt_nicht_als_offen(self):
        self.katalog_mit_zwölf_prozessen()
        s = self.zeilen(discovery.stand(self.wurzel), 'Bereich × Soll')
        self.assertEqual(s['EIN Einkauf']['entfällt'], '1')
        self.assertEqual(s['EIN Einkauf']['offen'], '2')  # EIN-BE-02 und -03; -01 ist außerhalb, -04 entfällt
        self.assertEqual(s['EIN Einkauf']['außerhalb'], '1')
        self.assertEqual(s['VER Versand']['zusammengelegt'], '1')
        total = s['**Summe**']
        self.assertEqual(sum(int(v) for k, v in total.items() if k != 'Bereich'), 12)

    def test_nur_staende_mit_prozessen_sind_spalten_in_reihenfolge(self):
        self.katalog_mit_zwölf_prozessen()
        kopf = discovery.stand(self.wurzel).split('## Bereich × Stand')[1].split('\n')[2]
        spalten = [c.strip() for c in kopf.strip('|').split('|')]
        self.assertEqual(spalten, ['Bereich'] + list(discovery.STAENDE) + ['Prozesse', 'Fragen offen'])  # hier sind alle belegt
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', 'erfasst'), prozess('LAG-WE-02', 'im Prototyp', 'neu', quellen='["I-01"]'),
                                               prozess('LAG-WE-03', 'Ist vertieft', quellen='["I-01"]')])
        for name in ('einkauf', 'versand'):
            (self.wurzel / 'discovery' / 'katalog' / f'{name}.toml').unlink()
        text = discovery.stand(self.wurzel)
        kopf = text.split('## Bereich × Stand')[1].split('\n')[2]
        self.assertEqual([c.strip() for c in kopf.strip('|').split('|')],
                         ['Bereich', 'erfasst', 'Ist vertieft', 'im Prototyp', 'Prozesse', 'Fragen offen'])
        t = self.zeilen(text, 'Bereich × Stand')
        self.assertEqual(t['**Summe**']['Prozesse'], '3')
        self.assertNotIn('freigegeben', text.split('## Bereich × Soll')[0])

    def test_tabelle_ohne_prozesse_hat_nur_feste_spalten(self):
        text = discovery.stand(self.wurzel)
        kopf = text.split('## Bereich × Stand')[1].split('\n')[2]
        self.assertEqual([c.strip() for c in kopf.strip('|').split('|')], ['Bereich', 'Prozesse', 'Fragen offen'])

    def test_offene_fragen_nach_adressat_gruppiert(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        self.schreiben('fragen.md', '''# Fragen
- [ ] LAG-WE-01: Wer gibt frei? (an Leitung Lager)
- [ ] Gilt das überall?
- [ ] Wie oft im Monat? (an Disponent)
- [ ] Wer vertritt? (an Leitung Lager)
- [x] Schon erledigt? (an Disponent) → Ja, immer montags
- [ ] Welche Zone? (an Buchhaltung)
''')
        text = discovery.stand(self.wurzel)
        teil = text.split('## Offene Fragen (5)')[1].split('\n## ')[0]
        kopfe = [z for z in teil.splitlines() if z.startswith('###')]
        self.assertEqual(kopfe, ['### an Buchhaltung (1)', '### an Disponent (1)', '### an Leitung Lager (2)', '### ohne Adressat (1)'])
        self.assertIn('- LAG-WE-01: Wer gibt frei?\n- Wer vertritt?', teil)  # Adressat-Zusatz steht nur in der Überschrift
        self.assertNotIn('(an ', teil)
        self.assertNotIn('Schon erledigt', text)
        self.assertNotIn('immer montags', text)
        t = self.zeilen(text, 'Bereich × Stand')
        self.assertEqual(t['LAG Lager']['Fragen offen'], '1')  # Zuordnung zum Bereich bleibt trotz Zusatz
        self.assertEqual(t['**Summe**']['Fragen offen'], '5')

    def test_beantwortete_frage_mit_pfeil_zaehlt_nicht_offen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        self.schreiben('fragen.md', '- [x] Wer prüft? → Die Lagerleiterin (an wen auch immer)\n- [X] Noch eine → ja\n')
        k = discovery.laden(self.wurzel)
        self.assertEqual(k.offene_fragen(), [])
        self.assertEqual(k.hinweise(), [])
        self.assertIn('## Offene Fragen (0)\n\n_Keine._', discovery.stand(self.wurzel))

    def test_adressat_nur_am_zeilenende(self):
        self.assertEqual(discovery.Katalog.adressat('Wer? (an Leitung)'), ('Wer?', 'Leitung'))
        self.assertEqual(discovery.Katalog.adressat('Wer (an Leitung) prüft?'), ('Wer (an Leitung) prüft?', None))
        self.assertEqual(discovery.Katalog.adressat('Wer?  (an  Leitung  )  '), ('Wer?', 'Leitung'))
        self.assertEqual(discovery.Katalog.adressat('Wer? (an)'), ('Wer? (an)', None))

    def test_faktoren_mit_stichtag_und_betroffene_prozesse(self):
        self.katalog_mit_zwölf_prozessen()
        text = discovery.stand(self.wurzel, heute=datetime.date(2026, 11, 5))
        stichtag = text.split('## Faktoren mit Stichtag')[1].split('## Betroffene')[0]
        self.assertIn('- 01.01.2027 · F-REG-01 · Aufbewahrungspflicht für Belege (Vorschrift) · betrifft LAG-WE-03, LAG-WE-04', stichtag)
        self.assertNotIn('F-KUN-01', stichtag)  # Text statt Datum
        self.assertNotIn('überschritten', stichtag)
        je = text.split('## Betroffene Prozesse je Faktor')[1]
        self.assertIn('F-KUN-01 · Lieferfenster eines Großkunden (Kunde) · Änderung: jährlich im Herbst · 1 Prozess: LAG-WE-04', je)
        self.assertIn('F-SYS-01 · Altes Warenwirtschaftssystem (System) · 0 Prozesse', je)
        spaeter = discovery.stand(self.wurzel, heute=datetime.date(2027, 3, 1))
        self.assertIn('01.01.2027 (überschritten)', spaeter)

    def test_stichtag_als_text_datum(self):
        self.schreiben('faktoren.toml', '[[faktor]]\nid = "F-ORG-01"\nart = "Organisation"\nname = "Umzug"\naendert_sich = "15.03.2027"\n')
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', faktoren='["F-ORG-01"]')])
        self.assertIn('- 15.03.2027 · F-ORG-01', discovery.stand(self.wurzel, heute=datetime.date(2026, 1, 1)))

    def test_uebersicht_bei_leerem_katalog_und_bei_fehlern(self):
        text = discovery.stand(self.wurzel)
        self.assertIn('_Keine._', text)
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', stand='kaputt')])
        self.assertIn('hat 1 Fehler', discovery.stand(self.wurzel))


class Befehl(Basis):
    def lauf(self, *args):
        return subprocess.run([sys.executable, '-B', '-m', 'devflow2', 'discovery', *args], cwd=WURZEL,
                              capture_output=True, text=True, env={'PYTHONPATH': str(WURZEL), 'PATH': '/usr/bin:/bin',
                                                                   'DEVFLOW2_STATE': str(self.wurzel / 'zustand'),
                                                                   'DEVFLOW2_INSTANZ': '/dev/null'})

    def test_exitcode_und_ausgabe_von_pruefen(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        r = self.lauf('pruefen', str(self.wurzel))
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertIn('OK: keine Fehler', r.stdout)
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01', stand='fast fertig')])
        r = self.lauf('pruefen', str(self.wurzel))
        self.assertEqual(r.returncode, 1)
        self.assertIn('FEHLER: 1 Fehler', r.stdout)

    def test_pruefen_ohne_pfad_nimmt_das_arbeitsverzeichnis(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        r = subprocess.run([sys.executable, '-B', '-m', 'devflow2', 'discovery', 'pruefen'], cwd=self.wurzel,
                           capture_output=True, text=True, env={'PYTHONPATH': str(WURZEL), 'PATH': '/usr/bin:/bin'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_stand_und_falscher_aufruf(self):
        self.bereich('lager', 'LAG', 'Lager', [prozess('LAG-WE-01')])
        r = self.lauf('stand', str(self.wurzel))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('## Bereich × Stand', r.stdout)
        self.assertNotEqual(self.lauf('unsinn').returncode, 0)
        r = self.lauf('pruefen', str(self.wurzel / 'gibt-es-nicht'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('kein Ordner discovery/', r.stderr)


class Vorlagen(Basis):
    VORLAGEN = WURZEL / 'vorlagen' / 'discovery'

    def test_vorlagen_ergeben_einen_gueltigen_leeren_katalog(self):
        shutil.copy(self.VORLAGEN / 'bereich.toml', self.wurzel / 'discovery' / 'katalog' / 'ber.toml')
        shutil.copy(self.VORLAGEN / 'faktoren.toml', self.wurzel / 'discovery' / 'faktoren.toml')
        shutil.copy(self.VORLAGEN / 'fragen.md', self.wurzel / 'discovery' / 'fragen.md')
        k, text, code = self.pruefen()
        self.assertEqual((code, k.meldungen), (0, []), text)
        self.assertEqual(k.offene_fragen(), [])  # das auskommentierte Beispiel zählt nicht
        self.assertIn('0 offene Fragen', text)
        self.assertIn('## Offene Fragen (0)', discovery.stand(self.wurzel))
        self.assertEqual(len(k.bereiche), 1)
        self.assertIn('## Bereich × Stand', discovery.stand(self.wurzel))

    def test_prozessvorlage_hat_alle_abschnitte(self):
        (self.wurzel / 'discovery' / 'prozesse').mkdir()
        shutil.copy(self.VORLAGEN / 'prozess.md', self.wurzel / 'discovery' / 'prozesse' / 'BER-GRP-01.md')
        shutil.copy(self.VORLAGEN / 'bereich.toml', self.wurzel / 'discovery' / 'katalog' / 'ber.toml')
        with (self.wurzel / 'discovery' / 'katalog' / 'ber.toml').open('a', encoding='utf-8') as f:
            f.write('\n[[prozess]]\nid = "BER-GRP-01"\nname = "Beispiel"\ngruppe = "GRP"\nstand = "Ist vertieft"\n'
                    'soll = "bleibt"\nquellen = ["I-01"]\nbeschreibung = "prozesse/BER-GRP-01.md"\n')
        self.schreiben('faktoren.toml', '')
        k, text, code = self.pruefen()
        self.assertEqual(k.meldungen, [], text)

    def test_beispiele_in_vorlagen_sind_gueltig_wenn_einkommentiert(self):
        """Die auskommentierten Beispiele in bereich.toml und faktoren.toml sind echte, gültige Einträge."""
        def einkommentiert(name):
            zeilen, drin = [], False
            for z in (self.VORLAGEN / name).read_text(encoding='utf-8').splitlines():
                drin = drin or z.startswith('# [[')
                if drin and z.startswith('# ') and (' = ' in z or z.startswith('# [[')):
                    zeilen.append(z[2:])
            return '\n'.join(zeilen)
        self.schreiben('katalog/ber.toml', (self.VORLAGEN / 'bereich.toml').read_text(encoding='utf-8') + '\n' + einkommentiert('bereich.toml'))
        self.schreiben('faktoren.toml', einkommentiert('faktoren.toml'))
        self.schreiben('prozesse/BER-GRP-01.md', BESCHREIBUNG)
        k, text, code = self.pruefen()
        self.assertEqual(code, 0, text)
        self.assertEqual((len(k.prozesse), len(k.faktoren)), (1, 1), text)


if __name__ == '__main__':
    unittest.main()
