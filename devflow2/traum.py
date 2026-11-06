"""Dreaming (S5.3): Der Inhaber drückt „Träumen“, devflow steckt den Rest des laufenden 5-Stunden-Fensters in liegen gebliebene Arbeit.

Ein Traum startet nur über `starten` (Knopf im Dashboard), nie von selbst. Die Hintergrundschleife des Dashboard-Dienstes ruft
`takt()` auf; ein Takt startet höchstens den nächsten Schritt eines schon laufenden Traums. Reihenfolge, billig vor teuer: Rückblick
der Woche, Pflegeprüfung aller Projekte, Ideen ausarbeiten (mit Zweitmeinung von Sol, wenn Sol Kontingent hat), Code pflegen, dann je
gewähltem Punkt ein Vorschlag auf eigenem Branch (bauen, testen, Review durch Sol). Gemergt wird keiner davon ohne den Inhaber (`vorschlag.py`).

Grenzen, alle im Code:
- **Eine Steuerung:** `selbstlauf.startsperre` gilt für Traum und Selbstlauf. Solange ein Traum aktiv ist, startet kein Steuerlauf;
  läuft normale Arbeit, startet kein Traum.
- **Ein Fenster:** Beim Start steht das Fensterende von Claude dauerhaft im Traum (`fenster_ende`), jeder Traumlauf trägt es als
  Laufzeitgrenze (`bis`). Über das Fensterende geht nichts hinaus: kein Fortsetzen, keine Kontingentpause, auch nach einem Neustart nicht.
  Je Fenster gibt es höchstens einen Traum.
- **Frische Messung:** Fehlt sie oder ist sie älter als 2 h, holt `messung.py` sie mit einem Messaufruf je Modell (nur auf Klick bzw. vor einem
  Traumschritt). Scheitert der Aufruf, lehnt der Start mit Grund ab (Sol: nur dessen Schritte entfallen).
- **Grenzen vor jedem Modellstart:** `tor` läuft im Laufprozess unmittelbar vor jedem Start der CLI, auch bei Wiederholungen, und zählt
  ihn in `zaehler`. Geprüft werden frische Messung, `traum_grenze` und `traum_reserve` je Modell (`kontingent.traum_pruefen`).
- **Vorrang:** Abnahme, Antwort, Rückmeldung, Nacharbeit und Fortsetzen beenden den Traum nach dem laufenden Schritt.
- **Halt und Fensterende** beenden auch den laufenden Schritt. „Traum beenden“ lässt ihn zu Ende laufen, danach startet nichts mehr.

Zustand: `traeume/<id>.json` im Zustandsverzeichnis. Der Traumbericht entsteht am Ende als `berichte/traum-<id>.md` im Heimatprojekt.
"""
import datetime
import json
import os
import sys

from . import inhaber as _i
from . import Fehler, ablauf, auftraege, eingang, ereignis, grundlage, kontingent, laeufe, messung, pflege, plaene, projekte, repo, schreiben, selbstlauf, vorschlag
from .projekte import NAMEN, Projekt

TAKT = 10.0
MAX_VERSUCHE = 2  # ein Schritt, dessen Lauf scheitert, bekommt einen zweiten Versuch; er zählt und passiert das Tor wie der erste
TOLERANZ = 600  # Sekunden; so weit darf die gemessene Freigabezeit vom gespeicherten Fensterende abweichen, ohne als neues Fenster zu gelten
HEIM = 'devflow'  # Rückblick und Traumbericht gehören hierher, sonst ins erste angemeldete Projekt
VORRANG = ('plan-abgenommen', 'plan-rueckmeldung', 'antwort')
SONNET = dict(cli_modell='sonnet', cli_effort='medium')
LESEND = ['--strict-mcp-config', '--tools', selbstlauf.EINGEBAUT]  # kein MCP, keine Shell, kein Schreibzugriff
TITEL = {'rueckblick': 'Rückblick der Woche', 'pflege': 'Pflegeprüfung', 'ideen': 'Ideen ausarbeiten', 'meinung': 'Zweitmeinung', 'code': 'Code pflegen',
         'vorschlag': 'Vorschlag bauen', 'test': 'Vorschlag testen', 'review': 'Vorschlag prüfen'}
MODELL = {'rueckblick': 'claude', 'ideen': 'claude', 'meinung': 'sol', 'code': 'claude', 'vorschlag': 'claude', 'review': 'sol'}  # Pflege und Tests brauchen kein Modell
HALT = 'Alles angehalten'
FENSTER = 'Fensterende erreicht'
FERTIG = 'Alle Schritte erledigt'
def inhaber_beendet():
    return f'{_i.von_gross()} beendet'


# --- Zustand -------------------------------------------------------------------------------------

def _ordner():
    return projekte.zustand() / 'traeume'


def _sichern(t):
    schreiben(_ordner() / f'{t["id"]}.json', json.dumps(t, indent=1, ensure_ascii=False) + '\n')


def laden(tid):
    try:
        return json.loads((_ordner() / f'{tid}.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise Fehler(f'Traum {tid} unbekannt') from None


def alle():
    """Alle Träume, ältester zuerst."""
    return [laden(d.stem) for d in sorted(_ordner().glob('*.json'))]


def aktiv():
    """Der laufende Traum oder None. Auch ein Traum, der nur noch seinen letzten Schritt abwartet, ist aktiv: Bis dahin bleibt die Sperre."""
    return next((t for t in alle() if t['zustand'] == 'laeuft'), None)


def grenzen():
    """{modell: {'grenze', 'reserve'}}. Bei verschiedenen Werten der Projekte gilt der strengste, wie beim Budget des Selbstlaufs."""
    cfgs = []
    for name in projekte.alle():
        try:
            cfgs.append(Projekt(name).cfg)
        except Fehler:
            continue
    cfgs = cfgs or [projekte.STANDARD]
    return {m: dict(grenze=min(c['traum_grenze'][m] for c in cfgs), reserve=max(c['traum_reserve'][m] for c in cfgs)) for m in NAMEN}


def _heim():
    namen = list(projekte.alle())
    return HEIM if HEIM in namen else namen[0]


# --- Prüfungen -----------------------------------------------------------------------------------

def _normale_arbeit(t=None):
    """Grund, wenn normale Arbeit läuft oder (`t`) seit dem Start des Traums dazugekommen ist, sonst ''. Pflegeläufe des Wochentimers zählen nicht."""
    laeufe_ = [l for l in laeufe.alle() if not l.get('traum') and l['art'] not in ('pflege', 'messung')]
    if any(l['zustand'] in laeufe.AKTIV for l in laeufe_):
        return 'Normale Arbeit hat Vorrang: Es läuft ein Lauf'
    if t is None:
        return ''
    if any(max(l['angelegt'], l.get('fortgesetzt') or 0) >= t['start'] for l in laeufe_):
        return 'Normale Arbeit hat Vorrang: Ein Lauf kam dazu'
    if any(e.get('art') in VORRANG and (e.get('zeit') or 0) >= t['start'] for e in ereignis.lesen()):
        return 'Normale Arbeit hat Vorrang: Abnahme, Rückmeldung oder Antwort kam dazu'
    return ''


def _stoppgrund(t):
    """Warum dieser Traum keinen weiteren Schritt mehr startet, sonst ''."""
    if t['zustand'] != 'laeuft':
        return t.get('grund') or 'Traum beendet'
    if t.get('stopp'):
        return t['stopp']
    if laeufe.angehalten():
        return HALT
    if laeufe.jetzt() >= t['fenster_ende']:
        return FENSTER
    return _normale_arbeit(t)


def _modell_grund(t, modell):
    """Warum `modell` jetzt nicht starten darf, sonst ''. Ist die Messung veraltet, wird erst gemessen (höchstens einmal je Modell und Traum, wenn es scheitert)."""
    if kontingent.messung_noetig(modell):
        fehler = messung.auffrischen(Projekt(_heim()), [modell], seit=t['start'])
        if fehler.get(modell):
            return fehler[modell]
    grund, frei = kontingent.traum_pruefen(modell, **grenzen()[modell])
    if not grund and modell == 'claude' and frei - t['fenster_ende'] > TOLERANZ:
        return FENSTER
    return grund


def _startpruefung(gemessen=True):
    """(Grund gegen den Start oder '', Fensterende). Zählt nur unter der Startsperre, für die Anzeige reicht der Blick von außen.

    Ohne `gemessen` (Anzeige, Vorprüfung) ist eine fehlende frische Messung von Claude kein Grund: `starten` holt sie, bevor es prüft."""
    if laeufe.angehalten():
        return 'devflow ist angehalten', None
    if not projekte.alle():
        return 'Kein Projekt angemeldet', None
    grund = _normale_arbeit()
    if grund:
        return grund, None
    if selbstlauf.wartet_arbeit():
        return 'Normale Arbeit hat Vorrang: Für den Selbstlauf liegen Ereignisse bereit', None
    if not gemessen and kontingent.messung_noetig('claude'):
        return '', None
    grund, frei = kontingent.traum_pruefen('claude', **grenzen()['claude'])
    if grund:
        return grund, None
    if any(abs(t['fenster_ende'] - frei) <= TOLERANZ for t in alle()):
        return 'In diesem 5-Stunden-Fenster gab es schon einen Traum', None
    return '', frei


def startgrund():
    """Für das Dashboard: warum „Träumen“ gerade nicht geht, sonst ''."""
    if aktiv():
        return 'Es läuft schon ein Traum'
    return _startpruefung(gemessen=False)[0]


# --- Bedienung -----------------------------------------------------------------------------------

def starten(herkunft):
    """Knopf „Träumen“. Rückgabe: der neue Traum, oder None, wenn schon einer läuft (Doppelklick, Neustart). Wirft Fehler, wenn nichts startet."""
    if herkunft != 'dashboard':
        raise Fehler(f'Einen Traum startet nur {_i.nom()} im Dashboard')
    if aktiv():
        return None
    grund = _startpruefung(gemessen=False)[0]  # billige Gründe zuerst: wo ohnehin nichts startet, wird nicht gemessen
    if grund:
        raise Fehler(grund)
    fehler = messung.auffrischen(Projekt(_heim()))  # außerhalb der Startsperre: Das Warten soll nichts blockieren
    if fehler:
        raise Fehler(' · '.join(fehler[m] for m in NAMEN if m in fehler))  # jedes Modell ohne frische Messung lehnt den Start ab
    with selbstlauf.startsperre():
        if aktiv():
            return None
        grund, ende = _startpruefung()
        if grund:
            raise Fehler(grund)
        tid = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        t = dict(id=tid, zustand='laeuft', herkunft=herkunft, start=laeufe.jetzt(), fenster_ende=ende, stopp=None, schritte=[],
                 zaehler={m: 0 for m in NAMEN})
        _sichern(t)
        return t


def beenden(grund=None):
    """Knopf „Traum beenden“: kein weiterer Schritt. Rückgabe: ob damit etwas beendet wurde. Der erste Grund bleibt."""
    with selbstlauf.startsperre():
        t = aktiv()
        if not t or t.get('stopp'):
            return False
        t['stopp'] = grund or inhaber_beendet()
        _sichern(t)
        return True


def halt():
    """Teil von „Alles anhalten“: Auch wenn der Halt gleich wieder aufgehoben wird, geht dieser Traum nicht weiter."""
    with selbstlauf.startsperre():
        t = aktiv()
        if t and t.get('stopp') != HALT:
            t['stopp'] = HALT
            _sichern(t)


# --- Tor im Laufprozess --------------------------------------------------------------------------

def tor(lauf):
    """Unmittelbar vor jedem Modellstart und jedem internen Schritt eines Traumlaufs. Modellstarts zählen, auch Wiederholungen."""
    modell = lauf['modell'] if lauf['modell'] in NAMEN else None
    with selbstlauf.startsperre():
        t = laden(lauf['traum'])
        grund = _stoppgrund(t) or (modell and _modell_grund(t, modell))
        if grund:
            raise laeufe.TraumEnde(grund)
        if modell:
            t['zaehler'][modell] += 1
            _sichern(t)


# --- Schritte ------------------------------------------------------------------------------------

def _hat_ideen(name):
    """Neue Ideen vom Inhaber oder der Steuerung; Pflegefunde im Eingang werden im Traum keine Pläne."""
    return any(i['zustand'] == 'neu' and i['herkunft'] != 'pflege' for i in eingang.liste(Projekt(name)))


def _schritte(t):
    """Alle Schritte in ihrer Reihenfolge. Ideen und Entwürfe stehen erst fest, wenn die Schritte davor durch sind."""
    namen = list(projekte.alle())
    yield dict(art='rueckblick', projekt=_heim())
    for n in namen:
        yield dict(art='pflege', projekt=n)
    for n in namen:
        try:
            if _hat_ideen(n):
                yield dict(art='ideen', projekt=n)
        except (Fehler, OSError) as err:
            print(f'devflow2 traum: {n}: {err}', file=sys.stderr)
    for s in t['schritte']:
        for name in s.get('entwuerfe', ()):
            yield dict(art='meinung', projekt=s['projekt'], datei=f'plan/{name}.md')
    for n in namen:
        yield dict(art='code', projekt=n)
    for v in vorschlag.von_traum(t['id']):  # teuer zuletzt: erst alle Projekte durchsehen, dann bauen
        yield dict(art='vorschlag', projekt=v['projekt'], datei=v['id'])
        if v['commit'] and Projekt(v['projekt']).cfg['test']:
            yield dict(art='test', projekt=v['projekt'], datei=v['id'])
        if v['commit'] and v.get('test') != 'rot':
            yield dict(art='review', projekt=v['projekt'], datei=v['id'])


def _schluessel(s):
    return s['art'], s['projekt'], s.get('datei')


def _naechster(t):
    """Der Schritt, der zu wiederholen ist, sonst der erste noch nicht begonnene, sonst None."""
    for s in t['schritte']:
        if s['zustand'] == 'wiederholen':
            return s
    begonnen = {_schluessel(s) for s in t['schritte']}
    return next((s for s in _schritte(t) if _schluessel(s) not in begonnen), None)


def _woche():
    """Fakten für den Rückblick: Läufe, Kosten, Nacharbeit und Fehler je Projekt der letzten sieben Tage, ohne Traumläufe."""
    seit = laeufe.jetzt() - 7 * 86400
    zeilen = []
    for name in projekte.alle():
        ls = [l for l in laeufe.alle(name) if l['angelegt'] >= seit and not l.get('traum')]
        kosten = sum(k for k in map(kontingent.lauf_kosten, ls) if k is not None)
        arten = {a: sum(l['art'] == a for l in ls) for a in dict.fromkeys(l['art'] for l in ls)}
        fehler = [l for l in ls if l['zustand'] == 'fehler']
        zeilen.append(f'- {name}: {len(ls)} Läufe ({", ".join(f"{a} {n}" for a, n in arten.items()) or "keine"}), '
                      f'{sum(1 for l in ls if l["art"] == "nacharbeit" and l.get("zaehlt"))} Nacharbeitsrunden, {len(fehler)} Fehler, {kosten:.2f} $ Claude')
        zeilen += [f'  - Fehler {l["id"]}: {(l.get("fehler") or "")[:200]}' for l in fehler[-5:]]
    return '\n'.join(zeilen)


def _lauf_starten(t, s):
    """Lauf für den Schritt starten. Rückgabe: seine ID, oder None, wenn es nichts zu tun gibt."""
    p, marke = Projekt(s['projekt']), dict(traum=t['id'], bis=t['fenster_ende'])
    art = s['art']
    if art in vorschlag.SCHRITTE:
        return vorschlag.schritt_starten(p, art, s['datei'], marke)['id']
    if art == 'pflege':
        lauf = pflege.starten(p, **marke)
        return lauf['id'] if lauf else None
    if art == 'meinung':
        return ablauf.meinung_anfordern(p, s['datei'], 'Der Entwurf entstand im Traum aus einer Idee im Eingang.', 'sol', **marke)['id']
    marke['traum_art'] = art
    if art == 'rueckblick':
        g = grundlage.fuer_rueckblick(projekte.alle())
        return laeufe.starten(p, 'traum', 'claude', p.repo, auftraege.traum_rueckblick(_woche(), g), cli_zusatz=LESEND, grundlage_stand=g['stand'], **SONNET, **marke)['id']
    g = grundlage.fuer(p)
    if art == 'ideen':
        s.setdefault('plaene_vorher', plaene.namen(p))
        return laeufe.starten(p, 'traum', 'claude', p.repo, auftraege.traum_ideen(p.name, g), satz='planung', grundlage_stand=g['stand'],
                              cli_zusatz=selbstlauf._argumente('ideen'), **SONNET, **marke)['id']
    return laeufe.starten(p, 'traum', 'claude', p.repo, auftraege.traum_code(p.name, g), cli_zusatz=LESEND, grundlage_stand=g['stand'], **marke)['id']


def _auslassen(t, s, grund):
    if s not in t['schritte']:
        t['schritte'].append(dict(s, versuche=0, laeufe=[]))
        s = t['schritte'][-1]
    s.update(zustand='ausgelassen', grund=grund[:300])
    _sichern(t)


def _schritt_starten(t, s):
    """Rückgabe: ob jetzt ein Lauf läuft. Was sich nicht starten lässt, wird ausgelassen und der Traum geht weiter."""
    if s not in t['schritte']:
        s = dict(s, zustand='offen', versuche=0, laeufe=[])
        t['schritte'].append(s)
    try:
        lid = _lauf_starten(t, s)
    except (Fehler, OSError) as err:
        _auslassen(t, s, str(err))
        return False
    if lid is None:
        _auslassen(t, s, 'Prüfung läuft schon')
        return False
    s.update(zustand='laeuft', lauf=lid, versuche=s['versuche'] + 1, laeufe=s['laeufe'] + [lid])
    s.pop('grund', None)
    _sichern(t)
    return True


def _auswerten(t, s, l):
    """Ende eines Laufs in den Schritt übernehmen. Endet ein Claude-Lauf vorzeitig, endet der Traum: Die nächsten Schritte brauchen Claude auch."""
    ablauf._weg(l.get('review_worktree'))
    if s['art'] == 'ideen':  # auch aus einem abgebrochenen Lauf: Entwürfe sind Commits und gehören in den Bericht
        s['entwuerfe'] = sorted(set(plaene.namen(Projekt(s['projekt']))) - set(s['plaene_vorher']))
    if s['art'] == 'code' and l['zustand'] == 'fertig':
        vorschlag.registrieren(t, s['projekt'], vorschlag.zerlegen(laeufe.ergebnis(l))[1])
    if l['zustand'] == 'fertig':
        s['zustand'] = 'fertig'
    elif l['zustand'] == 'fehler' and s['versuche'] < MAX_VERSUCHE and not t.get('stopp'):
        s.update(zustand='wiederholen', grund=(l.get('fehler') or '')[:300])
    elif l['zustand'] == 'fehler':
        s.update(zustand='fehler', grund=(l.get('fehler') or '')[:300])
    else:
        s.update(zustand='abgebrochen', grund=l.get('grund') or 'Lauf beendet')
        if l['modell'] != 'sol':
            t['stopp'] = t.get('stopp') or s['grund']
    _sichern(t)


def _beenden(t):
    vorschlag.traum_ende(t['id'])
    t.update(zustand='beendet', ende=laeufe.jetzt(), grund=t.get('stopp') or FERTIG)
    _sichern(t)


def _weiter(t):
    """Ein Takt für den laufenden Traum, unter der Startsperre."""
    grund = _stoppgrund(t)
    if grund and not t.get('stopp'):
        t['stopp'] = grund
        _sichern(t)
    s = next((x for x in t['schritte'] if x['zustand'] == 'laeuft'), None)
    if s:
        l = laeufe.verwaist_pruefen(laeufe.laden(s['lauf']))
        if l['zustand'] in laeufe.AKTIV:
            if t.get('stopp') != HALT and not laeufe.angehalten() and laeufe.jetzt() < t['fenster_ende']:
                return  # auch nach „Traum beenden“ und Vorrang darf der laufende Schritt zu Ende laufen
            l = laeufe.anhalten(l['id'])
        _auswerten(t, s, l)
    while not t.get('stopp'):
        s = _naechster(t)
        if s is None:
            break
        modell = MODELL.get(s['art'])
        grund = modell and _modell_grund(t, modell)
        if grund and modell == 'sol':
            _auslassen(t, s, grund)
            continue
        if grund:
            t['stopp'] = grund
            break
        if _schritt_starten(t, s):
            return
    _beenden(t)


def _abbrechen(tid, err):
    """Die Steuerung selbst ist gescheitert. Ein Traum, der nicht mehr weiterkommt, darf die Sperre nicht behalten."""
    t = laden(tid)
    for s in t['schritte']:
        if s['zustand'] == 'laeuft':
            laeufe.anhalten(s['lauf'])
            s.update(zustand='abgebrochen', grund='Steuerung gescheitert')
    t['stopp'] = t.get('stopp') or f'Steuerung gescheitert: {type(err).__name__}: {err}'[:300]
    _beenden(t)


def takt():
    """Ein Takt der Hintergrundschleife. Startet nie einen Traum."""
    with selbstlauf.startsperre():
        t = aktiv()
        try:
            if t:
                _weiter(t)
        except Exception as err:
            _abbrechen(t['id'], err)
            raise
    _berichten()


async def schleife():
    import asyncio
    from starlette.concurrency import run_in_threadpool
    takt_ = float(os.environ.get('DEVFLOW2_TRAUM_TAKT') or TAKT)
    while True:
        try:
            await run_in_threadpool(takt)
        except Exception as err:
            print(f'devflow2 traum: Takt fehlgeschlagen: {type(err).__name__}: {err}', file=sys.stderr)
        await asyncio.sleep(takt_)


# --- Bericht und Hinweis -------------------------------------------------------------------------

def plaene_von(t):
    return [f'plan/{n}.md' for s in t['schritte'] for n in s.get('entwuerfe', ())]


def vorschlaege_von(t):
    """Alles, was als Vorschlag fertig wurde. Spätere Entscheidungen ändern die Zahl nicht: Sie steht im Traumbericht und im Hinweis."""
    return [v for v in vorschlag.von_traum(t['id']) if v['commit']]


def _zahl(n, ein, viele):
    return f'{n} {ein if n == 1 else viele}'


def bilanz(t):
    """„Traum beendet: 3 Vorschläge, 1 Plan“"""
    return f'Traum beendet: {_zahl(len(vorschlaege_von(t)), "Vorschlag", "Vorschläge")}, {_zahl(len(plaene_von(t)), "Plan", "Pläne")}'


def _uhr(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%d.%m.%Y %H:%M') if ts else '–'


def _vorschlaege_text(t):
    vs = vorschlag.von_traum(t['id'])
    zeilen = ['', '## Vorschläge', '', '| Vorschlag | Projekt | Branch | Commit | Tests | Review | Zustand |', '|---|---|---|---|---|---|---|'] if vs else []
    for v in vs:
        r = vorschlag.review_stand(Projekt(v['projekt']), v, v['commit'])
        zustand = ' '.join(f'{v["zustand"]} {v.get("grund") or ""}'.split())
        zeilen.append(f'| {v["titel"].replace("|", "/")} | {v["projekt"]} | `{v["branch"]}` | {(v["commit"] or "–")[:7]} | {v.get("test") or "–"} | {r["urteil"] if r else "–"} | {zustand.replace("|", "/")} |')
    return zeilen


def bericht_text(t):
    zeilen = [f'# Traum {t["id"]}', '', f'**Kurz:** {bilanz(t)}.', '',
              f'- **Start:** {_uhr(t["start"])}', f'- **Ende:** {_uhr(t.get("ende"))}, {t.get("grund") or "läuft"}',
              f'- **Fensterende:** {_uhr(t["fenster_ende"])}',
              '- **Modellstarts:** ' + ', '.join(f'{NAMEN[m]} {n}' for m, n in t['zaehler'].items()),
              f'- **Planentwürfe:** {", ".join(plaene_von(t)) or "keine"}', '',
              '## Schritte', '', '| Schritt | Projekt | Zustand | Läufe | Hinweis |', '|---|---|---|---|---|']
    for s in t['schritte']:
        hinweis = ' '.join(str(s.get('grund') or s.get('datei') or '').split()).replace('|', '/')
        zeilen.append(f'| {TITEL[s["art"]]} | {s["projekt"]} | {s["zustand"]} | {", ".join(s["laeufe"]) or "–"} | {hinweis} |')
    zeilen += _vorschlaege_text(t)
    for s in t['schritte']:
        if s['art'] in ('rueckblick', 'code') and s['zustand'] == 'fertig':
            text = vorschlag.zerlegen(laeufe.ergebnis(laeufe.laden(s['lauf'])))[0].strip()
            zeilen += ['', f'## {TITEL[s["art"]]} · {s["projekt"]}', '', text or '(ohne Text)']
    return '\n'.join(zeilen) + '\n'


def _berichten():
    """Bericht des zuletzt beendeten Traums einmal schreiben, außerhalb der Startsperre: Der Commit wartet auf die Sperre des Repos."""
    ts = alle()
    t = ts[-1] if ts else None
    if not t or t['zustand'] != 'beendet' or 'bericht' in t:
        return
    datei = f'berichte/traum-{t["id"]}.md'
    try:
        p = Projekt(_heim())
        text = bericht_text(t)
        repo.auf_main(p, lambda texte: {datei: text}, f'docs: Traumbericht {t["id"]}', (datei,))
        t['bericht'] = dict(projekt=p.name, datei=datei)
    except (Fehler, OSError, IndexError) as err:
        t['bericht'] = dict(fehler=str(err)[:300])  # die Seite „Traum“ zeigt alles auch ohne Commit
    _sichern(t)


def hinweise():
    """Push-Schlüssel des zuletzt beendeten Traums, sobald sein Bericht steht. Nur Kennung und Zahlen."""
    ts = alle()
    t = ts[-1] if ts else None
    if not t or 'bericht' not in t:
        return set()
    return {f'traum:traum:{t["id"]}:{len(vorschlaege_von(t))}:{len(plaene_von(t))}'}


def kurz():
    """Für `lage()`."""
    t = aktiv()
    return None if t is None else dict(id=t['id'], fenster_ende=t['fenster_ende'], stopp=t.get('stopp'), zaehler=t['zaehler'])
