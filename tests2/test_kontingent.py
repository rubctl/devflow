"""Kontingent-Anzeige (S3.2): feste Logauszüge für Claude und Sol, fehlende und veraltete Werte, Kosten, Kopfzeile und Seite."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import kontingent, laeufe, web  # noqa: E402
from hilfen import Fall  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}

# Auszug aus einem echten Claude-Lauflog (gekürzt): zwei Ereignisse, das spätere gilt
CLAUDE = [
    '{"type":"system","subtype":"init","session_id":"010f09cb-d7e6-49e1-80f6-7ccc278a5e99"}',
    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1793397000,"rateLimitType":"five_hour","isUsingOverage":false,'
    '"unifiedWindows":{"five_hour":{"utilization":0.08,"resetsAt":1793397000,"status":"allowed"},'
    '"seven_day":{"utilization":0.62,"resetsAt":1793689200,"status":"allowed"}}},"session_id":"010f09cb-d7e6-49e1-80f6-7ccc278a5e99"}',
    'kein json mit rate_limit_event darin',
    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1793397000,"rateLimitType":"five_hour","isUsingOverage":false,'
    '"unifiedWindows":{"five_hour":{"utilization":0.1,"resetsAt":1793397000,"status":"allowed"},'
    '"seven_day":{"utilization":0.62,"resetsAt":1793689200,"status":"allowed"}}},"session_id":"010f09cb-d7e6-49e1-80f6-7ccc278a5e99"}',
    '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.5674545,"session_id":"010f09cb-d7e6-49e1-80f6-7ccc278a5e99"}',
]
# Auszug aus einer codex-Sitzungsdatei (gekürzt)
SOL = [
    '{"timestamp":"2026-10-30T17:30:00.000Z","type":"session_meta","payload":{"id":"01a18de3"}}',
    '{"timestamp":"2026-10-30T17:35:00.000Z","ordinal":90,"type":"event_msg","payload":{"type":"token_count","info":null,'
    '"rate_limits":{"limit_id":"codex","primary":{"used_percent":38.0,"window_minutes":300,"resets_at":1793388625},'
    '"secondary":{"used_percent":93.0,"window_minutes":10080,"resets_at":1793774722},"plan_type":"plus"}}}',
    '{"timestamp":"2026-10-30T17:39:31.459Z","ordinal":94,"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":5}},'
    '"rate_limits":{"limit_id":"codex","limit_name":null,"primary":{"used_percent":40.0,"window_minutes":300,"resets_at":1793388625},'
    '"secondary":{"used_percent":94.0,"window_minutes":10080,"resets_at":1793774722},"credits":{"has_credits":false},"plan_type":"plus"}}}',
]
SOL_STAND = 1793381971.459
CLAUDE_STAND = 1793382500
JETZT = 1793383000  # kurz nach beiden Messungen, vor allen Freigabezeiten


def schreiben(pfad, zeilen, mtime=None):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text('\n'.join(zeilen) + '\n', encoding='utf-8')
    if mtime:
        os.utime(pfad, (mtime, mtime))
    return pfad


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='df2-k-'))
        env = {'DEVFLOW2_STATE': str(self.tmp / 'state'), 'DEVFLOW2_CODEX_SITZUNGEN': str(self.tmp / 'codex')}
        self._alt = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        kontingent._ZWISCHEN.clear()
        self.addCleanup(self.aufraeumen)

    def aufraeumen(self):
        for k, v in self._alt.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        for p in self.tmp.rglob('*'):
            p.chmod(0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def claude_log(self, name, zeilen, mtime=CLAUDE_STAND):
        return schreiben(self.tmp / 'state' / 'laeufe' / name / 'log.jsonl', zeilen, mtime)

    def sol_datei(self, tag, zeilen, mtime=SOL_STAND, name='rollout-x'):
        return schreiben(self.tmp / 'codex' / '2026' / '10' / tag / f'{name}.jsonl', zeilen, mtime)


class ClaudeTest(Basis):
    def test_jungstes_ereignis_je_fenster(self):
        m = kontingent.claude_aus_zeilen(CLAUDE)
        self.assertEqual(m, {'5h': {'prozent': 10.0, 'frei': 1793397000.0}, 'woche': {'prozent': 62.0, 'frei': 1793689200.0}})

    def test_aus_dem_lauflog_mit_stand_und_bewertung(self):
        self.claude_log('lauf-1', CLAUDE)
        s = kontingent.stand(JETZT)['claude']
        self.assertEqual((s['5h']['zustand'], s['5h']['prozent'], s['5h']['frei'], s['5h']['stand']), ('aktuell', 10.0, 1793397000.0, CLAUDE_STAND))
        self.assertEqual((s['woche']['zustand'], s['woche']['prozent']), ('aktuell', 62.0))

    def test_neuester_lauf_gewinnt_und_fehlendes_fenster_kommt_aus_aelterem(self):
        self.claude_log('alt', CLAUDE, mtime=CLAUDE_STAND - 100)
        nur5 = [CLAUDE[1].replace('"seven_day":{"utilization":0.62,"resetsAt":1793689200,"status":"allowed"}', '"seven_day":"kaputt"').replace('0.08', '0.5')]
        self.claude_log('neu', nur5)
        s = kontingent.stand(JETZT)['claude']
        self.assertEqual(s['5h']['prozent'], 50.0)
        self.assertEqual(s['woche']['prozent'], 62.0)
        self.assertEqual(s['woche']['stand'], CLAUDE_STAND - 100)

    def test_aeltere_form_nur_rejected_ist_voll(self):
        z = '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1793397000,"rateLimitType":"five_hour"}}'
        self.assertEqual(kontingent.claude_aus_zeilen([z]), {'5h': {'prozent': 100.0, 'frei': 1793397000.0}})
        erlaubt = z.replace('rejected', 'allowed')
        self.assertEqual(kontingent.claude_aus_zeilen([erlaubt]), {'5h': {'prozent': None, 'frei': 1793397000.0}})

    def test_ungueltige_werte_werden_ignoriert(self):
        z = ('{"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":{"five_hour":{"utilization":"viel","resetsAt":"bald"},'
             '"seven_day":{"utilization":7,"resetsAt":-5}}}}')
        self.assertEqual(kontingent.claude_aus_zeilen([z, '[]', '{"type":"rate_limit_event"}', 'rate_limit_event']),
                         {'5h': {'prozent': None, 'frei': None}, 'woche': {'prozent': None, 'frei': None}})

    def test_keine_daten_ist_unbekannt(self):
        s = kontingent.stand(JETZT)['claude']  # nicht einmal ein Zustandsverzeichnis
        for f in s.values():
            self.assertEqual((f['zustand'], f['prozent']), ('unbekannt', None))
        self.claude_log('leer', ['{"type":"result"}'])
        self.assertEqual(kontingent.stand(JETZT)['claude']['5h']['zustand'], 'unbekannt')

    def test_veraltet_nach_zwei_stunden(self):
        self.claude_log('lauf-1', CLAUDE)
        f = kontingent.stand(CLAUDE_STAND + kontingent.ALTER + 60)['claude']['5h']
        self.assertEqual((f['zustand'], f['prozent']), ('veraltet', 10.0))
        f = kontingent.stand(CLAUDE_STAND + kontingent.ALTER - 60)['claude']['5h']
        self.assertEqual(f['zustand'], 'aktuell')

    def test_nach_der_freigabezeit_ist_der_wert_ueberholt(self):
        self.claude_log('lauf-1', CLAUDE)
        s = kontingent.stand(1793397001)['claude']
        self.assertEqual((s['5h']['zustand'], s['5h']['prozent'], s['5h']['frei']), ('unbekannt', None, 1793397000.0))
        self.assertEqual(s['woche']['zustand'], 'veraltet')  # Woche läuft noch, Messung ist aber alt


class SolTest(Basis):
    def test_jungste_messung_aus_den_zeilen(self):
        m = kontingent.sol_aus_zeilen(SOL, 1)
        self.assertEqual(m['5h'], {'prozent': 40.0, 'frei': 1793388625.0, 'stand': SOL_STAND})
        self.assertEqual(m['woche'], {'prozent': 94.0, 'frei': 1793774722.0, 'stand': SOL_STAND})

    def test_stand_aus_der_sitzungsdatei(self):
        self.sol_datei('30', SOL)
        s = kontingent.stand(JETZT)['sol']
        self.assertEqual((s['5h']['zustand'], s['5h']['prozent'], s['woche']['prozent']), ('aktuell', 40.0, 94.0))
        self.assertEqual(s['5h']['stand'], SOL_STAND)

    def test_neueste_sitzung_gewinnt_je_nach_zeitstempel(self):
        neu = [SOL[2].replace('2026-10-30T17:39:31.459Z', '2026-10-30T18:00:00.000Z').replace('40.0', '55.0')]
        self.sol_datei('29', neu, name='rollout-alt')
        self.sol_datei('30', SOL, name='rollout-neu')
        self.assertEqual(kontingent.stand(JETZT)['sol']['5h']['prozent'], 55.0)

    def test_fortgesetzte_sitzung_in_altem_tagesordner_gewinnt(self):
        alt = [SOL[2].replace('40.0', '20.0')]
        for i in range(kontingent.SOL_DATEIEN):
            self.sol_datei('30', alt, mtime=SOL_STAND - 500 + i, name=f'rollout-{i}')
        fortgesetzt = [SOL[2].replace('2026-10-30T17:39:31.459Z', '2026-10-30T17:50:00.000Z').replace('40.0', '95.0')]
        self.sol_datei('23', fortgesetzt, mtime=SOL_STAND + 100, name='rollout-fortgesetzt')
        f = kontingent.stand(JETZT)['sol']['5h']
        self.assertEqual((f['zustand'], f['prozent']), ('aktuell', 95.0))

    def test_nur_die_jungsten_dateien_werden_gelesen(self):
        for i in range(kontingent.SOL_DATEIEN):
            self.sol_datei('30', [SOL[2]], mtime=SOL_STAND + i, name=f'rollout-{i}')
        aelter = [SOL[2].replace('2026-10-30T17:39:31.459Z', '2026-10-30T17:50:00.000Z').replace('40.0', '99.0')]
        self.sol_datei('01', aelter, mtime=SOL_STAND - 1000, name='rollout-aelter')
        self.assertEqual(kontingent.stand(JETZT)['sol']['5h']['prozent'], 40.0)

    def test_veraltet_und_ueberholt(self):
        self.sol_datei('30', SOL)
        self.assertEqual(kontingent.stand(SOL_STAND + kontingent.ALTER + 5)['sol']['woche']['zustand'], 'veraltet')
        f = kontingent.stand(1793388626)['sol']['5h']
        self.assertEqual((f['zustand'], f['prozent']), ('unbekannt', None))

    def test_fehlende_und_fremde_formate_sind_unbekannt(self):
        self.assertTrue(all(f['zustand'] == 'unbekannt' for f in kontingent.stand(JETZT)['sol'].values()))  # kein Verzeichnis
        anders = ['{"timestamp":"2026-10-30T17:39:31Z","payload":{"rate_limits":{"limit_id":"codex","primary":{"percent":4},"secondary":null}}}',
                  '{"timestamp":"2026-10-30T17:39:31Z","payload":{"rate_limits":{"limit_id":"codex","primary":{"used_percent":4,"window_minutes":60}}}}',
                  '{"timestamp":"2026-10-30T17:39:31Z","payload":{"rate_limits":{"limit_id":"andere","primary":{"used_percent":4}}}}',
                  '{"payload":{"rate_limits":[]}}', 'rate_limits kaputt{']
        self.sol_datei('30', anders)
        self.assertTrue(all(f['zustand'] == 'unbekannt' for f in kontingent.stand(JETZT)['sol'].values()))

    def test_nur_das_ende_grosser_dateien_wird_gelesen(self):
        fuell = ['{"timestamp":"2026-10-30T17:00:00Z","x":"' + 'a' * 1000 + '"}'] * 700
        self.sol_datei('30', fuell + SOL)
        self.assertEqual(kontingent.stand(JETZT)['sol']['5h']['prozent'], 40.0)

    def test_nur_lesend(self):
        self.sol_datei('30', SOL)
        wurzel = self.tmp / 'codex'

        def abbild():
            return sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in wurzel.rglob('*'))
        vorher = abbild()
        for p in wurzel.rglob('*'):
            p.chmod(0o555 if p.is_dir() else 0o444)
        kontingent.stand(JETZT)
        self.assertEqual(abbild(), vorher)

    def test_ohne_umgebung_wird_die_echte_ablage_nur_als_pfad_gebildet(self):
        os.environ.pop('DEVFLOW2_CODEX_SITZUNGEN')
        self.assertEqual(kontingent.codex_sitzungen(), Path.home() / '.codex' / 'sessions')


class KostenTest(Basis):
    def lauf(self, lid, modell='claude', aufgabe='A1', zeilen=None, projekt='demo', **mehr):
        laeufe.speichern({'id': lid, 'projekt': projekt, 'art': 'entwickeln', 'modell': modell, 'aufgabe': aufgabe, 'zustand': 'fertig',
                          'angelegt': mehr.pop('angelegt', time.time()), **mehr})
        if zeilen is not None:
            self.claude_log(lid, zeilen)

    def test_summe_der_result_ereignisse(self):
        self.assertAlmostEqual(kontingent.kosten_aus_zeilen(CLAUDE), 0.5674545)
        zwei = ['{"type":"result","total_cost_usd":0.25}', '{"type":"result","total_cost_usd":"x"}', '{"type":"assistant","total_cost_usd":9}',
                '{"type":"result","total_cost_usd":0.5}', 'total_cost_usd kaputt']
        self.assertAlmostEqual(kontingent.kosten_aus_zeilen(zwei), 0.75)
        self.assertIsNone(kontingent.kosten_aus_zeilen(['{"type":"result"}']))

    def test_je_aufgabe_und_lauf_sol_und_fehlendes_ohne_kosten(self):
        self.lauf('c1', zeilen=['{"type":"result","total_cost_usd":0.5}'], angelegt=100)
        self.lauf('c2', zeilen=['{"type":"result","total_cost_usd":0.25}'], angelegt=200)
        self.lauf('c3', zeilen=['{"type":"assistant"}'], angelegt=300)  # kein Ergebnis
        self.lauf('s1', modell='codex', aufgabe='A1', zeilen=['{"type":"result","total_cost_usd":5}'], angelegt=400)
        self.lauf('c4', aufgabe='A2', angelegt=500)  # Log fehlt
        self.lauf('c5', aufgabe=None, zeilen=['{"type":"result","total_cost_usd":1}'], angelegt=600)
        je_aufgabe, je_lauf = kontingent.kosten()
        self.assertEqual(je_aufgabe[('demo', 'A1')]['laeufe'], 4)
        self.assertEqual(je_aufgabe[('demo', 'A1')]['bekannt'], 2)
        self.assertAlmostEqual(je_aufgabe[('demo', 'A1')]['summe'], 0.75)
        self.assertEqual((je_aufgabe[('demo', 'A2')]['bekannt'], je_aufgabe[('demo', 'A2')]['summe']), (0, 0.0))
        self.assertAlmostEqual(je_aufgabe[('demo', '')]['summe'], 1.0)
        self.assertEqual([e['lauf']['id'] for e in je_lauf], ['c5', 'c4', 's1', 'c3', 'c2', 'c1'])
        self.assertIsNone(next(e for e in je_lauf if e['lauf']['id'] == 's1')['kosten'])

    def test_aenderung_im_log_wird_erkannt(self):
        self.lauf('c1', zeilen=['{"type":"result","total_cost_usd":0.5}'])
        self.assertAlmostEqual(kontingent.kosten()[0][('demo', 'A1')]['summe'], 0.5)
        self.claude_log('c1', ['{"type":"result","total_cost_usd":0.5}', '{"type":"result","total_cost_usd":0.5}'], mtime=CLAUDE_STAND + 5)
        self.assertAlmostEqual(kontingent.kosten()[0][('demo', 'A1')]['summe'], 1.0)


class WebTest(Fall):
    def setUp(self):
        super().setUp()
        kontingent._ZWISCHEN.clear()
        self.client = TestClient(web.anwendung(), headers=LOGIN)
        self.jetzt = time.time()

    def messungen(self):
        j = int(self.jetzt)
        claude = CLAUDE[3].replace('1793397000', str(j + 3600)).replace('1793689200', str(j + 3 * 86400))
        schreiben(Path(os.environ['DEVFLOW2_STATE']) / 'laeufe' / 'c1' / 'log.jsonl', [claude, CLAUDE[4]])
        sol = SOL[2].replace('2026-10-30T17:39:31.459Z', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.jetzt - 60))
                             ).replace('1793388625', str(j + 7200)).replace('1793774722', str(j + 5 * 86400))
        schreiben(Path(os.environ['DEVFLOW2_CODEX_SITZUNGEN']) / '2026' / '10' / '30' / 'rollout-x.jsonl', [sol])

    def test_kopfzeile_ohne_daten_zeigt_unbekannt_auf_jeder_seite(self):
        for pfad in ('/', '/p/demo', '/p/demo/a/A1', '/kontingent', '/hinweise', '/gibt-es-nicht'):
            r = self.client.get(pfad)
            kopf = r.text.split('</header>')[0]
            for k in ('claude-5h', 'claude-woche', 'sol-5h', 'sol-woche'):
                self.assertIn(f'data-kontingent="{k}"', kopf, pfad)
            self.assertEqual(kopf.count('</span>unbekannt</span>'), 4, pfad)
            self.assertIn('href="/kontingent"', kopf)

    def test_kopfzeile_mit_prozent_freigabe_und_stand(self):
        self.messungen()
        kopf = self.client.get('/').text.split('</header>')[0]
        for k, prozent in (('claude-5h', '10 %'), ('claude-woche', '62 %'), ('sol-5h', '40 %'), ('sol-woche', '94 %')):
            feld = kopf.split(f'data-kontingent="{k}"')[1].split('</div>')[0]
            self.assertIn(prozent, feld, k)
            self.assertIn('frei ', feld, k)
            self.assertIn('role="progressbar"', feld, k)
        self.assertNotIn('unbekannt', kopf)
        self.assertIn(f'frei {web_uhr(self.jetzt + 3600)}', kopf)

    def test_kopfzeile_faellt_bei_defekter_quelle_nicht_aus(self):
        self.messungen()
        orig = kontingent.stand
        kontingent.stand = lambda *a: 1 / 0
        self.addCleanup(setattr, kontingent, 'stand', orig)
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('unbekannt', r.text.split('</header>')[0])

    def test_kontingentseite_fenster_und_kosten(self):
        self.messungen()
        laeufe.speichern({'id': 'c1', 'projekt': 'demo', 'art': 'entwickeln', 'modell': 'claude', 'aufgabe': 'A1', 'zustand': 'fertig', 'angelegt': self.jetzt})
        laeufe.speichern({'id': 'x1', 'projekt': 'demo', 'art': 'review', 'modell': 'codex', 'aufgabe': '<b>A9</b>', 'zustand': 'fehler', 'angelegt': self.jetzt - 5})
        laeufe.speichern({'id': 'x2', 'projekt': 'demo', 'art': 'steuern', 'modell': 'claude', 'aufgabe': '', 'zustand': 'fertig', 'angelegt': self.jetzt - 9})
        r = self.client.get('/kontingent')
        self.assertEqual(r.status_code, 200)
        s = r.text.split('<main>')[1]
        self.assertIn('0,57 $', s)
        self.assertIn('Summe', s)
        self.assertIn('ohne Aufgabe', s)
        self.assertIn('<progress value="94" max="100"', s)
        self.assertIn('&lt;b&gt;A9&lt;/b&gt;', s)
        self.assertNotIn('<b>A9</b>', s)
        self.assertIn('/lauf/c1', s)

    def test_kontingentseite_ohne_laeufe_und_daten(self):
        r = self.client.get('/kontingent')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Noch keine Läufe.', r.text)
        self.assertIn('unbekannt', r.text)

    def test_nur_get(self):
        self.assertEqual(self.client.post('/kontingent', headers={'Origin': 'http://testserver'}).status_code, 405)
        self.assertEqual(TestClient(web.anwendung()).get('/kontingent').status_code, 403)


def web_uhr(ts):
    from devflow2.web_kontingent import uhr
    return uhr(ts)


class FormatTest(unittest.TestCase):
    def test_geld_und_uhr(self):
        from devflow2.web_kontingent import geld, uhr
        self.assertEqual((geld(None), geld(0.5674545), geld(1234.5)), ('–', '0,57 $', '1 234,50 $'))
        self.assertEqual(uhr(None), '–')
        self.assertRegex(uhr(1793383000, 1793383000 + 60), r'^\d\d:\d\d$')
        self.assertRegex(uhr(1793383000, 1793383000 + 3 * 86400), r'^\d\d\.\d\d\. \d\d:\d\d$')


if __name__ == '__main__':
    unittest.main()
