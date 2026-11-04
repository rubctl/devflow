"""Nachgebaute CLIs `claude` und `codex` für Tests. Aufruf: fake_cli.py <claude|codex> <argv…>

Verhalten aus einem Drehbuch $FAKE_DIR/<werkzeug>.json (Liste von Schritten, je Aufruf wird einer verbraucht).
Jeder Aufruf wird in $FAKE_DIR/aufrufe.jsonl protokolliert.
"""
import fcntl
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

werkzeug, argv = sys.argv[1], sys.argv[2:]
d = Path(os.environ['FAKE_DIR'])
with open(d / 'sperre', 'w') as s:
    fcntl.flock(s, fcntl.LOCK_EX)
    buch = json.loads((d / f'{werkzeug}.json').read_text())
    schritt = buch.pop(0) if buch else {'tue': 'leer'}
    (d / f'{werkzeug}.json').write_text(json.dumps(buch))
    with open(d / 'aufrufe.jsonl', 'a') as f:
        f.write(json.dumps({'werkzeug': werkzeug, 'argv': argv, 'cwd': os.getcwd(), 'schritt': schritt.get('tue')}) + '\n')

if werkzeug == 'claude':
    sid = argv[argv.index('--resume') + 1] if '--resume' in argv else f'sess-claude-{time.time_ns()}'
else:
    sid = argv[2] if argv[1:2] == ['resume'] else f'thread-codex-{time.time_ns()}'


def aus(obj):
    print(json.dumps(obj), flush=True)


def ende(text, fehler=False, code=0, struktur=None):
    if werkzeug == 'claude':
        r = {'type': 'result', 'result': text, 'is_error': fehler, 'session_id': sid}
        if struktur is not None and '--json-schema' in argv:
            r['structured_output'] = struktur
        aus(r)
    else:
        if '-o' in argv and not fehler:
            Path(argv[argv.index('-o') + 1]).write_text(text)
        aus({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text[:200]}})
        if fehler:
            aus({'type': 'turn.failed', 'error': {'message': text}})
    sys.exit(code)


aus({'type': 'system', 'subtype': 'init', 'session_id': sid} if werkzeug == 'claude' else {'type': 'thread.started', 'thread_id': sid})
if 'limit' in schritt and werkzeug == 'codex':  # wie codex: rate_limits stehen in der Sitzungsdatei, nicht in der Ausgabe
    import datetime
    g = schritt['limit']
    sitz = Path(os.environ['DEVFLOW2_CODEX_SITZUNGEN'])
    sitz.mkdir(parents=True, exist_ok=True)
    (sitz / f'rollout-{time.time_ns()}.jsonl').write_text(json.dumps({'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'payload': {'rate_limits': {
        'primary': {'used_percent': g['5h'], 'resets_at': g['frei'], 'window_minutes': 300},
        'secondary': {'used_percent': g['woche'], 'resets_at': g['frei'] + 86400, 'window_minutes': 10080}}}}) + '\n')
elif 'limit' in schritt:  # Kontingent, wie es die CLI während eines Laufs meldet: {'5h': Prozent, 'woche': Prozent, 'frei': Epoche}
    g = schritt['limit']
    aus({'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {
        'five_hour': {'utilization': g['5h'] / 100, 'resetsAt': g['frei']},
        'seven_day': {'utilization': g['woche'] / 100, 'resetsAt': g['frei'] + 86400}}}})
tue = schritt['tue']
if tue == 'commit':
    for name, inhalt in schritt.get('dateien', {'arbeit.txt': 'x\n'}).items():
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text(inhalt)
    for name in schritt.get('loeschen', []):
        Path(name).unlink(missing_ok=True)
    subprocess.run(['git', 'add', '-A'], check=True)
    subprocess.run(['git', 'commit', '-q', '-m', schritt.get('nachricht', 'feat: arbeit')], check=True)
    ende(schritt.get('ergebnis', 'Gebaut, Tests grün.'))
elif tue == 'schreiben':  # wie ein Lauf mit Edit und Write, aber ohne Shell: Dateien ändern, nichts committen
    for name, inhalt in schritt.get('dateien', {'arbeit.txt': 'x\n'}).items():
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text(inhalt)
    ende(schritt.get('ergebnis', 'Geändert.'))
elif tue == 'ohne_commit':
    Path('lose.txt').write_text('lose\n')
    ende('fertig')
elif tue == 'frage':
    marker = schritt.get('marker') or (re.search(r'„(FRAGE AN [^“]*?:)“', ' '.join(argv)) or [None, 'FRAGE AN DEN INHABER:'])[1]  # so, wie der Auftrag es verlangt
    ende(marker + ' Soll die Kachel Celsius oder Fahrenheit zeigen?\n1. Celsius\n2. Fahrenheit\nEmpfehlung: Celsius')
elif tue == 'urteil':
    u = schritt['urteil']
    ende(json.dumps(u), struktur=u)
elif tue == 'text':
    ende(schritt['text'])
elif tue == 'kontingent':
    reset = int(time.time() + schritt.get('in', 1))
    if werkzeug == 'claude':
        aus({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': reset}})
        ende("You've hit your limit", fehler=True, code=1)
    aus({'type': 'error', 'message': 'usage limit reached', 'resets_at': reset})
    ende('usage limit reached', fehler=True, code=1)
elif tue == 'fehler':  # CLI scheitert mit der Meldung aus dem Drehbuch (z. B. abgelaufene Anmeldung)
    ende(schritt['text'], fehler=True, code=1)
elif tue == 'schlafen':
    time.sleep(schritt.get('s', 60))
    ende('ausgeschlafen')
else:
    print(f'Drehbuch leer oder unbekannt: {tue}', file=sys.stderr)
    sys.exit(3)
