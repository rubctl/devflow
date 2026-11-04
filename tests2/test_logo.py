"""Logo (S4.1): Signet-SVG, App-Icons im Manifest, Push-Badge, Favicon; Maskable mit Schutzzone."""
from pathlib import Path
import re
import struct
import unittest

from devflow2 import web_html, web_push

STATIC = Path(web_push.__file__).with_name('static')


def png_groesse(pfad):
    daten = pfad.read_bytes()
    assert daten[:8] == b'\x89PNG\r\n\x1a\n', pfad
    return struct.unpack('>II', daten[16:24])


class IconsTest(unittest.TestCase):
    def test_manifest_hat_alle_groessen_je_any_und_maskable(self):
        icons = web_push.MANIFEST['icons']
        self.assertEqual(sorted((i['sizes'], i['purpose']) for i in icons),
                         [('192x192', 'any'), ('192x192', 'maskable'), ('512x512', 'any'), ('512x512', 'maskable')])
        for i in icons:  # kein „any maskable“ in einem Eintrag, Datei da und genau so groß wie angegeben
            self.assertEqual(i['type'], 'image/png')
            px = int(i['sizes'].split('x')[0])
            self.assertEqual(png_groesse(STATIC / i['src'].removeprefix('/static/')), (px, px), i['src'])

    def test_badge_96_px_und_einfarbig(self):
        self.assertEqual(png_groesse(STATIC / 'icons' / 'badge-96.png'), (96, 96))
        svg = (STATIC / 'logo' / 'devflow-badge.svg').read_text(encoding='utf-8')
        self.assertEqual(set(re.findall(r'#[0-9A-Fa-f]{6}', svg)), {'#FFFFFF'})

    def test_service_worker_verweist_auf_vorhandene_dateien(self):
        sw = (STATIC / 'sw.js').read_text(encoding='utf-8')
        pfade = re.findall(r"/static/[\w/.-]+\.png", sw)
        self.assertIn('/static/icons/badge-96.png', pfade)
        for p in pfade:
            self.assertTrue((STATIC / p.removeprefix('/static/')).is_file(), p)

    def test_favicon_ico_mit_16_32_48(self):
        ico = (STATIC / 'favicon.ico').read_bytes()
        _, art, anzahl = struct.unpack('<HHH', ico[:6])
        self.assertEqual((art, anzahl), (1, 3))
        groessen = []
        for i in range(anzahl):
            breite, hoehe, _, _, _, _, laenge, offset = struct.unpack('<BBBBHHII', ico[6 + 16 * i:22 + 16 * i])
            self.assertEqual(ico[offset:offset + 4], b'\x89PNG')
            self.assertEqual(len(ico[offset:offset + laenge]), laenge)
            groessen.append((breite, hoehe))
        self.assertEqual(groessen, [(16, 16), (32, 32), (48, 48)])
        for px in (16, 32, 48):
            self.assertEqual(png_groesse(STATIC / 'icons' / f'favicon-{px}.png'), (px, px))

    def test_maskable_liegt_im_kreis_der_schutzzone(self):
        svg = (STATIC / 'logo' / 'devflow-icon-maskable.svg').read_text(encoding='utf-8')
        breite = float(re.search(r'viewBox="0 0 (\d+) ', svg).group(1))
        verschiebung, faktor = (float(x) for x in re.search(r'translate\(([\d.]+) [\d.]+\) scale\(([\d.]+)\)', svg).groups())
        mitte = breite / 2
        self.assertAlmostEqual(verschiebung + 60 * faktor, mitte, places=1)  # Signet (Mitte 60,60) sitzt mittig
        ecke = ((60 - 8) ** 2 + (60 - 8) ** 2) ** 0.5 * faktor  # weiteste Kachelecke (8,8), ohne Eselsohr
        self.assertLessEqual(ecke, 0.4 * breite)  # Kreis mit 80 % Durchmesser

    def test_kopf_und_favicon_verweisen_auf_vorhandene_dateien(self):
        html = str(web_html.seite('x'))
        for ziel in re.findall(r'(?:src|href)="(/static/(?:logo|icons|favicon)[^"]*)"', html):
            self.assertTrue((STATIC / ziel.removeprefix('/static/')).is_file(), ziel)
        self.assertIn('/static/logo/devflow-signet.svg', html)
        self.assertIn('/static/favicon.ico', html)


if __name__ == '__main__':
    unittest.main()
