"""Pläne abnehmen (S3.3): Liste, Planansicht, Abnehmen mit Stufen, Änderung wünschen, Schutz, Ereignis."""
import logging
import re
import unittest
import warnings

warnings.filterwarnings('ignore', 'Using `httpx`')
from starlette.testclient import TestClient  # noqa: E402

from devflow2 import ablauf, ereignis, plaene, projekte, roadmap, web, inhaber  # noqa: E402
from hilfen import ROADMAP, Fall, git  # noqa: E402

LOGIN = {'Tailscale-User-Login': 'inhaber@github'}
HERKUNFT = {**LOGIN, 'Origin': 'http://testserver'}
logging.getLogger('httpx').setLevel(logging.WARNING)

ENTWURF = """# Vorhaben Neu · Claude · 30.10.2026

**Stand:** Entwurf

Nach der Abnahme durch den Inhaber wird diese Zeile genau so ersetzt: `**Stand:** abgenommen vom Inhaber am TT.MM.JJJJ`.

## Aufgaben

| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |
|---|---|---|---|---|---|
| N1 | <b>Erste</b> | claude | `a.py` | Test grün | leicht |
| N2 | Zweite | sol | `b.py` | Test grün | |

## Risiken

- keine
"""
OHNE_STUFE = ENTWURF.replace(' | Stufe |', ' |').replace('|---|---|---|---|---|---|', '|---|---|---|---|---|').replace(' | leicht |', ' |').replace('| claude | `a.py` | Test grün | leicht |', '| claude | `a.py` | Test grün |').replace('| sol | `b.py` | Test grün | |', '| sol | `b.py` | Test grün |')


class Basis(Fall):
    plan_text = ENTWURF
    datei = 'plan/wetter.md'

    def setUp(self):
        super().setUp()
        self.client = TestClient(web.anwendung(), headers=HERKUNFT, follow_redirects=False)

    def post(self, aktion, daten=None, **kw):
        return self.client.post(f'/p/demo/plan/wetter/{aktion}', data=daten or {}, **kw)

    def abnehmen(self, **stufen):
        daten = {'gesehen': plaene.pruefsumme((self.repo / self.datei).read_text())}
        daten.update({f'stufe.{k}': v for k, v in stufen.items()})
        return self.post('abnehmen', daten)

    def plan(self):
        return (self.repo / self.datei).read_text()

    def anzahl(self):
        return int(git(self.repo, 'rev-list', '--count', 'main'))


class AbnehmenTest(Basis):
    def test_abnahme_schreibt_vorlagenzeile_und_stufen_in_einem_commit(self):
        vorher = self.anzahl()
        r = self.abnehmen(N1='normal', N2='schwer')
        self.assertEqual((r.status_code, r.headers['location']), (303, '/p/demo/plan/wetter?meldung=plan-abgenommen'))
        erwartet = ENTWURF.replace('**Stand:** Entwurf', f'**Stand:** abgenommen {inhaber.von()} am {ablauf.datum()}') \
            .replace('| Test grün | leicht |', '| Test grün | normal |').replace('| Test grün | |', '| Test grün | schwer |')
        self.assertEqual(self.plan(), erwartet)
        self.assertEqual(self.anzahl(), vorher + 1)
        self.assertEqual(git(self.repo, 'show', '--name-only', '--format=%s', 'HEAD').split('\n')[0], f'plan: plan/wetter.md abgenommen {inhaber.von()} am {ablauf.datum()}')
        self.assertEqual(git(self.repo, 'show', '--name-only', '--format=', 'HEAD'), 'plan/wetter.md')
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertIn('Plan abgenommen', self.client.get(r.headers['location']).text)

    def test_zweimal_senden_ergibt_einen_commit_und_zweite_abnahme_wird_abgelehnt(self):
        self.abnehmen(N1='leicht')
        nach_erster, plan = self.anzahl(), self.plan()
        r = self.abnehmen(N1='schwer', N2='schwer')
        self.assertEqual(r.status_code, 409)
        self.assertIn('schon abgenommen', r.text)
        self.assertEqual((self.anzahl(), self.plan()), (nach_erster, plan))
        self.assertEqual(len(ereignis.lesen()), 1)

    def test_plan_uebernehmen_akzeptiert_den_abgenommenen_plan_mit_stufen(self):
        self.abnehmen(N1='normal', N2='schwer')
        self.assertEqual(ablauf.plan_uebernehmen(self.p, self.datei), ['N1', 'N2'])
        self.assertEqual((self.p.stufe('N1'), self.p.stufe('N2')), ('normal', 'schwer'))
        self.assertEqual(self.zeile('N2')[3], 'bereit')

    def test_ohne_gewaehlte_stufe_bleibt_die_alte(self):
        self.abnehmen()
        self.assertEqual((self.p.stufe('N1'), self.p.stufe('N2')), ('leicht', ''))

    def test_plan_ohne_stufenspalte_bekommt_sie(self):
        (self.repo / self.datei).write_text(OHNE_STUFE)
        git(self.repo, 'commit', '-qam', 'ohne Stufe')
        self.abnehmen(N2='schwer')
        self.assertIn('| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |\n|---|---|---|---|---|---|', self.plan())
        self.assertIn('| N1 | <b>Erste</b> | claude | `a.py` | Test grün | |', self.plan())
        self.assertIn('| N2 | Zweite | sol | `b.py` | Test grün | schwer |', self.plan())
        self.assertEqual(ablauf.plan_uebernehmen(self.p, self.datei), ['N1', 'N2'])

    def test_unbekannte_stufe_oder_aufgabe_ergibt_400_ohne_aenderung(self):
        vorher = self.plan()
        for stufen in ({'N1': 'riesig'}, {'X9': 'leicht'}):
            self.assertEqual(self.abnehmen(**stufen).status_code, 400)
        self.assertEqual((self.plan(), len(ereignis.lesen())), (vorher, 0))

    def test_geaenderter_plan_wird_nicht_blind_abgenommen(self):
        gesehen = plaene.pruefsumme(self.plan())
        (self.repo / self.datei).write_text(self.plan() + '\nNachtrag\n')
        git(self.repo, 'commit', '-qam', 'Nachtrag')
        r = self.post('abnehmen', {'gesehen': gesehen})
        self.assertEqual(r.status_code, 409)
        self.assertIn('geändert', r.text)
        self.assertIsNone(roadmap.plan(self.plan())['abgenommen'])
        self.assertEqual(self.post('abnehmen').status_code, 409)  # ohne Prüfsumme

    def test_unbekannter_plan_404_und_fremde_dateien_nicht_erreichbar(self):
        for name in ('gibtsnicht', '_vorlage', '..%2FROADMAP'):
            self.assertEqual(self.client.post(f'/p/demo/plan/{name}/abnehmen', data={}).status_code, 404)
            self.assertEqual(self.client.get(f'/p/demo/plan/{name}').status_code, 404)

    def test_abnahme_meldet_ereignis_ohne_text(self):
        self.abnehmen()
        e, = ereignis.lesen()
        self.assertEqual((e['art'], e['projekt'], e['bezug']), ('plan-abgenommen', 'demo', self.datei))

    def test_ereignis_nicht_schreibbar_aendert_plan_und_sagt_es(self):
        (self.tmp / 'state').mkdir(exist_ok=True)
        ereignis.datei().mkdir()  # ist ein Verzeichnis → Schreiben scheitert
        r = self.abnehmen()
        self.assertEqual(r.headers['location'], '/p/demo/plan/wetter?meldung=ohne-ereignis')
        self.assertIn(f'abgenommen {inhaber.von()}', self.plan())


class RueckmeldungTest(Basis):
    def test_wunsch_landet_als_abschnitt_in_einem_commit(self):
        vorher = self.anzahl()
        r = self.post('aenderung', {'wunsch': '  Bitte N2\nsplitten <b>x</b>  '})
        self.assertEqual((r.status_code, r.headers['location']), (303, '/p/demo/plan/wetter?meldung=rueckmeldung'))
        self.assertEqual(self.plan(), ENTWURF + f'\n## Rückmeldung {inhaber.kennung()}\n\n- **{ablauf.datum()}:** Bitte N2 splitten <b>x</b>\n')
        self.assertEqual(self.anzahl(), vorher + 1)
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%s', 'main'), 'plan: Änderung gewünscht zu plan/wetter.md')
        self.assertEqual(ereignis.lesen()[0]['art'], 'plan-rueckmeldung')
        self.assertNotIn(f'abgenommen {inhaber.von()} am', self.plan().split('\n')[2])

    def test_zweimal_senden_ergibt_einen_commit_ein_weiterer_wunsch_ergaenzt(self):
        self.post('aenderung', {'wunsch': 'Eins'})
        nach_erster = self.anzahl()
        r = self.post('aenderung', {'wunsch': 'Eins'})
        self.assertEqual(r.headers['location'], '/p/demo/plan/wetter?meldung=rueckmeldung-schon')
        self.assertEqual((self.anzahl(), len(ereignis.lesen())), (nach_erster, 1))
        self.post('aenderung', {'wunsch': 'Zwei'})
        self.assertEqual(self.plan().count(f'## Rückmeldung {inhaber.kennung()}'), 1)
        self.assertEqual(self.plan().rstrip().split('\n')[-2:], [f'- **{ablauf.datum()}:** Eins', f'- **{ablauf.datum()}:** Zwei'])

    def test_abschnitt_mitten_im_plan_wird_vor_dem_naechsten_ergaenzt(self):
        (self.repo / self.datei).write_text(ENTWURF.replace('## Risiken', f'## Rückmeldung {inhaber.kennung()}\n\n- **01.01.2026:** alt\n\n## Risiken'))
        git(self.repo, 'commit', '-qam', 'mit Rückmeldung')
        self.post('aenderung', {'wunsch': 'neu'})
        self.assertIn(f'- **01.01.2026:** alt\n- **{ablauf.datum()}:** neu\n\n## Risiken', self.plan())

    def test_leer_zu_lang_und_abgenommen(self):
        vorher = self.plan()
        self.assertEqual(self.post('aenderung', {'wunsch': '  '}).status_code, 400)
        self.assertEqual(self.post('aenderung', {'wunsch': 'x' * 4001}).status_code, 400)
        self.assertEqual(self.plan(), vorher)
        self.abnehmen()
        nach = self.plan()
        r = self.post('aenderung', {'wunsch': 'zu spät'})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.plan(), nach)


class SchutzTest(Basis):
    def test_fremde_origin_get_und_ohne_login_aendern_nichts(self):
        vorher = (self.anzahl(), self.plan())
        ohne = TestClient(web.anwendung(), follow_redirects=False)
        for aktion, daten in (('abnehmen', {'gesehen': plaene.pruefsumme(self.plan())}), ('aenderung', {'wunsch': 'x'})):
            pfad = f'/p/demo/plan/wetter/{aktion}'
            self.assertEqual(ohne.post(pfad, data=daten, headers={**LOGIN, 'Origin': 'https://boese.example'}).status_code, 403)
            self.assertEqual(ohne.post(pfad, data=daten, headers=LOGIN).status_code, 403)  # keine Origin
            self.assertEqual(ohne.post(pfad, data=daten, headers={**LOGIN, 'Origin': 'http://testserver', 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
            self.assertEqual(ohne.post(pfad, data=daten, headers={'Origin': 'http://testserver'}).status_code, 403)  # kein Login
            self.assertEqual(self.client.get(f'/p/demo/plan/wetter/{aktion}').status_code, 405)
        self.assertEqual((self.anzahl(), self.plan(), ereignis.lesen()), (*vorher, []))

    def test_unerlaubter_inhaltstyp_und_zu_gross(self):
        self.assertEqual(self.client.post('/p/demo/plan/wetter/aenderung', content=b'{}', headers={'Content-Type': 'application/json'}).status_code, 415)
        self.assertEqual(self.client.post('/p/demo/plan/wetter/aenderung', data={'wunsch': 'x' * 40_000}).status_code, 413)
        self.assertNotIn('Rückmeldung', self.plan())

    def test_nicht_committete_aenderung_am_plan_blockiert(self):
        (self.repo / self.datei).write_text(self.plan() + '\nHand\n')
        r = self.abnehmen()
        self.assertEqual(r.status_code, 409)
        self.assertIsNone(roadmap.plan(self.plan())['abgenommen'])


class SeitenTest(Basis):
    def test_liste_zeigt_stand_je_projekt_und_planansicht_stufenwahl(self):
        s = self.client.get('/plaene').text
        self.assertIn('href="/p/demo/plan/wetter"', s)
        kopf(self, s, 'Zur Abnahme', 1)
        self.assertNotIn('plan/_vorlage', s)
        self.assertIn('href="/p/demo/plaene"', self.client.get('/p/demo').text)
        self.assertNotIn('href="/plaene"', self.client.get('/p/demo').text)
        s = self.client.get('/p/demo/plan/wetter').text
        self.assertIn('action="/p/demo/plan/wetter/abnehmen"', s)
        self.assertIn('action="/p/demo/plan/wetter/aenderung"', s)
        self.assertIn('name="stufe.N1"', s)
        self.assertIn('name="stufe.N2"', s)
        self.assertIn(f'name="gesehen" value="{plaene.pruefsumme(self.plan())}"', s)
        self.assertIn('<option value="leicht" selected>leicht</option>', s)
        self.assertEqual(s.count('rc-btn-primary'), 1)
        self.assertNotIn('<b>Erste</b>', s)  # maskiert
        self.assertIn('&lt;b&gt;Erste&lt;/b&gt;', s)

    def test_abgenommen_zeigt_stand_und_stufen_ohne_formulare(self):
        self.abnehmen(N1='schwer')
        s = self.client.get('/p/demo/plan/wetter').text
        self.assertIn(f'abgenommen am {ablauf.datum()}', s)
        self.assertNotIn('<form', s)
        self.assertIn('schwer', s)
        self.assertIn('0 von 2 Aufgaben in der Roadmap', self.client.get('/plaene').text)
        ablauf.plan_uebernehmen(self.p, self.datei)
        self.assertIn('2 von 2 Aufgaben in der Roadmap', self.client.get('/plaene').text)

    def test_rueckmeldung_sichtbar_in_der_liste(self):
        self.post('aenderung', {'wunsch': 'x'})
        self.assertIn('Änderung gewünscht', self.client.get('/plaene').text)


def felder(html, aktion):
    """Die versteckten Felder des Formulars mit dieser Aktion, so wie der Browser sie senden würde."""
    form = re.search(r'<form method="post" action="' + re.escape(aktion) + r'"[^>]*>(.*?)</form>', html, re.S)
    assert form, f'kein Formular {aktion}'
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form.group(1)))


def kopf(test, html, gruppe, n):
    """Überschrift der Gruppe mit Anzahl: auf der Projektseite als Kicker, auf der globalen Seite als h3."""
    test.assertRegex(html, rf'{gruppe}<span class="rc-kicker-num">\({n}\)</span>|<h3>{gruppe} \({n}\)</h3>')


def plantext(titel, stand, *ids):
    zeilen = ''.join(f'| {i} | Aufgabe {i} | claude | `a.py` | Test grün | |\n' for i in ids)
    return (f'# {titel}\n\n**Stand:** {stand}\n\n## Kurz\n\n- Kurzfassung von {titel}\n\n## Aufgaben\n\n'
            f'| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium | Stufe |\n|---|---|---|---|---|---|\n{zeilen}')


def abgenommen(tag):
    return f'abgenommen {inhaber.von()} am {tag}'


def lege_plaene(repo, plaene_, aufgaben=()):
    """`plaene_`: {Name: Text}; `aufgaben`: [(ID, Status)] werden an die Roadmap des Projekts gehängt (nicht committet, die Seiten lesen nur)."""
    for name, text in plaene_.items():
        (repo / 'plan' / f'{name}.md').write_text(text)
    if aufgaben:
        rm = repo / 'ROADMAP.md'
        text = rm.read_text()
        zeilen = ''.join(f'| {i} | Aufgabe {i} | Claude | {st} | 01.11. | |\n' for i, st in aufgaben)
        rm.write_text(text.replace('| 0.1 | Projekt angelegt | Claude | erledigt | 29.10. | |\n', '| 0.1 | Projekt angelegt | Claude | erledigt | 29.10. | |\n' + zeilen))


class GruppenTest(Basis):
    """S14.3b: Gruppen „Zur Abnahme“, „In Arbeit“, „Erledigt“ mit Zuordnung, Reihenfolge und Projektfilter."""

    def setUp(self):
        super().setUp()
        lege_plaene(self.repo, {
            'wetter': plantext('Vorhaben Wetter · Claude · 30.10.2026', 'Entwurf', 'N1'),
            'entwurf-alt': plantext('Alt · Claude · 01.11.2026', 'Entwurf', 'E1'),
            'entwurf-neu': plantext('Neu · Claude · 05.11.2026', 'Entwurf', 'E2'),
            'arbeit-a': plantext('Arbeit A · Claude · 01.11.2026', abgenommen('03.11.2026'), 'W1'),
            'arbeit-b': plantext('Arbeit B · Claude · 01.11.2026', abgenommen('04.11.2026'), 'W2'),
            'halb': plantext('Halb · Claude · 01.11.2026', abgenommen('02.11.2026'), 'H1', 'H2'),
            'fertig-1': plantext('Fertig 1 · Claude · 01.11.2026', abgenommen('01.11.2026'), 'F1'),
            'fertig-2': plantext('Fertig 2 · Claude · 01.11.2026', abgenommen('02.11.2026'), 'F2', 'F3'),
            'kaputt': '# Kaputt\n\n**Stand:** Entwurf\n\n| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium |\n|---|---|---|---|---|\n| X | nur zwei | a |\n',
        }, [('W1', 'offen'), ('W2', 'in Arbeit'), ('H1', 'erledigt'), ('H2', 'Review'), ('F1', 'erledigt'), ('F2', 'erledigt'), ('F3', 'entfällt')])
        self.html = self.client.get('/p/demo/plaene').text

    def reihenfolge(self, gruppe, *namen):
        """Alle `namen` stehen in dieser Reihenfolge in der Gruppe und sonst kein Plan."""
        ab = self.html.index(f'aria-label="{gruppe}"')
        ende = self.html.find('class="liste entscheidungen" aria-label="', ab)
        inhalt = self.html[ab:ende if ende > 0 else len(self.html)]
        gefunden = re.findall(r'href="/p/demo/plan/([^"/]+)"', inhalt)
        self.assertEqual([n for i, n in enumerate(gefunden) if n not in gefunden[:i]], list(namen))

    def test_zur_abnahme_entwuerfe_neueste_zuerst_mit_knopf(self):
        kopf(self, self.html, 'Zur Abnahme', 3)
        self.reihenfolge('Zur Abnahme', 'entwurf-neu', 'entwurf-alt', 'wetter')
        f = felder(self.html, '/p/demo/plan/entwurf-neu/abnehmen')
        self.assertEqual((f['zurueck'], f['gesehen']), ('plaene', plaene.pruefsumme((self.repo / 'plan' / 'entwurf-neu.md').read_text())))
        self.assertIn('Kurzfassung von Neu', self.html)

    def test_in_arbeit_abgenommen_mit_offenen_aufgaben_neueste_zuerst(self):
        kopf(self, self.html, 'In Arbeit', 3)
        self.reihenfolge('In Arbeit', 'arbeit-b', 'arbeit-a', 'halb')
        self.assertNotIn('action="/p/demo/plan/arbeit-a/abnehmen"', self.html)
        self.assertIn('2 von 2 Aufgaben in der Roadmap, 1 erledigt', self.html)

    def test_erledigt_eingeklappt_unten_mit_anzahl_und_ohne_abgenommen_offene(self):
        self.reihenfolge('Erledigt', 'fertig-2', 'fertig-1')
        self.assertRegex(self.html, r'<details class="erledigt" id="erledigt"><summary>Erledigt \(2\)</summary>')
        self.assertNotIn('<details class="erledigt" id="erledigt" open', self.html)
        reihe = [self.html.index(f'aria-label="{g}"') for g in ('Zur Abnahme', 'In Arbeit', 'Nicht lesbar', 'Erledigt')]
        self.assertEqual(reihe, sorted(reihe))
        self.assertEqual(self.html.count('class="erledigt"'), 1)

    def test_nicht_lesbarer_plan_steht_eigens_und_nicht_unter_zur_abnahme(self):
        kopf(self, self.html, 'Nicht lesbar', 1)
        self.reihenfolge('Nicht lesbar', 'kaputt')

    def test_leere_gruppen_entfallen(self):
        for n in ('wetter', 'entwurf-alt', 'entwurf-neu', 'arbeit-a', 'arbeit-b', 'halb', 'kaputt'):
            (self.repo / 'plan' / f'{n}.md').unlink()
        s = self.client.get('/p/demo/plaene').text
        self.assertIn('Erledigt (2)', s)
        for g in ('Zur Abnahme', 'In Arbeit', 'Nicht lesbar'):
            self.assertNotIn(g, s)
        for n in ('fertig-1', 'fertig-2'):
            (self.repo / 'plan' / f'{n}.md').unlink()
        s = self.client.get('/p/demo/plaene').text
        self.assertIn('Keine Pläne.', s)
        self.assertNotIn('Erledigt', s)

    def test_gleiches_datum_nach_planname_absteigend_zahlen_als_zahlen(self):
        for n in ('s9', 's10'):
            (self.repo / 'plan' / f'{n}.md').write_text(plantext(f'{n} · Claude · 05.11.2026', 'Entwurf', 'Z1'))
        (self.repo / 'plan' / 'entwurf-neu.md').unlink()
        self.html = self.client.get('/p/demo/plaene').text
        self.reihenfolge('Zur Abnahme', 's10', 's9', 'entwurf-alt', 'wetter')

    def test_zurueck_ist_nur_aus_der_positivliste(self):
        self.assertEqual(felder(self.client.get('/plaene').text, '/p/demo/plan/entwurf-neu/abnehmen')['zurueck'], 'plaene-alle')
        r = self.client.post('/p/demo/plan/wetter/abnehmen', data={'gesehen': 'falsch', 'zurueck': 'https://böse.example/'})
        self.assertEqual(r.status_code, 409)
        self.assertIn('href="/p/demo/plan/wetter"', r.text)


class KnopfTest(Basis):
    def test_abnehmen_per_knopf_geht_zurueck_auf_die_plaene_seite_des_projekts(self):
        f = felder(self.client.get('/p/demo/plaene').text, '/p/demo/plan/wetter/abnehmen')
        self.assertEqual(f['zurueck'], 'plaene')
        r = self.client.post('/p/demo/plan/wetter/abnehmen', data=f)
        self.assertEqual((r.status_code, r.headers['location']), (303, '/p/demo/plaene?meldung=plan-abgenommen'))
        self.assertIn(abgenommen(ablauf.datum()), self.plan())
        s = self.client.get(r.headers['location']).text
        self.assertIn('Plan abgenommen', s)
        kopf(self, s, 'In Arbeit', 1)  # Aufgaben noch nicht in der Roadmap: offen
        self.assertNotIn('Zur Abnahme', s)

    def test_von_der_globalen_seite_geht_es_auf_die_globale_seite_zurueck(self):
        f = felder(self.client.get('/plaene').text, '/p/demo/plan/wetter/abnehmen')
        self.assertEqual(f['zurueck'], 'plaene-alle')
        r = self.client.post('/p/demo/plan/wetter/abnehmen', data=f)
        self.assertEqual(r.headers['location'], '/plaene?meldung=plan-abgenommen')

    def test_aenderung_wuenschen_von_der_projektseite(self):
        r = self.client.post('/p/demo/plan/wetter/aenderung', data={'wunsch': 'bitte kürzer', 'zurueck': 'plaene'})
        self.assertEqual(r.headers['location'], '/p/demo/plaene?meldung=rueckmeldung')
        s = self.client.get('/p/demo/plaene').text
        self.assertIn('Plan: Änderung gewünscht', s)
        self.assertNotIn('action="/p/demo/plan/wetter/abnehmen"', s)
        kopf(self, s, 'Zur Abnahme', 1)


class ProjektFilterTest(Basis):
    """S14.3b K1/K5: `/p/<projekt>/plaene` zeigt nur die Pläne dieses Projekts, `/plaene` alle."""

    def setUp(self):
        super().setUp()
        self.zweit = self.tmp / 'rubview'
        self.zweit.mkdir()
        git(self.zweit, 'init', '-q', '-b', 'main')
        (self.zweit / 'ROADMAP.md').write_text(ROADMAP.replace('# demo', '# rubview'))
        (self.zweit / 'AGENTS.md').write_text('# Regeln\n')
        (self.zweit / 'plan').mkdir()
        (self.zweit / 'plan' / 'fremd.md').write_text(plantext('Fremd · Claude · 04.11.2026', 'Entwurf', 'R1'))
        (self.zweit / 'devflow.toml').write_text('test = "true"\n')
        git(self.zweit, 'add', '-A')
        git(self.zweit, 'commit', '-q', '-m', 'start')
        projekte.anmelden(self.zweit, 'rubview')

    def test_projektseite_zeigt_nur_eigene_plaene(self):
        s = self.client.get('/p/demo/plaene').text
        self.assertIn('/p/demo/plan/wetter"', s)
        self.assertNotIn('rubview', s.split('<main>')[1])
        self.assertNotIn('/plan/fremd', s)
        r = self.client.get('/p/rubview/plaene').text
        self.assertIn('/p/rubview/plan/fremd"', r)
        self.assertNotIn('/plan/wetter', r)
        self.assertEqual(felder(r, '/p/rubview/plan/fremd/abnehmen')['zurueck'], 'plaene')

    def test_globale_seite_zeigt_alle_gruppiert_nach_projekt(self):
        s = self.client.get('/plaene').text
        self.assertLess(s.index('/p/demo/plan/wetter"'), s.index('/p/rubview/plan/fremd"'))
        self.assertIn('href="/p/rubview/plaene"', s)
        self.assertEqual(s.count('<h3>Zur Abnahme (1)</h3>'), 2)

    def test_unbekanntes_oder_ungueltiges_projekt_404(self):
        for pfad in ('/p/gibtsnicht/plaene', '/p/..%2Fx/plaene', '/p/DEMO/plaene'):
            self.assertEqual(self.client.get(pfad).status_code, 404, pfad)

    def test_dieselben_rechte_wie_die_globale_seite(self):
        fremd = TestClient(web.anwendung(), follow_redirects=False)
        for pfad in ('/plaene', '/p/demo/plaene'):
            self.assertIn(fremd.get(pfad).status_code, (401, 403), pfad)
        self.assertEqual(self.client.get('/p/demo/plaene').status_code, 200)

    def test_planseite_fuehrt_zurueck_auf_die_projektliste(self):
        self.assertIn('href="/p/demo/plaene"', self.client.get('/p/demo/plan/wetter').text)


class RoadmapTextTest(unittest.TestCase):
    def test_plan_abnehmen_ohne_stand_zeile_oder_tabelle(self):
        with self.assertRaises(Exception):
            roadmap.plan_abnehmen('# X\n\n| ID | Aufgabe | Entwickler | Bereich | Abnahmekriterium |\n|---|---|---|---|---|\n| 1 | a | claude | b | c |\n', '01.01.2026', {})
        with self.assertRaises(Exception):
            roadmap.plan_abnehmen('# X\n\n**Stand:** Entwurf\n', '01.01.2026', {})


if __name__ == '__main__':
    unittest.main()
