"""Der Inhaber (S8.8): wer plant, entscheidet und abnimmt, als Einstellung `inhaber` in `instanz.toml`.

Der Wert ist ein Vorname oder die Rolle selbst (`Inhaber`, auch `der Inhaber`). Daraus entstehen die Formen für die Texte der Aufträge,
Fehlermeldungen und Marker in Roadmap, Plänen und Entscheidungen. Ein Name bleibt in jedem Fall gleich (`von Anna`, `an Anna`), die Rolle
wird gebeugt (`vom Inhaber`, `an den Inhaber`). Maschinell gelesene Marker (`abgenommen von …`, `bestätigt von …`, `Antwort …`) erkennt
devflow neben der eigenen Form immer auch in der Rollenform, weil die Vorlagen und Docs die Rolle nennen.
"""
import re

from . import instanz

ROLLE = 'Inhaber'
ROLLE_AKK = 'den Inhaber'


def name():
    """Der eingestellte Wert; leer gilt als Rolle."""
    return (instanz.laden().get('inhaber') or '').strip() or ROLLE


def ist_rolle(wert=None):
    return (name() if wert is None else wert).strip().lower() in ('inhaber', 'der inhaber')


def kennung():
    """Ohne Artikel, wie ein Name am Zeilenende oder nach einem Wort steht: `Antwort Anna`, `Rückmeldung Inhaber`."""
    return ROLLE if ist_rolle() else name()


def nom():
    return 'der Inhaber' if ist_rolle() else name()


def nom_gross():
    """Am Satzanfang."""
    return nom()[:1].upper() + nom()[1:]


def akk():
    return 'den Inhaber' if ist_rolle() else name()


def dat():
    return 'dem Inhaber' if ist_rolle() else name()


def von():
    return 'vom Inhaber' if ist_rolle() else f'von {name()}'


def von_gross():
    return von()[:1].upper() + von()[1:]


def zu():
    return 'zum Inhaber' if ist_rolle() else f'zu {name()}'


def an():
    return f'an {akk()}'


def fuer():
    return f'für {akk()}'


def bei():
    return 'beim Inhaber' if ist_rolle() else f'bei {name()}'


def frage_marker():
    """Beginn einer Frage in der letzten Antwort eines Laufs: `FRAGE AN ANNA:` bzw. `FRAGE AN DEN INHABER:`."""
    return f'FRAGE AN {akk().upper()}:'


def frage_marker_alle():
    """Die eigene Form und die Rollenform: `FRAGE AN ANNA:` und `FRAGE AN DEN INHABER:` (die Vorlagen nennen die Rolle)."""
    return tuple(dict.fromkeys((frage_marker(), f'FRAGE AN {ROLLE_AKK.upper()}:')))


def _gen_name():
    n = name()
    return n + ("'" if n[-1:] in 'sßxz' else 's')


def besitz(sache, artikel):
    """`Annas Abnahmezeile` bzw. `die Abnahmezeile des Inhabers`."""
    return f'{artikel} {sache} des Inhabers' if ist_rolle() else f'{_gen_name()} {sache}'


def namens():
    """`in Annas Namen` bzw. `im Namen des Inhabers`."""
    return 'im Namen des Inhabers' if ist_rolle() else f'in {_gen_name()} Namen'


def namen():
    """Alle Schreibweisen, die als Name des Inhabers in Markern gelten: die eingestellte und die Rolle."""
    return list(dict.fromkeys([kennung(), ROLLE]))


def von_muster():
    """Regex-Teil für `von Anna` / `vom Inhaber` (eigene Form und Rollenform)."""
    formen = list(dict.fromkeys([von(), 'vom Inhaber']))
    return '(?:' + '|'.join(re.escape(f) for f in formen) + ')'


def kennung_muster():
    """Regex-Teil für `Anna` / `Inhaber` als Namensteil eines Markers (`Antwort Inhaber`, `Rückmeldung Anna`)."""
    return '(?:' + '|'.join(re.escape(n) for n in namen()) + ')'


def an_formen():
    """`an Anna` und `an den Inhaber` (eigene Form und Rollenform)."""
    return list(dict.fromkeys([an(), 'an den Inhaber']))


class Muster:
    """Regex, der erst beim Gebrauch gebaut wird (Name des Inhabers steht in `instanz.toml`); `search`, `fullmatch`, `sub` wie bei `re.Pattern`."""

    def __init__(self, bau, flags=0):
        self._bau, self._flags = bau, flags

    def __getattr__(self, attr):
        return getattr(re.compile(self._bau(), self._flags), attr)
