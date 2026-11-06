"""Kennzahlen zur Nacharbeit (S15.4), getrennt nach Ursache und nach Auftragsart.

Reine Auswertung der Läufe (nur lesend). Eine Aufgabe zählt zu „mit K-Kriterien“, wenn einer ihrer Läufe `kriterien_auftrag` (Entwicklung/Nacharbeit nannte K-Kriterien)
oder `kriterien_soll` (Review nannte K-IDs, S15.3) trägt; sonst zu „Altbestand“. Maßgeblich ist die tatsächlich verwendete Auftragsart,
kein Datum. Befunde ohne `kategorie` (Altbefunde) stehen unter „nicht zugeordnet“.

Ursache einer zählenden Nacharbeitsrunde = was der Lauf davor in derselben Aufgabe ergab: Review mit Nacharbeit → `review`,
rote Tests (Testlauf oder Vorbereitung) → `tests`, Rebase-Konflikt → `konflikt`, sonst → `hinweise` (Hinweise der steuernden KI).
Aktive Zeit = Dauer (start bis ende) der beendeten Läufe der Arten entwickeln, nacharbeit, vorbereiten, test und review
abzüglich `pause` (Kontingentwartezeit, Zeit zwischen Anhalten/Fehler und Fortsetzen); offene Läufe fehlen.
"""
from . import kriterien, laeufe

GRUPPEN = ('altbestand', 'kriterien')
URSACHEN = ('review', 'tests', 'konflikt', 'hinweise')
NICHT_ZUGEORDNET = 'nicht zugeordnet'
AKTIVE_ARTEN = ('entwickeln', 'nacharbeit', 'vorbereiten', 'test', 'review')


def _ursache(vorher):
    """Ursache einer zählenden Nacharbeitsrunde aus dem vorherigen Lauf (None = es gab keinen)."""
    if vorher is None:
        return 'hinweise'
    art = vorher.get('art')
    if art == 'review' and vorher.get('urteil') == 'nacharbeit':
        return 'review'
    if art == 'test' and vorher.get('rc') not in (0, None):
        return 'tests'
    if art == 'vorbereiten':
        if vorher.get('konflikt') is not None:
            return 'konflikt'
        if vorher.get('test_rc'):
            return 'tests'
    return 'hinweise'


def _leer():
    return {'aufgaben': 0, 'bewertet': 0, 'mit_nacharbeit': 0, 'mit_tests': 0, 'runden': 0, 'ursachen': {u: 0 for u in URSACHEN},
            'befunde': {}, 'zeit': 0.0, 'mit_zeit': 0}


def _aufgabe(ls):
    """Kennzahlen einer Aufgabe aus ihren Läufen (nach `angelegt` sortiert)."""
    z = {'review': False, 'nacharbeit': False, 'tests': False, 'runden': 0, 'ursachen': {u: 0 for u in URSACHEN}, 'befunde': {}, 'zeit': 0.0}
    vorher = None
    for l in ls:
        art = l.get('art')
        if art == 'nacharbeit' and l.get('zaehlt'):
            z['runden'] += 1
            u = _ursache(vorher)
            z['ursachen'][u] += 1
            z['tests'] = z['tests'] or u == 'tests'
        if art == 'review' and l.get('urteil'):
            z['review'] = True
            z['nacharbeit'] = z['nacharbeit'] or l['urteil'] == 'nacharbeit'
            for b in l.get('befunde') or ():
                k = b.get('kategorie') if isinstance(b, dict) else None
                k = k if k in kriterien.KATEGORIEN else NICHT_ZUGEORDNET
                z['befunde'][k] = z['befunde'].get(k, 0) + 1
        if art in AKTIVE_ARTEN and l.get('start') and l.get('ende'):
            warte = l['ende'] - l['wartet_seit'] if l.get('wartet_seit') and l['wartet_seit'] <= l['ende'] else 0  # angehalten in der Kontingentwartezeit
            z['zeit'] += max(0, l['ende'] - l['start'] - (l.get('pause') or 0) - warte)
        if not (art == 'nacharbeit' and not l.get('zaehlt')):  # kurzes Nachhaken ändert die Ursache nicht
            vorher = l
    return z


def auswerten(alle):
    """{'altbestand': {...}, 'kriterien': {...}} aus einer Lauflist; Aufgaben ohne Entwicklung, Nacharbeit oder Review fehlen."""
    je = {}
    for l in alle:
        if l.get('aufgabe'):
            je.setdefault((l.get('projekt'), l['aufgabe']), []).append(l)
    out = {g: _leer() for g in GRUPPEN}
    for ls in je.values():
        if not any(l.get('art') in ('entwickeln', 'nacharbeit', 'review') for l in ls):
            continue
        ls.sort(key=lambda l: l.get('angelegt', 0))
        a = _aufgabe(ls)
        g = out['kriterien' if any(l.get('kriterien_soll') or l.get('kriterien_auftrag') for l in ls) else 'altbestand']
        g['aufgaben'] += 1
        g['runden'] += a['runden']
        for u, n in a['ursachen'].items():
            g['ursachen'][u] += n
        g['mit_tests'] += a['tests']
        if a['review']:
            g['bewertet'] += 1
            g['mit_nacharbeit'] += a['nacharbeit']
        for k, n in a['befunde'].items():
            g['befunde'][k] = g['befunde'].get(k, 0) + n
        if a['zeit']:
            g['zeit'] += a['zeit']
            g['mit_zeit'] += 1
    return out


def stand(projekt=None):
    return auswerten(laeufe.alle(projekt))
