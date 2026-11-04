"""Aufruf: python -m devflow2 mcp [steuerung|planung|ideen] | web | lauf <id> | lage | anmelden <pfad> [name] | instanz [einstellung] | aktualisieren [--version v2.N] [--zurueck] | format [migrieren]"""
import json
import sys

from . import Fehler


ZUSTAND = ('mcp', 'web', 'lauf', 'lage', 'anmelden')  # Befehle, die den Zustand lesen oder ändern


def main(argv):
    if argv[:1] and argv[0] in ZUSTAND:
        from . import format as zustandsformat
        zustandsformat.bereit()  # stellt den Zustand vorwärts um (mit Sicherung) oder bricht ab, wenn er neuer ist als diese Fassung
    if argv[:1] == ['format'] and len(argv) in (1, 2):
        from . import format as zustandsformat
        print(zustandsformat.bereit() or 'Zustand ist im Format dieser Fassung' if argv[1:] == ['migrieren'] else zustandsformat.FORMAT)
        return
    if argv[:1] == ['aktualisieren']:
        from .aktualisieren import main as aktualisieren_main
        return aktualisieren_main(argv[1:])
    if argv[:1] == ['mcp']:
        from .mcp_server import main as mcp_main
        return mcp_main(*argv[1:2])
    if argv == ['web']:
        from .web import main as web_main
        return web_main()
    if argv[:1] == ['lauf'] and len(argv) == 2:
        from .laeufe import ausfuehren
        return ausfuehren(argv[1])
    if argv[:1] == ['lage']:
        from .ablauf import lage
        print(json.dumps(lage(), indent=1, ensure_ascii=False))
        return
    if argv[:1] == ['anmelden'] and len(argv) in (2, 3):
        from .projekte import anmelden
        print(anmelden(*argv[1:]))
        return
    if argv[:1] == ['instanz'] and len(argv) in (1, 2):
        from . import instanz
        print(instanz.wert(argv[1]) if len(argv) == 2 else json.dumps(instanz.laden(), indent=1, ensure_ascii=False))
        return
    raise SystemExit(__doc__)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Fehler as e:
        raise SystemExit(f'devflow2: {e}')
