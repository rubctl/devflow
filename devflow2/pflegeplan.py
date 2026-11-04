"""Routine-Updates (S5.2): Pflegeplan `plan/pflege.md`, Werkzeug `pflege_aufgabe`, Aufgaben `P.<datum>.<n>`, Diff-Prüfung vor dem Merge.

Durchgesetzt wird im Code, nicht im Prompt. Gültig ist nur der committete Stand des Basisbranchs: abgenommen, nicht widerrufen, vollständig
und passend zum Deploybefehl aus `devflow.toml`. Was dem nicht genügt, ergibt keine Aufgabe. Die Aufgaben entstehen aus dem letzten
Prüfergebnis (`pflege.letzter`), nicht aus Angaben der steuernden KI; sie kann eine Auswahl treffen und Bedenken melden, also den Umfang
nur enger machen. Major-Sprünge, bekannte Brüche, Migrationen und unklare Einordnung werden ein Planentwurf mit Frage an den Inhaber.

Vor dem Merge vergleicht `abweichungen` den tatsächlichen Diff mit dem Umfang: nur die Dateien aus dem Plan und die eigenen Berichte, in
Manifesten nur die Versionen der Aufgabe und genau deren Ziel, in Lockdateien nur erlaubte Sprünge im Paketumfang, auch je Abhängigkeitskante,
und nichts an Paketen mit bekanntem Bruch. Was sich nicht prüfen lässt, gilt als Abweichung.

Stand: `pflege/<projekt>-updates.json` im Zustandsverzeichnis (Freigaben je Aufgabe, gemeldete Entwürfe).
"""
import datetime
import json
from pathlib import Path
import re
import shlex
import time
import tomllib

from . import inhaber as _i
from . import Fehler, ablauf, ereignis, pflege, plaene, projekte, repo, roadmap, schreiben
from .ablauf import datum
from .pflege import _name
from .projekte import NAMEN

DATEI = f'plan/{plaene.PFLEGEPLAN}.md'
VORLAGE = 'plan/_pflege-vorlage.md'
ID = re.compile(r'P\.\d{4}-\d{2}-\d{2}\.\d+')
SPRUENGE = ('patch', 'minor')
GESCHLOSSEN = ('erledigt', 'entfällt')
MAX_ALTER = 2 * pflege.INTERVALL  # ältere Prüfergebnisse sagen über den Stand nichts mehr
ZEILE = re.compile(r'^[ \t]*\*\*Pflege:\*\*(.*)$', re.M)
ABGENOMMEN = _i.Muster(lambda: r'abgenommen ' + _i.von_muster() + r' am\s+\S+')
CODE = re.compile(r'`([^`]+)`')
BRUCH = re.compile(r'\s*[-*]\s*`([^`]+)`(?:\s*:\s*(.*))?$')
VERSION = re.compile(r'\d+(?:\.\d+){0,2}')
KOPF = re.compile(r'(?:[A-Za-z][A-Za-z0-9._-]*\s*(?:\[[^\]]*\])?)?')  # Paketname mit Extras in PEP-508-Anforderungen; npm-Bereiche haben keinen
TEIL = re.compile(r'([\^~<>=!]*)\s*(\d+(?:\.\d+)*)')
UNTERGRENZEN = ('', '=', '==', '>=', '^', '~', '~=')  # die Zielversion selbst ist erlaubt; nicht bei <, <=, > oder !=
NPM_ABSCHNITTE = ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies')
TOML_LOCKS = ('uv.lock', 'poetry.lock', 'Cargo.lock')
TOML_QUELLE = ('source', 'sdist', 'wheels', 'checksum', 'files')


def ist_pflege(aufgabe_id):
    return bool(ID.fullmatch(aufgabe_id or ''))


# --- Pflegeplan --------------------------------------------------------------------------------

def _abschnitte(text):
    out, aktuell = {}, None
    for z in text.split('\n'):
        if z.startswith('## '):
            aktuell = z[3:].strip()
            if aktuell in out:
                raise Fehler(f'{DATEI}: Abschnitt „{aktuell}“ gibt es doppelt')
            out[aktuell] = []
        elif aktuell:
            out[aktuell].append(z)
    return out


def _werte(teile, titel, feld):
    """Werte in Backticks der Zeile `**<feld>:**` im Abschnitt; Platzhalter gelten als nicht ausgefüllt."""
    for z in teile.get(titel, ()):
        if re.match(rf'\s*[-*]?\s*\*\*{feld}:\*\*', z):
            werte = CODE.findall(z)
            if werte and not any('…' in w for w in werte):
                return werte
            break
    raise Fehler(f'{DATEI}: unter „{titel}“ fehlt **{feld}:** mit einem Wert in Backticks')


def _ein_wert(teile, titel, feld):
    werte = _werte(teile, titel, feld)
    if len(werte) != 1:
        raise Fehler(f'{DATEI}: unter „{titel}“ erwartet **{feld}:** genau einen Wert')
    return werte[0]


def _art(pfad):
    """Art der Paketdatei; None für alles, was nicht in einen Pflegeumfang gehört."""
    n = Path(pfad).name
    if n == 'package.json':
        return 'npm'
    if n == 'package-lock.json':
        return 'npm-lock'
    if n == 'pyproject.toml':
        return 'pyproject'
    if n in TOML_LOCKS:
        return 'toml-lock'
    if re.fullmatch(r'requirements[^/]*\.txt', n):
        return 'requirements'
    return 'fremd' if n in pflege.LOCKDATEIEN else None


def _inhalt(text):
    """Der Plan als Dict aus dem Text; unvollständig oder mehr als erlaubt ist ein Fehler."""
    teile = _abschnitte(text)
    pakete = _werte(teile, 'Umfang', 'Pakete')
    if 'alle' in pakete and len(pakete) > 1:
        raise Fehler(f'{DATEI}: **Pakete:** ist entweder `alle` oder eine Liste von Paketen')
    dateien = _werte(teile, 'Umfang', 'Dateien')
    falsch = [d for d in dateien if not pflege._pfad_ok(d) or _art(d) is None]
    if falsch:
        raise Fehler(f'{DATEI}: im Umfang stehen nur Manifeste und Lockdateien, nicht {", ".join(falsch)}')
    spruenge = _werte(teile, 'Sprünge', 'Erlaubt')
    if set(spruenge) - set(SPRUENGE):
        raise Fehler(f'{DATEI}: erlaubt sind nur {" und ".join(SPRUENGE)}; Major bleibt {_i.bei()}')
    rueckweg = ' '.join(z.strip() for z in teile.get('Rückweg', ())).strip()
    if not rueckweg or '…' in rueckweg:
        raise Fehler(f'{DATEI}: der Abschnitt „Rückweg“ ist nicht ausgefüllt')
    brueche = {}
    for z in teile.get('Bekannte Brüche und Migrationen', ()):
        m = BRUCH.match(z)
        if m:
            brueche[_name(m.group(1))] = roadmap.zelle(m.group(2) or 'im Pflegeplan genannt')[:200]
    deploy = _ein_wert(teile, 'Deploy', 'Ziel')
    return dict(pakete=None if pakete == ['alle'] else {_name(x) for x in pakete}, dateien=dateien, spruenge=spruenge, rueckweg=rueckweg,
                brueche=brueche, test=_ein_wert(teile, 'Tests', 'Befehl'), deploy='' if deploy == 'kein' else deploy)


def _text(p):
    """Committeter Text des Pflegeplans im Basisbranch; '' ohne Plan. Eine Änderung im Arbeitsverzeichnis sperrt: Sie kann ein Widerruf sein."""
    if repo.git(p.repo, 'status', '--porcelain', '--', DATEI):
        raise Fehler(f'{DATEI} hat nicht committete Änderungen; es gilt nur der committete Stand')
    return repo.git(p.repo, 'show', f'{p.cfg["basis"]}:{DATEI}', check=False)


def _widerrufen(text):
    return any(z.strip().lower().startswith('widerrufen') for z in ZEILE.findall(text))


def laden(p):
    """Der gültige Pflegeplan. Fehler: fehlt, nicht abgenommen, widerrufen, unvollständig oder passt nicht zum Deploybefehl."""
    text = _text(p)
    if not text:
        raise Fehler(f'{p.name}: Kein Pflegeplan {DATEI}; Vorlage {VORLAGE}')
    if _widerrufen(text):
        raise Fehler(f'{DATEI} ist widerrufen; es entstehen keine Routine-Updates')
    if not ABGENOMMEN.match(roadmap.plan_stand(text) or ''):
        raise Fehler(f'{DATEI} ist nicht abgenommen; erwartet die Zeile „**Stand:** abgenommen {_i.von()} am <Datum>“')
    if [z.strip().lower() for z in ZEILE.findall(text)] != ['aktiv']:
        raise Fehler(f'{DATEI}: erwartet genau eine Zeile `**Pflege:** aktiv`')
    plan = _inhalt(text)
    if plan['deploy'] != p.cfg['deploy']:
        raise Fehler(f'{DATEI}: Deployziel passt nicht zu `deploy` in devflow.toml')
    return plan


def stand(p):
    """Für die Seite „Pflege“: (Zustand, Grund) mit Zustand `aktiv`, `fehlt`, `widerrufen` oder `ungültig`."""
    try:
        text = _text(p)
        if not text:
            return 'fehlt', f'Ohne {DATEI} entstehen keine Routine-Updates.'
        if _widerrufen(text):
            return 'widerrufen', 'Es entstehen keine Routine-Updates, offene Aufgaben werden nicht gemergt.'
        laden(p)
    except Fehler as err:
        return 'ungültig', str(err)
    return 'aktiv', ''


def widerrufen(p):
    """Knopf im Dashboard: Zeile `**Pflege:** widerrufen` als Commit auf main. Rückgabe: True, wenn sie neu ist."""
    neu = []

    def aendern(texte):
        text = texte[DATEI]
        if not text:
            raise Fehler(f'{p.name}: Kein Pflegeplan {DATEI}')
        if _widerrufen(text):
            return {DATEI: text, 'ROADMAP.md': texte['ROADMAP.md']}
        geaendert, n = ZEILE.subn('**Pflege:** widerrufen', text)
        neu.append(True)
        return {DATEI: geaendert if n else text.rstrip('\n') + '\n\n**Pflege:** widerrufen\n',
                'ROADMAP.md': roadmap.protokoll(texte['ROADMAP.md'], f'{datum()} · Pflegeplan widerrufen · Herkunft dashboard')}

    repo.auf_main(p, aendern, f'plan: {DATEI} widerrufen', pfade=(DATEI, 'ROADMAP.md'))
    return bool(neu)


# --- Einordnung --------------------------------------------------------------------------------

def _zahl(version):
    return tuple((pflege.zahlen(version) + [0, 0, 0])[:3])


def sprung(von, nach):
    """Wie `pflege.sprung`, aber vorsichtig: `patch` und `minor` nur bei klaren, steigenden Versionen. Vorabversionen, Rückschritte und
    alles, was vor 1.0 per SemVer brechen darf (Minor unter 0.x, jeder Sprung unter 0.0.x), sind `unklar`."""
    if not VERSION.fullmatch(von or '') or not VERSION.fullmatch(nach or '') or _zahl(nach) <= _zahl(von):
        return 'unklar'
    s = pflege.sprung(von, nach)
    a = _zahl(von)
    if s == 'minor' and a[0] == 0 or s == 'patch' and a[:2] == (0, 0):
        return 'unklar'
    return s


def _fix(f):
    """Kleinste Fix-Version über der aktuellen; '' ohne eindeutige aktuelle Version oder ohne Fix."""
    if not VERSION.fullmatch(f['version'] or ''):
        return ''
    return min((b for b in f['behoben'] if VERSION.fullmatch(b) and _zahl(b) > _zahl(f['version'])), key=_zahl, default='')


def _kandidaten(e):
    """Mögliche Updates aus dem Prüfergebnis: Veraltetes und Lücken mit ihrer Fix-Version."""
    out = [dict(paket=v['paket'], von=v['aktuell'], nach=v['neu'], anlass='veraltet') for v in e['veraltet']]
    out += [dict(paket=f['paket'], von=f['version'], nach=_fix(f), anlass=f'Lücke {pflege.kennung(f)}') for f in e['funde']]
    return out


def einordnen(k, plan, bedenken):
    """(`routine`, Sprung), (`frage`, Grund) oder (`ausserhalb`, Grund). Der Paketumfang geht vor: Was der Inhaber ausgeschlossen hat, kommt nicht als Frage wieder."""
    name = _name(k['paket'])
    if plan['pakete'] is not None and name not in plan['pakete']:
        return 'ausserhalb', 'nicht im Paketumfang'
    if name in plan['brueche']:
        return 'frage', f'bekannter Bruch oder Migration: {plan["brueche"][name]}'
    if name in bedenken:
        return 'frage', f'Bedenken: {bedenken[name]}'
    if not k['nach']:
        return 'frage', 'keine Fix-Version bekannt'
    s = sprung(k['von'], k['nach'])
    if s == 'major':
        return 'frage', 'Major-Sprung'
    if s == 'unklar':
        return 'frage', 'Einordnung unklar'
    return ('routine', s) if s in plan['spruenge'] else ('ausserhalb', f'{s} ist im Pflegeplan nicht erlaubt')


# --- Stand -------------------------------------------------------------------------------------

def _datei(name):
    return projekte.zustand() / 'pflege' / f'{projekte.slug(name)}-updates.json'


def _lesen(name):
    try:
        d = json.loads(_datei(name).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return dict(aufgaben={}, entwuerfe={})
    return d


def _sichern(name, d):
    schreiben(_datei(name), json.dumps(d, indent=1, ensure_ascii=False) + '\n')


def _schluessel(k):
    return f'{_name(k["paket"])}@{k["nach"] or k["anlass"]}'


def _bekannt(k, d, status):
    """Wiederholter Fund: Paket in einer offenen Aufgabe, Zielversion schon behandelt (auch verworfen) oder schon als Entwurf gemeldet."""
    if _schluessel(k) in d['entwuerfe']:
        return True
    for aid, a in d['aufgaben'].items():
        for x in a['pakete']:
            if _name(x['paket']) == _name(k['paket']):
                if status.get(aid, 'entfällt') not in GESCHLOSSEN:
                    return True
                if VERSION.fullmatch(k['nach']) and _zahl(k['nach']) <= _zahl(x['nach']):
                    return True
    return False


def freigabe(p, aufgabe_id):
    a = _lesen(p.name)['aufgaben'].get(aufgabe_id)
    if not a:
        raise Fehler(f'{aufgabe_id}: keine Freigabe; Pflegeaufgaben entstehen nur über pflege_aufgabe')
    return a


# --- Werkzeug pflege_aufgabe -------------------------------------------------------------------

def _sortieren(e, plan, gewaehlt, bedenken, d, status):
    routine, fragen, ausserhalb, bekannt = {}, {}, [], []
    for k in _kandidaten(e):
        name = _name(k['paket'])
        if gewaehlt and name not in gewaehlt:
            continue
        klasse, grund = einordnen(k, plan, bedenken)
        wort = f'{k["paket"]} {k["von"]} → {k["nach"] or "?"}'
        if klasse == 'ausserhalb':
            ausserhalb.append(dict(paket=name, text=f'{wort}: {grund}'))
        elif _bekannt(k, d, status):
            bekannt.append(wort)
        elif klasse == 'frage':
            fragen[_schluessel(k)] = {**k, 'grund': grund}
        elif name not in routine or _zahl(k['nach']) > _zahl(routine[name]['nach']):
            routine[name] = {**k, 'sprung': grund}
    return list(routine.values()), fragen, ausserhalb, sorted(set(bekannt))


def _entwurf_text(heute, nr, fragen, plan, p):
    zeilen = [f'| {roadmap.zelle(k["paket"])} | {k["von"] or "–"} | {k["nach"] or "–"} | {roadmap.zelle(k["grund"])} |' for k in fragen]
    return '\n'.join([
        f'# Vorhaben Pflege-Update {heute} · devflow · {datum()}', '', '**Stand:** Entwurf', '', '## Ziel', '',
        f'Diese Updates aus der Pflegeprüfung laufen nicht unter dem Pflegeplan und brauchen {_i.besitz("Entscheidung", "die")}.', '',
        '| Paket | Aktuell | Neu | Grund |', '|---|---|---|---|', *zeilen, '', '## Aufgaben', '',
        roadmap.PLANKOPF_STUFE, '|---|---|---|---|---|---|',
        f'| U.{heute}.{nr} | Update: {roadmap.zelle(", ".join(sorted({k["paket"] for k in fragen})))} | {p.cfg["entwickler"]} | '
        f'{", ".join(f"`{x}`" for x in plan["dateien"])} | Tests grün, Brüche im Code behoben, Rückweg geprüft | schwer |', '',
        '## Risiken', '', f'- Rückweg laut Pflegeplan: {roadmap.eine_zeile(plan["rueckweg"])}', ''])


def _entwurf_und_frage(p, fragen, plan, heute):
    nr = next(n for n in range(1, 1000) if not (p.repo / 'plan' / f'pflege-{heute}-{n}.md').exists())
    liste = list(fragen.values())
    datei = plaene.entwurf(p, f'pflege-{heute}-{nr}', _entwurf_text(heute, nr, liste, plan, p))
    punkte = '\n'.join(f'- {k["paket"]} {k["von"] or "?"} → {k["nach"] or "?"}: {k["grund"]}' for k in liste)
    f = ablauf.frage_stellen(p, f'Pflege: {len(liste)} Update(s) laufen nicht unter dem Pflegeplan. Entwurf: {datei}\n\n{punkte}',
                             ['Plan abnehmen: normale Kette mit Abnahme', 'Liegen lassen: nichts passiert, derselbe Fund meldet sich nicht wieder'],
                             'Entwurf und Änderungshinweise der Pakete ansehen, dann abnehmen.')
    return datei, f['frage']


def _aufgabe_eintragen(p, routine, d, heute):
    pakete = [dict(paket=k['paket'], von=k['von'], nach=k['nach'], sprung=k['sprung']) for k in routine]
    titel = ', '.join(f'{k["paket"]} {k["von"]} → {k["nach"]}' for k in pakete[:4]) + (f' und {len(pakete) - 4} weitere' if len(pakete) > 4 else '')
    wer = NAMEN[p.cfg['entwickler']]

    def fn(t):
        muster = re.compile(rf'P\.{heute}\.(\d+)')
        ids = [m.group(1) for x in (*(a['id'] for a in roadmap.aufgaben(t)), *d['aufgaben']) if (m := muster.fullmatch(x))]
        aid = f'P.{heute}.{1 + max(map(int, ids), default=0)}'
        t = roadmap.anlegen(t, aid, f'Routine-Update: {titel}', repo.tag(), wer, 'bereit', f'Pflegeplan `{DATEI}`')
        return roadmap.protokoll(t, f'{datum()} · Pflege: {aid} angelegt ({len(pakete)} Pakete) · Pflegeplan {DATEI}'), aid

    aid = repo.roadmap_aendern(p, fn, 'roadmap: Pflegeaufgabe angelegt')
    d['aufgaben'][aid] = dict(zeit=time.time(), pakete=pakete)
    _sichern(p.name, d)
    return aid, pakete


def aufgabe_anlegen(p, pakete=(), bedenken=None):
    """Update-Aufgabe aus dem letzten Prüfergebnis, nur unter gültigem Pflegeplan.

    `pakete` wählt aus (leer: alles Passende), `bedenken` (Paket → Grund) schickt ein Paket zum Inhaber. Beides kann den Umfang nur enger machen.
    Ein gewähltes Paket jenseits des Plans lehnt den ganzen Aufruf ab. Rückgabe: Aufgabe, Pakete, Entwurf, Frage, Ausgelassenes.
    """
    ablauf._bereit_zum_start(p)
    plan = laden(p)
    e = pflege.letzter(p.name)
    if not e:
        raise Fehler(f'{p.name}: Noch keine Pflegeprüfung; erst prüfen')
    if e.get('traum'):
        raise Fehler(f'{p.name}: Die letzte Prüfung stammt aus einem Traum und löst keine Update-Aufgabe aus; erst normal prüfen')
    if time.time() - e['zeit'] > MAX_ALTER:
        raise Fehler(f'{p.name}: Die letzte Pflegeprüfung ({e["tag"]}) ist zu alt; erst neu prüfen')
    gewaehlt = {_name(x) for x in pakete}
    bedenken = {_name(k): roadmap.zelle(v)[:200] for k, v in (bedenken or {}).items()}
    fremd = (gewaehlt | set(bedenken)) - {_name(k['paket']) for k in _kandidaten(e)}
    if fremd:
        raise Fehler(f'Nicht im letzten Prüfergebnis: {", ".join(sorted(fremd))}')
    heute = datetime.date.today().isoformat()
    with pflege._sperre():
        d = _lesen(p.name)
        status = {a['id']: a['status'] for a in roadmap.aufgaben(p.roadmap.read_text(encoding='utf-8'))}
        routine, fragen, ausserhalb, bekannt = _sortieren(e, plan, gewaehlt, bedenken, d, status)
        zu_weit = [a['text'] for a in ausserhalb if a['paket'] in gewaehlt]
        if zu_weit:
            raise Fehler(f'Überschreitet den Pflegeplan, keine Aufgabe: {"; ".join(zu_weit)}')
        entwurf, frage = None, None
        if fragen:
            entwurf, frage = _entwurf_und_frage(p, fragen, plan, heute)
            d['entwuerfe'].update({s: dict(datei=entwurf, frage=frage) for s in fragen})
            _sichern(p.name, d)
        aid, liste = _aufgabe_eintragen(p, routine, d, heute) if routine else (None, [])
    return dict(aufgabe=aid, pakete=liste, entwurf=entwurf, frage=frage, ausserhalb=[a['text'] for a in ausserhalb], bekannt=bekannt)


def nach_pruefung(p, e):
    """Nach einem Pflegelauf: Selbstlauf nur wecken, wenn unter gültigem Plan neue Arbeit liegt."""
    try:
        status = {a['id']: a['status'] for a in roadmap.aufgaben(p.roadmap.read_text(encoding='utf-8'))}
        routine, fragen, _, _ = _sortieren(e, laden(p), set(), {}, _lesen(p.name), status)
    except Fehler:
        return
    if routine or fragen:
        ereignis.still('pflege-routine', p.name, e['tag'])


# --- Kette: Auftrag und Tests ------------------------------------------------------------------

def auftrag(p, aufgabe_id, zusatz=''):
    """Auftrag an den Entwickler mit dem freigegebenen Umfang; andere Aufgaben bleiben unverändert. Widerrufen oder ohne Freigabe: kein Start."""
    if not ist_pflege(aufgabe_id):
        return zusatz
    plan, a = laden(p), freigabe(p, aufgabe_id)
    zeilen = '\n'.join(f'- {x["paket"]} {x["von"]} → {x["nach"]} ({x["sprung"]})' for x in a['pakete'])
    text = f"""Routine-Update unter dem Pflegeplan `{DATEI}`. Bringe genau diese Pakete auf genau diese Versionen:
{zeilen}

Ändern darfst du nur {", ".join(f"`{x}`" for x in plan["dateien"])} und deinen Bericht. In Manifesten ändern sich nur die Versionen dieser Pakete, keine Skripte und keine neuen Pakete; Lockdateien erzeugt das Werkzeug des Projekts. Installiere nichts in laufende Dienste oder fremde Umgebungen. devflow vergleicht den Diff vor dem Merge mit diesem Umfang; jede Abweichung verhindert den Merge.
Die Tests laufen in einer frischen Kopie des committeten Stands mit eigenem HOME: `{plan["test"]}`.
Geht das Update nicht ohne Codeänderung, Migration oder größeren Sprung, erzwinge nichts und stelle die Frage {_i.an()}."""
    return f'{text}\n\n{zusatz}' if zusatz else text


def test_befehl(p, aufgabe_id):
    """Testbefehl des Pflegeplans in einer frischen Kopie von HEAD mit eigenem HOME und knapper Umgebung ('' für andere Aufgaben).
    So zählt nur der committete Stand, und nichts berührt die Umgebung des laufenden Dienstes."""
    if not ist_pflege(aufgabe_id):
        return ''
    return ('set -o pipefail; wurzel=$(mktemp -d) || exit 1; trap \'rm -rf "$wurzel"\' EXIT; '
            'mkdir "$wurzel/projekt" "$wurzel/home" && git archive HEAD | tar -x -C "$wurzel/projekt" && cd "$wurzel/projekt" && '
            f'env -i HOME="$wurzel/home" PATH="$PATH" LANG=C.UTF-8 CI=1 bash -c {shlex.quote(laden(p)["test"])}')


# --- Diff-Prüfung ------------------------------------------------------------------------------

def _anforderung(x):
    return _name(re.match(r'[A-Za-z0-9._-]*', x.strip()).group(0))


def _bereich(x):
    """(Gerüst, [(Operator, Version), …]) einer Versionsanforderung; None für alles andere (URL, Git, Alias, Tag, Platzhalter, Vorabversion).
    Das Gerüst ist alles, was ein Update nicht ändern darf: Name, Extras, Operatoren, Marker."""
    kern, _, marker = str(x).partition(';')
    kopf = KOPF.match(kern).group(0)
    geruest = TEIL.sub(r'\1#', kern[len(kopf):])
    teile = TEIL.findall(kern[len(kopf):])
    return ((kopf.strip(), geruest, marker.strip()), teile) if teile and re.fullmatch(r'[\^~<>=!#,|\s]*', geruest) else None


def _bereich_passt(alt, neu, ziel):
    """Ändert die Anforderung nur Versionen, und zwar genau auf die Zielversion? Obere Grenzen bleiben; was das nicht zulässt, entscheidet der Inhaber."""
    a, n = _bereich(alt), _bereich(neu)
    return bool(a and n) and a[0] == n[0] and all(x == y or op in UNTERGRENZEN and y == ziel for (_, x), (op, y) in zip(a[1], n[1]))


def _traegt(x, ziel):
    b = _bereich(x)
    return bool(b) and any(op in UNTERGRENZEN and v == ziel for op, v in b[1])


def _npm_ziel(stellen, pfad, paket):
    """Der Eintrag, den Node für `paket` von `pfad` aus findet: erst im eigenen node_modules, dann Ebene für Ebene aufwärts."""
    while True:
        treffer = stellen.get(f'{pfad}/node_modules/{paket}'.lstrip('/'))
        if treffer or not pfad:
            return treffer
        pfad = pfad.rpartition('/node_modules/')[0]


def _npm_lock(text):
    d = json.loads(text)
    stellen = d.pop('packages')
    d.pop('dependencies', None)  # Doppel von `packages` in lockfileVersion 2
    pakete, kanten = {}, {}
    for pfad, e in stellen.items():
        name = _name(pfad.rsplit('node_modules/', 1)[-1])
        if 'node_modules/' in pfad and not e.get('link'):
            pakete.setdefault(name, {})[str(e.get('version', ''))] = [e.get('resolved'), e.get('integrity')]
        for paket in (n for a in NPM_ABSCHNITTE for n in e.get(a, {})):
            ziel = _npm_ziel(stellen, pfad, paket)
            if ziel:
                kanten.setdefault((name, _name(paket)), {}).setdefault(pfad, set()).add(str(ziel.get('version', '')))
    return dict(pakete=pakete, angaben={}, rest=d, kanten=kanten)


def _toml_abhaengigkeiten(e):
    """(Name, Angabe) je Abhängigkeit: Cargo `name version quelle`, uv Tabellen mit `name` und `version`, Poetry Name → Bereich."""
    ab = e.get('dependencies', ())
    if isinstance(ab, dict):
        return [(n, str(w)) for n, w in ab.items()]
    teile = [x.split() if isinstance(x, str) else [x['name'], x.get('version', '')] for x in ab]
    return [(t[0], t[1] if len(t) > 1 else '') for t in teile]


def _toml_lock(text):
    d = tomllib.loads(text)
    eintraege = d.pop('package')
    for k in ('content-hash', 'files'):  # ändern sich mit jedem Update (Poetry)
        d.get('metadata', {}).pop(k, None)
    pakete, kanten = {}, {}
    for e in eintraege:
        pakete.setdefault(_name(e['name']), {})[str(e.get('version', ''))] = [e.get(k) for k in TOML_QUELLE]
    for e in eintraege:
        for name, angabe in _toml_abhaengigkeiten(e):
            versionen = pakete.get(_name(name), ())
            if versionen:  # eindeutig: die eine Version; sonst die Angabe, bei Poetry ein Bereich, dessen Änderung als unklar zählt
                stelle = kanten.setdefault((_name(e['name']), _name(name)), {}).setdefault(str(e.get('version', '')), set())
                stelle.update(versionen if len(versionen) == 1 else {angabe})
    return dict(pakete=pakete, angaben={}, rest=d, kanten=kanten)


def _npm(text):
    d = json.loads(text)
    angaben = {(a, _name(n)): w for a in NPM_ABSCHNITTE for n, w in d.pop(a, {}).items()}
    return dict(pakete={}, angaben=angaben, rest=d, kanten={})


def _pyproject(text):
    d = tomllib.loads(text)
    projekt = d.get('project', {})
    gruppen = {'dependencies': projekt.pop('dependencies', []),
               **{f'optional/{g}': l for g, l in projekt.pop('optional-dependencies', {}).items()},
               **{f'gruppe/{g}': l for g, l in d.pop('dependency-groups', {}).items()}}
    return dict(pakete={}, rest=d, kanten={}, angaben={(g, _anforderung(x) if isinstance(x, str) else str(x)): x for g, l in gruppen.items() for x in l})


def _requirements(text):
    pakete, angaben, rest = {}, {}, []
    for zeile in text.replace('\\\n', ' ').split('\n'):
        z = zeile.split(' #')[0].strip()
        pin = re.fullmatch(r'([A-Za-z0-9._-]+)(\[[^\]]*\])?==([^\s;]+)(.*)', z)
        if pin:
            name, extras, version, zusatz = _name(pin.group(1)), pin.group(2) or '', pin.group(3), pin.group(4).strip()
            pakete.setdefault(name, {})[version] = [zusatz]
            marker = re.sub(r'--hash=\S+', '', zusatz).strip()
            if extras or marker:
                rest.append(f'{name}{extras} {marker}')
        elif z.startswith('-'):
            rest.append(z)
        elif z and not z.startswith('#'):
            angaben[('requirements', _anforderung(z))] = z
    return dict(pakete=pakete, angaben=angaben, rest=rest, kanten={})


LESER = {'npm-lock': _npm_lock, 'toml-lock': _toml_lock, 'npm': _npm, 'pyproject': _pyproject, 'requirements': _requirements}


def _wechsel(a, n, spruenge):
    """Wechsel zwischen zwei Versionsmengen, die kein erlaubter Sprung erklärt: Jede weggefallene Version braucht ein erlaubtes Ziel, jede neue eine erlaubte Herkunft."""
    return sorted({(v, max(n, key=_zahl)) for v in a - n if not any(sprung(v, x) in spruenge for x in n)}
                  | {(max(a, key=_zahl), v) for v in n - a if not any(sprung(x, v) in spruenge for x in a)})


def _vergleiche(a, n):
    """(alte, neue Versionen) einer Kante, je Stelle des Elternpakets (npm: Pfad, TOML: Elternversion), sonst ginge die Zuordnung verloren. Stellen auf
    beiden Seiten zählen gegen sich selbst. Der Rest ist umgezogen oder zusammengeführt und nicht zuzuordnen: Eine entfallene Stelle zählt gegen alle neuen
    Versionen der Kante, eine neue gegen alle alten, auch wenn auf der anderen Seite keine Stelle dazukommt. Lieber eine Frage zu viel."""
    gemeinsam = a.keys() & n.keys()
    alt, neu = ({v for s in x.values() for v in s} for x in (a, n))
    entfallen, hinzu = ({v for s in x.keys() - gemeinsam for v in x[s]} for x in (a, n))
    unklar = {(v, x) for v in entfallen for x in neu} | {(v, x) for v in alt for x in hinzu}
    return [(a[s], n[s]) for s in gemeinsam] + [({v}, {x}) for v, x in unklar]


def _vergleichen(pfad, alt, neu, plan, ziele):
    """Abweichungen einer Paketdatei: außerhalb der Versionen, Pakete, die nicht zur Aufgabe gehören, Anforderungen abseits des Ziels, nicht erlaubte
    Sprünge (auch je Abhängigkeitskante, sonst fiele ein Auflösungswechsel auf eine schon vorhandene Version nicht auf), bekannte Brüche, geänderte Quellen."""
    m = []
    if alt['rest'] != neu['rest']:
        m.append(f'{pfad}: Änderung außerhalb der Paketversionen')
    for k in alt['angaben'].keys() ^ neu['angaben'].keys():
        m.append(f'{pfad}: Paket {k[-1]} neu oder entfernt')
    angefasst = {k[-1] for k in alt['angaben'].keys() | neu['angaben'].keys() if alt['angaben'].get(k) != neu['angaben'].get(k)}
    for k in alt['angaben'].keys() & neu['angaben'].keys():
        if alt['angaben'][k] != neu['angaben'][k]:
            if k[-1] not in ziele:
                m.append(f'{pfad}: {k[-1]} gehört nicht zur Aufgabe')
            elif not _bereich_passt(alt['angaben'][k], neu['angaben'][k], ziele[k[-1]]):
                m.append(f'{pfad}: {k[-1]}: Anforderung ändert mehr als die Version auf {ziele[k[-1]]}')
    kanten = {k: [(a, n) for a, n in _vergleiche(alt['kanten'][k], neu['kanten'][k]) if a != n] for k in alt['kanten'].keys() & neu['kanten'].keys()}
    for k, paare in sorted(kanten.items()):
        wechsel = sorted({w for a, n in paare for w in _wechsel(a, n, plan['spruenge'])})
        m += [f'{pfad}: {k[1]} unter {k[0]}: {von} → {nach} ist kein erlaubter Sprung' for von, nach in wechsel]
    versetzt = {k[-1] for k, paare in kanten.items() if paare}
    for name in sorted(alt['pakete'].keys() | neu['pakete'].keys() | versetzt):
        a, n = alt['pakete'].get(name, {}), neu['pakete'].get(name, {})
        if a != n or name in versetzt:
            angefasst.add(name)
            if plan['pakete'] is not None and name not in plan['pakete']:
                m.append(f'{pfad}: {name} liegt außerhalb des Paketumfangs')
        m += [f'{pfad}: {name} {v}: Quelle oder Prüfsumme geändert' for v in sorted(a.keys() & n.keys()) if a[v] != n[v]]
        if a and n:
            m += [f'{pfad}: {name} {von} → {nach} ist kein erlaubter Sprung' for von, nach in _wechsel(set(a), set(n), plan['spruenge'])]
    m += [f'{pfad}: {name}: bekannter Bruch oder Migration: {plan["brueche"][name]}' for name in sorted(angefasst & plan['brueche'].keys())]
    return m


def _geaendert(p, basis, branch):
    """(alter Modus, neuer Modus, Art, Pfad) je geänderter Datei."""
    teile = repo.git(p.repo, 'diff', '--raw', '--no-renames', '-z', basis, branch).split('\0')
    return [(*(f := kopf.lstrip(':').split())[:2], f[4], pfad) for kopf, pfad in zip(teile[::2], teile[1::2])]


def _erreicht(neu, x):
    name, ziel = _name(x['paket']), x['nach']
    return ziel in neu['pakete'].get(name, ()) or any(k[-1] == name and _traegt(w, ziel) for k, w in neu['angaben'].items())


def _diff_pruefen(p, aufgabe_id, branch, plan, a):
    basis = repo.git(p.repo, 'merge-base', p.cfg['basis'], branch)
    ziele = {_name(x['paket']): x['nach'] for x in a['pakete']}
    m, staende = [], []
    for alt_modus, neu_modus, art, pfad in _geaendert(p, basis, branch):
        if pfad == f'berichte/{aufgabe_id}.md' or pfad.startswith(f'berichte/{aufgabe_id}-') and pfad.endswith('.md'):
            if art in 'AM' and neu_modus == '100644':
                continue
        if pfad not in plan['dateien']:
            m.append(f'{pfad} liegt außerhalb des Umfangs')
            continue
        leser = LESER.get(_art(pfad))
        if art != 'M' or alt_modus != '100644' or neu_modus != '100644' or leser is None:
            m.append(f'{pfad}: Änderung nicht prüfbar')
            continue
        try:
            alt, neu = (leser(repo.git(p.repo, 'show', f'{c}:{pfad}')) for c in (basis, branch))
        except (Fehler, ValueError, KeyError, TypeError, AttributeError):  # auch tomllib.TOMLDecodeError und json: Inhalt nicht wie erwartet
            m.append(f'{pfad}: Inhalt nicht lesbar')
            continue
        m += _vergleichen(pfad, alt, neu, plan, ziele)
        staende.append(neu)
    m += [f'{x["paket"]}: Zielversion {x["nach"]} nicht erreicht' for x in a['pakete'] if not any(_erreicht(n, x) for n in staende)]
    return m


def abweichungen(p, aufgabe_id, branch):
    """Gründe gegen den Merge einer Pflegeaufgabe (leer: im Umfang; andere Aufgaben immer leer). Ein ungültiger Plan oder eine fehlende
    Freigabe sperren ebenso wie ein Diff, der sich nicht prüfen lässt."""
    if not ist_pflege(aufgabe_id):
        return []
    try:
        return _diff_pruefen(p, aufgabe_id, branch, laden(p), freigabe(p, aufgabe_id))
    except (Fehler, ValueError) as err:
        return [str(err)]


def merge_frage(aufgabe_id, gruende):
    """(Frage, Optionen, Empfehlung) statt eines Merges."""
    liste = '\n'.join(f'- {g}' for g in gruende[:20])
    return (f'{aufgabe_id} wird nicht gemergt: Der Diff oder der Pflegeplan passt nicht zur Freigabe.\n\n{liste}',
            ['Nacharbeit: den Diff auf den Umfang zurückführen', 'Aufgabe entfällt', 'Eigener Plan: gehört die Änderung dazu, normal abnehmen'],
            'Nacharbeit mit dem Hinweis auf die Abweichung; wird mehr gebraucht, ein eigener Plan. Eine Antwort hebt die Prüfung nicht auf.')


def deploy_grund(p, aufgabe_id):
    """Vor dem automatischen Deploy einer Pflegeaufgabe: Grund dagegen oder ''."""
    if not ist_pflege(aufgabe_id):
        return ''
    try:
        laden(p)
    except Fehler as err:
        return str(err)
    return ''

