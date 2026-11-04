"""Routine-Updates (S5.2): Pflegeplan, Werkzeug `pflege_aufgabe`, Entwurf und Frage, Doppelte, Diff-Prüfung vor dem Merge, Tests in einer Kopie."""
import json
import logging
import os
from pathlib import Path
import time
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import Fehler, ablauf, ereignis, laeufe, pflege, pflegeplan, plaene, roadmap, selbstlauf, web  # noqa: E402
from devflow2.ablauf import FrageOffen  # noqa: E402
from hilfen import Fall, git  # noqa: E402

logging.getLogger('httpx').setLevel(logging.WARNING)

PLAN = """# Pflegeplan · demo · Claude · 31.10.2026

**Stand:** abgenommen vom Inhaber am 31.10.2026

**Pflege:** aktiv

## Umfang

- **Pakete:** `alle`
- **Dateien:** `uv.lock`, `pyproject.toml`, `package.json`, `package-lock.json`, `requirements.txt`

## Sprünge

- **Erlaubt:** `patch`, `minor`

## Tests

- **Befehl:** `python3 -c "print(1)"`

## Deploy

- **Ziel:** `kein`

## Rückweg

Vorherige Lockdatei einspielen und neu installieren.

## Bekannte Brüche und Migrationen

- `sperrig`: braucht eine Datenmigration
"""

UV = """version = 1

[[package]]
name = "flask"
version = "2.2.5"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "idna"
version = "3.7.0"
source = { registry = "https://pypi.org/simple" }
"""

PYPROJECT = '[project]\nname = "demo"\nversion = "1.0"\ndependencies = ["flask>=2.2,<3", "idna"]\n'
PAKET = json.dumps({'name': 'w', 'scripts': {'test': 'x'}, 'dependencies': {'lodash': '^4.17.15'}}, indent=1)
LOCK = json.dumps({'lockfileVersion': 3, 'packages': {'': {'name': 'w'}, 'node_modules/lodash': {'version': '4.17.15', 'resolved': 'https://r/l-15.tgz', 'integrity': 'a'}}}, indent=1)


def veraltet(paket, aktuell, neu):
    return dict(paket=paket, aktuell=aktuell, neu=neu, sprung=pflege.sprung(aktuell, neu), quelle='uv')


def luecke(paket, version, behoben, ids=('CVE-1',)):
    return dict(paket=paket, version=version, eco='pypi', ids=list(ids), schwere='hoch', behoben=list(behoben), quellen=['pip-audit'], orte=[])


class Grund(Fall):
    plan_inhalt = PLAN

    def setUp(self):
        super().setUp()
        for name, inhalt in {'uv.lock': UV, 'pyproject.toml': PYPROJECT, 'package.json': PAKET, 'package-lock.json': LOCK}.items():
            (self.repo / name).write_text(inhalt)
        if self.plan_inhalt:
            (self.repo / 'plan/pflege.md').write_text(self.plan_inhalt)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'pflege')

    def plan_setzen(self, text):
        (self.repo / 'plan/pflege.md').write_text(text)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'plan')

    def ergebnis(self, veraltete=(), funde=(), zeit=None):
        e = dict(projekt='demo', zeit=zeit or time.time(), tag='2026-10-31', commit='abc', pruefungen=[], funde=list(funde), veraltet=list(veraltete),
                 geprueft=['pip-audit'], nicht_geprueft=[], hinweise=[])
        pflege.stand_speichern(e)
        return e

    def anlegen(self, *a, **k):
        return pflegeplan.aufgabe_anlegen(self.p, *a, **k)

    def anzahl_p(self):
        return sum(pflegeplan.ist_pflege(a['id']) for a in roadmap.aufgaben(self.roadmap()))


class PlanPruefung(Grund):
    def keine_aufgabe(self, muster):
        self.ergebnis([veraltet('flask', '2.2.5', '2.2.6')])
        with self.assertRaisesRegex(Fehler, muster):
            self.anlegen()
        self.assertEqual(self.anzahl_p(), 0)

    def test_abgenommener_plan_gilt(self):
        plan = pflegeplan.laden(self.p)
        self.assertIsNone(plan['pakete'])
        self.assertEqual(plan['spruenge'], ['patch', 'minor'])
        self.assertEqual(plan['deploy'], '')
        self.assertIn('sperrig', plan['brueche'])

    def test_fehlender_plan(self):
        git(self.repo, 'rm', '-q', 'plan/pflege.md')
        git(self.repo, 'commit', '-q', '-m', 'weg')
        self.keine_aufgabe('Kein Pflegeplan')

    def test_nicht_abgenommen(self):
        self.plan_setzen(PLAN.replace('**Stand:** abgenommen vom Inhaber am 31.10.2026', '**Stand:** Entwurf'))
        self.keine_aufgabe('nicht abgenommen')

    def test_abnahmezeile_nur_weiter_unten_zaehlt_nicht(self):
        self.plan_setzen(PLAN.replace('**Stand:** abgenommen vom Inhaber am 31.10.2026', '**Stand:** Entwurf') + '\n**Stand:** abgenommen vom Inhaber am 01.01.2026\n')
        self.keine_aufgabe('nicht abgenommen')

    def test_widerrufen(self):
        self.plan_setzen(PLAN.replace('**Pflege:** aktiv', '**Pflege:** widerrufen'))
        self.keine_aufgabe('widerrufen')

    def test_widerruf_gewinnt_gegen_aktiv(self):
        self.plan_setzen(PLAN + '\n**Pflege:** Widerrufen am 31.10.\n')
        self.keine_aufgabe('widerrufen')

    def test_nicht_committete_aenderung_sperrt(self):
        (self.repo / 'plan/pflege.md').write_text(PLAN.replace('**Pflege:** aktiv', '**Pflege:** widerrufen'))
        self.keine_aufgabe('nicht committete')

    def test_nicht_committeter_plan_gilt_nicht(self):
        git(self.repo, 'rm', '-q', '--cached', 'plan/pflege.md')
        git(self.repo, 'commit', '-q', '-m', 'aus dem Index')
        self.keine_aufgabe('nicht committete')

    def test_unvollstaendig_oder_zu_weit(self):
        for alt, neu, muster in (('`alle`', '`…`', 'Pakete'), ('`python3 -c "print(1)"`', '`…`', 'Befehl'), ('`patch`, `minor`', '`patch`, `major`', 'Major bleibt'),
                                 ('`uv.lock`', '`app/main.py`', 'Manifeste und Lockdateien'), ('`uv.lock`', '`../uv.lock`', 'Manifeste und Lockdateien'),
                                 ('Vorherige Lockdatei einspielen und neu installieren.', '…', 'Rückweg'), ('**Pflege:** aktiv', '**Pflege:** vielleicht', '**Pflege:** aktiv'.replace('*', r'\*'))):
            with self.subTest(neu):
                self.plan_setzen(PLAN.replace(alt, neu))
                self.keine_aufgabe(muster)

    def test_deployziel_muss_zur_toml_passen(self):
        self.plan_setzen(PLAN.replace('`kein`', '`ops/deploy.sh`'))
        self.keine_aufgabe('Deployziel')

    def test_ohne_pruefung_keine_aufgabe(self):
        with self.assertRaisesRegex(Fehler, 'Noch keine Pflegeprüfung'):
            self.anlegen()

    def test_altes_ergebnis_gilt_nicht(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.2.6')], zeit=time.time() - 15 * 86400)
        with self.assertRaisesRegex(Fehler, 'zu alt'):
            self.anlegen()

    def test_halt_verhindert_die_aufgabe(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.2.6')])
        ablauf.anhalten('alles')
        with self.assertRaisesRegex(Fehler, 'angehalten'):
            self.anlegen()

    def test_aufgabe_anlegen_nimmt_keine_pflege_ids(self):
        with self.assertRaisesRegex(Fehler, 'pflege_aufgabe'):
            ablauf.aufgabe_anlegen(self.p, 'P.2026-10-31.1', 'Eigenmächtig')

    def test_werkzeugsaetze(self):
        self.assertIn('pflege_aufgabe', selbstlauf.WERKZEUGE)
        self.assertNotIn('aufgabe_anlegen', selbstlauf.WERKZEUGE)
        self.assertNotIn('pflege_aufgabe', selbstlauf.PLANUNG)

    def test_widerruf_per_funktion_und_wiederholt(self):
        self.assertTrue(pflegeplan.widerrufen(self.p))
        self.assertEqual(pflegeplan.stand(self.p)[0], 'widerrufen')
        self.assertIn('Pflegeplan widerrufen', self.roadmap())
        self.assertFalse(pflegeplan.widerrufen(self.p))
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_plan_entwurf_kann_abgenommenen_oder_widerrufenen_plan_nicht_ersetzen(self):
        pflegeplan.widerrufen(self.p)
        with self.assertRaisesRegex(Fehler, 'ist abgenommen'):
            plaene.entwurf(self.p, 'pflege', PLAN.replace('**Stand:** abgenommen vom Inhaber am 31.10.2026', '**Stand:** Entwurf'))


@unittest.skipUnless((Path(__file__).parents[1] / 'plan/_pflege-vorlage.md').exists(), 'die Pflegevorlage liegt nur im Entwicklungsrepo (plan/), nicht im Release')
class Vorlage(Grund):
    def text(self):
        return (Path(__file__).parents[1] / 'plan/_pflege-vorlage.md').read_text(encoding='utf-8')

    def test_leere_vorlage_gilt_nicht(self):
        self.plan_setzen(self.text())
        with self.assertRaisesRegex(Fehler, 'nicht abgenommen'):
            pflegeplan.laden(self.p)

    def test_ausgefuellte_vorlage_gilt(self):
        text = self.text().replace('**Stand:** Entwurf', '**Stand:** abgenommen vom Inhaber am 31.10.2026').replace('- **Pakete:** `…`', '- **Pakete:** `alle`')
        text = text.replace('- **Dateien:** `…`', '- **Dateien:** `uv.lock`').replace('- **Befehl:** `…`', '- **Befehl:** `make test`')
        text = text.replace('- **Ziel:** `…`', '- **Ziel:** `kein`').replace('\n…\n', '\nVorherige Lockdatei einspielen.\n')
        self.plan_setzen(text)
        self.assertEqual(pflegeplan.laden(self.p)['test'], 'make test')


class Deploy(Grund):
    deploy = 'echo deploy'
    plan_inhalt = PLAN.replace('`kein`', '`echo deploy`')

    def test_widerruf_nach_dem_merge_stoppt_den_automatischen_deploy(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        aid = self.anlegen()['aufgabe']
        pflegeplan.widerrufen(self.p)
        ablauf._nach_merge(self.p, dict(automatisch=True, aufgabe=aid))
        self.assertFalse([l for l in laeufe.alle() if l['art'] == 'deploy'])
        frage = roadmap.fragen(self.roadmap())[0]
        self.assertEqual(frage['bezug'], aid)
        self.assertIn('nicht deployt', frage['text'])

    def test_gueltiger_plan_deployt(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        aid = self.anlegen()['aufgabe']
        ablauf._nach_merge(self.p, dict(automatisch=True, aufgabe=aid))
        self.ruhig()
        self.assertEqual([l['art'] for l in laeufe.alle()], ['deploy'])


class Abnahme(Grund):
    plan_inhalt = ''

    def test_dashboard_nimmt_pflegeplan_ohne_aufgaben_ab(self):
        plaene.entwurf(self.p, 'pflege', PLAN.replace('**Stand:** abgenommen vom Inhaber am 31.10.2026', '**Stand:** Entwurf'))
        self.assertEqual(pflegeplan.stand(self.p)[0], 'ungültig')
        info = plaene.info(self.p, 'pflege')
        plaene.abnehmen(self.p, 'pflege', {}, info['pruefsumme'])
        self.assertEqual(pflegeplan.stand(self.p), ('aktiv', ''))
        self.assertEqual([e['art'] for e in ereignis.lesen()], ['pflege-routine'])

    def test_normaler_plan_braucht_weiter_aufgaben(self):
        with self.assertRaisesRegex(Fehler, 'Aufgabentabelle'):
            plaene.entwurf(self.p, 'andere', '# X\n\n**Stand:** Entwurf\n')


class Einordnung(Grund):
    def test_sprung_vorsichtig(self):
        for von, nach, s in (('1.2.3', '1.2.4', 'patch'), ('1.2.3', '1.3.0', 'minor'), ('1.2.3', '2.0.0', 'major'), ('0.27.0', '0.27.2', 'patch'),
                             ('0.27.0', '0.28.1', 'unklar'), ('0.0.3', '0.0.4', 'unklar'), ('1.2rc1', '1.2.1', 'unklar'), ('1.2.3', '1.2.4b1', 'unklar'),
                             ('1.2.3', '1.2.2', 'unklar'), ('1.2.3.4', '1.2.3.5', 'unklar'), ('', '1.0.0', 'unklar'), ('1.2', '1.2.0', 'unklar'), ('2024.1', '2024.2', 'minor')):
            with self.subTest(f'{von}→{nach}'):
                self.assertEqual(pflegeplan.sprung(von, nach), s)

    def test_routine_wird_aufgabe_mit_id(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2'), veraltet('idna', '3.7.0', '3.7.1')])
        r = self.anlegen()
        heute = time.strftime('%Y-%m-%d')
        self.assertEqual(r['aufgabe'], f'P.{heute}.1')
        self.assertEqual([x['paket'] for x in r['pakete']], ['flask', 'idna'])
        self.assertIsNone(r['entwurf'])
        z = self.zeile(r['aufgabe'])
        self.assertEqual(z[3], 'bereit')
        self.assertIn('flask 2.2.5 → 2.3.2', z[1])
        self.assertIn(r['aufgabe'], self.roadmap().split('## Protokoll')[1])

    def test_nummern_zaehlen_weiter(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2'), veraltet('idna', '3.7.0', '3.7.1')])
        a = self.anlegen(pakete=['flask'])['aufgabe']
        b = self.anlegen(pakete=['idna'])['aufgabe']
        self.assertTrue(a.endswith('.1') and b.endswith('.2'))

    def test_major_nur_entwurf_und_frage(self):
        self.ergebnis([veraltet('flask', '2.2.5', '3.0.0')])
        r = self.anlegen()
        self.assertIsNone(r['aufgabe'])
        self.assertEqual(self.anzahl_p(), 0)
        heute = time.strftime('%Y-%m-%d')
        self.assertEqual(r['entwurf'], f'plan/pflege-{heute}-1.md')
        text = (self.repo / r['entwurf']).read_text()
        self.assertIn('**Stand:** Entwurf', text)
        self.assertIn('Major-Sprung', text)
        self.assertFalse(roadmap.ABNAHME.search(text))
        self.assertEqual([a['id'] for a in roadmap.plan(text)['aufgaben']], [f'U.{heute}.1'])
        frage = roadmap.fragen(self.roadmap())[0]
        self.assertEqual(f'F{frage["nr"]}', r['frage'])
        self.assertIn(r['entwurf'], frage['text'])
        self.assertEqual(frage['bezug'], 'allgemein')

    def test_bekannter_bruch_aus_dem_plan(self):
        self.ergebnis([veraltet('Sperrig', '1.0.0', '1.0.1')])
        r = self.anlegen()
        self.assertIsNone(r['aufgabe'])
        self.assertIn('Datenmigration', (self.repo / r['entwurf']).read_text())

    def test_bedenken_der_ki_machen_nur_enger(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.2.6'), veraltet('idna', '3.7.0', '3.7.1')])
        r = self.anlegen(bedenken={'Flask': 'Änderungshinweis nennt Bruch | zweite Spalte'})
        self.assertEqual([x['paket'] for x in r['pakete']], ['idna'])
        self.assertIn('Änderungshinweis nennt Bruch / zweite Spalte', (self.repo / r['entwurf']).read_text())

    def test_tippfehler_in_bedenken_oder_auswahl_lehnt_ab(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.2.6')])
        for k in (dict(bedenken={'flaks': 'Bruch'}), dict(pakete=['flaks'])):
            with self.assertRaisesRegex(Fehler, 'flaks'):
                self.anlegen(**k)
        self.assertEqual(self.anzahl_p(), 0)

    def test_unklare_einordnung(self):
        self.ergebnis([veraltet('httpx', '0.27.0', '0.28.1'), veraltet('beta', '1.0.0', '1.1.0rc1')])
        r = self.anlegen()
        self.assertIsNone(r['aufgabe'])
        self.assertEqual((self.repo / r['entwurf']).read_text().count('Einordnung unklar'), 2)

    def test_luecke_mit_fix_ist_routine_ohne_fix_frage(self):
        self.ergebnis(funde=[luecke('flask', '2.2.2', ['2.2.5', '2.3.2']), luecke('idna', '3.7.0', []), luecke('zwei', '1.0.0, 3.0.0', ['3.0.1'])])
        r = self.anlegen()
        self.assertEqual([(x['paket'], x['nach'], x['sprung']) for x in r['pakete']], [('flask', '2.2.5', 'patch')])
        text = (self.repo / r['entwurf']).read_text()
        self.assertEqual(text.count('keine Fix-Version bekannt'), 2)

    def test_hoechstes_ziel_gewinnt(self):
        self.ergebnis([veraltet('flask', '2.2.2', '2.2.9')], [luecke('flask', '2.2.2', ['2.2.5'])])
        self.assertEqual([x['nach'] for x in self.anlegen()['pakete']], ['2.2.9'])

    def test_major_und_fix_getrennt(self):
        self.ergebnis([veraltet('flask', '2.2.2', '3.0.0')], [luecke('flask', '2.2.2', ['2.2.5'])])
        r = self.anlegen()
        self.assertEqual([x['nach'] for x in r['pakete']], ['2.2.5'])
        self.assertIn('Major-Sprung', (self.repo / r['entwurf']).read_text())


class Ueberschritten(Grund):
    plan_inhalt = PLAN.replace('`alle`', '`flask`').replace('`patch`, `minor`', '`patch`')

    def test_ausserhalb_des_umfangs_keine_aufgabe(self):
        self.ergebnis([veraltet('idna', '3.7.0', '3.7.1')])
        r = self.anlegen()
        self.assertIsNone(r['aufgabe'])
        self.assertIsNone(r['entwurf'])
        self.assertIn('idna', r['ausserhalb'][0])
        self.assertEqual(self.anzahl_p(), 0)

    def test_major_ausserhalb_des_umfangs_ist_keine_frage(self):
        self.ergebnis([veraltet('idna', '3.7.0', '4.0.0')])
        self.assertIsNone(self.anlegen()['entwurf'])

    def test_sprung_ueber_der_erlaubnis_keine_aufgabe(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.0')])
        r = self.anlegen()
        self.assertIsNone(r['aufgabe'])
        self.assertIn('minor', r['ausserhalb'][0])

    def test_gewaehltes_paket_jenseits_des_plans_lehnt_alles_ab(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.2.6'), veraltet('idna', '3.7.0', '3.7.1')])
        with self.assertRaisesRegex(Fehler, 'Überschreitet'):
            self.anlegen(pakete=['flask', 'idna'])
        self.assertEqual(self.anzahl_p(), 0)


class Doppelte(Grund):
    def test_wiederholter_fund_keine_zweite_aufgabe(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        self.assertTrue(self.anlegen()['aufgabe'])
        r = self.anlegen()
        self.assertIsNone(r['aufgabe'])
        self.assertEqual(r['bekannt'], ['flask 2.2.5 → 2.3.2'])
        self.assertEqual(self.anzahl_p(), 1)

    def test_neuere_version_bei_offener_aufgabe_wartet(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        self.anlegen()
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.3')])
        self.assertIsNone(self.anlegen()['aufgabe'])

    def test_erledigt_oder_entfallen_gleiche_version_nicht_wieder_neuere_schon(self):
        for status in ('entfällt', 'erledigt'):
            with self.subTest(status):
                self.ergebnis([veraltet('flask', '2.2.5', f'2.3.{ord(status[1])}')])
                aid = self.anlegen()['aufgabe']
                ablauf.status_setzen(self.p, aid, status)
                self.assertIsNone(self.anlegen()['aufgabe'])
                self.ergebnis([veraltet('flask', '2.2.5', f'2.3.{ord(status[1]) + 1}')])
                neu = self.anlegen()['aufgabe']
                self.assertTrue(neu)
                ablauf.status_setzen(self.p, neu, 'entfällt')

    def test_wiederholter_major_keine_zweite_frage(self):
        self.ergebnis([veraltet('flask', '2.2.5', '3.0.0')])
        self.assertTrue(self.anlegen()['entwurf'])
        r = self.anlegen()
        self.assertIsNone(r['entwurf'])
        self.assertEqual(len(roadmap.fragen(self.roadmap())), 1)
        self.assertFalse((self.repo / f'plan/pflege-{time.strftime("%Y-%m-%d")}-2.md').exists())

    def test_gleicher_fund_aus_zwei_quellen_eine_frage_und_ein_entwurf(self):
        self.ergebnis([veraltet('flask', '2.2.5', '3.0.0'), veraltet('flask', '2.2.5', '3.0.0')])
        self.assertEqual((self.repo / self.anlegen()['entwurf']).read_text().count('Major-Sprung'), 1)

    def test_ereignis_nur_bei_neuer_arbeit_unter_gueltigem_plan(self):
        e = self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        pflegeplan.nach_pruefung(self.p, e)
        self.assertEqual([x['art'] for x in ereignis.lesen()], ['pflege-routine'])
        ereignis.entfernen(ereignis.lesen())
        self.anlegen()
        pflegeplan.nach_pruefung(self.p, e)
        self.assertEqual(ereignis.lesen(), [])

    def test_kein_ereignis_ohne_gueltigen_plan(self):
        pflegeplan.widerrufen(self.p)
        pflegeplan.nach_pruefung(self.p, self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')]))
        self.assertEqual(ereignis.lesen(), [])


class DiffPruefung(Grund):
    """Prüft `abweichungen` direkt gegen einen Branch in der Testumgebung."""

    def setUp(self):
        super().setUp()
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        self.aid = self.anlegen()['aufgabe']
        git(self.repo, 'checkout', '-q', '-b', 'zweig')

    def tearDown(self):
        git(self.repo, 'checkout', '-q', 'main')
        super().tearDown()

    def stand(self, dateien=None, loeschen=()):
        for name, inhalt in (dateien or {}).items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(inhalt)
        for name in loeschen:
            (self.repo / name).unlink()
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'update')
        return pflegeplan.abweichungen(self.p, self.aid, 'zweig')

    def uv(self):
        return UV.replace('2.2.5', '2.3.2')

    def test_im_umfang(self):
        self.assertEqual(self.stand({'uv.lock': self.uv(), f'berichte/{self.aid}.md': '# Bericht\n', f'berichte/{self.aid}-review-sol.md': '# R\n'}), [])

    def test_andere_datei(self):
        self.assertTrue(any('app.py liegt außerhalb' in g for g in self.stand({'uv.lock': self.uv(), 'app.py': 'x\n'})))

    def test_fremder_bericht(self):
        self.assertTrue(any('außerhalb' in g for g in self.stand({'uv.lock': self.uv(), 'berichte/A1.md': 'x\n'})))

    def test_plan_oder_einstellungen_geaendert(self):
        self.assertEqual(len([g for g in self.stand({'uv.lock': self.uv(), 'plan/pflege.md': 'x\n', 'devflow.toml': '\n'}) if 'außerhalb' in g]), 2)

    def test_ziel_nicht_erreicht(self):
        self.assertTrue(any('Zielversion' in g for g in self.stand({f'berichte/{self.aid}.md': 'x\n'})))

    def test_andere_zielversion(self):
        self.assertTrue(any('Zielversion' in g for g in self.stand({'uv.lock': UV.replace('2.2.5', '2.2.9')})))

    def test_transitiver_major_in_der_lockdatei(self):
        self.assertTrue(any('idna 3.7.0 → 4.0.0' in g for g in self.stand({'uv.lock': self.uv().replace('3.7.0', '4.0.0')})))

    def test_quelle_bei_gleicher_version(self):
        self.assertTrue(any('Quelle' in g for g in self.stand({'uv.lock': self.uv().replace('name = "idna"\nversion = "3.7.0"\nsource = { registry = "https://pypi.org/simple" }',
                                                                                           'name = "idna"\nversion = "3.7.0"\nsource = { registry = "https://boese.example/simple" }')})))

    def test_kaputte_lockdatei(self):
        self.assertTrue(any('nicht lesbar' in g for g in self.stand({'uv.lock': 'kaputt'})))

    def test_lockdatei_geloescht(self):
        self.assertTrue(any('nicht prüfbar' in g for g in self.stand(loeschen=['uv.lock'])))

    def test_manifest_neue_abhaengigkeit(self):
        neu = PYPROJECT.replace('"idna"]', '"idna", "boese"]')
        self.assertTrue(any('boese neu oder entfernt' in g for g in self.stand({'uv.lock': self.uv(), 'pyproject.toml': neu})))

    def test_manifest_aenderung_ausserhalb_der_versionen(self):
        neu = PYPROJECT + '[tool.uv.sources]\nflask = { git = "https://boese.example/flask" }\n'
        self.assertTrue(any('außerhalb der Paketversionen' in g for g in self.stand({'uv.lock': self.uv(), 'pyproject.toml': neu})))

    def test_manifest_anderes_paket(self):
        neu = PYPROJECT.replace('"idna"', '"idna>=3.8"')
        self.assertTrue(any('idna gehört nicht zur Aufgabe' in g for g in self.stand({'uv.lock': self.uv(), 'pyproject.toml': neu})))

    def test_manifest_der_aufgabe_darf_sich_aendern(self):
        self.assertEqual(self.stand({'uv.lock': self.uv(), 'pyproject.toml': PYPROJECT.replace('flask>=2.2,<3', 'flask>=2.3.2,<3')}), [])

    def test_manifest_allein_erreicht_das_ziel(self):
        self.assertEqual(self.stand({'pyproject.toml': PYPROJECT.replace('flask>=2.2,<3', 'flask>=2.3.2,<3')}), [])

    def test_manifest_major_statt_ziel(self):
        gruende = self.stand({'pyproject.toml': PYPROJECT.replace('flask>=2.2,<3', 'flask>=12.3.2,<13')})
        self.assertTrue(any('Anforderung ändert mehr' in g for g in gruende))
        self.assertTrue(any('Zielversion 2.3.2 nicht erreicht' in g for g in gruende))

    def test_manifest_aendert_mehr_als_die_version(self):
        for neu in ('flask[async]>=2.3.2,<3', "flask>=2.3.2,<3; python_version<'3.13'", 'flask>2.3.2,<3', 'flask>=2.3.2,<4', 'flask>=2.2,<2.3.2',
                    'flask @ https://boese.example/flask-2.3.2.tar.gz', 'flask>=2.3.2rc1,<3'):
            with self.subTest(neu=neu):
                git(self.repo, 'reset', '-q', '--hard', 'main')
                gruende = self.stand({'uv.lock': self.uv(), 'pyproject.toml': PYPROJECT.replace('flask>=2.2,<3', neu)})
                self.assertTrue(any('Anforderung ändert mehr' in g for g in gruende), gruende)

    def test_obere_grenze_erreicht_das_ziel_nicht(self):
        self.assertFalse(pflegeplan._traegt('flask>=2.2,<2.3.2', '2.3.2'))
        self.assertFalse(pflegeplan._traegt('flask>=12.3.2,<13', '2.3.2'))
        self.assertFalse(pflegeplan._traegt('flask2>=1.0; python_version>="2.3.2"', '2.3.2'))
        self.assertTrue(pflegeplan._traegt('flask==2.3.2', '2.3.2'))
        self.assertTrue(pflegeplan._traegt('^2.3.2', '2.3.2'))

    def test_pin_mit_neuen_extras(self):
        git(self.repo, 'checkout', '-q', 'main')
        (self.repo / 'requirements.txt').write_text('flask==2.2.5\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'pins')
        git(self.repo, 'checkout', '-q', '-B', 'zweig')
        self.assertEqual(self.stand({'requirements.txt': 'flask==2.3.2\n'}), [])
        self.assertTrue(any('außerhalb der Paketversionen' in g for g in self.stand({'requirements.txt': 'flask[async]==2.3.2\n'})))

    def test_neue_python_mindestversion_in_der_lockdatei(self):
        self.assertTrue(any('außerhalb der Paketversionen' in g for g in self.stand({'uv.lock': 'requires-python = ">=3.13"\n' + self.uv()})))

    def bruch(self, paket):
        """Plan auf main nennt `paket` unter den bekannten Brüchen, die Freigabe bleibt."""
        git(self.repo, 'checkout', '-q', 'main')
        self.plan_setzen(PLAN.replace('`sperrig`', f'`{paket}`'))
        git(self.repo, 'checkout', '-q', 'zweig')

    def test_transitives_paket_mit_bekanntem_bruch(self):
        self.bruch('idna')
        gruende = self.stand({'uv.lock': self.uv().replace('3.7.0', '3.7.1')})
        self.assertEqual(gruende, ['uv.lock: idna: bekannter Bruch oder Migration: braucht eine Datenmigration'])

    def test_neues_transitives_paket_mit_bekanntem_bruch(self):
        gruende = self.stand({'uv.lock': self.uv() + '\n[[package]]\nname = "sperrig"\nversion = "1.0.0"\n'})
        self.assertTrue(any('sperrig: bekannter Bruch' in g for g in gruende))

    def test_anderes_paket_ohne_bruch_bleibt_erlaubt(self):
        self.assertEqual(self.stand({'uv.lock': self.uv().replace('3.7.0', '3.7.1')}), [])

    def test_ohne_freigabe_kein_merge(self):
        self.assertTrue(any('keine Freigabe' in g for g in pflegeplan.abweichungen(self.p, 'P.2026-01-01.9', 'zweig')))

    def test_andere_aufgaben_sind_nicht_betroffen(self):
        self.assertEqual(pflegeplan.abweichungen(self.p, 'A1', 'zweig'), [])


class DiffNpm(Grund):
    def setUp(self):
        super().setUp()
        self.ergebnis([dict(paket='lodash', aktuell='4.17.15', neu='4.17.21', sprung='patch', quelle='npm outdated', ort='.')])
        self.aid = self.anlegen()['aufgabe']
        git(self.repo, 'checkout', '-q', '-b', 'zweig')

    def tearDown(self):
        git(self.repo, 'checkout', '-q', 'main')
        super().tearDown()

    def stand(self, paket, lock):
        (self.repo / 'package.json').write_text(json.dumps(paket, indent=1))
        (self.repo / 'package-lock.json').write_text(json.dumps(lock, indent=1))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'update')
        return pflegeplan.abweichungen(self.p, self.aid, 'zweig')

    def neu(self):
        return (json.loads(PAKET.replace('^4.17.15', '^4.17.21')),
                {'lockfileVersion': 3, 'packages': {'': {'name': 'w'}, 'node_modules/lodash': {'version': '4.17.21', 'resolved': 'https://r/l-21.tgz', 'integrity': 'b'}}})

    def test_im_umfang(self):
        self.assertEqual(self.stand(*self.neu()), [])

    def test_skript_im_manifest(self):
        paket, lock = self.neu()
        paket['scripts']['postinstall'] = 'curl boese'
        self.assertTrue(any('außerhalb der Paketversionen' in g for g in self.stand(paket, lock)))

    def test_neues_transitives_paket_im_umfang_alle_ist_erlaubt(self):
        paket, lock = self.neu()
        lock['packages']['node_modules/klein'] = {'version': '1.0.0'}
        self.assertEqual(self.stand(paket, lock), [])

    def test_neues_paket_im_manifest(self):
        paket, lock = self.neu()
        paket['devDependencies'] = {'neu': '1.0.0'}
        self.assertEqual(self.stand(paket, lock), ['package.json: Paket neu neu oder entfernt'])

    def test_manifest_nur_ziel(self):
        paket, lock = self.neu()
        for wert in ('^14.17.21', '~4.17.21', 'npm:boese@4.17.21', 'latest', '^4.17.21 || ^5.0.0'):
            with self.subTest(wert=wert):
                git(self.repo, 'reset', '-q', '--hard', 'main')
                paket['dependencies']['lodash'] = wert
                self.assertTrue(any('Anforderung ändert mehr' in g for g in self.stand(paket, lock)))


HELFER = {'lockfileVersion': 3, 'packages': {
    '': {'name': 'w', 'dependencies': {'eltern': '^1.0.0', 'lodash': '^4.17.15', 'helper': '^2.0.0'}},
    'node_modules/lodash': {'version': '4.17.15', 'resolved': 'https://r/l-15.tgz', 'integrity': 'a'},
    'node_modules/helper': {'version': '2.0.0', 'resolved': 'https://r/h-2.tgz', 'integrity': 'h2'},
    'node_modules/eltern': {'version': '1.0.0', 'resolved': 'https://r/e-1.tgz', 'integrity': 'e1', 'dependencies': {'helper': '^1.0.0'}},
    'node_modules/eltern/node_modules/helper': {'version': '1.0.0', 'resolved': 'https://r/h-1.tgz', 'integrity': 'h1'},
    'node_modules/anderes': {'version': '1.0.0', 'resolved': 'https://r/a-1.tgz', 'integrity': 'a1', 'dependencies': {'helper': '^1.0.0'}},
    'node_modules/anderes/node_modules/helper': {'version': '1.0.0', 'resolved': 'https://r/h-1.tgz', 'integrity': 'h1'}}}


class DiffAufloesung(Grund):
    """Versionen hängen an Verwendungsstellen: Ein Wechsel auf eine schon vorhandene Version ist auch ein Sprung."""

    def setUp(self):
        super().setUp()
        (self.repo / 'package-lock.json').write_text(json.dumps(HELFER))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'lock')
        self.ergebnis([dict(paket='lodash', aktuell='4.17.15', neu='4.17.21', sprung='patch', quelle='npm outdated', ort='.')])
        self.aid = self.anlegen()['aufgabe']
        git(self.repo, 'checkout', '-q', '-b', 'zweig')

    def tearDown(self):
        git(self.repo, 'checkout', '-q', 'main')
        super().tearDown()

    def stand(self, aendern):
        lock = json.loads(json.dumps(HELFER))
        lock['packages']['node_modules/lodash'].update(version='4.17.21', resolved='https://r/l-21.tgz', integrity='b')
        aendern(lock['packages'])
        (self.repo / 'package-lock.json').write_text(json.dumps(lock))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'update')
        return pflegeplan.abweichungen(self.p, self.aid, 'zweig')

    def eltern(self, pakete, version='1.1.0', helper='^2.0.0'):
        pakete['node_modules/eltern'].update(version=version, resolved='https://r/e-11.tgz', integrity='e11', dependencies={'helper': helper})

    def test_nichts_nebenbei(self):
        self.assertEqual(self.stand(lambda pakete: None), [])

    def test_deduplizierung_auf_major(self):
        def aendern(pakete):
            self.eltern(pakete)
            del pakete['node_modules/eltern/node_modules/helper'], pakete['node_modules/anderes'], pakete['node_modules/anderes/node_modules/helper']
        self.assertIn('package-lock.json: helper unter eltern: 1.0.0 → 2.0.0 ist kein erlaubter Sprung', self.stand(aendern))

    def test_deduplizierung_auf_major_bei_gleichen_versionen_in_der_datei(self):
        def aendern(pakete):  # `anderes` nutzt weiter 1.0.0, die Versionsmenge von helper bleibt gleich
            self.eltern(pakete)
            del pakete['node_modules/eltern/node_modules/helper']
        self.assertEqual(self.stand(aendern), ['package-lock.json: helper unter eltern: 1.0.0 → 2.0.0 ist kein erlaubter Sprung'])

    def test_kante_auf_neue_hoehere_stelle(self):
        def aendern(pakete):  # umgekehrt: eltern zieht eine eigene Kopie in einer höheren Major-Version an
            self.eltern(pakete, helper='^3.0.0')
            pakete['node_modules/eltern/node_modules/helper'].update(version='3.0.0', resolved='https://r/h-3.tgz', integrity='h3')
        self.assertTrue(any('helper unter eltern: 1.0.0 → 3.0.0' in g for g in self.stand(aendern)))

    def test_erlaubter_sprung_der_kante(self):
        def aendern(pakete):
            self.eltern(pakete, helper='^1.1.0')
            pakete['node_modules/eltern/node_modules/helper'].update(version='1.1.0', resolved='https://r/h-11.tgz', integrity='h11')
        self.assertEqual(self.stand(aendern), [])

    def test_abhaengigkeit_faellt_weg(self):
        def aendern(pakete):
            pakete['node_modules/eltern'].update(version='1.1.0', resolved='https://r/e-11.tgz', integrity='e11')
            del pakete['node_modules/eltern']['dependencies'], pakete['node_modules/eltern/node_modules/helper']
        self.assertEqual(self.stand(aendern), [])

    def test_neue_kante_ist_kein_sprung(self):
        def aendern(pakete):
            pakete['node_modules/klein'] = {'version': '1.0.0', 'resolved': 'https://r/k.tgz', 'integrity': 'k'}
            pakete['node_modules/eltern']['dependencies']['klein'] = '^1.0.0'
        self.assertEqual(self.stand(aendern), [])


def _mehrfach():
    """`eltern` an vier Stellen, nach Kunde: a, b und d nutzen helper 1.x, c nutzt 2.0.0."""
    pakete = {'': {'name': 'w', 'dependencies': {'a': '^1.0.0', 'b': '^1.0.0', 'c': '^1.0.0', 'd': '^1.0.0'}}}
    for kunde, eltern, helper in (('a', '1.0.0', '1.0.0'), ('b', '1.2.0', '1.0.0'), ('c', '2.0.0', '2.0.0'), ('d', '1.3.0', '1.2.0')):
        pfad = f'node_modules/{kunde}'
        pakete[pfad] = {'version': '1.0.0', 'resolved': f'https://r/{kunde}.tgz', 'integrity': kunde, 'dependencies': {'eltern': '*'}}
        pakete[f'{pfad}/node_modules/eltern'] = {'version': eltern, 'resolved': f'https://r/e-{eltern}.tgz', 'integrity': eltern, 'dependencies': {'helper': '*'}}
        pakete[f'{pfad}/node_modules/eltern/node_modules/helper'] = _helfer(helper)
    return {'lockfileVersion': 3, 'packages': pakete}


def _helfer(version):
    return {'version': version, 'resolved': f'https://r/h-{version}.tgz', 'integrity': version}


class DiffMehrereStellen(Grund):
    """Dasselbe Elternpaket an mehreren Stellen: Die Versionsmengen bleiben gleich, nur die einzelne Stelle wechselt."""

    A = 'node_modules/a/node_modules/eltern'
    C = 'node_modules/c/node_modules/eltern'
    MAJOR = 'package-lock.json: helper unter eltern: 1.0.0 → 2.0.0 ist kein erlaubter Sprung'

    def setUp(self):
        super().setUp()
        (self.repo / 'package-lock.json').write_text(json.dumps(_mehrfach()))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'lock')
        self.ergebnis([dict(paket='a', aktuell='1.0.0', neu='1.1.0', sprung='minor', quelle='npm outdated', ort='.')])
        self.aid = self.anlegen()['aufgabe']

    def stand(self, aendern):
        git(self.repo, 'checkout', '-q', '-B', 'zweig', 'main')
        lock = _mehrfach()
        lock['packages']['node_modules/a'].update(version='1.1.0', resolved='https://r/a-11.tgz', integrity='a11')
        aendern(lock['packages'])
        (self.repo / 'package-lock.json').write_text(json.dumps(lock))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'update')
        gruende = pflegeplan.abweichungen(self.p, self.aid, 'zweig')
        git(self.repo, 'checkout', '-q', 'main')
        return gruende

    def helfer(self, pakete, eltern, version):
        pakete[f'{eltern}/node_modules/helper'] = _helfer(version)

    def hochziehen(self, pakete):
        """Hoisting: Die Stelle von a zieht nach oben, a findet sie dort weiter."""
        for rest in ('', '/node_modules/helper'):
            pakete[f'node_modules/eltern{rest}'] = pakete.pop(f'{self.A}{rest}')

    def test_nichts_nebenbei(self):
        self.assertEqual(self.stand(lambda pakete: None), [])

    def test_major_auf_vorhandene_version(self):
        def aendern(pakete):
            pakete[self.A].update(version='1.1.0', resolved='https://r/e-11.tgz', integrity='e11')
            self.helfer(pakete, self.A, '2.0.0')
        self.assertEqual(self.stand(aendern), [self.MAJOR])

    def test_tausch_zwischen_stellen(self):
        def aendern(pakete):
            self.helfer(pakete, self.A, '2.0.0')
            self.helfer(pakete, self.C, '1.0.0')
        self.assertIn(self.MAJOR, self.stand(aendern))

    def test_umzug_mit_major(self):
        def aendern(pakete):
            self.helfer(pakete, self.A, '2.0.0')
            self.hochziehen(pakete)
        self.assertIn(self.MAJOR, self.stand(aendern))

    def test_umzug_bei_verschiedenen_majors_ergibt_frage(self):
        """Die umgezogene Stelle lässt sich nicht zuordnen; die Kante kennt 1.x und 2.0.0, also ist ein Major-Wechsel nicht auszuschließen."""
        self.assertIn(self.MAJOR, self.stand(self.hochziehen))

    def test_umzug_ohne_wechsel(self):
        def einheitlich(pakete):  # keine zweite Major-Version an der Kante
            self.helfer(pakete, self.C, '1.0.0')
            self.helfer(pakete, 'node_modules/d/node_modules/eltern', '1.0.0')
        def umziehen(pakete):
            einheitlich(pakete)
            self.hochziehen(pakete)
        lock = _mehrfach()
        einheitlich(lock['packages'])
        (self.repo / 'package-lock.json').write_text(json.dumps(lock))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'einheitlich')
        self.assertEqual(self.stand(umziehen), [])

    def test_erlaubter_sprung_auf_vorhandene_version(self):
        self.assertEqual(self.stand(lambda pakete: self.helfer(pakete, self.A, '1.2.0')), [])

    def test_wechsel_auf_vorhandene_version_zaehlt_als_angefasst(self):
        wechsel = lambda pakete: self.helfer(pakete, self.A, '1.2.0')
        self.plan_setzen(PLAN.replace('- **Pakete:** `alle`', '- **Pakete:** `a`, `eltern`'))
        self.assertEqual(self.stand(wechsel), ['package-lock.json: helper liegt außerhalb des Paketumfangs'])
        self.plan_setzen(PLAN.replace('`sperrig`', '`helper`'))
        self.assertEqual(self.stand(wechsel), ['package-lock.json: helper: bekannter Bruch oder Migration: braucht eine Datenmigration'])


def _zusammen(helper):
    """a und b mit eigener Kopie eltern 1.0.0 samt helper 1.0.0, oben liegt schon eltern 1.1.0 mit `helper`."""
    pakete = {'': {'name': 'w', 'dependencies': {'a': '^1.0.0', 'b': '^1.0.0', 'eltern': '^1.1.0'}},
              'node_modules/eltern': {'version': '1.1.0', 'resolved': 'https://r/e-11.tgz', 'integrity': 'e11', 'dependencies': {'helper': '*'}},
              'node_modules/eltern/node_modules/helper': _helfer(helper)}
    for kunde in 'ab':
        pfad = f'node_modules/{kunde}'
        pakete[pfad] = {'version': '1.0.0', 'resolved': f'https://r/{kunde}.tgz', 'integrity': kunde, 'dependencies': {'eltern': '*'}}
        pakete[f'{pfad}/node_modules/eltern'] = {'version': '1.0.0', 'resolved': 'https://r/e-1.tgz', 'integrity': 'e1', 'dependencies': {'helper': '*'}}
        pakete[f'{pfad}/node_modules/eltern/node_modules/helper'] = _helfer('1.0.0')
    return {'lockfileVersion': 3, 'packages': pakete}


def _zusammengefuehrt(helper):
    """Update a 1.0.0 → 1.1.0: a nutzt die eltern oben, seine Kopie entfällt; die von b hält helper 1.0.0 im Lockfile."""
    lock = _zusammen(helper)
    pakete = lock['packages']
    pakete['node_modules/a'].update(version='1.1.0', resolved='https://r/a-11.tgz', integrity='a11')
    del pakete['node_modules/a/node_modules/eltern'], pakete['node_modules/a/node_modules/eltern/node_modules/helper']
    return lock


class DiffZusammenfuehrung(Grund):
    """Eine Stelle entfällt und keine kommt hinzu: Sie wird trotzdem gegen alle neuen Versionen der Kante geprüft."""

    MAJOR = 'package-lock.json: helper unter eltern: 1.0.0 → 2.0.0 ist kein erlaubter Sprung'

    def pruefen(self, helper):
        (self.repo / 'package-lock.json').write_text(json.dumps(_zusammen(helper)))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'lock')
        self.ergebnis([dict(paket='a', aktuell='1.0.0', neu='1.1.0', sprung='minor', quelle='npm outdated', ort='.')])
        aid = self.anlegen()['aufgabe']
        git(self.repo, 'checkout', '-q', '-b', 'zweig')
        (self.repo / 'package-lock.json').write_text(json.dumps(_zusammengefuehrt(helper)))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'update')
        gruende = pflegeplan.abweichungen(self.p, aid, 'zweig')
        git(self.repo, 'checkout', '-q', 'main')
        return gruende

    def test_major_auf_vorhandene_stelle(self):
        self.assertEqual(self.pruefen('2.0.0'), [self.MAJOR])

    def test_erlaubter_sprung_auf_vorhandene_stelle(self):
        self.assertEqual(self.pruefen('1.1.0'), [])

    def test_gleiche_version_auf_vorhandene_stelle(self):
        self.assertEqual(self.pruefen('1.0.0'), [])

    def test_entfallende_stelle_zaehlt_als_angefasst(self):
        self.plan_setzen(PLAN.replace('- **Pakete:** `alle`', '- **Pakete:** `a`, `eltern`'))
        self.assertEqual(self.pruefen('1.1.0'), ['package-lock.json: helper liegt außerhalb des Paketumfangs'])


class TomlKanten(unittest.TestCase):
    def kanten(self, text):
        return pflegeplan._toml_lock(text)['kanten']

    def test_uv_mit_zwei_versionen(self):
        text = '''[[package]]
name = "eltern"
version = "1.0.0"
dependencies = [{ name = "helper", version = "1.0.0" }, { name = "idna" }]
[[package]]
name = "helper"
version = "1.0.0"
[[package]]
name = "helper"
version = "2.0.0"
[[package]]
name = "idna"
version = "3.7.0"
'''
        self.assertEqual(self.kanten(text), {('eltern', 'helper'): {'1.0.0': {'1.0.0'}}, ('eltern', 'idna'): {'1.0.0': {'3.7.0'}}})

    def test_cargo(self):
        text = '''[[package]]
name = "eltern"
version = "1.0.0"
dependencies = ["helper 2.0.0 (registry+https://r)", "idna"]
[[package]]
name = "helper"
version = "1.0.0"
[[package]]
name = "helper"
version = "2.0.0"
[[package]]
name = "idna"
version = "1.0.0"
'''
        self.assertEqual(self.kanten(text), {('eltern', 'helper'): {'1.0.0': {'2.0.0'}}, ('eltern', 'idna'): {'1.0.0': {'1.0.0'}}})

    def test_poetry_bereich_bei_mehreren_versionen(self):
        text = '''[[package]]
name = "eltern"
version = "1.0.0"
[package.dependencies]
helper = "^1.0"
idna = ">=3"
fehlt = "^1"
[[package]]
name = "helper"
version = "1.0.0"
[[package]]
name = "helper"
version = "2.0.0"
[[package]]
name = "idna"
version = "3.7.0"
[metadata]
content-hash = "abc"
python-versions = "^3.11"
'''
        lock = pflegeplan._toml_lock(text)
        self.assertEqual(lock['kanten'], {('eltern', 'helper'): {'1.0.0': {'^1.0'}}, ('eltern', 'idna'): {'1.0.0': {'3.7.0'}}})
        self.assertEqual(lock['rest'], {'metadata': {'python-versions': '^3.11'}})
        self.assertEqual(pflegeplan._wechsel({'^1.0'}, {'^2.0'}, pflegeplan.SPRUENGE), [('^1.0', '^2.0')])

    def uv(self, *stellen):
        """uv.lock mit `eltern` je (Elternversion, helper-Version)."""
        eltern = ''.join(f'[[package]]\nname = "eltern"\nversion = "{v}"\ndependencies = [{{ name = "helper", version = "{h}" }}]\n' for v, h in stellen)
        return pflegeplan._toml_lock(eltern + '[[package]]\nname = "helper"\nversion = "1.0.0"\n[[package]]\nname = "helper"\nversion = "2.0.0"\n')

    def test_stellen_derselben_elternversion(self):
        plan = dict(spruenge=pflegeplan.SPRUENGE, pakete=None, brueche={})
        alt = self.uv(('1.0.0', '1.0.0'), ('1.2.0', '1.0.0'), ('2.0.0', '2.0.0'))
        major = ['uv.lock: helper unter eltern: 1.0.0 → 2.0.0 ist kein erlaubter Sprung']
        self.assertEqual(pflegeplan._vergleichen('uv.lock', alt, self.uv(('1.1.0', '2.0.0'), ('1.2.0', '1.0.0'), ('2.0.0', '2.0.0')), plan, {})[:1], major)
        # neue Elternversion ohne Wechsel: nicht zuzuordnen, bei zwei Majors an der Kante also eine Frage, bei einem keine
        self.assertEqual(pflegeplan._vergleichen('uv.lock', alt, self.uv(('1.1.0', '1.0.0'), ('1.2.0', '1.0.0'), ('2.0.0', '2.0.0')), plan, {})[:1], major)
        self.assertEqual(self.helper(('1.0.0', '1.0.0'), ('1.2.0', '1.0.0'), neu=(('1.1.0', '1.0.0'), ('1.2.0', '1.0.0'))), [])

    def helper(self, *alt, neu):
        plan = dict(spruenge=pflegeplan.SPRUENGE, pakete=None, brueche={})
        return [g for g in pflegeplan._vergleichen('uv.lock', self.uv(*alt), self.uv(*neu), plan, {}) if 'helper' in g]

    def test_zusammenfuehrung_auf_vorhandene_elternversion(self):
        self.assertIn('uv.lock: helper unter eltern: 1.0.0 → 2.0.0 ist kein erlaubter Sprung',
                      self.helper(('1.0.0', '1.0.0'), ('1.1.0', '2.0.0'), ('1.2.0', '1.0.0'), neu=(('1.1.0', '2.0.0'), ('1.2.0', '1.0.0'))))

    def test_zusammenfuehrung_innerhalb_erlaubter_spruenge(self):
        self.assertEqual(self.helper(('1.0.0', '1.0.0'), ('1.1.0', '1.1.0'), ('1.2.0', '1.0.0'), neu=(('1.1.0', '1.1.0'), ('1.2.0', '1.0.0'))), [])


class Kette(Grund):
    """Ganze Kette mit Attrappen."""

    def start(self, automatisch=True):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        self.aid = self.anlegen()['aufgabe']
        return ablauf.entwickeln(self.p, self.aid, automatisch=automatisch)

    def bericht(self):
        return f'berichte/{self.aid}.md'

    def test_im_umfang_wird_gemergt(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        self.aid = self.anlegen()['aufgabe']
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'uv.lock': UV.replace('2.2.5', '2.3.2'), self.bericht(): '# Bericht\n'}}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, self.aid)
        self.warten(lambda: self.zeile(self.aid)[3] == 'erledigt')
        self.ruhig()
        self.assertIn('2.3.2', (self.repo / 'uv.lock').read_text())
        auftrag = self.aufrufe('claude')[0]['argv'][-1]
        self.assertIn('flask 2.2.5 → 2.3.2', auftrag)
        self.assertIn('`uv.lock`', auftrag)
        self.assertIn('flask 2.2.5 → 2.3.2', self.aufrufe('codex')[0]['argv'][-1])

    def test_transitiver_bruch_kein_merge_sondern_frage(self):
        self.plan_setzen(PLAN.replace('`sperrig`', '`idna`'))
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'uv.lock': UV.replace('2.2.5', '2.3.2').replace('3.7.0', '3.7.1')}}], codex=[self.bestanden()])
        self.start()
        self.warten(lambda: self.zeile(self.aid)[3] == 'Braucht dich')
        self.ruhig()
        self.assertIn('3.7.0', (self.repo / 'uv.lock').read_text())
        self.assertFalse([l for l in laeufe.alle() if l['art'] == 'mergen'])

    def test_major_an_einer_von_mehreren_stellen_kein_merge_sondern_frage(self):
        (self.repo / 'package-lock.json').write_text(json.dumps(_mehrfach()))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'lock')
        self.ergebnis([dict(paket='a', aktuell='1.0.0', neu='1.1.0', sprung='minor', quelle='npm outdated', ort='.')])
        self.aid = self.anlegen()['aufgabe']
        neu = _mehrfach()
        neu['packages']['node_modules/a'].update(version='1.1.0', resolved='https://r/a-11.tgz', integrity='a11')
        neu['packages'][f'{DiffMehrereStellen.A}/node_modules/helper'] = _helfer('2.0.0')
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'package-lock.json': json.dumps(neu)}}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, self.aid)
        self.warten(lambda: self.zeile(self.aid)[3] == 'Braucht dich')
        self.ruhig()
        self.assertEqual(json.loads((self.repo / 'package-lock.json').read_text()), _mehrfach())
        self.assertFalse([l for l in laeufe.alle() if l['art'] == 'mergen'])
        self.assertIn('helper unter eltern: 1.0.0 → 2.0.0', self.roadmap())

    def test_zusammenfuehrung_auf_major_kein_merge_sondern_frage(self):
        (self.repo / 'package-lock.json').write_text(json.dumps(_zusammen('2.0.0')))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'lock')
        self.ergebnis([dict(paket='a', aktuell='1.0.0', neu='1.1.0', sprung='minor', quelle='npm outdated', ort='.')])
        self.aid = self.anlegen()['aufgabe']
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'package-lock.json': json.dumps(_zusammengefuehrt('2.0.0'))}}], codex=[self.bestanden()])
        ablauf.entwickeln(self.p, self.aid)
        self.warten(lambda: self.zeile(self.aid)[3] == 'Braucht dich')
        self.ruhig()
        self.assertEqual(json.loads((self.repo / 'package-lock.json').read_text()), _zusammen('2.0.0'))
        self.assertFalse([l for l in laeufe.alle() if l['art'] == 'mergen'])
        self.assertIn('helper unter eltern: 1.0.0 → 2.0.0', self.roadmap())

    def test_diff_ausserhalb_kein_merge_sondern_frage(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'uv.lock': UV.replace('2.2.5', '2.3.2'), 'app.py': 'x = 1\n'}}], codex=[self.bestanden()])
        self.start()
        self.warten(lambda: self.zeile(self.aid)[3] == 'Braucht dich')
        self.ruhig()
        self.assertIn('2.2.5', (self.repo / 'uv.lock').read_text())
        self.assertFalse((self.repo / 'app.py').exists())
        self.assertFalse([l for l in laeufe.alle() if l['art'] == 'mergen'])
        frage = roadmap.fragen(self.roadmap())[0]
        self.assertEqual(frage['bezug'], self.aid)
        self.assertIn('app.py liegt außerhalb', frage['text'])
        with self.assertRaises(FrageOffen):
            ablauf.mergen(self.p, self.aid)

    def test_antwort_hebt_die_pruefung_nicht_auf(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'app.py': 'x = 1\n'}}], codex=[self.bestanden()])
        self.start()
        self.warten(lambda: self.zeile(self.aid)[3] == 'Braucht dich')
        self.ruhig()
        ablauf.antwort_eintragen(self.p, roadmap.fragen(self.roadmap())[0]['nr'], 'Mergen!', 'dashboard')
        with self.assertRaises(FrageOffen):
            ablauf.mergen(self.p, self.aid)
        self.assertFalse((self.repo / 'app.py').exists())

    def test_widerruf_vor_dem_start(self):
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        self.aid = self.anlegen()['aufgabe']
        pflegeplan.widerrufen(self.p)
        with self.assertRaisesRegex(Fehler, 'widerrufen'):
            ablauf.entwickeln(self.p, self.aid)
        self.assertFalse(laeufe.alle())
        self.assertFalse(git(self.repo, 'branch', '--list', 'claude-*'))

    def test_widerruf_vor_dem_merge(self):
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'uv.lock': UV.replace('2.2.5', '2.3.2')}}], codex=[self.bestanden()])
        self.start(automatisch=False)
        self.ruhig()
        ablauf.review_anfordern(self.p, self.aid, automatisch=False)
        self.ruhig()
        pflegeplan.widerrufen(self.p)
        with self.assertRaises(FrageOffen):
            ablauf.mergen(self.p, self.aid)
        self.assertEqual(self.zeile(self.aid)[3], 'Braucht dich')
        self.assertIn('widerrufen', roadmap.fragen(self.roadmap())[0]['text'])
        self.assertIn('2.2.5', (self.repo / 'uv.lock').read_text())

    def test_widerruf_nach_dem_start_des_mergelaufs(self):
        """Auch der Lauf selbst prüft: Unter der Repo-Sperre kommt die Frage, nicht der Merge."""
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'uv.lock': UV.replace('2.2.5', '2.3.2')}}], codex=[self.bestanden()])
        self.start(automatisch=False)
        self.ruhig()
        ablauf.review_anfordern(self.p, self.aid, automatisch=False)
        self.ruhig()
        z = ablauf.zweig(self.p, self.aid)
        ablauf._pflege_tor(self.p, self.aid, z['branch'])
        pflegeplan.widerrufen(self.p)
        lauf = dict(id='x', art='mergen', projekt='demo', aufgabe=self.aid, branch=z['branch'], worktree=z['worktree'], entwickler='claude', zustand='laeuft', angelegt=time.time(), modell='intern', cwd=str(self.repo))
        laeufe.verzeichnis('x').mkdir(parents=True, exist_ok=True)
        with open(self.tmp / 'log', 'a') as log, self.assertRaises(FrageOffen):
            ablauf.intern(lauf, log)
        self.assertEqual(self.zeile(self.aid)[3], 'Braucht dich')
        self.assertIn('2.2.5', (self.repo / 'uv.lock').read_text())

    def test_aufgabe_ohne_freigabe_startet_nicht(self):
        repo_text = roadmap.anlegen(self.roadmap(), 'P.2026-01-01.1', 'Von Hand', '29.10.', 'Claude', 'bereit')
        (self.repo / 'ROADMAP.md').write_text(repo_text)
        git(self.repo, 'commit', '-q', '-am', 'von Hand')
        with self.assertRaisesRegex(Fehler, 'keine Freigabe'):
            ablauf.entwickeln(self.p, 'P.2026-01-01.1')


class Isolierung(Grund):
    """Tests einer Pflegeaufgabe laufen in einer Kopie: ohne ignorierte Dateien, ohne Umgebung der Sitzung, mit eigenem HOME."""
    plan_inhalt = PLAN.replace('`python3 -c "print(1)"`',
                               '`test -z "$DEVFLOW2_STATE" && test ! -e geheim.env && test -e arbeit.txt && test "$HOME" != "$ECHTES_HOME" && pwd > "$HOME/ort" && case $PWD in *-claude-*) exit 3;; esac`')

    def test_lauf_in_kopie(self):
        (self.repo / '.gitignore').write_text('__pycache__/\ngeheim.env\n')
        git(self.repo, 'commit', '-q', '-am', 'ignorieren')
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        aid = self.anlegen()['aufgabe']
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'arbeit.txt': 'x\n'}}])
        ablauf.entwickeln(self.p, aid, automatisch=False)
        self.ruhig()
        wt = ablauf.zweig(self.p, aid)['worktree']
        with open(f'{wt}/geheim.env', 'w') as f:
            f.write('x\n')
        os.environ['ECHTES_HOME'] = os.environ.get('HOME', '')
        self.addCleanup(os.environ.pop, 'ECHTES_HOME')
        l = ablauf.testen(self.p, aid)
        self.ruhig()
        l = laeufe.laden(l['id'])
        self.assertEqual(l.get('rc'), 0, l.get('ergebnis'))

    def test_rote_tests_bleiben_rot(self):
        self.plan_setzen(self.plan_inhalt.replace('test -z "$DEVFLOW2_STATE"', 'false'))
        self.ergebnis([veraltet('flask', '2.2.5', '2.3.2')])
        aid = self.anlegen()['aufgabe']
        self.drehbuch(claude=[{'tue': 'commit', 'dateien': {'arbeit.txt': 'x\n'}}])
        ablauf.entwickeln(self.p, aid, automatisch=False)
        self.ruhig()
        l = ablauf.testen(self.p, aid)
        self.ruhig()
        self.assertNotEqual(laeufe.laden(l['id']).get('rc'), 0)

    def test_andere_aufgaben_testen_wie_bisher(self):
        self.assertEqual(ablauf._testbefehl(self.p, 'A1'), self.p.cfg['test'])


class Dashboard(Grund):
    HERKUNFT = {'Tailscale-User-Login': 'inhaber@github', 'Origin': 'http://testserver'}

    def client(self):
        return TestClient(web.anwendung(), headers=self.HERKUNFT, follow_redirects=False)

    def test_seite_zeigt_stand_und_knopf(self):
        r = self.client().get('/p/demo/pflege')
        self.assertIn('Pflegeplan aktiv', r.text)
        self.assertIn('/pflege/widerrufen', r.text)

    def test_knopf_widerruft(self):
        r = self.client().post('/pflege/widerrufen', data={'projekt': 'demo'})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(pflegeplan.stand(self.p)[0], 'widerrufen')
        seite = self.client().get('/p/demo/pflege').text
        self.assertIn('Pflegeplan widerrufen', seite)
        self.assertNotIn('/pflege/widerrufen', seite)

    def test_ohne_origin_abgelehnt(self):
        c = TestClient(web.anwendung(), headers={'Tailscale-User-Login': 'inhaber@github'}, follow_redirects=False)
        self.assertEqual(c.post('/pflege/widerrufen', data={'projekt': 'demo'}).status_code, 403)
        self.assertEqual(pflegeplan.stand(self.p)[0], 'aktiv')
