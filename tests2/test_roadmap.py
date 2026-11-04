"""Roadmap-, Plan-, Urteils- und Kontingentfunktionen ohne Läufe."""
import time
import unittest

from devflow2 import Fehler, ablauf, laeufe, roadmap, inhaber
from hilfen import PLAN, ROADMAP


class RoadmapTest(unittest.TestCase):
    def test_anlegen_setzen_und_stand(self):
        t = roadmap.anlegen(ROADMAP, 'A1', 'Wetter | Kachel', '29.10.', 'Claude', 'bereit', 'Plan `plan/x.md`')
        self.assertIn('| A1 | Wetter / Kachel | Claude | bereit | 29.10. | Plan `plan/x.md` |', t)
        t = roadmap.setze(t, 'A1', '30.10.', 'in Arbeit', stand='Branch `claude-a1`')
        t = roadmap.setze(t, 'A1', '30.10.', stand='Entwicklung fertig')
        self.assertIn('| A1 | Wetter / Kachel | Claude | in Arbeit | 30.10. | Plan `plan/x.md` · Stand: Entwicklung fertig |', t)
        with self.assertRaises(Fehler):
            roadmap.setze(t, 'A1', '30.10.', 'fertig')
        with self.assertRaises(Fehler):
            roadmap.anlegen(t, 'A1', 'doppelt', '30.10.')

    def test_kanban_wie_vorlage(self):
        t = roadmap.anlegen(ROADMAP, 'A1', '**Kachel** mit Ort', '29.10.', 'Claude', 'Review')
        t = roadmap.kanban(t)
        self.assertIn('**Review** (1)\n\n- `A1` Kachel · Claude · 29.10.', t)
        self.assertIn('**Braucht dich** –', t)
        self.assertIn('Offen: 0 · erledigt: 1', t)
        self.assertEqual(roadmap.kanban(t), t)

    def test_frage_und_antwort(self):
        t = roadmap.anlegen(ROADMAP, 'A1', 'Kachel', '29.10.', status='bereit')
        t, nr = roadmap.frage(t, '29.10.', 'Celsius oder Fahrenheit?', ['Celsius', 'Fahrenheit'], 'Celsius', 'A1')
        t, nr2 = roadmap.frage(t, '29.10.', 'Zweite Frage?', [], '')
        self.assertEqual((nr, nr2), (1, 2))
        self.assertNotIn('_Keine._', t)
        self.assertLess(t.index('### F2'), t.index('### F1'))
        self.assertEqual([f['nr'] for f in roadmap.fragen(t)], [2, 1])
        t, bezug = roadmap.antwort(t, 1, '30.10.', 'Celsius', 'dashboard')
        self.assertEqual(bezug, 'A1')
        self.assertIn(f'**Antwort {inhaber.kennung()} (30.10., dashboard):** Celsius', t)
        self.assertEqual([f['nr'] for f in roadmap.fragen(t)], [2])
        with self.assertRaises(Fehler):
            roadmap.antwort(t, 1, '30.10.', 'nochmal', 'dashboard')

    def test_protokoll_und_neue_tabelle(self):
        t = roadmap.protokoll(ROADMAP, '30.10.2026 · neu')
        self.assertIn('## Protokoll\n\n- 30.10.2026 · neu\n- 29.10.2026', t)
        t = roadmap.neue_tabelle(t, 'Wetter ([Plan](plan/wetter.md))', [('A1', 'Kachel', 'Claude', 'bereit', '30.10.', '')])
        self.assertLess(t.index('### Wetter'), t.index('## Protokoll'))
        self.assertEqual([a['id'] for a in roadmap.aufgaben(t)], ['0.1', 'A1'])
        with self.assertRaises(Fehler):
            roadmap.neue_tabelle(t, 'nochmal', [('A1', 'x', '', 'bereit', '', '')])

    def test_plan_lesen(self):
        p = roadmap.plan(PLAN)
        self.assertEqual(p['abgenommen'], '29.10.2026')
        self.assertEqual([a['id'] for a in p['aufgaben']], ['A1', 'A2'])
        self.assertEqual(p['aufgaben'][1]['entwickler'], 'sol')
        self.assertIsNone(roadmap.plan(PLAN.replace('**Stand:** abgenommen', '**Stand:** Entwurf, abgenommen'))['abgenommen'])


class UrteilTest(unittest.TestCase):
    def test_p2_macht_bestanden_zu_nacharbeit(self):
        u = ablauf.urteil('Hier: {"urteil": "bestanden", "bericht": "x", "befunde": [{"nr": "M1", "schwere": "P2 Lücke", "ort": "a:1", "was": "y"}]}')
        self.assertEqual(u['urteil'], 'nacharbeit')
        u = ablauf.urteil('{"urteil": "bestanden", "bericht": "x", "befunde": [{"nr": "M1", "schwere": "P3", "ort": "a:1", "was": "y"}]}')
        self.assertEqual(u['urteil'], 'bestanden')
        with self.assertRaises(Fehler):
            ablauf.urteil('Sieht gut aus.')


class KontingentTest(unittest.TestCase):
    def test_freigabezeiten(self):
        jetzt = 1_800_000_000
        self.assertEqual(laeufe.frei_ab(jetzt + 3600, jetzt), jetzt + 3600)
        self.assertEqual(laeufe.frei_ab(str((jetzt + 3600) * 1000), jetzt), jetzt + 3600)
        self.assertEqual(laeufe.frei_ab(None, jetzt), jetzt + 1800)
        self.assertEqual(laeufe.frei_ab(jetzt - 5, jetzt), jetzt + 60)
        self.assertEqual(laeufe.frei_ab(jetzt + 30 * 86400, jetzt), jetzt + 7 * 86400)
        ziel = laeufe.frei_ab('3:05 PM', jetzt)
        self.assertTrue(jetzt < ziel <= jetzt + 86400 and time.localtime(ziel).tm_hour == 15)
        # genannte Uhrzeit gerade vorbei: in 5 Minuten erneut, nicht erst morgen
        kurz_danach = time.mktime(time.localtime(ziel)[:4] + (0, 20, 0, 0, -1))
        self.assertEqual(laeufe.frei_ab('3:00 PM', kurz_danach), kurz_danach + 300)
        self.assertEqual(laeufe.frei_ab('3:00 PM', kurz_danach + 4 * 3600) - (kurz_danach + 4 * 3600), 86400 - 4 * 3600 - 20)
        self.assertEqual(laeufe.frei_ab('3:00 PM', kurz_danach - 20 + 3 * 3600) - (kurz_danach - 20 + 3 * 3600), 86400 - 3 * 3600)
        # über Mitternacht: „11:59 PM“ um 00:00:20 ist 80 s her
        mitternacht = time.mktime(time.localtime(ziel)[:3] + (0, 0, 20, 0, 0, -1))
        self.assertEqual(laeufe.frei_ab('11:59 PM', mitternacht), mitternacht + 300)

    def test_auswerten(self):
        out = '\n'.join(['{"type":"system","session_id":"sess-12345678"}',
                         '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1800000000}}',
                         '{"type":"result","is_error":true,"result":"You\'ve hit your limit"}'])
        self.assertEqual(laeufe.auswerten(out, ''), ('sess-12345678', True, 1800000000, "You've hit your limit"))
        ok = '{"type":"thread.started","thread_id":"thread-123456789"}\n{"type":"item.completed","item":{"type":"agent_message","text":"usage limit im Code zitiert"}}'
        self.assertEqual(laeufe.auswerten(ok, '')[:2], ('thread-123456789', False))


if __name__ == '__main__':
    unittest.main()
