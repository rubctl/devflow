"""Was beim Inhaber Neues liegt, als Schlüssel `<art>:<projekt>:<rest>` (S2.4): nur Art und Kennung, nie Texte.

Arten: `braucht` (Aufgabe auf „Braucht dich“), `frage` (offene Frage), `fehler` (Lauf fehlgeschlagen, ohne neueren
Lauf derselben Aufgabe). Eine Aufgabe, die wegen einer eigenen offenen Frage auf „Braucht dich“ steht, zählt nur als
Frage, damit eine neue Frage genau einen Hinweis ergibt. Dazu `budget`: Das Tagesbudget des Selbstlaufs hat einen Start verhindert,
`pflege`: eine hohe oder kritische Sicherheitslücke im letzten Ergebnis der Pflegeprüfung (S5.1), und `traum`: Ein Traum ist beendet (S5.3).
Fehler von Traumläufen melden sich nicht einzeln, sie stehen im Traumbericht.
"""
from . import Fehler, laeufe, pflege, projekte, roadmap, selbstlauf, traum
from .projekte import Projekt


def offene_fehler(ls):
    """Fehlgeschlagene Läufe, nach denen für dieselbe Aufgabe (bzw. Art) nichts Neueres gestartet wurde."""
    neuester = {}
    for l in ls:
        if not l.get('traum'):
            neuester[(l['projekt'], l.get('aufgabe') or l['art'])] = l
    return [l for l in neuester.values() if l['zustand'] == 'fehler']


def projekt_ereignisse(name):
    p = Projekt(name)
    text = p.roadmap.read_text(encoding='utf-8')
    fragen = roadmap.fragen(text)
    mit_frage = {f['bezug'] for f in fragen}
    aus = {f'frage:{name}:{f["nr"]}' for f in fragen}
    aus |= {f'braucht:{name}:{a["id"]}' for a in roadmap.aufgaben(text)
            if a['status'] == 'Braucht dich' and a['id'] not in mit_frage}
    aus |= {f'fehler:{name}:{l["id"]}' for l in offene_fehler(laeufe.alle(name))}
    aus |= pflege.push_schluessel(name)
    return aus


def ereignisse():
    """(Schlüssel aller aktuellen Ereignisse, Projekte, die sich nicht lesen ließen)."""
    aus, defekt = selbstlauf.hinweise() | traum.hinweise(), set()
    for name in projekte.alle():
        try:
            aus |= projekt_ereignisse(name)
        except (Fehler, OSError, ValueError, KeyError):
            defekt.add(name)
    return aus, defekt
