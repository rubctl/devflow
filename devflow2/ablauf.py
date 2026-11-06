"""Abläufe hinter den MCP-Werkzeugen: Stand, Plan übernehmen, Entwickeln, Test, Review, Nacharbeit, Merge, Deploy.

Mit `automatisch=True` startet jeder fertige Lauf den nächsten Schritt selbst:
entwickeln → vorbereiten → Review → (Nacharbeit → vorbereiten → Review, höchstens zwei Runden) → mergen → deployen.
`vorbereiten` ist der gemeinsame Schritt vor jedem Review: Rebase auf einen festgehaltenen Basiscommit, Tests. Gemergt wird nur, was in genau
diesem Integrationsstand getestet und reviewt ist; ist `main` seit dem Review fachlich weiter, geht die Kette zurück zu `vorbereiten` (S14.2).
Was der Inhaber entscheiden muss, landet als offene Frage in der Roadmap und setzt die Aufgabe auf „Braucht dich“. Solange sie
offen ist, startet für diese Aufgabe nichts mehr; die Kette wartet. Antworten, Ideen und Halte tragen ihre Herkunft
(`roadmap.HERKUNFT`). Sie kommt vom Zugang, also vom aufrufenden Code; der Standard ist `steuerung`, das Recht ohne Vorrang.
"""
import datetime
import json
from pathlib import Path
import re
import shutil
import sys

from . import inhaber as _i
from . import Fehler, auftraege, ereignis, grundlage, laeufe, projekte, repo, roadmap, schreiben, selbstlauf
from .projekte import NAMEN, Projekt
from .repo import git, ok, tag

MAX_RUNDEN = 2
def halt_nur_dashboard():
    return f'„Alles fortsetzen“ geht nur im Dashboard: Den globalen Halt hebt nur {_i.nom()} auf'


def _pflege():
    from . import pflegeplan  # erst beim Aufruf: pflegeplan baut auf diesem Modul auf
    return pflegeplan


def _traum():
    from . import traum  # erst beim Aufruf: traum baut auf diesem Modul auf
    return traum


def _vorschlag():
    from . import vorschlag  # erst beim Aufruf: vorschlag baut auf diesem Modul auf
    return vorschlag


class FrageOffen(Fehler):
    """Die Aufgabe wartet auf eine Antwort. Im Abschluss eines Laufs ist das kein Fehler: Die Kette endet hier."""


class NeuPruefen(Fehler):
    """Der reviewte Stand ist nicht der Integrationsstand, der gemergt würde: zurück zu Vorbereitung, Tests und Review."""


def datum():
    return datetime.date.today().strftime('%d.%m.%Y')


def kurz(lauf):
    felder = ('id', 'aufgabe', 'art', 'modell', 'zustand', 'runde', 'urteil', 'rc', 'fehler', 'frei_ab', 'branch', 'merge',
              'hinweise', 'halt_herkunft', 'kurz', 'basis_commit', 'test_commit', 'commit', 'konflikt', 'neu_pruefen')
    return {k: lauf[k] for k in felder if lauf.get(k) is not None}


# --- Lesen --------------------------------------------------------------------------------------

def lage():
    out = {'angehalten': laeufe.angehalten(), 'halt': laeufe.halt(), 'selbstlauf': selbstlauf.stand(), 'traum': _traum().kurz(), 'projekte': {}}
    for name in projekte.alle():
        try:
            p = Projekt(name)
            text = p.roadmap.read_text(encoding='utf-8')
            auf = roadmap.aufgaben(text)
        except (Fehler, OSError) as e:
            out['projekte'][name] = {'fehler': str(e)}
            continue
        ls = laeufe.alle(name)
        out['projekte'][name] = dict(
            kanban={s: n for s in roadmap.STATUS if (n := sum(a['status'] == s for a in auf))},
            **({'hinweise': h} if (h := grundlage.hinweise(p)) else {}),
            braucht_dich=[f'{a["id"]} {a["aufgabe"][:80]}' for a in auf if a['status'] == 'Braucht dich'],
            **({'anmeldung': m} if (m := [laeufe.login_hinweis(l) for l in ls if l['zustand'] == 'fehler' and l.get('login')]) else {}),
            vorschlaege=[f'{v["id"]} {v["titel"][:80]}' for v in _vorschlag().offene(name)],
            blockiert=[f'{a["id"]}: {a["details"][-160:]}' for a in auf if a['status'] == 'blockiert'],
            offene_fragen=[f'F{f["nr"]} · {f["bezug"]}' for f in roadmap.fragen(text)],
            laeufe=[kurz(l) for l in ls if l['zustand'] in laeufe.AKTIV],
            zuletzt=[kurz(l) for l in ls if l['zustand'] not in laeufe.AKTIV][-5:])
    return out


def plan_zeile(p, aufgabe):
    for datei in sorted((p.repo / 'plan').glob('*.md')):
        if not datei.name.startswith('_'):
            for row in roadmap.plan(datei.read_text(encoding='utf-8'))['aufgaben']:
                if row['id'] == aufgabe:
                    return {**row, 'datei': f'plan/{datei.name}'}
    return None


def kontext(p, aufgabe):
    _, c = roadmap.finde(p.roadmap.read_text(encoding='utf-8'), aufgabe)
    return dict(projekt=p.name, id=aufgabe, aufgabe=c[1], wer=c[2], status=c[3], details=c[5],
                basis=p.cfg['basis'], test=p.cfg['test'], plan=plan_zeile(p, aufgabe),
                karte_auslassen=p.cfg['karte_auslassen'])


def _aufgabentext(k, auftrag=''):
    """Text, nach dem Entscheidungen für eine Aufgabe ausgewählt werden: Titel, Bereich, Abnahmekriterium und der Zusatzauftrag der Entwicklung."""
    return ' '.join(x for x in (k['aufgabe'], (k['plan'] or {}).get('bereich'), (k['plan'] or {}).get('kriterium'), k.get('auftrag'), auftrag) if x)


def _mit_grundlage(p, k, auftrag=''):
    """Masterplan, Überblick und relevante bestätigte Entscheidungen für den Auftrag. Der Stand kommt auf die Basis, nicht aus dem Branch.
    `auftrag`: Zusatzauftrag der Entwicklung, wenn er noch nicht in `k` steht (Review, Nacharbeit)."""
    k['grundlage'] = grundlage.fuer(p, _aufgabentext(k, auftrag))
    return k


def _stand_von(k):
    return dict(grundlage_stand=k['grundlage']['stand'])


_EIGEN = None  # Lauf, dessen Abschluss gerade den nächsten Schritt startet


def aktiv(p, aufgabe):
    return [l for l in laeufe.alle(p.name, aufgabe) if l['zustand'] in laeufe.AKTIV and l['id'] != _EIGEN]


def zweig(p, aufgabe):
    """Branch, Worktree, Entwickler und letzte Entwicklersitzung einer Aufgabe."""
    ls = laeufe.alle(p.name, aufgabe)
    start = [l for l in ls if l['art'] == 'entwickeln']
    if not start:
        raise Fehler(f'Für Aufgabe {aufgabe} gibt es keinen Entwicklerlauf')
    z = {k: start[-1][k] for k in ('branch', 'worktree', 'entwickler')}
    z['zusatzauftrag'] = start[-1].get('zusatzauftrag', '')
    z['hinweise'] = [l['hinweise'] for l in ls if l['art'] == 'nacharbeit' and l.get('hinweise')
                     and l['angelegt'] >= start[-1]['angelegt']]
    if not git(p.repo, 'rev-parse', '--verify', '-q', f'refs/heads/{z["branch"]}', check=False):
        raise Fehler(f'Branch {z["branch"]} gibt es nicht mehr')
    sitzungen = [l['session'] for l in ls if l['art'] in ('entwickeln', 'nacharbeit') and l.get('session')
                 and l['modell'] == z['entwickler'] and l['angelegt'] >= start[-1]['angelegt']]
    z['session'] = sitzungen[-1] if sitzungen else None
    return z


def aufgabe(p, aufgabe_id):
    k = kontext(p, aufgabe_id)
    k['laeufe'] = [kurz(l) for l in laeufe.alle(p.name, aufgabe_id)]
    try:
        k['zweig'] = zweig(p, aufgabe_id)
    except Fehler:
        k['zweig'] = None
    orte = [Path(k['zweig']['worktree'])] if k['zweig'] else []
    k['berichte'] = sorted({str(f.relative_to(o)) for o in orte + [p.repo] if o.is_dir()
                            for f in (o / 'berichte').glob(f'{aufgabe_id}*.md')})
    k['fragen'] = [f for f in roadmap.fragen(p.roadmap.read_text(encoding='utf-8'), nur_offen=False) if f['bezug'] == aufgabe_id]
    return k


def lauf(lid, zeilen=40):
    l = laeufe.laden(lid)
    return {**kurz(l), 'log': laeufe.log_text(l, zeilen), 'ergebnis': (l.get('ergebnis') or '')[-3000:]}


# --- Stand --------------------------------------------------------------------------------------

def _stand(p, aufgabe_id, status=None, stand=None, wer=None):
    nachricht = f'roadmap: {aufgabe_id}' + (f' → {status}' if status else '') + (f' ({stand[:60]})' if stand else '')
    repo.roadmap_aendern(p, lambda t: roadmap.setze(t, aufgabe_id, tag(), status=status, wer=wer, stand=stand), nachricht)


def status_setzen(p, aufgabe_id, status, wer=None, details=None):
    repo.roadmap_aendern(p, lambda t: roadmap.setze(t, aufgabe_id, tag(), status, wer, details), f'roadmap: {aufgabe_id} → {status}')
    return roadmap.aufgaben(p.roadmap.read_text(encoding='utf-8'))


def aufgabe_anlegen(p, aufgabe_id, titel, wer='', status='offen', details='', abschnitt=None):
    if _pflege().ist_pflege(aufgabe_id):
        raise Fehler(f'{aufgabe_id}: Pflegeaufgaben entstehen nur über pflege_aufgabe')
    if _vorschlag().ist_vorschlag(aufgabe_id):
        raise Fehler(f'{aufgabe_id}: Vorschläge aus dem Traum werden nur durch {_i.besitz("„Übernehmen“", "das")} zur Aufgabe')
    repo.roadmap_aendern(p, lambda t: roadmap.anlegen(t, aufgabe_id, titel, tag(), wer, status, details, abschnitt),
                         f'roadmap: {aufgabe_id} angelegt')
    return kontext(p, aufgabe_id)


def frage_stellen(p, frage, optionen=(), empfehlung='', aufgabe_id=None, gesperrt=False):
    def fn(t):
        t, nr = roadmap.frage(t, tag(), frage, list(optionen), empfehlung, aufgabe_id or '')
        if aufgabe_id:
            t = roadmap.setze(t, aufgabe_id, tag(), 'Braucht dich', stand=f'Frage F{nr} {_i.an()}')
        return t, nr
    nr = repo.roadmap_aendern(p, fn, f'roadmap: Frage {_i.an()}{" zu " + aufgabe_id if aufgabe_id else ""}', gesperrt=gesperrt)
    return {'frage': f'F{nr}'}


def antwort_eintragen(p, nr, antwort, herkunft='steuerung'):
    nr = int(str(nr).lstrip('Ff'))
    def fn(t):
        t, bezug = roadmap.antwort(t, nr, tag(), antwort, herkunft)
        if (any(a['id'] == bezug and a['status'] == 'Braucht dich' for a in roadmap.aufgaben(t))
                and not any(f['bezug'] == bezug for f in roadmap.fragen(t))):
            t = roadmap.setze(t, bezug, tag(), 'bereit', stand=f'Antwort auf F{nr} liegt vor')
        return roadmap.protokoll(t, f'{datum()} · Antwort auf F{nr} · Herkunft {herkunft}'), bezug
    bezug = repo.roadmap_aendern(p, fn, f'roadmap: {roadmap.antwort_name(herkunft)} auf F{nr}')
    ereignis.still('antwort', p.name, f'F{nr}')
    return {'frage': f'F{nr}', 'bezug': bezug}


def protokoll(p, text):
    repo.roadmap_aendern(p, lambda t: roadmap.protokoll(t, f'{datum()} · {text}'), 'roadmap: Protokoll')
    return 'eingetragen'


def plan_uebernehmen(p, datei):
    pfad = (p.repo / datei).resolve()
    if pfad.parent != (p.repo / 'plan').resolve() or not pfad.is_file():
        raise Fehler(f'{datei}: erwartet eine Datei plan/<vorhaben>.md im Projekt')
    datei = f'plan/{pfad.name}'
    if git(p.repo, 'status', '--porcelain', '--', datei):
        raise Fehler(f'{datei} hat nicht committete Änderungen; abgenommen gilt nur der committete Stand')
    pl = roadmap.plan(pfad.read_text(encoding='utf-8'))
    if not pl['abgenommen']:
        raise Fehler(f'{datei} ist nicht abgenommen; erwartet die Zeile „**Stand:** abgenommen {_i.von()} am <Datum>“')
    if not pl['aufgaben']:
        raise Fehler(f'{datei}: keine Aufgabentabelle „{roadmap.PLANKOPF}“')
    rows = []
    for a in pl['aufgaben']:
        entw = a['entwickler'] or p.cfg['entwickler']
        if entw not in NAMEN:
            raise Fehler(f'{a["id"]}: Entwickler {a["entwickler"]!r} unbekannt (claude oder sol)')
        rows.append((a['id'], a['aufgabe'], NAMEN[entw], 'bereit', tag(), f'Plan `{datei}`'))
    repo.roadmap_aendern(p, lambda t: roadmap.protokoll(
        roadmap.neue_tabelle(t, f'{pl["titel"]} ([Plan]({datei}))', rows),
        f'{datum()} · Plan {datei} (abgenommen {pl["abgenommen"]}) übernommen: {len(rows)} Aufgaben'),
        f'roadmap: Plan {datei} übernommen')
    return [r[0] for r in rows]


# --- Arbeit -------------------------------------------------------------------------------------

def _reviewer(p, entwickler):
    r = p.cfg['reviewer']
    return r if r != entwickler else ('sol' if entwickler == 'claude' else 'claude')


def offene_fragen(p, aufgabe_id=None):
    """Offene Fragen zu einer Aufgabe; ohne Aufgabe alle mit Aufgabenbezug. Fragen ohne Bezug sperren nichts."""
    return [f for f in roadmap.fragen(p.roadmap.read_text(encoding='utf-8'))
            if f['bezug'] == aufgabe_id or (aufgabe_id is None and f['bezug'] != 'allgemein')]


def _gesperrt(fragen):
    if fragen:
        liste = ', '.join(f'F{f["nr"]} zu {f["bezug"]}' for f in fragen)
        raise FrageOffen(f'Wartet auf Antwort: {liste}; bis dahin startet dafür nichts')


def _bereit_zum_start(p, aufgabe_id=None):
    """Vor jeder Nebenwirkung: kein globaler Halt, keine offene Frage zur Aufgabe, nichts läuft schon für sie."""
    if laeufe.angehalten():
        raise Fehler(f'devflow ist angehalten; {halt_nur_dashboard()}')
    if aufgabe_id:
        _gesperrt(offene_fragen(p, aufgabe_id))
    if aufgabe_id and aktiv(p, aufgabe_id):
        raise Fehler(f'Für Aufgabe {aufgabe_id} läuft schon etwas')


def _deploy_gesperrt(p, aufgabe_id):
    """Deployt wird der ganze Stand von main: Es sperrt die eigene Frage und jede zu einer schon gemergten Aufgabe. Ohne
    Aufgabe (Werkzeug `deployen`) lässt sich der Deploy keiner zuordnen, dann sperrt jede Frage mit Aufgabenbezug. Ein Traumvorschlag
    sperrt, bis er übernommen und gemergt ist."""
    _vorschlag().deploy_sperre(p, aufgabe_id)

    def gemergt(a):
        return any(l['art'] == 'mergen' and l.get('merge') for l in laeufe.alle(p.name, a))
    _gesperrt([f for f in offene_fragen(p) if aufgabe_id is None or f['bezug'] == aufgabe_id or gemergt(f['bezug'])])


def entwickeln(p, aufgabe_id, auftrag='', entwickler=None, automatisch=True):
    _bereit_zum_start(p, aufgabe_id)
    k = kontext(p, aufgabe_id)
    if k['status'] in ('erledigt', 'entfällt'):
        raise Fehler(f'Aufgabe {aufgabe_id} ist {k["status"]}')
    entw = (entwickler or (k['plan'] or {}).get('entwickler') or p.cfg['entwickler']).lower()
    if entw not in NAMEN:
        raise Fehler(f'Entwickler {entw!r} unbekannt (claude oder sol)')
    auftrag = _pflege().auftrag(p, aufgabe_id, auftrag)
    branch = p.branch(aufgabe_id, entw)
    wt = p.worktree(branch)
    if git(p.repo, 'rev-parse', '--verify', '-q', f'refs/heads/{branch}', check=False) or wt.exists():
        raise Fehler(f'Branch {branch} oder Worktree {wt} gibt es schon; nacharbeit() verwenden oder aufräumen')
    git(p.repo, 'worktree', 'add', '-q', '-b', branch, str(wt), p.cfg['basis'])
    k.update(branch=branch, worktree=str(wt), auftrag=auftrag)
    _mit_grundlage(p, k)
    try:
        _stand(p, aufgabe_id, 'in Arbeit', f'Branch `{branch}`, Entwicklung läuft',
               wer=f'{NAMEN[entw]}, Review {NAMEN[_reviewer(p, entw)]}')
        l = laeufe.starten(p, 'entwickeln', entw, wt, auftraege.entwickeln(k), aufgabe=aufgabe_id, branch=branch,
                           worktree=str(wt), entwickler=entw, automatisch=automatisch, zusatzauftrag=auftrag, **_stand_von(k))
    except Exception:
        ok(p.repo, 'worktree', 'remove', str(wt))
        ok(p.repo, 'branch', '-d', branch)
        if k['status'] != 'in Arbeit':
            try:
                _stand(p, aufgabe_id, k['status'], 'Start abgebrochen')
            except Fehler:
                pass
        raise
    return kurz(l)


def _testbefehl(p, aufgabe_id):
    """Pflegeaufgaben testen nach ihrem Pflegeplan in einer Kopie, alle anderen mit dem Befehl aus devflow.toml."""
    return _pflege().test_befehl(p, aufgabe_id) or p.cfg['test']


def testen(p, aufgabe_id, automatisch=False):
    _bereit_zum_start(p, aufgabe_id)
    befehl = _testbefehl(p, aufgabe_id)
    if not befehl:
        raise Fehler(f'Für {p.name} ist kein Testbefehl in devflow.toml hinterlegt')
    z = zweig(p, aufgabe_id)
    l = laeufe.starten(p, 'test', 'shell', z['worktree'], befehl, aufgabe=aufgabe_id, branch=z['branch'],
                       worktree=z['worktree'], testlauf=True, automatisch=automatisch)
    return kurz(l)


def _reviews(p, aufgabe_id):
    return [l for l in laeufe.alle(p.name, aufgabe_id) if l['art'] == 'review' and l.get('urteil')]


def _pruefstand(p, aufgabe_id, kopf):
    """Die Vorbereitung, auf der ein Review von `kopf` aufsetzen darf: die jüngste der Aufgabe, ganz fertig, ohne Konflikt, Tests grün, genau
    dieser Kopf, und `main` ist seit ihrem Basiscommit höchstens um Statuscommits weiter. Eine abgebrochene oder noch laufende jüngere
    Vorbereitung macht jede ältere ungültig: Kein alter Prüfstand gilt für einen neuen Stand."""
    l = next((x for x in reversed(laeufe.alle(p.name, aufgabe_id)) if x['art'] == 'vorbereiten'), None)
    if (l and l.get('arbeit_fertig') and l.get('kopf') == kopf and l.get('konflikt') is None and not l.get('test_rc')
            and not repo.fachliche_commits(p, l['basis'], p.cfg['basis'])):
        return l
    return None


def vorbereiten(p, aufgabe_id, automatisch=True, reviewer=None, vorgaben_geaendert=None):
    """Gemeinsamer Schritt vor jedem Review (interner Lauf `vorbereiten`): Rebase auf einen festgehaltenen Basiscommit, falls `main` fachlich
    weiter ist, dann Tests; danach startet das Review. Konflikt oder rote Tests führen bei `automatisch` zur Nacharbeit."""
    _bereit_zum_start(p, aufgabe_id)
    z = zweig(p, aufgabe_id)
    _stand(p, aufgabe_id, stand=f'Vorbereitung vor dem Review: Rebase auf {p.cfg["basis"]}, Tests')
    l = laeufe.starten(p, 'vorbereiten', 'intern', z['worktree'], aufgabe=aufgabe_id, branch=z['branch'], worktree=z['worktree'],
                       entwickler=z['entwickler'], automatisch=automatisch, reviewer=reviewer, vorgaben_geaendert=vorgaben_geaendert)
    return kurz(l)


def review_anfordern(p, aufgabe_id, reviewer=None, automatisch=True, vorgaben_geaendert=None, vorbereitung=None):
    """Review genau des vorbereiteten Stands. Fehlt die passende Vorbereitung (`_pruefstand`), startet erst sie; das Review folgt dann
    von selbst. `vorbereitung`: die Vorbereitung, aus deren Abschluss der Aufruf kommt."""
    _bereit_zum_start(p, aufgabe_id)
    k, z = kontext(p, aufgabe_id), zweig(p, aufgabe_id)
    wt = z['worktree']
    repo.rebase_abbrechen(wt)  # kein Review eines halben Rebase
    if git(wt, 'status', '--porcelain'):
        raise Fehler(f'Worktree {wt} ist nicht sauber; erst committen lassen (nacharbeit)')
    commit = git(wt, 'rev-parse', 'HEAD')
    basis_commit = git(p.repo, 'merge-base', p.cfg['basis'], commit)
    if commit == basis_commit:
        raise Fehler(f'Branch {z["branch"]} hat keine eigenen Commits')
    rev = (reviewer or _reviewer(p, z['entwickler'])).lower()
    if rev not in NAMEN:
        raise Fehler(f'Reviewer {rev!r} unbekannt')
    if rev == z['entwickler']:
        raise Fehler(f'{NAMEN[rev]} hat {aufgabe_id} entwickelt; das Review macht das jeweils andere Modell')
    stand = _pruefstand(p, aufgabe_id, commit)
    if not stand:
        if vorbereitung and vorbereitung.get('kopf') != commit:
            raise Fehler(f'Branch {z["branch"]} hat sich nach der Vorbereitung geändert; neues review_anfordern()')
        return vorbereiten(p, aufgabe_id, automatisch, reviewer, vorgaben_geaendert)
    alt = _reviews(p, aufgabe_id)
    runde = len(alt) + 1
    rwt = p.worktree(f'review-{projekte.slug(aufgabe_id)}-{runde}')
    _weg(rwt)
    git(p.repo, 'worktree', 'add', '-q', '--detach', str(rwt), commit)
    vorher = alt[-1]['befunde'] if alt and alt[-1]['urteil'] == 'nacharbeit' else []
    k.update(branch=z['branch'], cwd=str(rwt), commit=commit, basis_commit=basis_commit,
             vorher_commit=alt[-1].get('commit', basis_commit) if alt else basis_commit, vorgaben_geaendert=vorgaben_geaendert)
    _mit_grundlage(p, k, z['zusatzauftrag'])
    if _pflege().ist_pflege(aufgabe_id) or _vorschlag().ist_vorschlag(aufgabe_id):
        k['auftrag'] = z['zusatzauftrag']  # der Umfang steht bei Pflegeaufgaben und Vorschlägen nicht in einem Plan
    try:
        _stand(p, aufgabe_id, 'Review', f'Review {NAMEN[rev]} Runde {runde} läuft')
        l = laeufe.starten(p, 'review', rev, rwt, auftraege.review(k, runde, vorher, NAMEN[rev]), aufgabe=aufgabe_id,
                           branch=z['branch'], worktree=wt, review_worktree=str(rwt), commit=commit, runde=runde,
                           basis_commit=stand['basis'], test_commit=stand.get('getestet'), vorbereitung=stand['id'],
                           reviewer=rev, entwickler=z['entwickler'], schema=auftraege.REVIEW_SCHEMA,
                           automatisch=automatisch, **_stand_von(k))
    except Exception:
        _weg(rwt)
        raise
    return kurz(l)


def _nacharbeit_starten(p, aufgabe_id, grund, details, automatisch, zaehlt=True, **extra):
    _bereit_zum_start(p, aufgabe_id)
    k, z = kontext(p, aufgabe_id), zweig(p, aufgabe_id)
    repo.rebase_abbrechen(z['worktree'])  # kein Modelllauf in einem halben Rebase
    k.update(branch=z['branch'], worktree=z['worktree'])
    _mit_grundlage(p, k, z['zusatzauftrag'])
    # Zählende Runden (Review, rote Tests, Hinweise) starten frisch: die alte Sitzung trägt den ganzen Verlauf
    # und macht jeden Schritt teuer. Kurzes Nachhaken (nicht committet, ROADMAP) setzt die Sitzung fort.
    sitzung = None if zaehlt else z['session']
    if not sitzung:
        # Was die alte Sitzung wusste und nicht im Repo steht: Zusatzauftrag, frühere Hinweise, Fragen mit Antworten.
        fragen = [f['text'] for f in roadmap.fragen(p.roadmap.read_text(encoding='utf-8'), nur_offen=False)
                  if f['bezug'] == aufgabe_id]
        k.update(auftrag=z['zusatzauftrag'], vorgaben=z['hinweise'] + fragen)
    prompt = auftraege.nacharbeit(k, grund, details, frisch=not sitzung)
    _stand(p, aufgabe_id, 'in Arbeit', 'Nacharbeit läuft')
    l = laeufe.starten(p, 'nacharbeit', z['entwickler'], z['worktree'], prompt, aufgabe=aufgabe_id, branch=z['branch'],
                       worktree=z['worktree'], entwickler=z['entwickler'], session=sitzung,
                       automatisch=automatisch, zaehlt=zaehlt, **_stand_von(k), **extra)
    return kurz(l)


def nacharbeit(p, aufgabe_id, hinweise='', automatisch=True):
    return _nacharbeit_starten(p, aufgabe_id, 'Hinweise der steuernden KI:', hinweise, automatisch, hinweise=hinweise)


def _merge_pruefen(p, branch, entwickler, aufgabe_id, gesperrt=False):
    """Bestandenes Gegenreview auf genau dem Integrationsstand, der gemergt würde. Durch diese Prüfung muss jeder Merge, auch ein
    fortgesetzter: Deshalb steht hier die Sperre für Traumvorschläge.

    Der Branch steht auf dem Kopf des Reviews (reviewter Commit plus devflows Reviewbericht) oder unterscheidet sich davon nur in ROADMAP.md
    (devflows Rebase über Statuscommits). Seit dem Basiscommit des Reviews darf `main` nur Statuscommits von devflow bekommen haben
    (`repo.statuscommit`: Autor und Pfade); sonst, und bei einem Review ohne Vorbereitung oder ungetestetem Stand, `NeuPruefen`."""
    r = _reviews(p, aufgabe_id)
    _vorschlag().merge_tor(p, branch, aufgabe_id, gesperrt)
    if not r or r[-1]['urteil'] != 'bestanden':
        raise Fehler(f'Für Aufgabe {aufgabe_id} liegt kein bestandenes Review vor')
    r, name = r[-1], p.cfg['basis']
    if r['reviewer'] == entwickler:
        raise Fehler(f'Das letzte Review von {aufgabe_id} ist ein Selbstreview; nötig ist das andere Modell')
    kopf = git(p.repo, 'rev-parse', f'refs/heads/{branch}')
    if kopf != r.get('kopf') and not (r.get('kopf') and repo.nur_status_dazu(p, r['kopf'], kopf)):
        raise Fehler(f'Branch {branch} hat sich nach dem bestandenen Review geändert; neues Review nötig')
    if set(repo.STATUSPFADE) & set(git(p.repo, 'diff', '--name-only', f'{name}...{kopf}').split('\n')):
        raise Fehler(f'Branch {branch} ändert ROADMAP.md; den Status pflegt nur devflow')
    _vorgaben_tor(p, r)
    if not r.get('basis_commit'):
        raise NeuPruefen('Das Review lief ohne Vorbereitung (Rebase, Tests)')
    if r.get('test_commit') != r['commit'] and _testbefehl(p, aufgabe_id):
        raise NeuPruefen('Der reviewte Stand ist nicht getestet')
    fach = repo.fachliche_commits(p, r['basis_commit'], name)
    if fach:
        raise NeuPruefen(f'{name} hat sich seit dem Review fachlich geändert ({len(fach)} Commits, zuletzt `{fach[-1][:7]}`)')
    return r


def _vorgaben_tor(p, review):
    """Hat sich der bestätigte Stand (Masterplan, bestätigte Entscheidungen) seit dem Beginn des Reviews geändert? Dann muss die Auswirkung vor dem
    Merge geprüft werden. Reviews ohne gemerkten Stand (vor S9.1) gelten als unverändert."""
    alt = review.get('grundlage_stand')
    if alt is None:
        return
    sachen = grundlage.unterschied(alt, grundlage.stand(p))
    if sachen:
        raise grundlage.VorgabenGeaendert(sachen)


def _masterplan_tor(p, aufgabe_id, branch, gesperrt=False):
    """Ein Branch darf Masterplan und bestätigte Entscheidungen nicht ändern; das tut nur der Inhaber. Statt eines Merges kommt eine Frage."""
    sperren = grundlage.gesperrte_aenderungen(p, branch)
    if sperren:
        frage, optionen, empfehlung = grundlage.gesperrt_frage(aufgabe_id, sperren)
        nr = frage_stellen(p, frage, optionen, empfehlung, aufgabe_id, gesperrt=gesperrt)['frage']
        raise FrageOffen(f'{aufgabe_id} ändert Vorgaben, die nur {_i.nom()} ändert ({nr}); kein Merge')


def _pflege_tor(p, aufgabe_id, branch, gesperrt=False):
    """Pflegeaufgaben: tatsächlicher Diff gegen den Umfang des Pflegeplans. Weicht er ab, kommt eine Frage statt eines Merges."""
    gruende = _pflege().abweichungen(p, aufgabe_id, branch)
    if gruende:
        frage, optionen, empfehlung = _pflege().merge_frage(aufgabe_id, gruende)
        nr = frage_stellen(p, frage, optionen, empfehlung, aufgabe_id, gesperrt=gesperrt)['frage']
        raise FrageOffen(f'{aufgabe_id} weicht vom Pflegeplan ab ({nr}); kein Merge')


def mergen(p, aufgabe_id, automatisch=True):
    _bereit_zum_start(p, aufgabe_id)
    offen = [l for l in laeufe.alle(p.name, aufgabe_id) if l['art'] == 'mergen' and l.get('merge') and l['zustand'] == 'fehler']
    if offen:
        raise Fehler(f'{aufgabe_id} ist schon gemergt (`{offen[-1]["merge"][:7]}`), der Abschluss fehlt: fortsetzen("{offen[-1]["id"]}")')
    z = zweig(p, aufgabe_id)
    try:
        _merge_pruefen(p, z['branch'], z['entwickler'], aufgabe_id)
    except grundlage.VorgabenGeaendert as e:
        if not automatisch:
            raise Fehler(f'Vorgaben haben sich seit dem Review geändert ({e}); vor dem Merge prüfen: review_anfordern("{aufgabe_id}")') from None
        protokoll(p, f'devflow · {aufgabe_id}: Vorgaben seit dem Review geändert ({e}); Prüfung vor dem Merge')
        return review_anfordern(p, aufgabe_id, automatisch=True, vorgaben_geaendert=e.sachen)
    except NeuPruefen as e:
        _masterplan_tor(p, aufgabe_id, z['branch'])  # ein Tor, das ohnehin sperrt, kostet kein neues Review
        _pflege_tor(p, aufgabe_id, z['branch'])
        if not automatisch:
            raise Fehler(f'{e}; vor dem Merge neu prüfen: review_anfordern("{aufgabe_id}")') from None
        protokoll(p, f'devflow · {aufgabe_id}: {e}; zurück zu Vorbereitung, Tests und Review')
        return review_anfordern(p, aufgabe_id, automatisch=True)
    _masterplan_tor(p, aufgabe_id, z['branch'])
    _pflege_tor(p, aufgabe_id, z['branch'])
    l = laeufe.starten(p, 'mergen', 'intern', p.repo, aufgabe=aufgabe_id, branch=z['branch'], worktree=z['worktree'],
                       entwickler=z['entwickler'], automatisch=automatisch)
    return kurz(l)


def deployen(p, aufgabe_id=None):
    _bereit_zum_start(p)
    _deploy_gesperrt(p, aufgabe_id)
    if not p.cfg['deploy']:
        raise Fehler(f'Für {p.name} ist kein Deploybefehl in devflow.toml hinterlegt')
    return kurz(laeufe.starten(p, 'deploy', 'shell', p.repo, p.cfg['deploy'], aufgabe=aufgabe_id))


def meinung_anfordern(p, datei, frage='', reviewer='sol', **extra):
    _bereit_zum_start(p)
    reviewer = reviewer.lower()
    if reviewer not in NAMEN:
        raise Fehler(f'Reviewer {reviewer!r} unbekannt')
    if not git(p.repo, 'ls-files', '--', datei) or git(p.repo, 'status', '--porcelain', '--', datei):
        raise Fehler(f'{datei} ist nicht (sauber) committet; die Zweitmeinung prüft den committeten Stand')
    stempel = datetime.datetime.now().strftime('%H%M%S')
    rwt = p.worktree(f'meinung-{projekte.slug(Path(datei).stem)}-{stempel}')
    commit = git(p.repo, 'rev-parse', 'HEAD')
    g = grundlage.fuer(p, (p.repo / datei).read_text(encoding='utf-8'))
    git(p.repo, 'worktree', 'add', '-q', '--detach', str(rwt), commit)
    try:
        l = laeufe.starten(p, 'meinung', reviewer, rwt, auftraege.meinung(p.name, datei, frage, NAMEN[reviewer], g, p.cfg['basis']),
                           review_worktree=str(rwt), datei=datei, reviewer=reviewer, commit=commit, grundlage_stand=g['stand'], **extra)
    except Exception:
        _weg(rwt)
        raise
    return kurz(l)


def _halt_protokoll(name, was, herkunft):
    """Halt ins Protokoll. Die Notbremse wirkt auch, wenn das scheitert (z. B. fremde Änderung an ROADMAP.md)."""
    try:
        protokoll(Projekt(name), f'{was} · Herkunft {herkunft}')
    except Fehler as err:
        print(f'devflow2: Halt nicht im Protokoll von {name}: {err}', file=sys.stderr)


def anhalten(ziel, herkunft='steuerung'):
    roadmap.herkunft_pruefen(herkunft)
    if ziel == 'alles':
        schreiben(projekte.zustand() / 'angehalten', f'{datum()} · {herkunft}\n')
        _traum().halt()
        gestoppt = [laeufe.anhalten(l['id'], herkunft) for l in laeufe.alle() if l['zustand'] in laeufe.AKTIV]
        for name in dict.fromkeys(l['projekt'] for l in gestoppt):
            _halt_protokoll(name, 'Alles angehalten', herkunft)
        return [kurz(l) for l in gestoppt]
    vorher = laeufe.laden(ziel)['zustand']
    l = laeufe.anhalten(ziel, herkunft)
    if vorher in laeufe.AKTIV and l['zustand'] == 'angehalten':
        _halt_protokoll(l['projekt'], f'Lauf {ziel} angehalten', herkunft)
    return kurz(l)


def fortsetzen(ziel, herkunft='steuerung'):
    """„alles“ hebt den Halt auf, nur aus dem Dashboard. Eine Lauf-ID setzt den Lauf fort, wenn kein Dashboard-Halt und keine
    offene Frage dagegensteht; fehlt seine Review-Kopie, wird sie neu angelegt."""
    roadmap.herkunft_pruefen(herkunft)
    if ziel == 'alles':
        if herkunft != 'dashboard':
            raise Fehler(halt_nur_dashboard())
        (projekte.zustand() / 'angehalten').unlink(missing_ok=True)
        return {'angehaltene_laeufe': [l['id'] for l in laeufe.alle() if l['zustand'] == 'angehalten']}
    if laeufe.angehalten():
        raise Fehler(f'devflow ist angehalten; {halt_nur_dashboard()}')
    l = laeufe.laden(ziel)
    if l.get('traum'):
        raise Fehler(laeufe.TRAUM_FORT)
    if l.get('halt_herkunft') == 'dashboard' and herkunft != 'dashboard':
        raise Fehler(f'Lauf {ziel} wurde im Dashboard angehalten; fortsetzen lässt er sich nur dort')
    if l['art'] == 'steuern' and any(x['art'] == 'steuern' and x['id'] != ziel and x['zustand'] in laeufe.AKTIV for x in laeufe.alle()):
        raise Fehler('Es läuft schon ein Steuerlauf; einer zur Zeit')
    p = Projekt(l['projekt'])
    if l['art'] == 'deploy':
        _deploy_gesperrt(p, l.get('aufgabe'))
    elif l.get('aufgabe'):
        _gesperrt(offene_fragen(p, l['aufgabe']))
    cwd = Path(l['cwd'])
    if l['art'] in ('entwickeln', 'nacharbeit') and cwd.exists() and not l.get('arbeit_fertig') and repo.rebase_abbrechen(cwd):
        protokoll(p, f'devflow · {l["aufgabe"]}: halben Rebase in {cwd.name} vor dem Fortsetzen abgebrochen')  # kein Modelllauf darin
    if not cwd.exists() and not l.get('arbeit_fertig'):
        if l['art'] not in ('review', 'meinung'):
            raise Fehler(f'Arbeitsverzeichnis {cwd} fehlt; Lauf {ziel} kann nicht fortgesetzt werden')
        if l['art'] == 'review' and git(p.repo, 'rev-parse', '-q', '--verify', f'refs/heads/{l["branch"]}', check=False) != l['commit']:
            raise Fehler(f'Branch {l["branch"]} steht nicht mehr auf dem geprüften Commit; neues review_anfordern() statt fortsetzen')
        git(p.repo, 'worktree', 'add', '-q', '--detach', str(cwd), l['commit'])
    neu = laeufe.fortsetzen(ziel)
    if l.get('login') and l.get('aufgabe'):
        _login_erledigt(p, l['aufgabe'], ziel)
    return kurz(neu)


def _login_erledigt(p, aufgabe_id, lauf_id):
    """Der Eintrag „Braucht dich“ wegen abgelaufener Anmeldung verschwindet mit dem Fortsetzen (nur, wenn er von diesem Lauf stammt)."""
    _, z = roadmap.finde(p.roadmap.read_text(encoding='utf-8'), aufgabe_id)
    if z[3] == 'Braucht dich' and f'(Lauf {lauf_id})' in z[5] and not offene_fragen(p, aufgabe_id):
        _stand(p, aufgabe_id, 'in Arbeit', f'Lauf {lauf_id} nach erneuter Anmeldung fortgesetzt')


# --- Abschluss eines Laufs (im Laufprozess) ------------------------------------------------------

def _weg(pfad):
    """Losgelöste Review-/Meinungskopie entfernen; Proben darin sind Wegwerfdateien, daher --force."""
    if not pfad or not Path(pfad).exists() or not re.search(r'-(review|meinung)-[^/]+$', str(pfad)):
        return
    wurzel = git(pfad, 'rev-parse', '--path-format=absolute', '--git-common-dir', check=False)
    if wurzel:
        ok(Path(wurzel).parent, 'worktree', 'remove', '--force', str(pfad))
    if Path(pfad).exists():
        shutil.rmtree(pfad, ignore_errors=True)


def fehlgeschlagen(lauf):
    """Fehler sichtbar machen. Die Review-Kopie bleibt, damit fortsetzen() dort weitermachen kann."""
    if not lauf.get('aufgabe') or lauf.get('traum'):
        return
    fort = f'fortsetzen("{lauf["id"]}")'
    if lauf.get('login'):
        _stand(Projekt(lauf['projekt']), lauf['aufgabe'], 'Braucht dich', laeufe.login_hinweis(lauf)[:300])
        return
    if lauf.get('merge'):
        text = f'gemergt `{lauf["merge"][:7]}`, Abschluss offen: {fort}. {lauf.get("fehler") or ""}'
    else:
        text = f'Lauf {lauf["id"]} fehlgeschlagen: {(lauf.get("fehler") or "")[:200]}'
    p = Projekt(lauf['projekt'])
    wartet = offene_fragen(p, lauf['aufgabe']) or _vorschlag().wartet(lauf['aufgabe'])
    _stand(p, lauf['aufgabe'], 'Braucht dich' if wartet else 'blockiert', text[:300])


def abschluss(lauf):
    global _EIGEN
    p = Projekt(lauf['projekt'])
    _EIGEN = lauf['id']
    try:
        if lauf.get('vorschlag'):
            _vorschlag().abschluss(p, lauf)
            return
        {'entwickeln': _nach_entwicklung, 'nacharbeit': _nach_entwicklung, 'test': _nach_test, 'vorbereiten': _nach_vorbereitung, 'review': _nach_review,
         'meinung': _nach_meinung, 'mergen': _nach_merge, 'deploy': _nach_deploy, 'steuern': _nach_steuern,
         'pflege': lambda p, lauf: None, 'messung': lambda p, lauf: None, 'traum': lambda p, lauf: None}[lauf['art']](p, lauf)
    except FrageOffen as e:
        protokoll(p, f'devflow · {lauf.get("aufgabe") or p.name}: Kette wartet. {e}')
    finally:
        _EIGEN = None


def _runde_oder_frage(p, aufgabe_id, grund, details):
    runden = sum(1 for l in laeufe.alle(p.name, aufgabe_id) if l['art'] == 'nacharbeit' and l.get('zaehlt'))
    if runden >= MAX_RUNDEN:
        frage_stellen(p, f'Aufgabe {aufgabe_id} ist nach {runden} Nacharbeitsrunden noch nicht fertig. {grund}\n\n{details[:1500]}',
                      ['Weitere Runde mit konkreten Hinweisen', 'Aufgabe zurückstellen', 'Selbst ansehen und entscheiden'],
                      'Bericht und Befunde ansehen; bei klarer Richtung eine weitere Runde mit Hinweisen.', aufgabe_id)
    else:
        _nacharbeit_starten(p, aufgabe_id, grund, details, automatisch=True)


def _nach_entwicklung(p, lauf):
    aid, wt, ergebnis = lauf['aufgabe'], lauf['worktree'], laeufe.ergebnis(lauf)
    marker = next((m for m in _i.frage_marker_alle() if m in ergebnis), None)
    if marker:
        frage_stellen(p, ergebnis.split(marker, 1)[1].strip(), [], '(siehe Frage)', aid)
        return
    if repo.rebase_abbrechen(wt):
        if not lauf.get('nachgehakt'):
            _nacharbeit_starten(p, aid, 'Dein Rebase war nicht abgeschlossen; devflow hat ihn abgebrochen, der Branch steht wieder auf dem Stand davor.',
                                f'Rebase erneut auf {p.cfg["basis"]}, schließe ihn mit `git rebase --continue` ab und committe; lass keinen halben Rebase zurück.',
                                lauf.get('automatisch'), zaehlt=False, nachgehakt=True)
        else:
            _stand(p, aid, 'blockiert', f'Halber Rebase nach Lauf {lauf["id"]}')
        return
    offen = git(wt, 'status', '--porcelain')
    if offen:
        if not lauf.get('nachgehakt'):
            _nacharbeit_starten(p, aid, 'Im Worktree sind Änderungen nicht committet:', offen, lauf.get('automatisch'),
                                zaehlt=False, nachgehakt=True)
        else:
            _stand(p, aid, 'blockiert', f'Worktree nach Lauf {lauf["id"]} nicht sauber')
        return
    if 'ROADMAP.md' in git(wt, 'diff', '--name-only', f'{p.cfg["basis"]}...HEAD').split('\n'):
        if not lauf.get('nachgehakt'):
            _nacharbeit_starten(p, aid, 'Der Branch ändert ROADMAP.md; den Status pflegt nur devflow.',
                                f'Nimm die Änderung zurück (`git checkout $(git merge-base {p.cfg["basis"]} HEAD) -- ROADMAP.md`) und committe.',
                                lauf.get('automatisch'), zaehlt=False, nachgehakt=True)
        else:
            _stand(p, aid, 'blockiert', f'Branch ändert ROADMAP.md (Lauf {lauf["id"]})')
        return
    vorne = int(git(wt, 'rev-list', '--count', f'{p.cfg["basis"]}..HEAD'))
    if not vorne:
        _stand(p, aid, 'blockiert', f'Lauf {lauf["id"]} endete ohne Commit')
        return
    _stand(p, aid, stand=f'Branch `{lauf["branch"]}`, Entwicklung fertig ({vorne} Commits)')
    if lauf.get('automatisch'):
        review_anfordern(p, aid, automatisch=True)  # erst die Vorbereitung (Rebase, Tests), dann das Review


def _nach_test(p, lauf):
    ok = lauf.get('rc') == 0
    _stand(p, lauf['aufgabe'], stand=f'Tests {"grün" if ok else "rot"} (Lauf {lauf["id"]})')
    if not lauf.get('automatisch'):
        return
    if ok:
        review_anfordern(p, lauf['aufgabe'], automatisch=True)
    else:
        _runde_oder_frage(p, lauf['aufgabe'], 'Die Projekttests schlagen fehl.',
                          f'Befehl: `{_testbefehl(p, lauf["aufgabe"])}`\nLetzte Ausgabe:\n{(lauf.get("ergebnis") or "")[-3000:]}')


def _nach_vorbereitung(p, lauf):
    """Konflikt oder rote Tests → Nacharbeit (bei `automatisch`), sonst das Review genau dieses Stands, auch ohne `automatisch`: Darum ging es."""
    aid, name = lauf['aufgabe'], p.cfg['basis']
    if lauf.get('konflikt') is not None:
        pfade = lauf['konflikt']
        _stand(p, aid, stand=f'Rebase auf {name} `{lauf["ziel"][:7]}` mit Konflikten: {", ".join(pfade)}'[:200])
        if lauf.get('automatisch'):
            _runde_oder_frage(p, aid, f'Der Rebase deines Branches auf {name} (`{lauf["ziel"][:7]}`) hat Konflikte. devflow hat ihn abgebrochen; '
                              f'der Branch steht unverändert auf `{lauf["ausgang"][:7]}`.',
                              'Konfliktpfade:\n' + '\n'.join(f'- {x}' for x in pfade)
                              + f'\n\nRebase selbst auf {name} (`git rebase {name}`), löse die Konflikte, schließe mit `git rebase --continue` ab '
                              'und lass keinen halben Rebase zurück. ROADMAP.md bleibt unverändert.')
        return
    if lauf.get('test_rc'):
        _stand(p, aid, stand=f'Tests rot nach der Vorbereitung (Lauf {lauf["id"]})')
        if lauf.get('automatisch'):
            _runde_oder_frage(p, aid, 'Die Projekttests schlagen fehl.',
                              f'Befehl: `{_testbefehl(p, aid)}`\nLetzte Ausgabe:\n{(lauf.get("ergebnis") or "")[-3000:]}')
        return
    review_anfordern(p, aid, reviewer=lauf.get('reviewer'), automatisch=lauf.get('automatisch'),
                     vorgaben_geaendert=lauf.get('vorgaben_geaendert'), vorbereitung=lauf)


def urteil(text):
    """Urteil aus der letzten Antwort des Reviewers; „bestanden“ mit P1/P2-Befund gilt als Nacharbeit."""
    kandidaten = [text.strip()]
    m = re.search(r'\{.*\}', text, re.S)
    if m:
        kandidaten.append(m.group(0))
    for k in kandidaten:
        try:
            d = json.loads(k)
        except ValueError:
            continue
        if (isinstance(d, dict) and d.get('urteil') in ('bestanden', 'nacharbeit') and isinstance(d.get('befunde'), list)
                and isinstance(d.get('bericht'), str) and all(isinstance(b, dict) for b in d['befunde'])):
            befunde = [{f: str(b.get(f, '')) for f in ('nr', 'schwere', 'ort', 'was')} for b in d['befunde']]
            schwer = any(b['schwere'].upper().startswith(('P1', 'P2')) for b in befunde)
            k = d.get('kurz')
            return dict(urteil='nacharbeit' if schwer else d['urteil'], befunde=befunde, bericht=d['bericht'],
                        kurz='\n'.join(' '.join(z.split()) for z in k.splitlines() if z.strip()) if isinstance(k, str) else '')
    raise Fehler('Review ohne gültiges Urteil (JSON mit urteil, befunde, bericht erwartet)')


def review_sichern(lauf):
    """Bericht auf den Branch committen (einmalig, `kopf` merkt den Stand), Kopie entfernen. Rückgabe: Urteil, Berichtsdatei."""
    aid, wt, rev = lauf['aufgabe'], lauf['worktree'], lauf['reviewer']
    u = urteil(laeufe.ergebnis(lauf))
    name = {1: 'review', 2: 'nachreview'}.get(lauf['runde'], f'nachreview-{lauf["runde"] - 1}')
    datei = f'berichte/{aid}-{name}-{rev}.md'
    if not lauf.get('kopf'):
        if git(wt, 'rev-parse', 'HEAD') != lauf['commit'] or git(wt, 'status', '--porcelain'):
            raise Fehler(f'Branch {lauf["branch"]} hat sich während des Reviews geändert; neues review_anfordern()')
        schreiben(Path(wt) / datei, u['bericht'].rstrip() + '\n')
        git(wt, 'add', '--', datei)
        git(wt, 'commit', '-q', '-m', f'docs: {aid} Review {NAMEN[rev]} Runde {lauf["runde"]}: {u["urteil"]}')
        lauf.update(urteil=u['urteil'], befunde=u['befunde'], kurz=u['kurz'], kopf=git(wt, 'rev-parse', 'HEAD'), bericht_datei=datei)
        laeufe.speichern(lauf)
    _weg(lauf['review_worktree'])
    return u, datei


def _nach_review(p, lauf):
    """Review sichern, nächsten Schritt starten."""
    aid, rev = lauf['aufgabe'], lauf['reviewer']
    u, datei = review_sichern(lauf)
    if u['urteil'] == 'bestanden':
        _stand(p, aid, stand=f'Review {NAMEN[rev]} Runde {lauf["runde"]} bestanden ({datei})'
               + ('' if lauf.get('automatisch') else ', Merge offen'))
        if lauf.get('automatisch'):
            mergen(p, aid, automatisch=True)
        return
    befunde = '\n'.join(f'- {b["nr"]} ({b["schwere"]}) {b["ort"]}: {b["was"]}' for b in u['befunde'])
    _stand(p, aid, stand=f'Review {NAMEN[rev]} Runde {lauf["runde"]}: Nacharbeit, {len(u["befunde"])} Befunde ({datei})')
    if lauf.get('automatisch'):
        _runde_oder_frage(p, aid, f'Das Review von {NAMEN[rev]} verlangt Nacharbeit (Bericht {datei} auf deinem Branch).', befunde)


def _schritt(log, text):
    log.write(json.dumps({'type': 'schritt', 'text': text}, ensure_ascii=False) + '\n')
    log.flush()


def _vorbereiten_intern(p, lauf, log, merken):
    """Rebase und Tests vor dem Review; jeder Schritt steht sofort am Lauf. Eine Wiederholung (fortsetzen) beginnt von vorn: Rest eines
    Rebase abbrechen, Basis neu festhalten, Tests neu. `ausgang`/`ziel`/`konflikt` beschreiben einen abgebrochenen Rebase, `rebases` alle
    gelungenen (Herkunft für die Vorschlagssperre), `kopf` den vorbereiteten und `getestet` den getesteten Commit."""
    aid, wt, name = lauf['aufgabe'], lauf['worktree'], p.cfg['basis']
    if repo.rebase_abbrechen(wt):
        _schritt(log, 'Rest eines unterbrochenen Rebase abgebrochen')
    if git(wt, 'status', '--porcelain'):
        raise Fehler(f'Worktree {wt} ist nicht sauber; erst committen lassen (nacharbeit)')
    if git(wt, 'symbolic-ref', '-q', 'HEAD', check=False) != f'refs/heads/{lauf["branch"]}':
        raise Fehler(f'Worktree {wt} steht nicht auf {lauf["branch"]}')
    ausgang, basis = git(wt, 'rev-parse', 'HEAD'), git(p.repo, 'rev-parse', name)
    merken(basis=basis, ausgang=ausgang, ziel=None, konflikt=None, kopf=None, getestet=None, test_rc=None, ergebnis=None)
    fach = repo.fachliche_commits(p, ausgang, basis)
    if fach:
        _schritt(log, f'{name} ist fachlich weiter ({len(fach)} Commits); Rebase auf {basis[:7]}')
        konflikt = repo.rebase(wt, basis)
        if konflikt is not None:
            merken(ziel=basis, konflikt=konflikt)
            _schritt(log, f'Rebase mit Konflikten abgebrochen: {", ".join(konflikt)}')
            return
        neu = git(wt, 'rev-parse', 'HEAD')
        merken(ziel=basis, rebase_kopf=neu, rebases=(lauf.get('rebases') or []) + [dict(ausgang=ausgang, ziel=basis, kopf=neu)])
    else:
        _schritt(log, f'{name} ist höchstens um Statuscommits weiter; kein Rebase')
    kopf = git(wt, 'rev-parse', 'HEAD')
    merken(kopf=kopf)
    befehl = _testbefehl(p, aid)
    if not befehl:
        return
    _schritt(log, f'Tests auf {kopf[:7]}')
    datei = laeufe.verzeichnis(lauf['id']) / 'tests.log'
    with open(datei, 'wb') as aus:
        rc = laeufe.befehl_ausfuehren(lauf, laeufe.befehl({**lauf, 'modell': 'shell', 'testlauf': True}, befehl), aus, aus, cwd=wt)
    if git(wt, 'rev-parse', 'HEAD') != kopf:
        raise Fehler(f'Branch {lauf["branch"]} hat sich während der Tests geändert')
    merken(getestet=kopf, test_rc=rc, ergebnis=datei.read_text(encoding='utf-8', errors='replace')[-6000:])
    _schritt(log, f'Tests {"grün" if rc == 0 else "rot"}')


def intern(lauf, log):
    """Interne Schritte: `vorbereiten` (Rebase, Tests vor dem Review), Pflegeprüfung und Merge in wiederholbaren Schritten; jeder Schritt
    gilt erst mit Nachweis im Git-Stand als erledigt.

    Merge:
    1. Alle Tore neu, auch beim Fortsetzen (offene Fragen, `_merge_pruefen` mit Vorschlagssperre, Masterplan, Pflegeumfang). Ist `main`
       seit dem Review fachlich weiter oder sind die Vorgaben geändert, endet der Lauf ohne Merge (`neu_pruefen`), und die Kette geht zurück zu `vorbereiten`.
    2. Sonst Rebase über die Statuscommits (`rebase_kopf`, `rebases`), Prüfung des neuen Stands, `--ff-only`; der Merge-SHA wird sofort gesichert.
    3. Status `erledigt` – geprüft am committeten ROADMAP.md, nicht am Arbeitsverzeichnis.
    4. Worktree und Branch entfernen (ohne Force; sonst Hinweis).
    5. Push; ein Fehler lässt den Lauf fehlschlagen, `fortsetzen` holt ihn nach.
    """
    if lauf['art'] == 'pflege':
        from . import pflege
        return pflege.ausfuehren(lauf, log)
    if lauf['art'] not in ('mergen', 'vorbereiten'):
        raise Fehler(f'Interner Schritt {lauf["art"]} unbekannt')
    p, aid = Projekt(lauf['projekt']), lauf['aufgabe']
    wt, basis, branch = lauf['worktree'], p.cfg['basis'], lauf['branch']

    def merken(**werte):
        lauf.update(werte)
        laeufe.speichern(lauf)

    if lauf['art'] == 'vorbereiten':
        return _vorbereiten_intern(p, lauf, log, merken)

    def pruefen():
        try:
            _merge_pruefen(p, branch, lauf['entwickler'], aid, gesperrt=True)
        except (NeuPruefen, grundlage.VorgabenGeaendert) as e:
            _masterplan_tor(p, aid, branch, gesperrt=True)  # ein Tor, das ohnehin sperrt, kostet kein neues Review
            _pflege_tor(p, aid, branch, gesperrt=True)
            if isinstance(e, grundlage.VorgabenGeaendert):  # wie in `mergen`: die Prüfung bekommt die geänderten Vorgaben mit
                merken(vorgaben_geaendert=e.sachen, neu_pruefen=f'Vorgaben seit dem Review geändert ({e})')
            else:
                merken(neu_pruefen=str(e))
            _schritt(log, f'{lauf["neu_pruefen"]}; kein Merge, zurück zu Vorbereitung, Tests und Review')
            return False
        return True

    with repo.sperre(p):
        if not lauf.get('merge'):
            _gesperrt(offene_fragen(p, aid))  # die Frage kann nach dem Start des Laufs gekommen sein
            if repo.rebase_abbrechen(wt):
                _schritt(log, 'Rest eines unterbrochenen Rebase abgebrochen')
            if not pruefen():
                return
            repo.main_bereit(p, ('ROADMAP.md',))  # Hindernis für den Statuscommit vor jeder Nebenwirkung erkennen
            if not ok(p.repo, 'merge-base', '--is-ancestor', basis, branch):
                ausgang, ziel = git(p.repo, 'rev-parse', f'refs/heads/{branch}'), git(p.repo, 'rev-parse', basis)
                konflikt = repo.rebase(wt, ziel)
                if konflikt is not None:
                    raise Fehler(f'Rebase von {branch} auf {basis} mit Konflikten in {", ".join(konflikt)}; Nacharbeit nötig')
                neu = git(wt, 'rev-parse', 'HEAD')
                merken(rebase_kopf=neu, rebases=(lauf.get('rebases') or []) + [dict(ausgang=ausgang, ziel=ziel, kopf=neu)])
                _schritt(log, f'Rebase über Statuscommits auf {ziel[:7]}')
                if not pruefen():  # der neue Stand: nur ROADMAP.md anders als der reviewte
                    return
            _masterplan_tor(p, aid, branch, gesperrt=True)  # auch nach dem Rebase: er macht aus einer Änderung am Masterplan keine erlaubte
            _pflege_tor(p, aid, branch, gesperrt=True)  # nach dem Rebase: geprüft wird genau der Stand, der gemergt wird
            git(p.repo, 'merge', '-q', '--ff-only', branch)
            merken(merge=git(p.repo, 'rev-parse', 'HEAD'))
            _schritt(log, f'{branch} gemergt: {lauf["merge"][:7]}')
        sha = lauf['merge']
        if roadmap.finde(repo.committet(p, 'ROADMAP.md'), aid)[1][3] != 'erledigt':
            eintrag = f'{aid} gemergt (`{sha[:7]}`)'

            def erledigt(t):
                neu = roadmap.setze(t, aid, tag(), 'erledigt', stand=f'gemergt `{sha[:7]}`')
                return neu if eintrag in t else roadmap.protokoll(neu, f'{datum()} · devflow · {eintrag}, Review bestanden')
            repo.roadmap_aendern(p, erledigt, f'roadmap: {aid} → erledigt', gesperrt=True)
    hinweise = []
    if Path(wt).exists() and not ok(p.repo, 'worktree', 'remove', wt):
        hinweise.append(f'Worktree {wt} nicht entfernt (nicht sauber); Branch {branch} bleibt')
    elif git(p.repo, 'rev-parse', '-q', '--verify', f'refs/heads/{branch}', check=False) and not ok(p.repo, 'branch', '-d', branch):
        hinweise.append(f'Branch {branch} nicht gelöscht')
    merken(hinweise=hinweise or None)
    for h in hinweise:
        _schritt(log, h)
    merken(push=repo.push(p))
    _schritt(log, lauf['push'])


def _nach_merge(p, lauf):
    if not lauf.get('merge') and lauf.get('neu_pruefen'):
        aid = lauf['aufgabe']
        protokoll(p, f'devflow · {aid}: {lauf["neu_pruefen"]}; kein Merge, zurück zu Vorbereitung, Tests und Review')
        if lauf.get('automatisch'):
            review_anfordern(p, aid, automatisch=True, vorgaben_geaendert=lauf.get('vorgaben_geaendert'))
        else:
            _stand(p, aid, stand=f'Kein Merge: {lauf["neu_pruefen"]}; neu prüfen mit review_anfordern'[:200])
        return
    _vorschlag().gemergt(lauf)
    if lauf.get('automatisch') and p.cfg['deploy']:
        grund = _pflege().deploy_grund(p, lauf['aufgabe'])
        if grund:
            frage_stellen(p, f'{lauf["aufgabe"]} ist gemergt, aber nicht deployt: {grund}', ['Deploy freigeben', 'Rückweg laut Pflegeplan'],
                          'Pflegeplan klären, dann entscheiden.', lauf['aufgabe'])
            return
        deployen(p, lauf['aufgabe'])


def _nach_deploy(p, lauf):
    ok = lauf.get('rc') == 0
    repo.roadmap_aendern(p, lambda t: roadmap.protokoll(
        t, f'{datum()} · devflow · Deploy {"ok" if ok else "FEHLGESCHLAGEN"} (Lauf {lauf["id"]})'), 'roadmap: Deploy')
    if not ok:
        frage_stellen(p, f'Deploy fehlgeschlagen (Lauf {lauf["id"]}). Letzte Ausgabe:\n{(lauf.get("ergebnis") or "")[-1500:]}',
                      ['Ursache von Claude prüfen und beheben lassen', 'Rückweg ausführen', 'Selbst ansehen'],
                      'Claude prüft die Ursache.', lauf.get('aufgabe'))


def _nach_steuern(p, lauf):
    """Nichts: Was der Steuerlauf tut, tut er über die Werkzeuge; neue Ereignisse holt `selbstlauf.pruefen` ab."""


def _nach_meinung(p, lauf):
    try:
        text = laeufe.ergebnis(lauf).strip()
        if not text:
            raise Fehler('Zweitmeinung ohne Text')
        ziel = f'berichte/{Path(lauf["datei"]).stem}-meinung-{lauf["reviewer"]}.md'
        repo.auf_main(p, lambda texte: {ziel: text + '\n'}, f'docs: Zweitmeinung {NAMEN[lauf["reviewer"]]} zu {lauf["datei"]}', (ziel,))
        lauf['bericht_datei'] = ziel
        laeufe.speichern(lauf)
    finally:
        _weg(lauf['review_worktree'])
