"""Nummerierte Abnahmekriterien (S15.1): `K1: …<br>K2: …` in der Spalte Abnahmekriterium eines Plans.

Neue Entwürfe müssen je Aufgabe mindestens ein K tragen, ohne doppelte Nummer (`pruefen`). Pläne ohne K-Nummern bleiben gültig
und gelten als **Altbestand**. Erkannt wird `K<Zahl>` am Anfang eines `<br>`-Abschnitts, auch fett und mit kurzem Titel
(`**K1 Belege:**`); `<br>` in Code-Spannen (Backticks, z. B. das Formatbeispiel) trennt nicht; Abschnitte ohne eigene Nummer gehören zum vorigen Kriterium (Aufzählungen), Text vor dem ersten K ist eine
Einleitung (Abschnittskopf) und erzeugt kein Kriterium.

Review Kriterium für Kriterium (S15.3): `soll` nennt die IDs, die das Review bewerten muss (leer nur bei ausdrücklichem Altbestand),
`bewerten` prüft `kriterien` und die Kategorien der Befunde im Review-JSON streng; jeder Formfehler ist ein `Fehler`.
"""
import re

from . import Fehler

TRENNER = re.compile(r'<br\s*/?>', re.I)
CODE = re.compile(r'`[^`\n]*`')
KOPF = re.compile(r'\*{0,2}K(\d{1,4})(?:[ \t]+([^:<*]{1,60}?))?[ \t]*:\*{0,2}[ \t]*(.*)', re.S)


def zerlegen(kriterium):
    """(Einleitung, [(Nummer als int, Text), …]) aus der Zelle Abnahmekriterium; Einleitung ist der Text vor dem ersten K ('' wenn keiner)."""
    einleitung, liste = [], []
    # <br> in Code-Spannen (`K1: …<br>K2: …`) ist Beispieltext: vor dem Trennen verbergen, danach wiederherstellen
    text = CODE.sub(lambda m: m[0].replace('<', '\x00'), kriterium or '')
    for teil in TRENNER.split(text):
        teil = teil.replace('\x00', '<').strip()
        if not teil:
            continue
        if m := KOPF.fullmatch(teil):
            liste.append([int(m[1]), (f'{m[2]}: ' if m[2] else '') + m[3].strip()])
        elif liste:
            liste[-1][1] += ' ' + teil
        else:
            einleitung.append(teil)
    return ' '.join(einleitung), [(n, t.strip()) for n, t in liste]


def doppelte(liste):
    nummern = [n for n, _ in liste]
    return sorted({n for n in nummern if nummern.count(n) > 1})


def altbestand(aufgaben):
    """True, wenn keine Aufgabe des Plans ein nummeriertes Kriterium hat (Plan vor S15.1)."""
    return not any(zerlegen(a['kriterium'])[1] for a in aufgaben)


def pruefen(aufgaben):
    """Fehlertext, wenn eine Aufgabe eines neuen Entwurfs keine K-Nummern oder doppelte hat, sonst None."""
    for a in aufgaben:
        liste = zerlegen(a['kriterium'])[1]
        if not liste:
            return f'Aufgabe {a["id"]}: das Abnahmekriterium braucht nummerierte Kriterien „K1: …<br>K2: …“ (plan/_vorlage.md)'
        if doppelt := doppelte(liste):
            return f'Aufgabe {a["id"]}: doppelte Kriteriennummer {", ".join(f"K{n}" for n in doppelt)}'
    return None


ERFUELLT = ('ja', 'nein', 'teilweise')
KATEGORIEN = ('kriterium', 'rand', 'sonst')


def soll(plan):
    """IDs `['K1', …]`, die das Review der Planaufgabe bewerten muss. `[]` heißt ausdrücklich Altbestand: keine Planzeile (Pflege, Vorschlag,
    Aufgabe ohne Plan) oder ein Plan ganz ohne K-Nummern (`plan['altbestand']`, siehe `ablauf.plan_zeile`). Eine Aufgabe ohne K in einem
    Plan mit K-Nummern ist kein Altbestand: Fehler, damit kein Review stillschweigend ohne Kriterien bestehen kann."""
    if not plan:
        return []
    ks = zerlegen(plan.get('kriterium'))[1]
    if ks:
        return list(dict.fromkeys(f'K{n}' for n, _ in ks))
    if plan.get('altbestand', True):
        return []
    raise Fehler(f'Aufgabe {plan.get("id", "?")} in {plan.get("datei", "dem Plan")} hat keine nummerierten Kriterien, der Plan aber schon; '
                 'das Review braucht sie (Plan korrigieren, K1: …<br>K2: …)')


def _text(x):
    return x.strip() if isinstance(x, str) else None


def bewerten(d, ids):
    """`(kriterien, befunde)` aus dem Review-JSON `d`, geprüft gegen die erwarteten IDs `ids` (`[]` = Altbestand).

    - `kriterien`: genau eine Bewertung je erwarteter ID, `erfuellt` aus ERFUELLT, bei „nein“/„teilweise“ mit Begründung; fehlende, doppelte,
      unbekannte IDs oder ungültige Werte sind ein Fehler. Altbestand darf `kriterien` weglassen (oder leer lassen).
    - Befunde tragen `kategorie` (KATEGORIEN) und ggf. `kriterium` (bekannte K-ID); `kategorie` „kriterium“ braucht bei neuen Aufgaben
      die K-ID. Altbefunde ohne Kategorie (nur Altbestand) bekommen `''` (nicht zugeordnet).
    - „nein“/„teilweise“ ist mindestens P2: ein P3-Befund zu dieser ID wird P2, fehlt einer, kommt ein Befund mit der Begründung dazu."""
    roh = d.get('kriterien')
    if roh is None and not ids:
        roh = []
    if not isinstance(roh, list) or not all(isinstance(x, dict) for x in roh):
        raise Fehler('kriterien fehlt: erwartet eine Liste [{id, erfuellt, begruendung}] mit ' + (', '.join(ids) or 'keinem Eintrag (Altbestand)'))
    kriterien = []
    for x in roh:
        kid, erf, grund = (_text(x.get('id')) or '').upper(), (_text(x.get('erfuellt')) or '').lower(), _text(x.get('begruendung'))
        if erf not in ERFUELLT:
            raise Fehler(f'kriterien: {kid or "?"} hat erfuellt={x.get("erfuellt")!r}; erlaubt sind ja, nein, teilweise')
        if grund is None or (erf != 'ja' and not grund):
            raise Fehler(f'kriterien: {kid or "?"} braucht eine Begründung')
        kriterien.append(dict(id=kid, erfuellt=erf, begruendung=grund))
    genannt = [k['id'] for k in kriterien]
    fehler = [(was, liste) for was, liste in (
        ('fehlend', [i for i in ids if i not in genannt]),
        ('doppelt', list(dict.fromkeys(i for i in genannt if genannt.count(i) > 1))),
        ('unbekannt', list(dict.fromkeys(i for i in genannt if i not in ids)))) if liste]
    if fehler:
        raise Fehler('kriterien: ' + '; '.join(f'{was} {", ".join(i or "(leer)" for i in liste)}' for was, liste in fehler)
                     + f' (erwartet genau {", ".join(ids) or "keine (Altbestand)"})')
    kriterien.sort(key=lambda k: ids.index(k['id']))
    befunde = []
    for b in d.get('befunde') or []:
        b = {f: str(b.get(f, '')) for f in ('nr', 'schwere', 'ort', 'was')} | dict(
            kategorie=(_text(b.get('kategorie')) or '').lower(), kriterium=(_text(b.get('kriterium')) or '').upper())
        if b['kategorie'] not in KATEGORIEN and (ids or b['kategorie']):
            raise Fehler(f'Befund {b["nr"]}: kategorie muss kriterium, rand oder sonst sein')
        if b['kriterium'] and b['kriterium'] not in ids:
            raise Fehler(f'Befund {b["nr"]}: unbekanntes Kriterium {b["kriterium"]}')
        if ids and b['kategorie'] == 'kriterium' and not b['kriterium']:
            raise Fehler(f'Befund {b["nr"]}: kategorie kriterium braucht die K-ID')
        befunde.append(b)
    for k in kriterien:
        if k['erfuellt'] == 'ja':
            continue
        eigene = [b for b in befunde if b['kriterium'] == k['id']]
        for b in eigene:
            if not b['schwere'].upper().startswith(('P1', 'P2')):
                b['schwere'] = 'P2'
        if not eigene:
            befunde.append(dict(nr=f'M{len(befunde) + 1}', schwere='P2', ort='Abnahmekriterium', was=f'{k["id"]} {k["erfuellt"]}: {k["begruendung"]}',
                                kategorie='kriterium', kriterium=k['id']))
    return kriterien, befunde
