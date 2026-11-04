"""Traum-Vorschläge (S5.4): Änderungen aus „Code pflegen“ als eigener Branch, gemergt erst nach dem „Übernehmen“ des Inhabers.

Register: `vorschlaege/<id>.json` im Zustandsverzeichnis, geschrieben, bevor es den Branch gibt, und nie gelöscht. Die ID ist
`traum-<traum-id>-<thema>`, der Branch `traum/<traum-id>-<thema>`. Bis zur Übernahme steht ein Vorschlag nicht in der Roadmap; seine Läufe
tragen die ID trotzdem als `aufgabe`, damit `ablauf.zweig` und die Reviews ihn nach der Übernahme wie jede Aufgabe finden.

Zustände: `entsteht` (im Traum) → `offen` (wartet auf den Inhaber) → `uebernommen` → `gemergt`, sonst `verworfen` oder `gescheitert`.

Sperre: `merge_tor` steht in `ablauf._merge_pruefen`, durch das jeder Merge muss (Werkzeug, Folgeschritt, Fortsetzen); `deploy_sperre` steht in
`ablauf._deploy_gesperrt`. Beide sehen das Register, nicht den Auftrag oder einen Hinweis. Die Traumläufe melden keine Ereignisse.

Schreibziele: Der Baulauf hat nur Read, Grep, Glob, Edit und Write, keine Shell, kein Git und kein MCP, und schreiben darf er nur im eigenen
Worktree (`SCHREIBEND`, `MODUS`). Den Commit macht devflow, nachdem `_schreibziele` den Worktree und die Haupt-Checkouts geprüft hat.
"""
import json
from pathlib import Path
import re
import sys

from . import inhaber as _i
from . import Fehler, ablauf, auftraege, grundlage, laeufe, projekte, repo, roadmap, schreiben
from .projekte import NAMEN, Projekt
from .repo import git, ok, tag

PRAEFIX = 'traum/'
MAX_JE_PROJEKT = 3
ENTWICKLER = 'claude'
# `dontAsk` lehnt ab, was nicht erlaubt ist: Mit `Edit(./**)` schreibt der Lauf nur im eigenen Worktree. Im Modus `auto` ginge Write auch nach außen.
SCHREIBEND = ['--strict-mcp-config', '--tools', 'Read,Grep,Glob,Edit,Write', '--allowedTools', 'Read,Grep,Glob,Edit(./**)']
MODUS = 'dontAsk'
VERBOTEN = ('ROADMAP.md',)  # den Status pflegt nur devflow
BLOCK = re.compile(r'```json\s*(\{.*?\})\s*```', re.S)
def nicht_uebernommen(vorschlag_id):
    return f'Vorschlag {vorschlag_id} ist nicht übernommen; gesperrt, bis {_i.nom()} im Dashboard „Übernehmen“ drückt'


# --- Register ------------------------------------------------------------------------------------

def _ordner():
    return projekte.zustand() / 'vorschlaege'


def _sichern(v):
    schreiben(_ordner() / f'{v["id"]}.json', json.dumps(v, indent=1, ensure_ascii=False) + '\n')


def laden(vid):
    try:
        return json.loads((_ordner() / f'{vid}.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise Fehler(f'Vorschlag {vid} unbekannt') from None


def alle(projekt=None):
    """Alle Vorschläge, ältester zuerst."""
    vs = [laden(d.stem) for d in sorted(_ordner().glob('*.json'))]
    return sorted((v for v in vs if projekt in (None, v['projekt'])), key=lambda v: v['angelegt'])


def von_traum(tid):
    return [v for v in alle() if v['traum'] == tid]


def offene(projekt):
    """Was unter „Braucht dich“ steht."""
    return [v for v in alle(projekt) if v['zustand'] == 'offen']


def ist_vorschlag(aufgabe_id):
    return str(aufgabe_id or '').startswith('traum-')


def zu(p, branch=None, aufgabe_id=None):
    """Der registrierte Vorschlag zu Branch oder Aufgabe in genau diesem Projekt, sonst None."""
    return next((v for v in alle(p.name) if v['id'] == aufgabe_id or v['branch'] == branch), None)


def zerlegen(text):
    """(Bericht ohne Themenblock, Themen) aus der letzten Antwort von „Code pflegen“. Ohne gültigen Block gibt es keine Themen."""
    treffer = list(BLOCK.finditer(text))
    if not treffer:
        return text, []
    m = treffer[-1]
    try:
        themen = [dict(thema=str(x['thema']), titel=roadmap.eine_zeile(str(x['titel']))[:120], auftrag=str(x['auftrag']).strip())
                  for x in json.loads(m.group(1))['vorschlaege']]
    except (ValueError, KeyError, TypeError):
        return text, []
    return (text[:m.start()] + text[m.end():]).strip(), [x for x in themen if x['titel'] and x['auftrag']]


def _thema(text, belegt):
    """Kurzname für Branch und ID, im ganzen Traum eindeutig: Vorschläge verschiedener Projekte teilen sich die ID-Reihe."""
    basis = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40].strip('-') or 'pflege'
    name, n = basis, 1
    while name in belegt:
        n += 1
        name = f'{basis}-{n}'
    return name


def registrieren(t, projekt, themen):
    """Themen eines Projekts eintragen, höchstens MAX_JE_PROJEKT. Die Zuordnung steht, bevor es einen Branch gibt."""
    p, tid = Projekt(projekt), t['id']
    vorhanden = von_traum(tid)
    if any(v['projekt'] == projekt for v in vorhanden):
        return
    belegt = {v['thema'] for v in vorhanden}
    for th in themen[:MAX_JE_PROJEKT]:
        thema = _thema(th['thema'], belegt)
        belegt.add(thema)
        branch = f'{PRAEFIX}{tid}-{thema}'
        _sichern(dict(id=f'traum-{tid}-{thema}', projekt=projekt, traum=tid, thema=thema, titel=th['titel'], auftrag=th['auftrag'], branch=branch,
                      worktree=str(p.worktree(projekte.slug(branch))), start=None, commit=None, zustand='entsteht', angelegt=laeufe.jetzt()))


# --- Schritte im Traum ---------------------------------------------------------------------------

def _status(pfad):
    try:
        return git(pfad, 'status', '--porcelain')
    except (Fehler, OSError):
        return ''


def _vorbereiten(p, v):
    """Worktree auf eigenem Branch anlegen; eine Wiederholung beginnt im selben Worktree wieder beim Startcommit."""
    wt, ref = v['worktree'], f'refs/heads/{v["branch"]}'
    if v['start']:
        if repo.worktrees(p).get(wt) != ref:
            raise Fehler(f'Worktree {wt} gehört nicht mehr zu {v["branch"]}')
        git(wt, 'reset', '-q', '--hard', v['start'])
        git(wt, 'clean', '-qfd')
    else:
        if repo.kopf(p, v['branch']) or Path(wt).exists():
            raise Fehler(f'Branch {v["branch"]} oder Worktree {wt} gibt es schon')
        v['start'] = git(p.repo, 'rev-parse', p.cfg['basis'])
        _sichern(v)
        git(p.repo, 'worktree', 'add', '-q', '-b', v['branch'], wt, v['start'])
    v['schnappschuss'] = {name: _status(pfad) for name, pfad in projekte.alle().items()}
    _sichern(v)


def _bauen(p, v, marke):
    _vorbereiten(p, v)
    k = dict(projekt=p.name, worktree=v['worktree'], branch=v['branch'], titel=v['titel'], auftrag=v['auftrag'], basis=p.cfg['basis'],
             grundlage=grundlage.fuer(p, f'{v["titel"]} {v["auftrag"]}'))
    return laeufe.starten(p, 'entwickeln', ENTWICKLER, v['worktree'], auftraege.traum_vorschlag(k), cli_zusatz=SCHREIBEND, cli_modus=MODUS, aufgabe=v['id'], branch=v['branch'],
                          worktree=v['worktree'], entwickler=ENTWICKLER, automatisch=False, zusatzauftrag=v['auftrag'], vorschlag=v['id'],
                          grundlage_stand=k['grundlage']['stand'], **marke)


def _testen(p, v, marke):
    return laeufe.starten(p, 'test', 'shell', v['worktree'], p.cfg['test'], aufgabe=v['id'], branch=v['branch'], worktree=v['worktree'],
                          testlauf=True, automatisch=False, vorschlag=v['id'], **marke)


def _pruefen(p, v, marke):
    rev = ablauf._reviewer(p, ENTWICKLER)
    commit = git(v['worktree'], 'rev-parse', 'HEAD')
    rwt = p.worktree(f'review-{projekte.slug(v["id"])}-1')
    ablauf._weg(rwt)
    git(p.repo, 'worktree', 'add', '-q', '--detach', str(rwt), commit)
    k = dict(projekt=p.name, id=v['id'], aufgabe=v['titel'], auftrag=v['auftrag'], basis=p.cfg['basis'], test=p.cfg['test'], branch=v['branch'], cwd=str(rwt),
             commit=commit, basis_commit=git(p.repo, 'merge-base', p.cfg['basis'], commit),
             grundlage=grundlage.fuer(p, f'{v["titel"]} {v["auftrag"]}'))
    try:
        return laeufe.starten(p, 'review', rev, rwt, auftraege.review(k, 1, [], NAMEN[rev]), aufgabe=v['id'], branch=v['branch'], worktree=v['worktree'],
                              review_worktree=str(rwt), commit=commit, runde=1, reviewer=rev, entwickler=ENTWICKLER, schema=auftraege.REVIEW_SCHEMA,
                              automatisch=False, vorschlag=v['id'], grundlage_stand=k['grundlage']['stand'], **marke)
    except Exception:
        ablauf._weg(rwt)
        raise


SCHRITTE = {'vorschlag': _bauen, 'test': _testen, 'review': _pruefen}


def schritt_starten(p, art, vid, marke):
    return SCHRITTE[art](p, laden(vid), marke)


def _schreibziele(p, v):
    """Grund, wenn der Baulauf etwas anderes getan hat, als im eigenen Worktree Dateien zu ändern, sonst ''. Keine Änderung ist auch ein Grund."""
    wt = v['worktree']
    if git(wt, 'rev-parse', 'HEAD') != v['start']:
        return 'Der Lauf hat selbst committet'
    fremd = [name for name, vorher in v['schnappschuss'].items() if name in projekte.alle() and _status(projekte.alle()[name]) != vorher]
    if fremd:
        return f'Schreibziel verletzt: Haupt-Checkout von {", ".join(fremd)} geändert'
    dateien = [d for d in (git(wt, 'diff', '--name-only', 'HEAD') + '\n' + git(wt, 'ls-files', '--others', '--exclude-standard')).split('\n') if d]
    verboten = sorted(set(dateien) & set(VERBOTEN))
    if verboten:
        return f'Schreibziel verletzt: {", ".join(verboten)}'
    return '' if dateien else 'Keine Änderung'


def _nach_bau(p, v, lauf):
    """devflow committet, nicht das Modell: Der Bericht gehört mit auf den Branch und bleibt für „Verwerfen“ im Register."""
    grund = _schreibziele(p, v)
    if grund:
        _scheitern(p, v, grund)
        return
    wt, datei = v['worktree'], f'berichte/{v["id"]}.md'
    text = f'# Vorschlag · {v["titel"]}\n\n**Traum:** {v["traum"]} · **Branch:** `{v["branch"]}`\n\n{laeufe.ergebnis(lauf).strip()}\n'
    schreiben(Path(wt) / datei, text)
    git(wt, 'add', '-A')
    git(wt, 'commit', '-q', '-m', f'traum: {v["titel"]}')
    bau = git(wt, 'rev-parse', 'HEAD')
    v.update(commit=bau, bau=bau, bericht=text)  # `commit` wandert mit dem Review weiter, `bau` bleibt für die Sperre
    _sichern(v)


def _nach_test(p, v, lauf):
    v['test'] = 'gruen' if lauf.get('rc') == 0 else 'rot'
    _sichern(v)


def _nach_review(p, v, lauf):
    ablauf.review_sichern(lauf)
    v['commit'] = lauf['kopf']
    _sichern(v)


def abschluss(p, lauf):
    """Ende eines Traumlaufs für einen Vorschlag. Keine Roadmap, kein Folgeschritt: Weiter geht es nur im Takt des Traums."""
    {'entwickeln': _nach_bau, 'test': _nach_test, 'review': _nach_review}[lauf['art']](p, laden(lauf['vorschlag']), lauf)


def _rebases(p, v):
    return [l['rebase_kopf'] for l in laeufe.alle(p.name, v['id']) if l.get('rebase_kopf')]


def _eigen(p, v, kopf):
    """Gehört dieser Branchstand zum Vorschlag? Sonst trägt nur ein anderer Branch denselben Namen."""
    return any(s and ok(p.repo, 'merge-base', '--is-ancestor', s, kopf) for s in [v['commit'] or v['start']] + _rebases(p, v))


def _entfernen(p, v):
    """Genau den registrierten Worktree und Branch entfernen, auch ungemergt (Ausnahme von „ohne Force“). Rückgabe: Hinweise zu dem, was stehen blieb."""
    wt, ref, branch = v['worktree'], f'refs/heads/{v["branch"]}', v['branch']
    baeume, kopf, hinweise = repo.worktrees(p), repo.kopf(p, branch), []
    meins = bool(kopf) and _eigen(p, v, kopf)
    if kopf and not meins:
        hinweise.append(f'Branch {branch} ist nicht mehr dieser Vorschlag und bleibt')
    if baeume.get(wt) == ref and meins:
        git(p.repo, 'worktree', 'remove', '--force', wt)
    elif Path(wt).exists():
        hinweise.append(f'Verzeichnis {wt} ist nicht der Worktree dieses Vorschlags und bleibt')
    if meins:
        git(p.repo, 'branch', '-D', branch)
    return hinweise


def _scheitern(p, v, grund):
    """Aus dem Lauf wurde kein Vorschlag; was davon liegt, gehört nur ihm."""
    v.update(zustand='gescheitert', grund=grund[:300], hinweis=' '.join(_entfernen(p, v)) or None)
    _sichern(v)


def traum_ende(tid):
    """Beim Ende des Traums erscheinen die fertigen Vorschläge unter „Braucht dich“, auch ohne Review. Der Rest wird aufgeräumt."""
    for v in von_traum(tid):
        if v['zustand'] != 'entsteht':
            continue
        try:
            if v['commit']:
                v['zustand'] = 'offen'
                _sichern(v)
            else:
                _scheitern(Projekt(v['projekt']), v, 'Im Traum nicht fertig geworden')
        except (Fehler, OSError) as err:
            print(f'devflow2 traum: {v["id"]}: {err}', file=sys.stderr)


# --- Sperre --------------------------------------------------------------------------------------

def _unfrei_in(p, ref):
    """Vorschläge ohne Freigabe, von denen schon ein Stand in `ref` steckt: Ein anderer Branch oder ein Handgriff hat sie hineingezogen. Es zählt die ganze
    Herkunft, nicht nur der letzte Kopf: Baucommit (vor dem Reviewbericht), `commit` und devflows Rebases, deren Commits andere Hashes tragen."""
    return [v for v in alle(p.name) if v['commit'] and v['zustand'] not in ('uebernommen', 'gemergt')
            and any(s and ok(p.repo, 'merge-base', '--is-ancestor', s, ref) for s in [v.get('bau'), v['commit']] + _rebases(p, v))]


def _zurueck(p, v, stand, gesperrt):
    """Der Branch ist nicht mehr der Stand, dem der Inhaber zugestimmt hat: Die Zustimmung verfällt, der Vorschlag liegt wieder bei ihm."""
    v.update(zustand='offen', commit=stand, freigabe=None, hinweis='Der Branch hat sich nach „Übernehmen“ geändert; bitte neu entscheiden')
    _sichern(v)
    repo.roadmap_aendern(p, lambda t: roadmap.setze(t, v['id'], tag(), 'Braucht dich', stand='Branch geändert, Übernehmen nötig'),
                         f'roadmap: {v["id"]} → Braucht dich', gesperrt=gesperrt)


def merge_tor(p, branch, aufgabe_id, reviews, eigener_rebase=None, gesperrt=False):
    """Vor jedem Merge. Erlaubt ist nur ein Vorschlag mit Freigabe, und nur der Stand, dem der Inhaber zugestimmt hat: genau dieser Commit, dazu devflows
    eigener Reviewbericht darauf und devflows eigener Rebase. Normale Branches dürfen keinen ungefreigegebenen Vorschlag mitbringen."""
    v = zu(p, branch, aufgabe_id)
    if v is None:
        if branch.startswith(PRAEFIX) or ist_vorschlag(aufgabe_id):
            raise Fehler(f'{branch} ist als Traumbranch nicht registriert; Merge gesperrt')
        drin = _unfrei_in(p, branch)
        if drin:
            raise Fehler(f'{branch} enthält {drin[0]["branch"]}; {nicht_uebernommen(drin[0]["id"])}')
        return
    if v['zustand'] == 'verworfen':
        raise Fehler(f'Vorschlag {v["id"]} ist verworfen; Merge gesperrt')
    freigabe = v.get('freigabe')
    if not freigabe:
        raise Fehler(nicht_uebernommen(v['id']))
    erlaubt = {freigabe['commit'], eigener_rebase} | {r.get('kopf') for r in reviews if r.get('commit') == freigabe['commit']}
    stand = repo.kopf(p, branch)
    if stand not in erlaubt:
        _zurueck(p, v, stand, gesperrt)
        raise ablauf.FrageOffen(f'{v["id"]}: Branch hat sich nach „Übernehmen“ geändert; kein Merge, bis {_i.nom()} neu zustimmt')


def deploy_sperre(p, aufgabe_id):
    """Vor jedem Deploy: Eine Aufgabe aus einem Vorschlag erst nach dem Merge, und main darf keinen ungefreigegebenen Vorschlag enthalten."""
    if ist_vorschlag(aufgabe_id):
        v = zu(p, aufgabe_id=aufgabe_id)
        if not v or v['zustand'] != 'gemergt':
            raise Fehler(f'{nicht_uebernommen(aufgabe_id)}; kein Deploy')
    drin = _unfrei_in(p, p.cfg['basis'])
    if drin:
        raise Fehler(f'{p.cfg["basis"]} enthält {drin[0]["branch"]}; {nicht_uebernommen(drin[0]["id"])}; kein Deploy')


def wartet(aufgabe_id):
    """Liegt die Aufgabe als Vorschlag wieder beim Inhaber?"""
    return ist_vorschlag(aufgabe_id) and any(v['id'] == aufgabe_id and v['zustand'] == 'offen' for v in alle())


def gemergt(lauf):
    if ist_vorschlag(lauf.get('aufgabe')):
        v = laden(lauf['aufgabe'])
        v.update(zustand='gemergt', merge=lauf.get('merge'), hinweis=None)
        _sichern(v)


# --- Entscheidung ------------------------------------------------------------------------------

def kopf(p, v):
    return repo.kopf(p, v['branch'])


def review_stand(p, v, stand):
    """Das Review, dessen Stand genau `stand` ist, sonst None."""
    return next((r for r in reversed(ablauf._reviews(p, v['id'])) if r.get('kopf') == stand), None)


def diffstat(p, v, stand):
    return git(p.repo, 'diff', '--stat', f'{v["start"]}...{stand}', check=False)


def _zeile_anlegen(p, v):
    """Erst die Übernahme macht aus dem Vorschlag eine Aufgabe: Im Traum selbst ist die Roadmap kein Schreibziel."""
    if not any(a['id'] == v['id'] for a in roadmap.aufgaben(p.roadmap.read_text(encoding='utf-8'))):
        repo.roadmap_aendern(p, lambda t: roadmap.protokoll(
            roadmap.anlegen(t, v['id'], v['titel'], tag(), f'{NAMEN[ENTWICKLER]}, Review {NAMEN[ablauf._reviewer(p, ENTWICKLER)]}', 'Review', f'Vorschlag aus Traum {v["traum"]}'),
            f'{ablauf.datum()} · Vorschlag {v["id"]} übernommen · Herkunft dashboard'), f'roadmap: {v["id"]} übernommen')


def uebernehmen(vid, gesehen, herkunft):
    """Knopf „Übernehmen“ für genau den angezeigten Commit. Rückgabe: 'merge', oder 'review', wenn dieser Stand noch kein bestandenes Gegenreview hat."""
    if herkunft != 'dashboard':
        raise Fehler(f'Übernehmen kann nur {_i.nom()} im Dashboard')
    v = laden(vid)
    if v['zustand'] not in ('offen', 'uebernommen'):
        raise Fehler(f'Vorschlag {vid} ist {v["zustand"]}')
    p = Projekt(v['projekt'])
    ablauf._bereit_zum_start(p, vid)
    stand = kopf(p, v)
    if not stand:
        raise Fehler(f'Branch {v["branch"]} gibt es nicht mehr')
    if stand != gesehen:
        raise Fehler(f'Branch {v["branch"]} steht nicht mehr auf dem angezeigten Stand; bitte neu ansehen')
    _zeile_anlegen(p, v)
    v.update(zustand='uebernommen', commit=stand, freigabe=dict(commit=stand, zeit=laeufe.jetzt(), herkunft=herkunft), hinweis=None)
    _sichern(v)
    r = review_stand(p, v, stand)
    if r and r['urteil'] == 'bestanden':
        ablauf.mergen(p, vid, automatisch=True)
        return 'merge'
    ablauf.review_anfordern(p, vid, automatisch=True)
    return 'review'


def _festhalten(p, v):
    """Bericht und Entscheidung bleiben auf main, auch wenn der Branch weg ist. Scheitert der Commit, steht beides noch im Register."""
    aid, datei = v['id'], f'berichte/{v["id"]}.md'
    text = f'{v.get("bericht") or "# Vorschlag · " + v["titel"]}\n\n**Entscheidung:** verworfen {_i.von()} am {ablauf.datum()} (Stand `{(v["commit"] or "–")[:7]}`).\n'

    def aendern(texte):
        rm = texte['ROADMAP.md']
        if any(a['id'] == aid for a in roadmap.aufgaben(rm)):
            rm = roadmap.setze(rm, aid, tag(), 'entfällt', stand='Vorschlag verworfen')
        return {'ROADMAP.md': roadmap.protokoll(rm, f'{ablauf.datum()} · Vorschlag {aid} verworfen · Herkunft dashboard'), datei: text}
    try:
        repo.auf_main_gesperrt(p, aendern, f'docs: Vorschlag {aid} verworfen', ('ROADMAP.md', datei))
    except (Fehler, OSError) as err:
        v['hinweis'] = ' '.join(x for x in (v.get('hinweis'), f'Bericht nicht im Repo: {err}'[:300]) if x)
        _sichern(v)


def verwerfen(vid, herkunft):
    """Knopf „Verwerfen“. Rückgabe: False, wenn er schon verworfen war (Doppelklick). Es läuft dafür nichts, und gemergt ist er noch nicht."""
    if herkunft != 'dashboard':
        raise Fehler(f'Verwerfen kann nur {_i.nom()} im Dashboard')
    v = laden(vid)
    if v['zustand'] == 'verworfen':
        return False
    if v['zustand'] not in ('offen', 'uebernommen'):
        raise Fehler(f'Vorschlag {vid} ist {v["zustand"]}')
    p = Projekt(v['projekt'])
    with repo.sperre(p):
        ls = laeufe.alle(p.name, vid)
        if any(l['zustand'] in laeufe.AKTIV for l in ls):
            raise Fehler(f'Für {vid} läuft gerade etwas; erst anhalten')
        if any(l['art'] == 'mergen' and l.get('merge') for l in ls):
            raise Fehler(f'{vid} ist schon gemergt')
        v.update(zustand='verworfen', freigabe=None, hinweis=' '.join(_entfernen(p, v)) or None, entscheidung=dict(was='verworfen', zeit=laeufe.jetzt(), herkunft=herkunft))
        _sichern(v)
        _festhalten(p, v)
    return True
