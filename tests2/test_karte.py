"""Repo-Karte: Namen aus Python, JS und Markdown, Ausschlüsse, Grenze, Umlaute, Fehlerfälle."""
from pathlib import Path
import shutil
import tempfile
import unittest

from devflow2.karte import karte
from hilfen import git


class KarteTest(unittest.TestCase):
    def setUp(self):
        self.wt = Path(tempfile.mkdtemp(prefix='df2-karte-'))
        self.addCleanup(shutil.rmtree, self.wt, ignore_errors=True)
        git(self.wt, 'init', '-q')

    def datei(self, name, inhalt='', binaer=False):
        pfad = self.wt / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        if binaer:
            pfad.write_bytes(inhalt)
        else:
            pfad.write_text(inhalt, encoding='utf-8')

    def zeilen(self, **kw):
        git(self.wt, 'add', '-A')
        return karte(self.wt, **kw).split('\n')

    def test_python_klasse_methoden_und_funktion(self):
        self.datei('datei.py', 'class Klasse:\n    def m1(self): pass\n    async def m2(self): pass\n\n\ndef funktion(): pass\n')
        self.assertEqual(self.zeilen(), ['datei.py: Klasse(m1, m2), funktion'])

    def test_javascript_function_pfeilfunktion_und_async(self):
        js = 'function a() {}\nexport const x = () => 1\nasync function b() {}\n'
        self.datei('skript.js', js)
        self.datei('modul.mjs', js)
        self.assertEqual(self.zeilen(), ['modul.mjs: a, x, b', 'skript.js: a, x, b'])

    def test_markdown_zeigt_erste_ueberschrift(self):
        self.datei('lies.md', 'Vorspann\n\n# Titel\n\n# Zweiter\n')
        self.datei('leer.md', 'nur Text\n')
        self.assertEqual(self.zeilen(), ['leer.md', 'lies.md: Titel'])

    def test_ausschluesse(self):
        for name in ('bild.png', 'logo.SVG', 'berichte/A1.md', '.versteckt', '.github/ci.yml', 'docs/x.md'):
            self.datei(name, '# X\n')
        self.datei('src/y.py', 'def f(): pass\n')
        self.assertEqual(self.zeilen(), ['docs/x.md: X', 'src/y.py: f'])
        self.assertEqual(karte(self.wt, auslassen=('docs/',)), 'src/y.py: f')

    def test_leeres_praefix_schliesst_nichts_aus(self):
        self.datei('src/y.py', 'def f(): pass\n')
        git(self.wt, 'add', '-A')
        self.assertEqual(karte(self.wt, auslassen=('',)), 'src/y.py: f')

    def test_grenze_kuerzt_mit_hinweis(self):
        for i in range(10):
            self.datei(f'datei{i}.py')
        zeilen = self.zeilen(grenze=35)
        self.assertEqual(zeilen[:3], ['datei0.py', 'datei1.py', 'datei2.py'])
        self.assertEqual(zeilen[3], '… und 7 weitere Dateien (`git ls-files`)')
        self.assertEqual(len(zeilen), 4)

    def test_umlaut_im_dateinamen(self):
        self.datei('ä.py', 'def größe(): pass\n')
        self.datei('berichte/ü.md', '# Bericht\n')
        self.datei('docs/ö.md', '# Ö\n')
        self.assertEqual(self.zeilen(auslassen=('docs/',)), ['ä.py: größe'])

    def test_kein_git_repo(self):
        leer = Path(tempfile.mkdtemp(prefix='df2-karte-leer-'))
        self.addCleanup(shutil.rmtree, leer, ignore_errors=True)
        self.assertEqual(karte(leer), '')

    def test_unlesbare_dateien_ergeben_nur_den_namen(self):
        self.datei('kaputt.py', 'def (:\n')
        self.datei('binaer.py', b'\xff\xfe\x00def f(): pass\n', binaer=True)
        self.assertEqual(self.zeilen(), ['binaer.py', 'kaputt.py'])


if __name__ == '__main__':
    unittest.main()
