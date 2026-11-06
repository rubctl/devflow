"""Gespräche in Text verwandeln und ablegen (S13.2): Audio lokal mit faster-whisper, Teams-Exporte (.vtt, .docx) mit Sprechern.

Aufruf über `ops/transkribieren.sh [<datei oder ordner>] [--rollen <toml>] [--modell <name>] [--material <ordner>] [--datum JJJJ-MM-TT]
[--offline] [--threads N]` oder `python -m devflow2.transkript …`. Nur Standardbibliothek; `faster_whisper` wird erst für Audio
importiert. Ablage: `<material>/interviews/<datum>-<nr>/transkript.md` und `quelle.<ext>`; das Original wandert erst aus dem Eingang,
wenn das Ergebnis vollständig angelegt ist. Siehe docs/discovery.md bzw. docs/installation.md.
"""
import datetime
import hashlib
import html
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib
import xml.etree.ElementTree as ET
import zipfile

from . import Fehler, schreiben

AUDIO = ('.m4a', '.mp3', '.wav', '.ogg')
TEAMS = ('.vtt', '.docx')
STANDARD_MODELL = 'small'
SPRACHE = 'de'
ZUSAMMENFASSEN = 60.0  # Sekunden: gleiche Sprecher in Folge werden bis zu dieser Länge zu einem Absatz
MAX_XML = 200 * 1024 * 1024  # Obergrenze für word/document.xml (Schutz vor Zip-Bomben)
SPRECHER_LEER = 'Sprecher'


# --- Segmente -----------------------------------------------------------------------------------------------

class Segment:
    """Ein Stück Gespräch: Beginn in Sekunden (oder None), Sprecher (oder None), Text."""
    __slots__ = ('start', 'sprecher', 'text')

    def __init__(self, start, sprecher, text):
        self.start, self.sprecher, self.text = start, sprecher, text

    def __eq__(self, other):
        return isinstance(other, Segment) and (self.start, self.sprecher, self.text) == (other.start, other.sprecher, other.text)

    def __repr__(self):
        return f'Segment({self.start!r}, {self.sprecher!r}, {self.text!r})'


def zeit(sekunden):
    s = int(sekunden)
    return f'{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}'


def _sekunden(text):
    """`H:MM:SS(.mmm)`, `MM:SS(.mmm)` oder `M:SS` → Sekunden."""
    teile = text.strip().replace(',', '.').split(':')
    zahl = 0.0
    for t in teile:
        zahl = zahl * 60 + float(t)
    return zahl


# --- VTT --------------------------------------------------------------------------------------------------------

_VTT_ZEIT = re.compile(r'^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})')
_VTT_STIMME = re.compile(r'<v(?:\.[^\s>]*)?\s+([^>]*)>')
_TAG = re.compile(r'<[^>]*>')


def _vtt_text(zeilen):
    """Cue-Text (mit `<v Name>`-Marken) → Liste (Sprecher, Text); ohne Marke ist der Sprecher None."""
    roh = ' '.join(z.strip() for z in zeilen if z.strip())
    stuecke, sprecher, pos = [], None, 0
    for m in _VTT_STIMME.finditer(roh):
        if roh[pos:m.start()].strip():
            stuecke.append((sprecher, roh[pos:m.start()]))
        sprecher, pos = html.unescape(m.group(1)).strip() or None, m.end()
    stuecke.append((sprecher, roh[pos:]))
    erg = []
    for wer, text in stuecke:
        text = re.sub(r'\s+', ' ', html.unescape(_TAG.sub('', text))).strip()
        if text:
            erg.append((wer, text))
    return erg


def vtt_lesen(text):
    """WebVTT (Teams-Export) → Segmente mit Sprechern. Kopf, NOTE-, STYLE- und REGION-Blöcke entfallen."""
    text = text.lstrip('﻿').replace('\r\n', '\n').replace('\r', '\n')
    if not text.lstrip().startswith('WEBVTT'):
        raise Fehler('Keine WebVTT-Datei (die erste Zeile muss WEBVTT lauten)')
    segmente = []
    for block in re.split(r'\n\s*\n', text):
        zeilen = block.split('\n')
        for i, z in enumerate(zeilen):
            m = _VTT_ZEIT.match(z)
            if m:
                start = _sekunden(m.group(1))
                for wer, t in _vtt_text(zeilen[i + 1:]):
                    segmente.append(Segment(start, wer, t))
                break
    if not segmente:
        raise Fehler('Die VTT-Datei enthält keinen Text')
    return zusammenfassen(segmente)


def zusammenfassen(segmente):
    """Aufeinanderfolgende Beiträge desselben Sprechers (höchstens ZUSAMMENFASSEN Sekunden ab Absatzbeginn) zu einem Absatz."""
    erg = []
    for s in segmente:
        if erg and s.sprecher is not None and erg[-1].sprecher == s.sprecher and s.start is not None and erg[-1].start is not None \
                and s.start - erg[-1].start < ZUSAMMENFASSEN:
            erg[-1].text += ' ' + s.text
        else:
            erg.append(Segment(s.start, s.sprecher, s.text))
    return erg


# --- DOCX ---------------------------------------------------------------------------------------------------------

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_ZEIT = r'\d{1,2}:\d{2}(?::\d{2})?'
_KOPF = [
    re.compile(rf'^(?P<n>.{{1,80}}?)\s+(?P<t>{_ZEIT})\s*$'),            # „Anna Muster  0:12“
    re.compile(rf'^(?P<t>{_ZEIT})\s*[-–—:]?\s+(?P<n>\S.{{0,79}})$'),    # „0:12 Anna Muster“ / „0:12 - Anna Muster“
]


def _docx_zeilen(pfad):
    """Alle Zeilen des Dokuments in Lesereihenfolge: ein Absatz oder Zeilenumbruch beginnt eine neue Zeile."""
    try:
        with zipfile.ZipFile(pfad) as z:
            try:
                info = z.getinfo('word/document.xml')
            except KeyError:
                raise Fehler('Keine Word-Datei (word/document.xml fehlt)') from None
            if info.file_size > MAX_XML:
                raise Fehler('Die Word-Datei ist unplausibel groß')
            wurzel = ET.fromstring(z.read(info))
    except zipfile.BadZipFile:
        raise Fehler('Keine gültige Word-Datei (kein ZIP)') from None
    except ET.ParseError as e:
        raise Fehler(f'Die Word-Datei ist beschädigt: {e}') from None
    zeilen = []
    for absatz in wurzel.iter(f'{_W}p'):
        aktuell = ['']
        for el in absatz.iter():
            if el.tag == f'{_W}t':
                aktuell[-1] += el.text or ''
            elif el.tag == f'{_W}tab':
                aktuell[-1] += ' '
            elif el.tag in (f'{_W}br', f'{_W}cr'):
                aktuell.append('')
        zeilen += [re.sub(r'\s+', ' ', z).strip() for z in aktuell]
    return [z for z in zeilen if z]


_PARTIKEL = {'van', 'von', 'vom', 'de', 'der', 'den', 'di', 'da', 'du', 'la', 'le', 'ter', 'zu', 'zur', 'dos', 'del', 'bin', 'al'}


def _name_moeglich(name):
    """Mehrwortige Namen beginnen mit Großbuchstaben (Partikel wie „van“ ausgenommen); „Schichtbeginn ist 6:00“ ist Text, kein Sprecher."""
    worte = name.split()
    return len(worte) <= 1 or all(not w[0].islower() or w.lower() in _PARTIKEL for w in worte)


def _kopf(zeile):
    for muster in _KOPF:
        m = muster.match(zeile)
        if m:
            name = m.group('n').strip(' -–—:')
            if name and not re.search(r'[.!?;]$', name) and _name_moeglich(name):
                return _sekunden(m.group('t')), name
    return None


def docx_lesen(pfad):
    """Teams-Transkript als Word → Segmente. Eine Kopfzeile „Name 0:12“ (oder „0:12 Name“) leitet einen Beitrag ein; die Zeile direkt
    danach ist immer Text. Ohne erkennbare Köpfe kommt der Text ohne Sprecher und Zeit heraus."""
    zeilen = _docx_zeilen(pfad)
    segmente, kopf, text = [], None, []

    def abschluss():
        if kopf and text:
            segmente.append(Segment(kopf[0], kopf[1], ' '.join(text)))
    nach_kopf = False
    for i, z in enumerate(zeilen):
        k = None if nach_kopf or i == len(zeilen) - 1 else _kopf(z)  # eine letzte Zeile ohne Text danach ist nie ein Kopf
        if k:
            abschluss()
            kopf, text, nach_kopf = k, [], True
        elif kopf:
            text.append(z)
            nach_kopf = False
    abschluss()
    if not segmente:
        if not zeilen:
            raise Fehler('Die Word-Datei enthält keinen Text')
        return [Segment(None, None, z) for z in zeilen]
    return zusammenfassen(segmente)


# --- Audio (faster-whisper) --------------------------------------------------------------------------------------

def home():
    return Path(os.environ.get('DEVFLOW2_HOME') or Path.home() / '.local/share/devflow2')


def modell_cache():
    return Path(os.environ.get('DEVFLOW2_WHISPER_CACHE') or home() / 'transkription' / 'modelle')


def whisper_erkenner(modell, offline=False, threads=0, meldung=print):
    """Lädt das Modell (vorhanden: ohne Netz; sonst einmalig aus dem Netz, außer mit `offline`) und liefert `erkennen(pfad)` → Segmente."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise Fehler('faster-whisper fehlt für Audio. Einrichten: ops/installieren.sh --mit-transkription '
                     '(Teams-Exporte .vtt/.docx gehen auch ohne)') from None
    cache = modell_cache()
    cache.mkdir(parents=True, exist_ok=True)
    args = dict(device='cpu', compute_type='int8', cpu_threads=threads, download_root=str(cache))
    try:
        m = WhisperModel(modell, local_files_only=True, **args)
    except Exception as e:
        if offline:
            raise Fehler(f'Modell „{modell}“ liegt nicht unter {cache} und --offline verbietet das Laden ({type(e).__name__})') from None
        meldung(f'Modell „{modell}“ ist noch nicht vorhanden: einmaliges Laden aus dem Netz (Hugging Face) nach {cache}. '
                'Danach läuft alles offline.')
        try:
            m = WhisperModel(modell, local_files_only=False, **args)
        except Exception as e2:
            raise Fehler(f'Modell „{modell}“ ließ sich nicht laden ({type(e2).__name__}: {e2})') from None

    def erkennen(pfad):
        segmente, info = m.transcribe(str(pfad), language=SPRACHE, vad_filter=True, beam_size=5)
        dauer, letzte, erg = getattr(info, 'duration', 0) or 0, -1, []
        for s in segmente:
            t = (s.text or '').strip()
            if t:
                erg.append(Segment(float(s.start), None, t))
            if dauer and int(s.end // 60) > letzte:
                letzte = int(s.end // 60)
                meldung(f'  … {zeit(s.end)} von {zeit(dauer)}')
        if not erg:
            raise Fehler('Whisper hat keine Sprache erkannt')
        return erg
    return erkennen


# --- Rollen -------------------------------------------------------------------------------------------------------

def rollen_lesen(pfad):
    """TOML mit Tabelle `[rollen]`: `"Name" = "Rolle"`. Ergebnis: Liste (Name, Rolle), längste Namen zuerst."""
    try:
        daten = tomllib.loads(Path(pfad).read_text(encoding='utf-8'))
    except OSError as e:
        raise Fehler(f'Rollen-Datei nicht lesbar: {e.strerror}') from None
    except tomllib.TOMLDecodeError as e:
        raise Fehler(f'Rollen-Datei ist kein gültiges TOML: {e}') from None
    tabelle = daten.get('rollen')
    if not isinstance(tabelle, dict) or not tabelle:
        raise Fehler('Rollen-Datei: Tabelle [rollen] mit Einträgen "Name" = "Rolle" fehlt')
    paare = []
    for name, rolle in tabelle.items():
        if not isinstance(rolle, str) or not rolle.strip() or not name.strip():
            raise Fehler(f'Rollen-Datei: „{name}“ braucht eine Rolle als nicht leeren Text')
        paare.append((name.strip(), rolle.strip()))
    return sorted(paare, key=lambda p: -len(p[0]))


def rollen_anwenden(segmente, paare):
    """Namen durch Rollen ersetzen, in Sprecherfeld und Text (ganze Wörter, ohne Rücksicht auf Groß-/Kleinschreibung; ein angehängtes „s“
    wie im Genitiv bleibt an der Rolle). Sprecher ohne Rolle werden „Sprecher 1“, „Sprecher 2“ … (nach Auftreten), damit mit `--rollen`
    nie ein Name aus dem Sprecherfeld stehen bleibt. Gibt (Segmente, Anzahl unbekannter Sprecher) zurück."""
    rolle = {n.casefold(): r for n, r in paare}
    muster = re.compile(r'(?<!\w)(' + '|'.join(re.escape(n) for n, _ in paare) + r')(s?)(?!\w)', re.IGNORECASE)  # paare: längste Namen zuerst
    nummern = {}

    def ersetzen(text):
        return muster.sub(lambda m: rolle[m.group(1).casefold()] + m.group(2), text)
    erg = []
    for s in segmente:
        wer = s.sprecher
        if wer is not None:
            if wer.casefold() in rolle:
                wer = rolle[wer.casefold()]
            else:
                ersetzt = ersetzen(wer)
                wer = f'{SPRECHER_LEER} {nummern.setdefault(wer, len(nummern) + 1)}' if ersetzt == wer else ersetzt
        erg.append(Segment(s.start, wer, ersetzen(s.text)))
    return erg, len(nummern)


# --- Ausgabe und Ablage ---------------------------------------------------------------------------------------------

def rollen_summe(paare):
    """Prüfsumme der Rollen-Zuordnung (None ohne Rollen); steht im Kopf, damit eine geänderte Datei erkannt wird."""
    return hashlib.sha256(repr(sorted(paare)).encode('utf-8')).hexdigest() if paare else None


def markdown(kennung, art, quelle, pruefsumme, segmente, datum, rollen=None, hinweis=None):
    kopf = [f'# Transkript {kennung}', '', f'- Art: {art}', f'- Quelle: `{quelle}`', f'- Prüfsumme: sha256:{pruefsumme}', f'- Datum: {datum}',
            '- Sprecher: ' + ('durch Rollen ersetzt' if rollen else ('aus der Quelle' if any(s.sprecher for s in segmente) else 'nicht unterschieden'))]
    if rollen:
        kopf.append(f'- Rollen: sha256:{rollen}')
    if hinweis:
        kopf.append(f'- Hinweis: {hinweis}')
    zeilen = []
    for s in segmente:
        marke = f'[{zeit(s.start)}]' if s.start is not None else ''
        zeilen.append(f'**{f"{marke} {s.sprecher}".strip()}:** {s.text}' if s.sprecher else f'{marke} {s.text}'.strip())
    return '\n'.join(kopf) + '\n\n---\n\n' + '\n\n'.join(zeilen) + '\n'


def pruefsumme(pfad):
    h = hashlib.sha256()
    with open(pfad, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


_KENNUNG = re.compile(r'^(\d{4}-\d{2}-\d{2})-(\d{2,})$')
_SUMME = re.compile(r'^- Prüfsumme: sha256:([0-9a-f]{64})$', re.M)
_ROLLEN = re.compile(r'^- Rollen: sha256:([0-9a-f]{64})$', re.M)


def uebernommen(interviews, summe, groesse, rsumme=None):
    """(Kennung, aktuell) des Gesprächs, das diese Quelle schon enthält (gleiche Prüfsumme, `quelle.*` gleich groß vorhanden), sonst None.
    `aktuell` ist falsch, wenn jetzt andere Rollen gelten als im Transkript (ohne Rollen im Aufruf ist es immer aktuell)."""
    if not interviews.is_dir():
        return None
    for ordner in sorted(interviews.iterdir()):
        t = ordner / 'transkript.md'
        if not _KENNUNG.match(ordner.name) or not t.is_file():
            continue
        kopf = t.read_text(encoding='utf-8', errors='replace')[:2000]
        m = _SUMME.search(kopf)
        if m and m.group(1) == summe and any(q.stat().st_size == groesse for q in ordner.glob('quelle.*')):
            r = _ROLLEN.search(kopf)
            return ordner.name, rsumme is None or (r is not None and r.group(1) == rsumme)
    return None


def naechste_kennung(interviews, datum):
    nummern = [int(m.group(2)) for p in interviews.iterdir() if (m := _KENNUNG.match(p.name)) and m.group(1) == datum] if interviews.is_dir() else []
    return f'{datum}-{max(nummern, default=0) + 1:02d}'


def aufraeumen(interviews):
    """Halbfertige Ablagen früherer Abbrüche (`.teil-*`) entfernen; sie enthalten nur Kopien, das Original liegt noch im Eingang."""
    if interviews.is_dir():
        for p in interviews.glob('.teil-*'):
            shutil.rmtree(p, ignore_errors=True)


def ablegen(quelle, interviews, datum, art, segmente, rollen=None, hinweis=None):
    """Transkript und Kopie des Originals vollständig in einem Hilfsordner anlegen, ihn umbenennen, erst dann das Original aus dem Eingang
    nehmen. Bricht etwas ab, bleibt das Original liegen; eine zweite Ausführung erkennt eine schon fertige Ablage an der Prüfsumme.
    `rollen` ist die Rollen-Prüfsumme oder None. Gibt (Kennung, Zustand) zurück: übernommen, schon übernommen oder aktualisiert
    (gleiche Quelle mit geänderten Rollen: nur `transkript.md` wird atomar ersetzt)."""
    summe, groesse = pruefsumme(quelle), quelle.stat().st_size
    schon = uebernommen(interviews, summe, groesse, rollen)
    if schon:
        kennung, aktuell = schon
        if not aktuell:
            schreiben(interviews / kennung / 'transkript.md',
                      markdown(kennung, art, f'quelle{quelle.suffix.lower()}', summe, segmente, kennung[:10], rollen, hinweis))
        quelle.unlink()
        return kennung, 'schon übernommen' if aktuell else 'aktualisiert'
    interviews.mkdir(parents=True, exist_ok=True)
    ext = quelle.suffix.lower()
    for _ in range(20):
        kennung = naechste_kennung(interviews, datum)
        teil = interviews / f'.teil-{kennung}'
        shutil.rmtree(teil, ignore_errors=True)
        teil.mkdir()
        try:
            shutil.copy2(quelle, teil / f'quelle{ext}')
            if pruefsumme(teil / f'quelle{ext}') != summe:
                raise Fehler('Die Kopie des Originals stimmt nicht mit dem Original überein')
            schreiben(teil / 'transkript.md', markdown(kennung, art, f'quelle{ext}', summe, segmente, datum, rollen, hinweis))
            ziel = interviews / kennung
            if ziel.exists():  # ein anderer Lauf war schneller: nächste Nummer
                shutil.rmtree(teil, ignore_errors=True)
                continue
            os.rename(teil, ziel)
        except BaseException:
            shutil.rmtree(teil, ignore_errors=True)
            raise
        quelle.unlink()
        return kennung, 'übernommen'
    raise Fehler('Keine freie Nummer für das Gespräch gefunden')


# --- Ablauf ---------------------------------------------------------------------------------------------------------

def dateien(pfad):
    """Datei oder Ordner (nicht rekursiv) → (zu verarbeiten, übersprungen). Versteckte Dateien und Word-Sperrdateien (~$…) zählen nicht."""
    pfad = Path(pfad)
    if pfad.is_file():
        if pfad.suffix.lower() not in AUDIO + TEAMS:
            raise Fehler(f'{pfad.name}: Art nicht unterstützt (Audio {", ".join(AUDIO)}; Teams {", ".join(TEAMS)})')
        return [pfad], []
    if not pfad.is_dir():
        raise Fehler(f'Nicht gefunden: {pfad}')
    gut, uebrig = [], []
    for p in sorted(pfad.iterdir()):
        if not p.is_file() or p.name.startswith(('.', '~$')):
            continue
        (gut if p.suffix.lower() in AUDIO + TEAMS else uebrig).append(p)
    return gut, uebrig


def verarbeiten(pfad, material, datum=None, modell=STANDARD_MODELL, rollen=None, erkenner=None, offline=False, threads=0, ausgabe=print):
    """Alles unter `pfad` übernehmen. `erkenner(pfad)` → Segmente ersetzt Whisper (Tests); sonst wird faster-whisper beim ersten Audio geladen.
    Gibt (Ergebnisse, Fehlerzahl) zurück; Ergebnisse sind Tupel (Datei, Kennung oder None, Meldung)."""
    datum = datum or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(datum)
    except ValueError:
        raise Fehler(f'Datum „{datum}“ ist nicht JJJJ-MM-TT') from None
    paare = rollen_lesen(rollen) if rollen else None
    rsumme = rollen_summe(paare)
    liste, uebrig = dateien(pfad)
    interviews = Path(material) / 'interviews'
    aufraeumen(interviews)
    ergebnisse, fehler = [], 0
    for p in uebrig:
        ausgabe(f'übersprungen (keine Audio- oder Teams-Datei): {p.name}')
    if not liste:
        ausgabe('Nichts zu übernehmen')
    for p in liste:
        try:
            if interviews.resolve() in p.resolve().parents:
                raise Fehler('liegt schon in der Ablage (material/interviews); als Eingabe nur Dateien außerhalb angeben')
            ext = p.suffix.lower()
            hinweis = None
            if ext == '.vtt':
                art, segmente = 'Teams-Export (VTT)', vtt_lesen(p.read_text(encoding='utf-8-sig', errors='replace'))
            elif ext == '.docx':
                art, segmente = 'Teams-Export (Word)', docx_lesen(p)
                if all(s.start is None for s in segmente):
                    hinweis = 'Sprecher und Zeitmarken wurden im Word-Dokument nicht erkannt'
            else:
                schon = uebernommen(interviews, pruefsumme(p), p.stat().st_size, rsumme)
                if schon and schon[1]:  # nicht noch einmal stundenlang rechnen
                    p.unlink()
                    ausgabe(f'{p.name}: schon übernommen als {schon[0]}')
                    ergebnisse.append((p, schon[0], 'schon übernommen'))
                    continue
                if erkenner is None:
                    erkenner = whisper_erkenner(modell, offline, threads, ausgabe)
                ausgabe(f'{p.name}: Transkription ({modell}, Deutsch) …')
                art, segmente = f'Audio, lokal transkribiert (faster-whisper, Modell {modell}, Deutsch)', erkenner(p)
            unbekannt = 0
            if paare:
                segmente, unbekannt = rollen_anwenden(segmente, paare)
            kennung, meldung = ablegen(p, interviews, datum, art, segmente, rsumme, hinweis)
            ausgabe(f'{p.name} → {kennung} ({meldung})')
            if unbekannt:
                ausgabe(f'  {unbekannt} Sprecher ohne Rolle, als „{SPRECHER_LEER} N“ eingetragen (Namen in --rollen ergänzen und neu aufrufen)')
            ergebnisse.append((p, kennung, meldung))
        except Fehler as e:
            fehler += 1
            ausgabe(f'FEHLER {p.name}: {e} (Original bleibt liegen)')
            ergebnisse.append((p, None, str(e)))
        except OSError as e:
            fehler += 1
            ausgabe(f'FEHLER {p.name}: {e.strerror or e} (Original bleibt liegen)')
            ergebnisse.append((p, None, str(e)))
        except Exception as e:  # z. B. ValueError aus dem Erkenner: diese Datei scheitert, die anderen laufen weiter
            fehler += 1
            ausgabe(f'FEHLER {p.name}: {type(e).__name__}: {e} (Original bleibt liegen)')
            ergebnisse.append((p, None, str(e)))
    return ergebnisse, fehler


AUFRUF = ('Aufruf: ops/transkribieren.sh [<datei oder ordner>] [--rollen <toml>] [--modell <name>] [--material <ordner>] '
          '[--datum JJJJ-MM-TT] [--offline] [--threads N]\n  Standard: material/neu/ → material/interviews/<datum>-<nr>/')


def main(argv):
    pfad, optionen, i = None, {}, 0
    mit_wert = {'--rollen': 'rollen', '--modell': 'modell', '--material': 'material', '--datum': 'datum', '--threads': 'threads'}
    while i < len(argv):
        a = argv[i]
        if a in mit_wert and i + 1 < len(argv):
            optionen[mit_wert[a]] = argv[i + 1]
            i += 2
        elif a == '--offline':
            optionen['offline'] = True
            i += 1
        elif a in ('-h', '--help'):
            print(AUFRUF)
            return 0
        elif not a.startswith('--') and pfad is None:
            pfad, i = a, i + 1
        else:
            print(AUFRUF, file=sys.stderr)
            return 64
    try:
        threads = int(optionen.get('threads', 0))
    except ValueError:
        print('--threads braucht eine Zahl', file=sys.stderr)
        return 64
    material = optionen.get('material', 'material')
    try:
        _, fehler = verarbeiten(pfad or str(Path(material) / 'neu'), material, optionen.get('datum'),
                                optionen.get('modell') or os.environ.get('DEVFLOW2_WHISPER_MODELL') or STANDARD_MODELL,
                                optionen.get('rollen'), None, optionen.get('offline', False), threads)
    except Fehler as e:
        print(f'FEHLER: {e}', file=sys.stderr)
        return 1
    return 1 if fehler else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
