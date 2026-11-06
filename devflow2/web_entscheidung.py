"""„Braucht dich“ als Entscheidungsliste (S14.3): je Eintrag eine Zeile mit Überschrift und Knopf; Kurzfassung und Details klappen auf.

Drei Gruppen: **Zu klären** (Blockade ohne Frage, Login-Problem, globaler Halt; steht oben), **Entscheidungen** (Vorschlag, Plan zur
Abnahme, offene Frage, fehlgeschlagener oder angehaltener Lauf) und **Zur Info** (neue Idee, Push offen, Plan mit Änderungswunsch;
eingeklappt). Die Knöpfe senden an dieselben Routen wie die bestehenden Formulare (Herkunft, Halt, Inhaberrechte), dazu `gesehen`
mit der Prüfsumme des angezeigten Stands und `zurueck=start`, damit es danach auf die Übersicht zurückgeht. Ein Klick wirkt sofort
(keine Rückfrage); nur „Verwerfen“ in den Details fragt nach.
"""
from urllib.parse import quote

from . import entscheidung, kurz, laeufe, plaene, repo, vorschlag
from . import eingang as eingang_logik
from . import web_html
from .push_ereignisse import offene_fehler
from .web_aktion import _knopf, alles_knoepfe, antwort_formular
from .web_html import t
from .web_md import inline, kurz_kasten, md

KLAEREN, ENTSCHEIDEN, INFO = 'klaeren', 'entscheiden', 'info'


def _w():
    from . import web  # erst beim Aufruf: web importiert dieses Modul über web_lage
    return web


def _kurz(text, ab=0):
    from .web_lage import _kurz as kurz_zeile
    return kurz_zeile(text, ab)


def _zurueck(ziel='start'):
    return t('input', type='hidden', name='zurueck', value=ziel)


def _gesehen(wert):
    return t('input', type='hidden', name='gesehen', value=wert)


def eintrag(gruppe, projekt, art, art_art, titel, *details, knopf=None, klasse=None):
    """Eine Zeile: Überschrift in der Zusammenfassung (klappt Kurzfassung und Details auf), der Knopf daneben."""
    kopf = t('summary', web_html.status(art, art_art), ' ', t('span', projekt, class_='leise') if projekt else None, ' ' if projekt else None,
             t('span', titel, class_='titel'))
    inhalt = [d for d in details if d is not None]
    return dict(gruppe=gruppe, li=t('li', t('details', kopf, t('div', *inhalt, class_='details')), t('div', knopf, class_='knopf') if knopf else None,
                                    class_='eintrag' + (f' {klasse}' if klasse else '')))


def _link(text, href):
    return t('p', t('a', text, href=href), class_='leise')


# --- einzelne Arten --------------------------------------------------------------------------------

def frage_eintrag(name, f):
    q = quote
    ziel = f'/p/{q(name)}/a/{q(f["bezug"])}#fragen' if f['bezug'] != 'allgemein' else f'/p/{q(name)}#fragen'
    e = entscheidung.empfehlung(f)
    knopf = hinweis = None
    if e:
        knopf = _knopf(f'/p/{q(name)}/frage/{f["nr"]}/empfehlung', 'Empfehlung annehmen', _gesehen(entscheidung.frage_stand(f)), _zurueck(),
                       titel=f'Trägt Option {e[0]} als deine Antwort ein: {e[1]}'[:200])
        hinweis = t('p', f'„Empfehlung annehmen“ trägt Option {e[0]} als Antwort ein: ', e[1], class_='hinweis')
    else:
        knopf = t('a', 'Antworten', href=ziel, class_='rc-btn', data_antworten=True)
        hinweis = t('p', 'Keine eindeutige Empfehlung: Antworte in eigenen Worten.', class_='leise')
    text = '\n'.join(f['text'].split('\n')[1:]).strip()
    return eintrag(ENTSCHEIDEN, name, 'offene Frage', 'warn', f'F{f["nr"]} · {f["bezug"]}: {_kurz(f["text"], 1)}',
                   hinweis, t('div', md(text), class_='md'), antwort_formular(name, f), _link('Zur Frage', ziel), knopf=knopf)


def aufgabe_eintrag(name, a):
    w = _w()
    return eintrag(KLAEREN, name, a['status'], 'warn', f'{a["id"]} {w.kurztitel(a["aufgabe"])}',
                   t('div', inline(a['details'], w.verweise(name, 'ROADMAP.md')), class_='leise') if a['status'] == 'blockiert' and a['details'] else None,
                   _link('Zur Aufgabe', f'/p/{quote(name)}/a/{quote(a["id"])}'))


def vorschlag_eintrag(name, p, v):
    from .web_traum import _vorschlag_stand, _verwerfen_knopf
    q = quote
    try:
        stand, r, pruefung = _vorschlag_stand(v, p)
    except Exception as err:  # ein kaputter Vorschlag darf die Liste nicht abbrechen
        return eintrag(KLAEREN, name, 'Vorschlag', 'error', v['titel'], t('p', f'Stand nicht lesbar: {err}', class_='fehler'), _link('Zum Vorschlag', f'/traum#{q(v["id"])}'))
    bestanden = bool(r and r['urteil'] == 'bestanden')
    folge = 'Der Merge startet sofort.' if bestanden else 'Das Gegenreview startet; gemergt wird erst, wenn es besteht.'
    knopf = (_knopf(f'/traum/vorschlag/{q(v["id"])}/uebernehmen', 'Übernehmen', _gesehen(stand), _zurueck(), titel=f'Übernimmt genau den Stand {stand[:7]}. {folge}')
             if stand else None)
    verwerfen = t('form', _zurueck(), _gesehen(stand or ''), t('button', 'Verwerfen', type='submit', class_='rc-btn'), method='post', action=f'/traum/vorschlag/{q(v["id"])}/verwerfen',
                  class_='aktion', data_bestaetigen=f'Branch {v["branch"]} und seinen Worktree entfernen, auch ungemergt?')
    return eintrag(ENTSCHEIDEN, name, 'Vorschlag', 'warn', v['titel'],
                   t('p', f'„Übernehmen“ nimmt genau den Stand {stand[:7]}. {folge}', class_='hinweis') if stand else t('p', f'Branch {v["branch"]} gibt es nicht mehr.', class_='fehler'),
                   kurz_kasten(kurz.aus_text(v.get('bericht') or '')), t('p', f'{v["branch"]} · Stand {(stand or v["commit"] or "–")[:7]} · {pruefung}', class_='leise'),
                   _link('Bericht und Änderungen', f'/traum#{q(v["id"])}'), verwerfen, knopf=knopf)


def plan_eintrag(name, p, pl, zurueck='start', projekt=True):
    """Plan zur Abnahme. `zurueck`: wohin es nach „Abnehmen“ geht (`start`, `plaene`, `plaene-alle`, siehe `web_aktion.ziel_nach`); `projekt=False` lässt den Projektnamen weg."""
    q = quote
    ziel = f'/p/{q(name)}/plan/{q(pl["name"])}'
    stufen = t('ul', [t('li', t('strong', a['id']), ' ', a['aufgabe'], ' ', t('span', a['stufe'] or f'Projekt ({p.cfg["stufe"] or "Standard"})', class_='leise'))
                      for a in pl['aufgaben']], class_='stufenliste')
    if pl['rueckmeldung']:
        return eintrag(INFO, name if projekt else None, 'Plan: Änderung gewünscht', 'idle', pl['titel'], kurz_kasten(pl['kurz']),
                       t('p', 'Ein Änderungswunsch liegt vor; abnehmen kannst du auf der Planseite.', class_='leise'), _link('Zum Plan', ziel))
    felder = [t('input', type='hidden', name=f'stufe.{a["id"]}', value=a['stufe'] or '') for a in pl['aufgaben']]
    stufen_text = ', '.join(f'{a["id"]} {a["stufe"] or "Standard"}' for a in pl['aufgaben'])
    knopf = _knopf(f'{ziel}/abnehmen', 'Abnehmen', *felder, _gesehen(pl['pruefsumme']), _zurueck(zurueck), titel=f'Nimmt den Plan mit diesen Stufen ab: {stufen_text}'[:200])
    wunsch = t('form', t('label', 'Änderung wünschen', t('textarea', class_='rc-input', name='wunsch', rows=3, required=True, maxlength=4000)), _zurueck(zurueck),
               t('button', 'Änderung wünschen', type='submit', class_='rc-btn'), method='post', action=f'{ziel}/aenderung', class_='aktion antwort')
    return eintrag(ENTSCHEIDEN, name if projekt else None, 'Plan zur Abnahme', 'warn', pl['titel'], kurz_kasten(pl['kurz']),
                   t('p', 'Stufen, mit denen „Abnehmen“ den Plan abnimmt (aus der Plantabelle):', class_='hinweis'), stufen, wunsch, _link('Ganzen Plan lesen', ziel), knopf=knopf)


def lauf_eintrag(name, l, fehler):
    w = _w()
    grund = entscheidung.fortsetzen_grund(l)
    login = bool(l.get('login'))
    titel = ' '.join(x for x in (l.get('aufgabe'), w.lauf_name(l)) if x)
    knopf = None
    if grund is None and (fehler or l['zustand'] == 'angehalten'):
        knopf = _knopf(f'/lauf/{quote(l["id"])}/fortsetzen', 'Fortsetzen', _gesehen(entscheidung.lauf_stand(l)), _zurueck(), titel='Setzt den Lauf in derselben Sitzung fort.')
    text = laeufe.login_hinweis(l) if login else (l.get('fehler') or '')[-200:]
    return eintrag(KLAEREN if login else ENTSCHEIDEN, name, 'Lauf fehlgeschlagen' if fehler else 'Lauf angehalten', 'error' if fehler else 'warn', titel,
                   kurz_kasten(l.get('kurz')), t('p', text, class_='leise') if text else None,
                   t('p', f'Fortsetzen ist gerade nicht möglich: {grund}', class_='hinweis') if grund and not l.get('traum') else None,
                   _link('Zum Lauf', f'/lauf/{quote(l["id"])}'), knopf=knopf)


def halt_eintrag():
    return eintrag(KLAEREN, None, 'Angehalten', 'warn', 'devflow ist angehalten: keine neuen Läufe, bis fortgesetzt wird.',
                   t('p', 'Angehaltene Läufe setzt du danach einzeln fort.', class_='leise'), knopf=alles_knoepfe())


def braucht_dich(name, p, text, auf, fragen, ls):
    """Einträge für „Braucht dich“ eines Projekts als Liste von Dicts `{gruppe, li}`."""
    w = _w()
    aus = []
    mit_frage = {f['bezug'] for f in fragen}
    aus += [frage_eintrag(name, f) for f in fragen]
    aus += [aufgabe_eintrag(name, a) for a in auf if a['status'] in ('Braucht dich', 'blockiert') and a['id'] not in mit_frage]
    aus += [vorschlag_eintrag(name, p, v) for v in vorschlag.offene(name)]
    aus += [plan_eintrag(name, p, pl) for pl in plaene.liste(p) if 'fehler' not in pl and not pl['abgenommen']]
    neu = [i for i in eingang_logik.liste(p) if i['zustand'] == 'neu']
    if neu:
        aus.append(eintrag(INFO, name, 'neue Idee' if len(neu) == 1 else f'{len(neu)} neue Ideen', 'idle', _kurz(neu[-1]['text']), _link('Zum Eingang', f'/p/{quote(name)}#ideen')))
    offen = repo.push_offen(p)
    if offen and offen['fehler']:
        aus.append(eintrag(INFO, name, 'Push offen', 'error', _kurz(offen['fehler'], 1),
                           t('p', 'main liegt lokal vor dem Remote; devflow versucht es beim nächsten Commit erneut, nie mit Force.', class_='leise')))
    aus += [lauf_eintrag(name, l, True) for l in offene_fehler(ls)]
    aus += [lauf_eintrag(name, l, False) for l in ls if l['zustand'] == 'angehalten']
    return aus


def liste(eintraege, sichtbar):
    """(Anzeige, Anzahl). Anzeige: Zu klären, Entscheidungen (ab `sichtbar` eingeklappt unter „n weitere“), eingeklappt „Zur Info“.
    Die Anzahl zählt nur, was auf den Inhaber wartet, nicht die Info."""
    klaeren = [e['li'] for e in eintraege if e['gruppe'] == KLAEREN]
    entscheiden = [e['li'] for e in eintraege if e['gruppe'] == ENTSCHEIDEN]
    info = [e['li'] for e in eintraege if e['gruppe'] == INFO]
    teile = []
    if klaeren:
        teile.append(t('ul', klaeren, class_='liste entscheidungen klaerung', aria_label='Zu klären'))
    if len(entscheiden) > sichtbar:
        teile += [t('ul', entscheiden[:sichtbar], class_='liste entscheidungen'),
                  t('details', t('summary', f'{len(entscheiden) - sichtbar} weitere'), t('ul', entscheiden[sichtbar:], class_='liste entscheidungen'))]
    elif entscheiden:
        teile.append(t('ul', entscheiden, class_='liste entscheidungen'))
    if info:
        teile.append(t('details', t('summary', f'Zur Info ({len(info)})'), t('ul', info, class_='liste entscheidungen'), class_='info'))
    return teile, len(klaeren) + len(entscheiden)
