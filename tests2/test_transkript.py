"""Transkription und Teams-Übernahme (S13.2): `devflow2/transkript.py` und `ops/transkribieren.sh`.

Statt Whisper gibt es eine Attrappe (`faster_whisper` als Modul im Test bzw. ein `erkenner`); VTT und DOCX sind synthetisch.
Kein Netz, kein Modell, kein echtes Audio.
"""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

import hilfen  # noqa: F401
from devflow2 import Fehler, transkript
from devflow2.transkript import Segment

WURZEL = Path(__file__).resolve().parent.parent
SKRIPT = WURZEL / 'ops' / 'transkribieren.sh'

VTT = '''﻿WEBVTT

NOTE Das ist eine Notiz

1
00:00:05.000 --> 00:00:08.500
<v Anna Muster>Guten Morgen, ich bin &amp; war schon</v>

2
00:00:09.000 --> 00:00:12.000
<v Anna Muster>im Lager.</v>

3
00:00:13.000 --> 00:00:15.000
<v Max Beispiel>Verstanden, Anna.</v>

4
01:02:03.250 --> 01:02:05.000
<v Max Beispiel>Später noch <c.gelb>etwas</c> Neues</v>

5
01:03:00.000 --> 01:03:02.000
Ohne Sprecher
'''


def docx_bauen(pfad, absaetze, xml=None):
    """Minimale Word-Datei: jeder Eintrag ein Absatz; ein Eintrag mit „\\n“ enthält Zeilenumbrüche (w:br)."""
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    teile = []
    for a in absaetze:
        runs = '<w:br/>'.join(f'<w:r><w:t xml:space="preserve">{z}</w:t></w:r>' for z in a.split('\n'))
        teile.append(f'<w:p>{runs}</w:p>')
    xml = xml if xml is not None else f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{"".join(teile)}</w:body></w:document>'
    with zipfile.ZipFile(pfad, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', xml)


class VttTest(unittest.TestCase):
    def test_sprecher_zeiten_und_zusammenfassung(self):
        s = transkript.vtt_lesen(VTT)
        self.assertEqual(s[0], Segment(5.0, 'Anna Muster', 'Guten Morgen, ich bin & war schon im Lager.'))  # zwei Cues, ein Absatz; Entität aufgelöst
        self.assertEqual(s[1], Segment(13.0, 'Max Beispiel', 'Verstanden, Anna.'))
        self.assertEqual(s[2], Segment(3723.25, 'Max Beispiel', 'Später noch etwas Neues'))  # Zeitabstand > 60 s: neuer Absatz; fremde Tags entfallen
        self.assertEqual(s[3], Segment(3780.0, None, 'Ohne Sprecher'))
        self.assertEqual(len(s), 4)

    def test_kurze_zeitangaben_und_mehrere_stimmen_in_einem_cue(self):
        s = transkript.vtt_lesen('WEBVTT\n\n00:07.000 --> 00:09.000\n<v A>Eins</v> <v B>Zwei</v>\n')
        self.assertEqual(s, [Segment(7.0, 'A', 'Eins'), Segment(7.0, 'B', 'Zwei')])

    def test_keine_vtt_und_leere_vtt_sind_fehler(self):
        with self.assertRaisesRegex(Fehler, 'WebVTT'):
            transkript.vtt_lesen('Hallo')
        with self.assertRaisesRegex(Fehler, 'keinen Text'):
            transkript.vtt_lesen('WEBVTT\n\nNOTE nur eine Notiz\n')


class DocxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-docx-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_name_vor_zeit(self):
        d = self.tmp / 'a.docx'
        docx_bauen(d, ['Besprechung 5. November', 'Anna Muster   0:05', 'Guten Morgen.', 'Noch ein Satz.', 'Max Beispiel   1:02:03', 'Ja, klar um 3:30'])
        self.assertEqual(transkript.docx_lesen(d), [Segment(5.0, 'Anna Muster', 'Guten Morgen. Noch ein Satz.'),
                                                    Segment(3723.0, 'Max Beispiel', 'Ja, klar um 3:30')])  # Text, der wie ein Kopf aussieht, direkt nach einem Kopf bleibt Text

    def test_text_mit_uhrzeit_ist_kein_sprecherkopf(self):
        d = self.tmp / 'm3.docx'
        docx_bauen(d, ['Anna Muster 0:05', 'Guten Morgen.', 'Schichtbeginn ist 6:00', 'Max Beispiel 0:20', 'Einverstanden.', 'Treffen wir uns um 9:30'])
        self.assertEqual(transkript.docx_lesen(d), [Segment(5.0, 'Anna Muster', 'Guten Morgen. Schichtbeginn ist 6:00'),
                                                    Segment(20.0, 'Max Beispiel', 'Einverstanden. Treffen wir uns um 9:30')])
        docx_bauen(d, ['Ludwig van Beethoven 0:05', 'Hallo.'])  # Namenspartikel sind erlaubt
        self.assertEqual(transkript.docx_lesen(d), [Segment(5.0, 'Ludwig van Beethoven', 'Hallo.')])

    def test_zeit_vor_name_und_zeilenumbruch_im_absatz(self):
        d = self.tmp / 'b.docx'
        docx_bauen(d, ['0:08 Anna Muster\nHallo zusammen.', '0:20 - Max Beispiel', 'Moin.'])
        self.assertEqual(transkript.docx_lesen(d), [Segment(8.0, 'Anna Muster', 'Hallo zusammen.'), Segment(20.0, 'Max Beispiel', 'Moin.')])

    def test_ohne_erkennbare_koepfe_kommt_der_text_ohne_sprecher(self):
        d = self.tmp / 'c.docx'
        docx_bauen(d, ['Erster Absatz.', 'Zweiter Absatz.'])
        self.assertEqual(transkript.docx_lesen(d), [Segment(None, None, 'Erster Absatz.'), Segment(None, None, 'Zweiter Absatz.')])

    def test_kaputte_dateien_sind_fehler(self):
        (self.tmp / 'x.docx').write_text('kein zip')
        with self.assertRaisesRegex(Fehler, 'kein ZIP'):
            transkript.docx_lesen(self.tmp / 'x.docx')
        with zipfile.ZipFile(self.tmp / 'y.docx', 'w') as z:
            z.writestr('anderes.xml', '<a/>')
        with self.assertRaisesRegex(Fehler, 'document.xml fehlt'):
            transkript.docx_lesen(self.tmp / 'y.docx')
        docx_bauen(self.tmp / 'z.docx', [], xml='<w:document')
        with self.assertRaisesRegex(Fehler, 'beschädigt'):
            transkript.docx_lesen(self.tmp / 'z.docx')
        docx_bauen(self.tmp / 'leer.docx', [])
        with self.assertRaisesRegex(Fehler, 'keinen Text'):
            transkript.docx_lesen(self.tmp / 'leer.docx')


class RollenTest(unittest.TestCase):
    PAARE = [('Anna Muster', 'Lagerleiterin'), ('Anna', 'Lagerleiterin'), ('Max', 'Disponent')]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-rollen-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def datei(self, text):
        p = self.tmp / 'rollen.toml'
        p.write_text(text)
        return p

    def test_lesen_sortiert_laengste_namen_zuerst(self):
        paare = transkript.rollen_lesen(self.datei('[rollen]\n"Max" = "Disponent"\n"Anna Muster" = "Lagerleiterin"\n'))
        self.assertEqual(paare, [('Anna Muster', 'Lagerleiterin'), ('Max', 'Disponent')])

    def test_fehlerhafte_dateien(self):
        for text, muster in (('x = 1\n', r'\[rollen\]'), ('[rollen]\nA = ""\n', 'Rolle'), ('[rollen]\nA = 3\n', 'Rolle'), ('[rollen\n', 'TOML')):
            with self.assertRaisesRegex(Fehler, muster):
                transkript.rollen_lesen(self.datei(text))
        with self.assertRaisesRegex(Fehler, 'nicht lesbar'):
            transkript.rollen_lesen(self.tmp / 'gibtsnicht.toml')

    def test_namen_in_sprecher_und_text_werden_ersetzt(self):
        paare = sorted(self.PAARE, key=lambda p: -len(p[0]))
        s, unbekannt = transkript.rollen_anwenden([
            Segment(1, 'Anna Muster', 'Hallo Max, danke. Annas Liste und anna selbst; Maximilian und Annabelle bleiben.'),
            Segment(2, 'Max', 'Hi Anna Muster.'),
            Segment(3, 'Fremde Person', 'Ich bin Max.'),
            Segment(4, 'Noch Jemand', 'Hm'),
            Segment(5, 'Fremde Person', 'Ja'),
            Segment(6, None, 'Anna ist da')], paare)
        self.assertEqual([x.sprecher for x in s], ['Lagerleiterin', 'Disponent', 'Sprecher 1', 'Sprecher 2', 'Sprecher 1', None])
        self.assertEqual(s[0].text, 'Hallo Disponent, danke. Lagerleiterins Liste und Lagerleiterin selbst; Maximilian und Annabelle bleiben.')
        self.assertEqual(s[1].text, 'Hi Lagerleiterin.')  # der längere Name geht vor
        self.assertEqual(s[2].text, 'Ich bin Disponent.')
        self.assertEqual(s[5].text, 'Lagerleiterin ist da')
        self.assertEqual(unbekannt, 2)

    def test_ersetzte_rolle_wird_nicht_noch_einmal_ersetzt(self):
        s, _ = transkript.rollen_anwenden([Segment(0, 'Max', 'Max und Chef')], [('Max', 'Chef'), ('Chef', 'Boss')])
        self.assertEqual((s[0].sprecher, s[0].text), ('Chef', 'Chef und Boss'))


class Attrappe:
    """Erkenner statt Whisper: liefert feste Segmente und zählt die Aufrufe."""
    def __init__(self, text='Hallo Max'):
        self.text, self.aufrufe = text, []

    def __call__(self, pfad):
        self.aufrufe.append(Path(pfad).name)
        if Path(pfad).read_bytes() == b'KAPUTT':
            raise Fehler('Whisper hat keine Sprache erkannt')
        return [Segment(0.0, None, self.text), Segment(75.5, None, 'Zweiter Satz')]


class Grund(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-trans-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.material = self.tmp / 'material'
        self.neu = self.material / 'neu'
        self.neu.mkdir(parents=True)
        self.interviews = self.material / 'interviews'
        self.log = []

    def lege(self, name, inhalt=b'AUDIO-1'):
        p = self.neu / name
        p.write_bytes(inhalt) if isinstance(inhalt, bytes) else p.write_text(inhalt)
        return p

    def lauf(self, pfad=None, **kw):
        kw.setdefault('datum', '2026-11-05')
        return transkript.verarbeiten(pfad or self.neu, self.material, ausgabe=self.log.append, **kw)

    def ordner(self):
        return sorted(p.name for p in self.interviews.iterdir()) if self.interviews.exists() else []


class AblageTest(Grund):
    def test_audio_wird_transkribiert_und_abgelegt(self):
        self.lege('Gespräch Lager.m4a')
        erkenner = Attrappe()
        ergebnisse, fehler = self.lauf(erkenner=erkenner)
        self.assertEqual((fehler, ergebnisse[0][1]), (0, '2026-11-05-01'))
        ziel = self.interviews / '2026-11-05-01'
        self.assertEqual(sorted(p.name for p in ziel.iterdir()), ['quelle.m4a', 'transkript.md'])
        self.assertEqual((ziel / 'quelle.m4a').read_bytes(), b'AUDIO-1')
        text = (ziel / 'transkript.md').read_text()
        self.assertIn('[00:00:00] Hallo Max', text)
        self.assertIn('[00:01:15] Zweiter Satz', text)
        self.assertIn('Sprecher: nicht unterschieden', text)
        self.assertIn('`quelle.m4a`', text)
        self.assertNotIn('Gespräch Lager', text)  # der Dateiname des Originals (kann Namen enthalten) steht nicht im Transkript
        self.assertEqual(list(self.neu.iterdir()), [])  # Original verschoben

    def test_vtt_und_docx_mit_rollen_und_fortlaufender_nummer(self):
        self.lege('a.vtt', VTT)
        docx_bauen(self.neu / 'b.docx', ['Anna Muster   0:05', 'Hallo Max.'])
        rollen = self.tmp / 'rollen.toml'
        rollen.write_text('[rollen]\n"Anna Muster" = "Lagerleiterin"\n"Max Beispiel" = "Disponent"\n"Max" = "Disponent"\n')
        _, fehler = self.lauf(rollen=rollen)
        self.assertEqual((fehler, self.ordner()), (0, ['2026-11-05-01', '2026-11-05-02']))
        vtt = (self.interviews / '2026-11-05-01' / 'transkript.md').read_text()
        self.assertIn('**[00:00:05] Lagerleiterin:** Guten Morgen', vtt)
        self.assertIn('**[01:02:03] Disponent:**', vtt)
        self.assertIn('Sprecher: durch Rollen ersetzt', vtt)
        self.assertNotIn('Anna Muster', vtt)
        self.assertNotIn('Beispiel', vtt)
        self.assertIn('Art: Teams-Export (VTT)', vtt)
        self.assertTrue((self.interviews / '2026-11-05-01' / 'quelle.vtt').is_file())
        self.assertTrue((self.interviews / '2026-11-05-02' / 'quelle.docx').is_file())
        self.assertIn('**[00:00:05] Lagerleiterin:** Hallo Disponent.', (self.interviews / '2026-11-05-02' / 'transkript.md').read_text())
        self.lauf()  # nichts mehr da
        self.assertEqual(self.log[-1], 'Nichts zu übernehmen')

    def test_ohne_rollen_bleiben_die_namen_der_quelle(self):
        self.lege('team.vtt', VTT)
        self.lauf()
        text = (self.interviews / '2026-11-05-01' / 'transkript.md').read_text()
        self.assertIn('**[00:00:05] Anna Muster:**', text)
        self.assertIn('Sprecher: aus der Quelle', text)

    def test_nummer_zaehlt_pro_datum_weiter(self):
        self.lege('a.vtt', VTT)
        self.lauf()
        self.lege('b.vtt', VTT.replace('Guten Morgen', 'Guten Abend'))
        self.lauf()
        self.lege('c.vtt', VTT.replace('Guten Morgen', 'Gute Nacht'))
        self.lauf(datum='2026-11-06')
        self.assertEqual(self.ordner(), ['2026-11-05-01', '2026-11-05-02', '2026-11-06-01'])

    def test_unbekannte_dateien_bleiben_liegen_sperrdateien_zaehlen_nicht(self):
        self.lege('notiz.md', 'x')
        self.lege('~$team.docx', 'sperre')
        self.lege('.versteckt.m4a', 'x')
        self.lege('foto.jpg', 'x')
        self.lauf(erkenner=Attrappe())
        self.assertEqual(sorted(p.name for p in self.neu.iterdir()), ['.versteckt.m4a', 'foto.jpg', 'notiz.md', '~$team.docx'])
        self.assertIn('übersprungen (keine Audio- oder Teams-Datei): notiz.md', self.log)
        self.assertEqual(self.ordner(), [])

    def test_einzelne_datei_irgendwo_und_nicht_unterstuetzte_art(self):
        fremd = self.tmp / 'Downloads'
        fremd.mkdir()
        (fremd / 'x.vtt').write_text(VTT)
        self.lauf(fremd / 'x.vtt')
        self.assertEqual(self.ordner(), ['2026-11-05-01'])
        self.assertFalse((fremd / 'x.vtt').exists())
        (fremd / 'y.pdf').write_text('x')
        with self.assertRaisesRegex(Fehler, 'nicht unterstützt'):
            self.lauf(fremd / 'y.pdf')
        with self.assertRaisesRegex(Fehler, 'Nicht gefunden'):
            self.lauf(self.tmp / 'gibtsnicht')
        with self.assertRaisesRegex(Fehler, 'JJJJ-MM-TT'):
            self.lauf(datum='5.11.')


class WiederholungTest(Grund):
    def test_bereits_uebernommene_datei_wird_nicht_doppelt_verarbeitet(self):
        self.lege('a.m4a')
        erkenner = Attrappe()
        self.lauf(erkenner=erkenner)
        self.lege('nochmal.m4a')  # gleicher Inhalt, anderer Name
        ergebnisse, fehler = self.lauf(erkenner=erkenner)
        self.assertEqual((fehler, erkenner.aufrufe, self.ordner()), (0, ['a.m4a'], ['2026-11-05-01']))
        self.assertEqual(ergebnisse[0][1:], ('2026-11-05-01', 'schon übernommen'))
        self.assertEqual(list(self.neu.iterdir()), [])

    def test_archivierte_quelle_als_eingabe_wird_nie_geloescht(self):
        self.lege('a.vtt', VTT)
        self.lege('b.m4a')
        self.lauf(erkenner=Attrappe())
        for name in ('2026-11-05-01/quelle.vtt', '2026-11-05-02/quelle.m4a'):
            q = self.interviews / name
            ergebnisse, fehler = self.lauf(q, erkenner=Attrappe())
            self.assertEqual((fehler, q.is_file()), (1, True), name)
            self.assertIn('Ablage', ergebnisse[0][2])
        self.assertEqual(self.ordner(), ['2026-11-05-01', '2026-11-05-02'])

    def test_rollen_nachtragen_ersetzt_das_transkript_in_derselben_ablage(self):
        self.lege('a.vtt', VTT)
        self.lauf()
        t = self.interviews / '2026-11-05-01' / 'transkript.md'
        self.assertIn('Anna Muster', t.read_text())
        rollen = self.tmp / 'rollen.toml'
        rollen.write_text('[rollen]\n"Anna Muster" = "Lagerleiterin"\n')
        self.lege('a.vtt', VTT)
        ergebnisse, fehler = self.lauf(rollen=rollen)
        self.assertEqual((fehler, self.ordner(), ergebnisse[0][1:]), (0, ['2026-11-05-01'], ('2026-11-05-01', 'aktualisiert')))
        text = t.read_text()
        self.assertIn('**[00:00:05] Lagerleiterin:**', text)
        self.assertNotIn('Anna Muster', text)
        self.assertIn('- Datum: 2026-11-05', text)
        self.assertEqual(list(self.neu.iterdir()), [])
        # gleiche Rollen noch einmal: nichts mehr zu tun; ohne Rollen bleibt die Fassung mit Rollen
        for kw in ({'rollen': rollen}, {}):
            self.lege('a.vtt', VTT)
            self.assertEqual(self.lauf(**kw)[0][0][2], 'schon übernommen')
            self.assertIn('Lagerleiterin', t.read_text())
        # unbekannter Sprecher wird nachgetragen; Audio wird dafür neu erkannt
        rollen.write_text('[rollen]\n"Anna Muster" = "Lagerleiterin"\n"Max Beispiel" = "Disponent"\n')
        self.lege('a.vtt', VTT)
        self.lauf(rollen=rollen)
        self.assertIn('Disponent', t.read_text())
        self.assertNotIn('Sprecher 1', t.read_text())

    def test_audio_mit_geaenderten_rollen_wird_neu_erkannt_ohne_rollen_nicht(self):
        erkenner = Attrappe('Hallo Max')
        self.lege('a.m4a')
        self.lauf(erkenner=erkenner)
        rollen = self.tmp / 'rollen.toml'
        rollen.write_text('[rollen]\n"Max" = "Disponent"\n')
        self.lege('a.m4a')
        self.lauf(rollen=rollen, erkenner=erkenner)
        self.assertEqual(erkenner.aufrufe, ['a.m4a', 'a.m4a'])
        self.assertIn('Hallo Disponent', (self.interviews / '2026-11-05-01' / 'transkript.md').read_text())
        self.lege('a.m4a')
        self.lauf(erkenner=erkenner)
        self.assertEqual(len(erkenner.aufrufe), 2)

    def test_abbruch_nach_der_ablage_vor_dem_verschieben_das_original_liegt_noch_im_eingang(self):
        self.lege('team.vtt', VTT)
        with mock.patch.object(Path, 'unlink', side_effect=OSError('Abbruch')):
            _, fehler = self.lauf()
        self.assertEqual(fehler, 1)
        self.assertEqual(self.ordner(), ['2026-11-05-01'])  # Ergebnis ist vollständig da
        self.assertTrue((self.neu / 'team.vtt').exists())  # das Original ging nicht verloren
        self.lauf()  # zweiter Aufruf legt nichts doppelt an und räumt den Eingang auf
        self.assertEqual((self.ordner(), list(self.neu.iterdir())), (['2026-11-05-01'], []))

    def test_abbruch_beim_schreiben_laesst_original_und_keine_halbe_ablage(self):
        self.lege('team.vtt', VTT)
        with mock.patch.object(transkript, 'schreiben', side_effect=OSError('Platte voll')):
            _, fehler = self.lauf()
        self.assertEqual(fehler, 1)
        self.assertEqual(self.ordner(), [])
        self.assertTrue((self.neu / 'team.vtt').exists())
        self.lauf()
        self.assertEqual(self.ordner(), ['2026-11-05-01'])

    def test_abbruch_des_prozesses_mittendrin_hinterlaesst_nur_aufraeumbares(self):
        self.lege('team.vtt', VTT)
        halb = self.interviews / '.teil-2026-11-05-01'
        halb.mkdir(parents=True)
        (halb / 'quelle.vtt').write_text('halb')
        self.lauf()
        self.assertEqual(self.ordner(), ['2026-11-05-01'])
        self.assertIn('Guten Morgen', (self.interviews / '2026-11-05-01' / 'transkript.md').read_text())

    def test_unerwartete_ausnahme_des_erkenners_stoppt_den_ordnerlauf_nicht(self):
        def erkenner(pfad):
            raise ValueError('kaputtes Audio')
        self.lege('a.m4a')
        self.lege('b.vtt', VTT)
        ergebnisse, fehler = self.lauf(erkenner=erkenner)
        self.assertEqual((fehler, self.ordner()), (1, ['2026-11-05-01']))
        self.assertEqual([p.name for p in self.neu.iterdir()], ['a.m4a'])
        self.assertTrue(any('FEHLER a.m4a: ValueError' in z for z in self.log))

    def test_fehler_bei_einer_datei_stoppt_die_anderen_nicht(self):
        self.lege('a.m4a', b'KAPUTT')
        self.lege('b.vtt', VTT)
        self.lege('c.docx', 'kein zip')
        ergebnisse, fehler = self.lauf(erkenner=Attrappe())
        self.assertEqual(fehler, 2)
        self.assertEqual(self.ordner(), ['2026-11-05-01'])
        self.assertEqual(sorted(p.name for p in self.neu.iterdir()), ['a.m4a', 'c.docx'])
        self.assertTrue(any('FEHLER a.m4a' in z and 'Original bleibt liegen' in z for z in self.log))


def faster_whisper_attrappe(modellordner, aufrufe):
    """Modul `faster_whisper`: das Modell „ist da“, sobald `modellordner` existiert; Laden ohne `local_files_only` legt es an (das Netz)."""
    class Seg:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t

    class WhisperModel:
        def __init__(self, name, **kw):
            aufrufe.append(('laden', name, kw['local_files_only'], kw['download_root']))
            if kw['local_files_only']:
                if not modellordner.exists():
                    raise FileNotFoundError('nicht im Cache')
            else:
                modellordner.mkdir(parents=True, exist_ok=True)

        def transcribe(self, pfad, **kw):
            aufrufe.append(('transcribe', Path(pfad).name, kw['language']))
            return iter([Seg(0, 2, ' Hallo '), Seg(61, 70, ' '), Seg(70, 80, 'Welt')]), types.SimpleNamespace(duration=80)
    return types.SimpleNamespace(WhisperModel=WhisperModel)


class WhisperTest(Grund):
    def setUp(self):
        super().setUp()
        self.aufrufe = []
        self.modell = self.tmp / 'modell-da'
        self.fake = faster_whisper_attrappe(self.modell, self.aufrufe)
        for patch in (mock.patch.dict(sys.modules, {'faster_whisper': self.fake}),
                      mock.patch.dict(os.environ, {'DEVFLOW2_WHISPER_CACHE': str(self.tmp / 'cache')})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_vorhandenes_modell_laeuft_ohne_laden_aus_dem_netz(self):
        self.modell.mkdir()
        self.lege('a.ogg')
        self.lauf(modell='medium')
        laden = [a for a in self.aufrufe if a[0] == 'laden']
        self.assertEqual(laden, [('laden', 'medium', True, str(self.tmp / 'cache'))])  # nur der lokale Versuch
        self.assertIn(('transcribe', 'a.ogg', 'de'), self.aufrufe)
        text = (self.interviews / '2026-11-05-01' / 'transkript.md').read_text()
        self.assertIn('[00:00:00] Hallo', text)
        self.assertIn('[00:01:10] Welt', text)  # leeres Segment entfällt
        self.assertIn('Modell medium', text)
        self.assertFalse(any('Netz' in z for z in self.log))

    def test_fehlendes_modell_wird_einmal_geladen_und_gemeldet(self):
        self.lege('a.wav')
        self.lege('b.mp3', b'AUDIO-2')
        self.lauf()
        laden = [a[2] for a in self.aufrufe if a[0] == 'laden']
        self.assertEqual(laden, [True, False])  # lokal versucht, dann einmal geladen; das zweite Audio nutzt dasselbe Modell
        self.assertEqual(sum('einmaliges Laden aus dem Netz' in z for z in self.log), 1)
        self.assertEqual(self.ordner(), ['2026-11-05-01', '2026-11-05-02'])

    def test_offline_verbietet_das_laden(self):
        self.lege('a.wav')
        _, fehler = self.lauf(offline=True)
        self.assertEqual(fehler, 1)
        self.assertEqual([a[2] for a in self.aufrufe if a[0] == 'laden'], [True])
        self.assertTrue(any('--offline verbietet' in z for z in self.log))
        self.assertTrue((self.neu / 'a.wav').exists())

    def test_ohne_faster_whisper_nennt_die_meldung_den_weg(self):
        self.lege('a.wav')
        self.lege('b.vtt', VTT)
        with mock.patch.dict(sys.modules, {'faster_whisper': None}):  # Import schlägt fehl
            _, fehler = self.lauf()
        self.assertEqual(fehler, 1)
        self.assertTrue(any('installieren.sh --mit-transkription' in z for z in self.log))
        self.assertEqual(self.ordner(), ['2026-11-05-01'])  # die VTT ging trotzdem durch


class SkriptTest(Grund):
    """`ops/transkribieren.sh` als Ganzes, mit `faster_whisper` als Datei der Attrappe."""

    def setUp(self):
        super().setUp()
        self.fremd = self.tmp / 'fremd'
        self.fremd.mkdir()
        (self.fremd / 'faster_whisper.py').write_text(
            'import os, types\nfrom pathlib import Path\n'
            'class WhisperModel:\n'
            '    def __init__(self, name, **kw):\n'
            '        m = Path(os.environ["FAKE_MODELL"])\n'
            '        if kw["local_files_only"] and not m.exists():\n'
            '            raise FileNotFoundError(name)\n'
            '        m.touch()\n'
            '    def transcribe(self, pfad, **kw):\n'
            '        return iter([types.SimpleNamespace(start=3, end=4, text="Probe")]), types.SimpleNamespace(duration=4)\n')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('DEVFLOW2_')}
        self.env.update(HOME=str(self.tmp / 'h'), PYTHONPATH=str(self.fremd), FAKE_MODELL=str(self.tmp / 'modell'),
                        DEVFLOW2_TRANSKRIPTION_PYTHON=sys.executable, DEVFLOW2_WHISPER_CACHE=str(self.tmp / 'cache'))

    def start(self, *args, **env):
        return subprocess.run(['bash', str(SKRIPT), *args], cwd=self.tmp, capture_output=True, text=True, env={**self.env, **env})

    def test_standardordner_ist_material_neu(self):
        self.lege('a.m4a')
        self.lege('b.vtt', VTT)
        r = self.start('--datum', '2026-11-05')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ordner(), ['2026-11-05-01', '2026-11-05-02'])
        self.assertIn('Probe', (self.interviews / '2026-11-05-01' / 'transkript.md').read_text())
        self.assertIn('einmaliges Laden aus dem Netz', r.stdout)
        self.lege('c.m4a', b'AUDIO-3')  # das Modell ist jetzt da: zweiter Lauf offline
        r = self.start('--datum', '2026-11-05', '--offline')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('Netz', r.stdout)

    def test_fehler_geben_exitcode_und_hilfe(self):
        self.lege('a.m4a')
        r = self.start('--offline', '--datum', '2026-11-05')  # Modell fehlt
        self.assertEqual(r.returncode, 1)
        self.assertIn('--offline verbietet', r.stdout)
        self.assertEqual(self.start('--unsinn').returncode, 64)
        self.assertEqual(self.start('--threads', 'x').returncode, 64)
        r = self.start('gibtsnicht')
        self.assertEqual((r.returncode, 'Nicht gefunden' in r.stderr), (1, True))
        self.assertEqual(self.start('--help').returncode, 0)

    def test_ohne_eigene_umgebung_sagt_das_skript_was_fehlt(self):
        self.lege('b.vtt', VTT)
        leer = self.tmp / 'leeres-home'
        r = self.start('--datum', '2026-11-05', DEVFLOW2_HOME=str(leer), DEVFLOW2_TRANSKRIPTION_PYTHON='')
        self.assertIn('installieren.sh --mit-transkription', r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # VTT geht auch ohne
        self.lege('a.m4a')
        r = self.start('--datum', '2026-11-05', DEVFLOW2_HOME=str(leer), DEVFLOW2_TRANSKRIPTION_PYTHON='', PYTHONPATH='')
        self.assertEqual(r.returncode, 1)
        self.assertIn('faster-whisper fehlt für Audio', r.stdout)
        self.assertTrue((self.neu / 'a.m4a').exists())


if __name__ == '__main__':
    unittest.main()
