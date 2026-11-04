"""Pläne abnehmen (S3.3): Liste, Stand und die beiden Handlungen „Abnehmen“ und „Änderung wünschen“.

Jede Handlung ist ein Commit auf `main` über `repo.auf_main` (nur die Plandatei) und meldet danach ein Ereignis für den
Selbstlauf (`ereignis.melden`). Abnehmen ist nur mit dem Stand möglich, den der Inhaber gesehen hat (`pruefsumme`).
"""
import hashlib
import re

from . import Fehler, ereignis, repo, roadmap
from . import inhaber as _i
from .ablauf import datum


EINGANG = 'eingang'  # plan/eingang.md ist der Ideeneingang (eingang.py), kein Plan
PFLEGEPLAN = 'pflege'  # plan/pflege.md (pflegeplan.py) hat keine Aufgaben und wird nicht übernommen
NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,60}')


def namen(p):
    return sorted(d.stem for d in (p.repo / 'plan').glob('*.md') if not d.name.startswith('_') and d.stem != EINGANG)


def pfad(p, name):
    datei = p.repo / 'plan' / f'{name}.md'
    if name.startswith('_') or name == EINGANG or not datei.is_file():
        raise Fehler(f'Plan {name} gibt es nicht')
    return datei


def pruefsumme(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def info(p, name):
    """Titel, Stand, Aufgaben und ob sie in der Roadmap stehen (`uebernommen`), ob eine Rückmeldung offen ist."""
    text = pfad(p, name).read_text(encoding='utf-8')
    pl = roadmap.plan(text)
    in_roadmap = {a['id'] for a in roadmap.aufgaben(p.roadmap.read_text(encoding='utf-8'))}
    return dict(name=name, text=text, titel=pl['titel'], abgenommen=pl['abgenommen'], aufgaben=pl['aufgaben'],
                stand=roadmap.plan_stand(text) or 'ohne Stand', pruefsumme=pruefsumme(text),
                uebernommen=sum(a['id'] in in_roadmap for a in pl['aufgaben']),
                rueckmeldung=roadmap.hat_rueckmeldung(text))


def liste(p):
    out = []
    for n in namen(p):
        try:
            out.append(info(p, n))
        except (Fehler, OSError) as err:
            out.append(dict(name=n, fehler=str(err)))
    return out


def stufen_pruefen(p, stufen):
    bekannt = p.cfg['stufen']
    schlecht = sorted({s for s in stufen.values() if s and s not in bekannt})
    if schlecht:
        raise Fehler(f'Stufe unbekannt: {", ".join(schlecht)}; bekannt: {", ".join(sorted(bekannt))}')


def abnehmen(p, name, stufen, gesehen):
    """Plan mit den gewählten Stufen abnehmen. `gesehen`: Prüfsumme des angezeigten Plans."""
    datei = pfad(p, name)
    rel = f'plan/{datei.name}'
    stufen_pruefen(p, stufen)
    tag = datum()

    def aendern(texte):
        text = texte[rel]
        if roadmap.ABNAHME.search(text):
            raise Fehler(f'{rel} ist schon abgenommen; ein abgenommener Plan wird nicht noch einmal abgenommen')
        if pruefsumme(text) != gesehen:
            raise Fehler(f'{rel} hat sich geändert, seit du ihn geöffnet hast; bitte neu laden und prüfen')
        return {rel: roadmap.plan_abnehmen(text, tag, stufen, aufgaben=name != PFLEGEPLAN)}

    repo.auf_main(p, aendern, f'plan: {rel} abgenommen {_i.von()} am {tag}', pfade=(rel,))
    ereignis.melden('pflege-routine' if name == PFLEGEPLAN else 'plan-abgenommen', p.name, rel)
    return rel


def rueckmeldung(p, name, wunsch):
    """Änderungswunsch in den Plan schreiben. Rückgabe: True, wenn ein Commit entstand (derselbe Wunsch zweimal: nein)."""
    datei = pfad(p, name)
    rel = f'plan/{datei.name}'
    tag = datum()
    neu = []

    def aendern(texte):
        if roadmap.ABNAHME.search(texte[rel]):
            raise Fehler(f'{rel} ist schon abgenommen; Änderungen dazu gehören in eine neue Aufgabe')
        text = roadmap.plan_rueckmeldung(texte[rel], tag, wunsch)
        neu.append(text != texte[rel])
        return {rel: text}

    repo.auf_main(p, aendern, f'plan: Änderung gewünscht zu {rel}', pfade=(rel,))
    if neu[0]:
        ereignis.melden('plan-rueckmeldung', p.name, rel)
    return neu[0]


def entwurf(p, name, text):
    """Planentwurf der steuernden KI als Commit auf `main`. Abnehmen bleibt dem Inhaber: kein Text mit Abnahmezeile, kein Überschreiben."""
    if not NAME.fullmatch(name) or name == EINGANG:
        raise Fehler(f'Planname {name!r} ungeeignet; erwartet z. B. wetter-kachel')
    rel = f'plan/{name}.md'
    if roadmap.ABNAHME.search(text):
        raise Fehler(f'Ein Entwurf trägt keine Abnahmezeile; Pläne nimmt nur {_i.nom()} ab')
    if not roadmap.plan_stand(text) or (name != PFLEGEPLAN and not roadmap.plan(text)['aufgaben']):
        raise Fehler(f'{rel}: erwartet eine Zeile **Stand:** und die Aufgabentabelle aus plan/_vorlage.md')

    def aendern(texte):
        if roadmap.ABNAHME.search(texte[rel]):
            raise Fehler(f'{rel} ist abgenommen; Änderungen dazu gehören in einen neuen Plan')
        return {rel: text if text.endswith('\n') else text + '\n'}

    repo.auf_main(p, aendern, f'plan: Entwurf {rel}', pfade=(rel,))
    return rel
