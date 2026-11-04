"""Frische Messung des Kontingents (S5.5): ein kleinstmöglicher Aufruf je Modell, wenn die Messung für den Traum fehlt oder veraltet ist.

Gemessen wird nur durch den Klick des Inhabers auf „Träumen“ und vor einem Traumschritt, nie von selbst. Der Aufruf ist ein eigener, kurzer Lauf der Art
`messung` (sichtbar unter `laeufe/`): kürzeste Eingabe, kein Werkzeug, kein MCP, leeres Temp-Verzeichnis statt Repo. Er zählt nicht als normale
Arbeit, nimmt keinen Laufplatz und löst kein Ereignis aus. Die Grenzwerte stehen danach im Lauflog (Claude: `rate_limit_event`) bzw. in der
codex-Sitzungsdatei (`rate_limits`), und `kontingent` liest sie wie jede andere Messung. Geschätzt wird nichts.
"""
import os
from pathlib import Path
import shutil
import tempfile
import time

from . import Fehler, kontingent, laeufe

NAMEN = {'claude': 'Claude', 'sol': 'Sol'}
AUFTRAG = 'Antworte nur mit: ok'
ZUSATZ = {'claude': ['--strict-mcp-config', '--tools', ''], 'sol': ['--skip-git-repo-check', '-s', 'read-only']}
CLI = {'claude': dict(cli_modell='haiku', cli_effort='low'), 'sol': {}}  # Sol: Standardmodell der CLI
ZEITLIMIT = 90.0


def warten():
    return float(os.environ.get('DEVFLOW2_MESSUNG_WARTEN') or ZEITLIMIT + 30)


def _grund(wer, text):
    return f'{NAMEN[wer]}: keine frische Messung, der Messaufruf ist gescheitert ({" ".join(str(text).split())[:240]})'


def _vorheriger_fehler(wer, seit):
    """Grund eines Messaufrufs dieses Modells seit `seit`, der keine frische Messung brachte, sonst ''.
    Wir werden nur gefragt, wenn die Messung nötig ist: Ein fertiger Aufruf aus den letzten `kontingent.ALTER` Sekunden lieferte also keine Grenzwerte."""
    for l in laeufe.alle():
        if l['art'] == 'messung' and l['modell'] == wer and l['angelegt'] >= max(seit, laeufe.jetzt() - kontingent.ALTER):
            if l['zustand'] == 'fertig':
                return _grund(wer, 'der Aufruf lieferte keine Grenzwerte')
            return _grund(wer, l.get('fehler') or l.get('grund') or l['zustand'])
    return ''


def auffrischen(projekt, modelle=('claude', 'sol'), seit=None):
    """Messaufrufe für alle `modelle` ohne frische Messung, parallel, mit Warten bis zum Ende.

    Rückgabe: {modell: Grund}, nur für Modelle, die danach noch keine frische Messung haben. Wirft nie.
    Mit `seit` (Beginn eines Traums) wird ein Modell, dessen Messaufruf seitdem scheiterte, nicht noch einmal gefragt.
    """
    fehler, offen, tmp = {}, {}, Path(tempfile.mkdtemp(prefix='df2-messung-'))
    try:
        for wer in modelle:
            if not kontingent.messung_noetig(wer):
                continue
            alt = seit is not None and _vorheriger_fehler(wer, seit)
            if alt:
                fehler[wer] = alt
                continue
            try:
                offen[wer] = laeufe.starten(projekt, 'messung', wer, tmp, AUFTRAG, cli_zusatz=ZUSATZ[wer], zeitlimit=ZEITLIMIT, **CLI[wer])['id']
            except (Fehler, OSError) as err:
                fehler[wer] = _grund(wer, err)
        ende = time.time() + warten()
        for wer, lid in offen.items():
            while True:
                l = laeufe.laden(lid)
                if l['zustand'] not in laeufe.AKTIV:
                    break
                if time.time() > ende:
                    laeufe.anhalten(lid)
                    l = laeufe.laden(lid)
                    l['fehler'] = 'Zeitüberschreitung'
                    break
                time.sleep(0.2)
            if l['zustand'] != 'fertig':
                fehler[wer] = _grund(wer, l.get('fehler') or l.get('grund') or l['zustand'])
            elif kontingent.messung_noetig(wer):
                fehler[wer] = _grund(wer, 'der Aufruf lieferte keine Grenzwerte')
    except Exception as err:  # eine Messung darf nie mehr kaputtmachen als sie ersetzt
        for wer in modelle:
            if kontingent.messung_noetig(wer):
                fehler.setdefault(wer, _grund(wer, f'{type(err).__name__}: {err}'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fehler
