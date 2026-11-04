"""Kontingent von Claude und Sol und Kosten je Aufgabe (S3.2). Nur lesend, ohne Netz, ohne Hochrechnung.

- **Claude:** `rate_limit_event` in den Lauflogs (`laeufe/<id>/log.jsonl`): `unifiedWindows.five_hour` und `seven_day` mit
  `utilization` (0 bis 1) und `resetsAt`. Die Ereignisse tragen keine Uhrzeit; als Zeitpunkt der Messung gilt die letzte
  Änderung der Logdatei.
- **Sol:** `rate_limits` der `token_count`-Ereignisse in den codex-Sitzungsdateien (`~/.codex/sessions/…/rollout-*.jsonl`,
  primär 5 h, sekundär Woche). Gelesen wird nur das Ende der jüngsten Dateien, geschrieben nie.
- **Kosten:** `total_cost_usd` der `result`-Ereignisse in den Claude-Lauflogs. Sol meldet keine Kosten.

Jeder Wert ist `aktuell`, `veraltet` (Messung älter als `ALTER`) oder `unbekannt` (nichts gemessen, Format fremd oder
Fenster schon wieder frei, also der Wert überholt). Fehler beim Lesen ergeben `unbekannt`, nie eine Ausnahme.
"""
import datetime
import json
import os
from pathlib import Path
import time

from . import laeufe

ALTER = 2 * 3600
NAMEN = {'5h': '5 h', 'woche': 'Woche'}
CLAUDE_FENSTER = {'five_hour': '5h', 'seven_day': 'woche'}
SOL_FENSTER = {'primary': ('5h', 300), 'secondary': ('woche', 10080)}
SOL_DATEIEN = 20
SOL_ENDE = 512 * 1024
CLAUDE_DATEIEN = 40
_ZWISCHEN = {}


def codex_sitzungen():
    return Path(os.environ.get('DEVFLOW2_CODEX_SITZUNGEN') or Path.home() / '.codex' / 'sessions')


def _zahl(x, lo, hi):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not lo <= x <= hi:
        return None
    return float(x)


def _zeit(x):
    return _zahl(x, 1, 4e9)


def _iso(text):
    try:
        return datetime.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        return None


def _mtime(pfad):
    try:
        return pfad.stat().st_mtime
    except OSError:
        return 0


def _zeilen(pfad, ende=None):
    """Zeilen einer Datei, bei `ende` nur die letzten Bytes (ohne die angeschnittene erste Zeile)."""
    with open(pfad, 'rb') as f:
        gross = ende is not None and pfad.stat().st_size > ende
        if gross:
            f.seek(-ende, os.SEEK_END)
        zeilen = f.read().decode('utf-8', errors='replace').splitlines()
    return zeilen[1:] if gross else zeilen


def _gemerkt(pfad, art, lesen):
    """Ergebnis je Datei, solange Größe und Änderungszeit gleich sind (die Kopfzeile erscheint auf jeder Seite)."""
    try:
        st = pfad.stat()
    except OSError:
        return None
    schluessel = (str(pfad), art)
    marke = (st.st_mtime_ns, st.st_size)
    if _ZWISCHEN.get(schluessel, (None,))[0] != marke:
        try:
            wert = lesen(pfad, st)
        except (OSError, ValueError):
            wert = None
        if len(_ZWISCHEN) > 2000:
            _ZWISCHEN.clear()
        _ZWISCHEN[schluessel] = (marke, wert)
    return _ZWISCHEN[schluessel][1]


# --- Claude -------------------------------------------------------------------------------------

def claude_aus_zeilen(zeilen):
    """Jüngster Stand je Fenster aus Logzeilen: {'5h': {'prozent', 'frei'}, 'woche': {…}}; Prozent kann fehlen (None)."""
    out = {}
    for zeile in zeilen:
        if 'rate_limit_event' not in zeile:
            continue
        try:
            ereignis = json.loads(zeile)
            info = ereignis['rate_limit_info']
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(ereignis, dict) or ereignis.get('type') != 'rate_limit_event' or not isinstance(info, dict):
            continue
        fenster = info.get('unifiedWindows')
        if isinstance(fenster, dict):
            for roh, name in CLAUDE_FENSTER.items():
                f = fenster.get(roh)
                if isinstance(f, dict):
                    u = _zahl(f.get('utilization'), 0, 1)
                    out[name] = {'prozent': None if u is None else round(u * 100, 1), 'frei': _zeit(f.get('resetsAt'))}
        elif info.get('rateLimitType') in CLAUDE_FENSTER:  # ältere Form: nur das gemeldete Fenster, voll nur bei „rejected“
            out[CLAUDE_FENSTER[info['rateLimitType']]] = {'prozent': 100.0 if info.get('status') == 'rejected' else None,
                                                          'frei': _zeit(info.get('resetsAt'))}
    return out


def _claude_datei(pfad):
    def lesen(p, st):
        return claude_aus_zeilen(_zeilen(p)), st.st_mtime
    return _gemerkt(pfad, 'claude', lesen)


def claude_messungen(wurzel=None):
    """Je Fenster der jüngste Stand über die neuesten Lauflogs: {'5h': {'prozent', 'frei', 'stand'}, …}."""
    wurzel = wurzel or laeufe.verzeichnis()
    try:
        logs = sorted((p / 'log.jsonl' for p in wurzel.iterdir() if p.is_dir()), key=_mtime, reverse=True)
    except OSError:
        return {}
    out = {}
    for pfad in logs[:CLAUDE_DATEIEN]:
        gefunden = _claude_datei(pfad)
        if gefunden:
            for name, w in gefunden[0].items():
                out.setdefault(name, {**w, 'stand': gefunden[1]})
        if len(out) == len(CLAUDE_FENSTER):
            break
    return out


# --- Sol ----------------------------------------------------------------------------------------

def sol_aus_zeilen(zeilen, standard=None):
    """Jüngster Stand je Fenster aus Zeilen einer Sitzungsdatei: {'5h': {'prozent', 'frei', 'stand'}, 'woche': {…}}."""
    out = {}
    for zeile in zeilen:
        if 'rate_limits' not in zeile:
            continue
        try:
            ereignis = json.loads(zeile)
            grenzen = ereignis['payload']['rate_limits']
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(grenzen, dict) or grenzen.get('limit_id') not in (None, 'codex'):
            continue
        stand = _iso(ereignis.get('timestamp')) or standard
        for roh, (name, minuten) in SOL_FENSTER.items():
            f = grenzen.get(roh)
            if not isinstance(f, dict) or f.get('window_minutes') not in (None, minuten):
                continue
            p = _zahl(f.get('used_percent'), 0, 100)
            if p is not None and (name not in out or (stand or 0) >= (out[name]['stand'] or 0)):
                out[name] = {'prozent': p, 'frei': _zeit(f.get('resets_at')), 'stand': stand}
    return out


def _sol_datei(pfad):
    def lesen(p, st):
        return sol_aus_zeilen(_zeilen(p, SOL_ENDE), st.st_mtime)
    return _gemerkt(pfad, 'sol', lesen)


def _jungste_sitzungen(wurzel, anzahl):
    """Die nach Änderungszeit jüngsten Sitzungsdateien unter `wurzel`, unabhängig vom Tagesordner.

    Eine fortgesetzte Sitzung schreibt im Ordner ihres Starttags weiter. Es werden nur die Änderungszeiten aller
    Dateien abgefragt, gelesen wird nur das Ende der jüngsten `anzahl`.
    """
    return sorted(wurzel.rglob('*.jsonl'), key=_mtime, reverse=True)[:anzahl]


def sol_messungen(wurzel=None):
    wurzel = wurzel or codex_sitzungen()
    try:
        dateien = _jungste_sitzungen(wurzel, SOL_DATEIEN)
    except OSError:
        return {}
    out = {}
    for pfad in dateien:
        for name, w in (_sol_datei(pfad) or {}).items():
            if name not in out or (w['stand'] or 0) > (out[name]['stand'] or 0):
                out[name] = w
    return out


# --- Bewertung ----------------------------------------------------------------------------------

def bewerten(messung, jetzt):
    """`messung`: {'prozent', 'frei', 'stand'} oder None → {'zustand', 'prozent', 'frei', 'stand', 'hinweis'}.

    Ist die Freigabezeit vorbei, gilt das Fenster als neu angefangen und der gemessene Wert als überholt: `unbekannt`
    (der neue Stand ist nicht gemessen, geschätzt wird nicht).
    """
    leer = {'zustand': 'unbekannt', 'prozent': None, 'frei': None, 'stand': None, 'hinweis': 'keine Messung'}
    if not messung:
        return leer
    frei, stand, prozent = messung.get('frei'), messung.get('stand'), messung.get('prozent')
    if frei is not None and frei <= jetzt:
        return {**leer, 'frei': frei, 'stand': stand, 'hinweis': 'Fenster seit der Messung neu begonnen'}
    if prozent is None:
        return {**leer, 'frei': frei, 'stand': stand, 'hinweis': 'Prozent nicht gemeldet'}
    veraltet = stand is None or jetzt - stand > ALTER
    return {'zustand': 'veraltet' if veraltet else 'aktuell', 'prozent': prozent, 'frei': frei, 'stand': stand,
            'hinweis': 'Messung älter als 2 h' if veraltet else ''}


def stand(jetzt=None):
    """{'claude': {'5h': …, 'woche': …}, 'sol': {…}} bewertet; wirft nie."""
    jetzt = time.time() if jetzt is None else jetzt
    out = {}
    for wer, messen in (('claude', claude_messungen), ('sol', sol_messungen)):
        try:
            gemessen = messen()
        except Exception:  # fremde Dateien dürfen die Anzeige nie abbrechen
            gemessen = {}
        out[wer] = {name: bewerten(gemessen.get(name), jetzt) for name in NAMEN}
    return out


def messung_noetig(wer, jetzt=None):
    """Fehlt `wer` eine frische Messung, wie `traum_pruefen` sie verlangt (5-Stunden-Fenster mit Ende, Woche)?"""
    try:
        s = stand(jetzt)[wer]
    except Exception:
        return True
    return s['5h']['zustand'] != 'aktuell' or s['5h']['frei'] is None or s['woche']['zustand'] != 'aktuell'


def traum_pruefen(wer, grenze, reserve, jetzt=None):
    """Darf `wer` im Traum starten? (Grund dagegen oder '', Freigabezeit des 5-Stunden-Fensters).

    Es braucht eine aktuelle Messung mit bekanntem Fensterende; ohne sie gibt es nichts, wogegen die Grenzen prüfen könnten.
    `grenze`: höchstens so viel Prozent des 5-Stunden-Fensters verbraucht. `reserve`: so viel Prozent der Woche bleiben übrig.
    """
    name = wer.capitalize()
    try:
        s = stand(jetzt)[wer]
    except Exception:  # wie bei der Anzeige: fremde Dateien brechen nichts ab, und ohne Messung startet nichts
        return f'{name}: Messung nicht lesbar', None
    fenster, woche = s['5h'], s['woche']
    if fenster['zustand'] != 'aktuell' or fenster['frei'] is None:
        return f'{name}: keine frische Messung des 5-Stunden-Fensters ({fenster["hinweis"] or "Fensterende unbekannt"})', None
    if fenster['prozent'] >= grenze:
        return f'{name}: Traumgrenze erreicht ({fenster["prozent"]:g} % von {grenze:g} % des 5-Stunden-Fensters)', None
    if woche['zustand'] != 'aktuell':
        return f'{name}: keine frische Messung der Woche ({woche["hinweis"]})', None
    if 100 - woche['prozent'] <= reserve:
        return f'{name}: Wochenreserve erreicht ({100 - woche["prozent"]:g} % verbleibend, Reserve {reserve:g} %)', None
    return '', fenster['frei']


# --- Kosten -------------------------------------------------------------------------------------

def kosten_aus_zeilen(zeilen):
    """Summe von `total_cost_usd` der `result`-Ereignisse; None, wenn keines eine Zahl liefert."""
    summe, da = 0.0, False
    for zeile in zeilen:
        if 'total_cost_usd' not in zeile:
            continue
        try:
            e = json.loads(zeile)
        except ValueError:
            continue
        k = _zahl(e.get('total_cost_usd'), 0, 1e6) if isinstance(e, dict) and e.get('type') == 'result' else None
        if k is not None:
            summe, da = summe + k, True
    return summe if da else None


def lauf_kosten(lauf):
    """Kosten eines Laufs in US-Dollar; None, wenn nicht bekannt (Sol, Shell, noch kein Ergebnis, Log fehlt)."""
    if lauf.get('modell') != 'claude':
        return None
    return _gemerkt(laeufe.verzeichnis(lauf['id']) / 'log.jsonl', 'kosten', lambda p, st: kosten_aus_zeilen(_zeilen(p)))


def kosten():
    """({(projekt, aufgabe): {'laeufe', 'bekannt', 'summe'}}, [lauf + kosten, neueste zuerst])."""
    je_lauf, je_aufgabe = [], {}
    for lauf in sorted(laeufe.alle(), key=lambda l: l.get('angelegt', 0), reverse=True):
        k = lauf_kosten(lauf)
        je_lauf.append({'lauf': lauf, 'kosten': k})
        z = je_aufgabe.setdefault((lauf.get('projekt', ''), lauf.get('aufgabe') or ''), {'laeufe': 0, 'bekannt': 0, 'summe': 0.0})
        z['laeufe'] += 1
        if k is not None:
            z['bekannt'] += 1
            z['summe'] += k
    return je_aufgabe, je_lauf


def aufgaben_bilanz(projekt, jetzt=None):
    """Läufe, Nacharbeitsrunden, Kosten und Dauer je Aufgabe eines Projekts (Roadmap-Tabelle, S4.4).

    {aufgabe: {'laeufe', 'runden' (zählende Nacharbeitsläufe wie im Ablauf), 'claude' (Claude-Läufe), 'bekannt' (davon mit Kosten),
    'kosten' (Summe in $), 'dauer' (Sekunden, None ohne gemessene Laufzeit), 'stufe', 'entwickler' und 'reviewer' als
    {'modell', 'cli_modell', 'cli_effort'} des letzten Laufs oder None}}. Aufgaben ohne Läufe fehlen. Sol meldet keine Kosten.
    """
    jetzt = time.time() if jetzt is None else jetzt
    out = {}
    for lauf in laeufe.alle(projekt):
        aufgabe = lauf.get('aufgabe')
        if not aufgabe:
            continue
        z = out.setdefault(aufgabe, {'laeufe': 0, 'runden': 0, 'claude': 0, 'bekannt': 0, 'kosten': 0.0, 'dauer': None,
                                     'stufe': '', 'entwickler': None, 'reviewer': None})
        z['laeufe'] += 1
        if lauf.get('art') == 'nacharbeit' and lauf.get('zaehlt'):
            z['runden'] += 1
        if lauf.get('modell') == 'claude':
            z['claude'] += 1
            k = lauf_kosten(lauf)
            if k is not None:
                z['bekannt'] += 1
                z['kosten'] += k
        if lauf.get('start'):
            ende = jetzt if lauf.get('zustand') in laeufe.AKTIV else lauf.get('ende') or jetzt  # fortgesetzter Lauf behält sein altes ende
            z['dauer'] = (z['dauer'] or 0) + max(0, ende - lauf['start'])
        if lauf.get('modell') in ('claude', 'sol'):
            wer = {k: lauf.get(k) or '' for k in ('modell', 'cli_modell', 'cli_effort')}
            if lauf.get('art') in ('entwickeln', 'nacharbeit'):
                z['entwickler'] = wer
                z['stufe'] = lauf.get('stufe') or z['stufe']
            elif lauf.get('art') == 'review':
                z['reviewer'] = wer
    return out
