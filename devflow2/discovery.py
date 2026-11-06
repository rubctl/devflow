"""Discovery-Katalog (S13.1): Prozesse und äußere Faktoren als TOML im Projekt, Prüfung und Übersicht.

Aufbau im Projekt:
    discovery/katalog/<bereich>.toml   [bereich], [[gruppe]], [[prozess]]
    discovery/faktoren.toml            [[faktor]]
    discovery/fragen.md                offene Fragen als `- [ ] … (an <Rolle>)`, beantwortete als `- [x] … → Antwort`
    discovery/<beschreibung>.md        Prozessbeschreibungen (Pfad steht im Prozess unter `beschreibung`)

Nur Standardbibliothek (tomllib). Aufruf: python -m devflow2 discovery pruefen|stand [<pfad>]
"""
import datetime
import re
import tomllib
from pathlib import Path

from . import Fehler

STAENDE = ('erfasst', 'Ist aufgenommen', 'Ist vertieft', 'Soll entworfen', 'Soll abgestimmt', 'im Prototyp',
           'freigegeben', 'in Umsetzung', 'umgesetzt')
SOLL_WERTE = ('offen', 'neu', 'bleibt', 'ändert sich', 'entfällt', 'zusammengelegt mit <id>', 'außerhalb')
ARTEN = ('Vorschrift', 'Kunde', 'Partner', 'System', 'Markt/Saison', 'Organisation')
PRIORITAETEN = ('hoch', 'mittel', 'niedrig')
ABSCHNITTE = ('Ist', 'Soll', 'Lücke Ist → Soll', 'Äußere Faktoren', 'Quellen')

_BESCHREIBUNG_AB = STAENDE.index('Ist vertieft')  # ab hier gehört eine Beschreibung dazu
_QUELLEN_AB = STAENDE.index('Ist aufgenommen')  # ab hier muss gesagt sein, woher das Ist stammt
_SOLL_AB = STAENDE.index('Soll entworfen')  # ab hier sollte `soll` nicht mehr `offen` sein
_KURZ_BIS = STAENDE.index('Soll abgestimmt')  # bis hier ersetzen Einzeiler die Beschreibungsdatei
SOLL_KURZ_GENUEG = ('bleibt', 'entfällt', 'außerhalb')  # (und „zusammengelegt mit …“): soll_kurz allein begründet
_ZUSAMMEN = re.compile(r'zusammengelegt mit (\S+)')
_FRAGE = re.compile(r'^\s*[-*]\s+\[( |x|X)\]\s*(.*)$')
_FRAGE_KOPF = re.compile(r'^\s*[-*]\s+\[')
_ADRESSAT = re.compile(r'\s*\(an\s+([^()]*?)\s*\)\s*$')
_KENNUNG = re.compile(r'\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*\b')

PROZESS_FELDER = ('id', 'name', 'gruppe', 'stand', 'soll', 'prioritaet', 'haeufigkeit', 'faktoren', 'quellen', 'beschreibung',
                 'ist_kurz', 'soll_kurz')
FAKTOR_FELDER = ('id', 'art', 'name', 'quelle', 'aendert_sich')


def _liste(werte):
    return ', '.join(f'„{w}“' for w in werte)


def _zahl(n, einzahl, mehrzahl):
    return f'{n} {einzahl if n == 1 else mehrzahl}'


def _datum(d):
    return d.strftime('%d.%m.%Y')


def _stichtag(wert):
    """(Datum oder None, Text). ValueError, wenn es nach Datum aussieht, aber keins ist."""
    if isinstance(wert, datetime.datetime):
        return wert.date(), ''
    if isinstance(wert, datetime.date):
        return wert, ''
    s = str(wert).strip()
    if not s:
        return None, ''
    for muster, reihe in ((r'(\d{1,2})\.(\d{1,2})\.(\d{4})', (2, 1, 0)), (r'(\d{4})-(\d{1,2})-(\d{1,2})', (0, 1, 2))):
        m = re.fullmatch(muster, s)
        if m:
            teile = [int(m.group(i + 1)) for i in range(3)]
            return datetime.date(teile[reihe[0]], teile[reihe[1]], teile[reihe[2]]), ''
    if re.fullmatch(r'[\d.\-/ ]+', s):
        raise ValueError(s)
    return None, s


class Katalog:
    """Gelesener Katalog samt Meldungen. Lesen bricht nie wegen Inhaltsfehlern ab, sondern sammelt sie."""

    def __init__(self, ordner):
        self.ordner = Path(ordner)
        self.wurzel = self.ordner.parent
        self.meldungen = []  # (stufe, datei, kennung, text), stufe 'Fehler', 'Warnung' oder 'Hinweis'
        self.bereiche = []  # {'id', 'name', 'datei', 'gruppen': [ids]}
        self.prozesse = []  # Felder des Prozesses plus 'datei', 'bereich', 'nr'
        self.faktoren = []
        self.fragen = []  # (offen, text)
        self._laden()

    # --- Meldungen
    def _fehler(self, datei, kennung, text):
        self.meldungen.append(('Fehler', datei, kennung, text))

    def _warnung(self, datei, kennung, text):
        self.meldungen.append(('Warnung', datei, kennung, text))

    def _hinweis(self, datei, kennung, text):
        self.meldungen.append(('Hinweis', datei, kennung, text))

    def fehler(self):
        return [m for m in self.meldungen if m[0] == 'Fehler']

    def warnungen(self):
        return [m for m in self.meldungen if m[0] == 'Warnung']

    def hinweise(self):
        return [m for m in self.meldungen if m[0] == 'Hinweis']

    @staticmethod
    def zeile(meldung):
        stufe, datei, kennung, text = meldung
        ort = f'{datei} · {kennung}' if kennung else datei
        return f'{stufe} · {ort}: {text}'

    # --- Lesen
    def _rel(self, pfad):
        return Path(pfad).relative_to(self.wurzel).as_posix()

    def _toml(self, pfad):
        datei = self._rel(pfad)
        try:
            text = Path(pfad).read_text(encoding='utf-8')
            return tomllib.loads(text), text
        except tomllib.TOMLDecodeError as e:
            self._fehler(datei, '', f'kein gültiges TOML ({e})')
        except (OSError, UnicodeDecodeError) as e:
            self._fehler(datei, '', f'nicht lesbar ({e})')
        return None, ''

    def _text(self, tab, schluessel, datei, kennung, pflicht=True):
        wert = tab.get(schluessel)
        if wert is None or wert == '':
            if pflicht:
                self._fehler(datei, kennung, f'Feld „{schluessel}“ fehlt oder ist leer.')
            return None
        if not isinstance(wert, str):
            self._fehler(datei, kennung, f'Feld „{schluessel}“ muss Text sein.')
            return None
        return wert.strip()

    def _textliste(self, tab, schluessel, datei, kennung):
        wert = tab.get(schluessel, [])
        if not isinstance(wert, list) or not all(isinstance(x, str) and x.strip() for x in wert):
            self._fehler(datei, kennung, f'Feld „{schluessel}“ muss eine Liste von Texten sein, z. B. {schluessel} = ["…"].')
            return []
        return [x.strip() for x in wert]

    def _tabellen(self, daten, schluessel, datei):
        wert = daten.get(schluessel, [])
        if not isinstance(wert, list) or not all(isinstance(x, dict) for x in wert):
            self._fehler(datei, '', f'„{schluessel}“ muss als [[{schluessel}]] geschrieben sein.')
            return []
        return wert

    def _unbekannt(self, tab, erlaubt, datei, kennung):
        for k in tab:
            if k not in erlaubt:
                self._warnung(datei, kennung, f'unbekanntes Feld „{k}“ (bekannt: {", ".join(erlaubt)}).')

    def _laden(self):
        katalog = self.ordner / 'katalog'
        dateien = sorted(katalog.glob('*.toml')) if katalog.is_dir() else []
        if not dateien:
            self._warnung(self._rel(katalog), '', 'keine Bereichsdatei (<bereich>.toml) gefunden.')
        for pfad in dateien:
            self._bereich(pfad)
        self._faktoren()
        self._fragen()
        self._verweise()

    def _bereich(self, pfad):
        datei = self._rel(pfad)
        daten, text = self._toml(pfad)
        if daten is None:
            return
        kopf = daten.get('bereich')
        if not isinstance(kopf, dict):
            self._fehler(datei, '', 'Abschnitt [bereich] fehlt.')
            kopf = {}
        bid = self._text(kopf, 'id', datei, 'bereich') or pfad.stem.upper()
        self._text(kopf, 'name', datei, bid)
        bereich = {'id': bid, 'name': kopf.get('name') or bid, 'datei': datei, 'gruppen': []}
        for vorhanden in self.bereiche:
            if vorhanden['id'] == bid:
                self._fehler(datei, bid, f'Bereich „{bid}“ gibt es schon in {vorhanden["datei"]}.')
        self.bereiche.append(bereich)
        for g in self._tabellen(daten, 'gruppe', datei):
            gid = self._text(g, 'id', datei, 'gruppe')
            self._text(g, 'name', datei, gid or 'gruppe')
            if gid in bereich['gruppen']:
                self._fehler(datei, gid, f'Gruppe „{gid}“ kommt in dieser Datei zweimal vor.')
            elif gid:
                bereich['gruppen'].append(gid)
        for nr, p in enumerate(self._tabellen(daten, 'prozess', datei), 1):
            self._prozess(p, nr, bereich, text)

    def _prozess(self, p, nr, bereich, text):
        datei = bereich['datei']
        pid = self._text(p, 'id', datei, f'Prozess Nr. {nr}')
        kennung = pid or f'Prozess Nr. {nr}'
        self._unbekannt(p, PROZESS_FELDER, datei, kennung)
        schon = sum(1 for q in self.prozesse if q['datei'] == datei and q['id'] == pid)
        eintrag = {'id': pid, 'datei': datei, 'bereich': bereich['id'], 'nr': nr, 'zeile': _zeile_von(text, pid, schon),
                   'name': self._text(p, 'name', datei, kennung) or '', 'gruppe': self._text(p, 'gruppe', datei, kennung)}
        if pid and not pid.startswith(bereich['id'] + '-'):
            self._warnung(datei, kennung, f'ID beginnt nicht mit der Bereichs-ID „{bereich["id"]}-“.')
        if eintrag['gruppe'] and eintrag['gruppe'] not in bereich['gruppen']:
            self._fehler(datei, kennung, f'Gruppe „{eintrag["gruppe"]}“ ist in dieser Datei nicht als [[gruppe]] angelegt'
                         + (f' (vorhanden: {", ".join(bereich["gruppen"])}).' if bereich['gruppen'] else ' (keine Gruppe angelegt).'))
        stand = self._text(p, 'stand', datei, kennung)
        if stand is not None and stand not in STAENDE:
            self._fehler(datei, kennung, f'Stand „{stand}“ ist nicht erlaubt. Erlaubt: {_liste(STAENDE)}.')
            stand = None
        eintrag['stand'] = stand
        soll = self._text(p, 'soll', datei, kennung)
        eintrag['soll'] = soll
        eintrag['zusammen'] = None
        if soll is not None and (soll not in SOLL_WERTE or soll.startswith('zusammengelegt')):
            # der wörtliche Platzhalter „zusammengelegt mit <id>“ ist kein Wert, sondern läuft durch die Zielprüfung
            m = _ZUSAMMEN.fullmatch(soll)
            if m:
                eintrag['zusammen'] = m.group(1)
            else:
                self._fehler(datei, kennung, f'Soll „{soll}“ ist nicht erlaubt. Erlaubt: {_liste(SOLL_WERTE)}.')
                eintrag['soll'] = None
        prio = self._text(p, 'prioritaet', datei, kennung, pflicht=False)
        if prio is not None and prio not in PRIORITAETEN:
            self._fehler(datei, kennung, f'Priorität „{prio}“ ist nicht erlaubt. Erlaubt: {_liste(PRIORITAETEN)}.')
        eintrag['prioritaet'] = prio
        eintrag['haeufigkeit'] = self._text(p, 'haeufigkeit', datei, kennung, pflicht=False)
        eintrag['faktoren'] = self._textliste(p, 'faktoren', datei, kennung)
        eintrag['quellen'] = self._textliste(p, 'quellen', datei, kennung)
        eintrag['beschreibung'] = self._text(p, 'beschreibung', datei, kennung, pflicht=False)
        eintrag['ist_kurz'] = self._text(p, 'ist_kurz', datei, kennung, pflicht=False)
        eintrag['soll_kurz'] = self._text(p, 'soll_kurz', datei, kennung, pflicht=False)
        self.prozesse.append(eintrag)
        self._beschreibung(eintrag, kennung)

    @staticmethod
    def _kurz_genuegt(p, stufe):
        """Einzeiler ersetzen die Beschreibung bis „Soll abgestimmt“; ab „im Prototyp“ nicht mehr."""
        if stufe > _KURZ_BIS:
            return False
        if p['ist_kurz'] and p['soll_kurz']:
            return True
        return bool(p['soll_kurz']) and (p['soll'] in SOLL_KURZ_GENUEG or bool(p['zusammen']))

    def _beschreibung(self, p, kennung):
        stufe = STAENDE.index(p['stand']) if p['stand'] else -1
        pfad = p['beschreibung']
        if not pfad:
            if stufe >= _BESCHREIBUNG_AB and not self._kurz_genuegt(p, stufe):
                hinweis = ('Feld „beschreibung“ mit Pfad relativ zu discovery/'
                           + ('' if stufe > _KURZ_BIS else ', oder die Einzeiler „ist_kurz“ und „soll_kurz“'
                              ' (bei Soll bleibt/entfällt/außerhalb/zusammengelegt genügt „soll_kurz“)'))
                self._warnung(p['datei'], kennung, f'Stand „{p["stand"]}“, aber keine Beschreibung ({hinweis}).')
            return
        ziel = (self.ordner / pfad).resolve()
        if not ziel.is_relative_to(self.ordner.resolve()):
            self._fehler(p['datei'], kennung, f'Beschreibung „{pfad}“ liegt außerhalb von discovery/.')
            return
        if not ziel.is_file():
            self._fehler(p['datei'], kennung, f'Beschreibung „{pfad}“ gibt es nicht (Pfad relativ zu discovery/).')
            return
        try:
            inhalt = ziel.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError) as e:
            self._fehler(p['datei'], kennung, f'Beschreibung „{pfad}“ nicht lesbar ({e}).')
            return
        titel = {re.sub(r'\s+', ' ', t.replace('->', '→')).strip().lower()
                 for t in re.findall(r'^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$', inhalt, re.M)}
        fehlt = [a for a in ABSCHNITTE if a.lower() not in titel]
        if fehlt:
            self._warnung(p['datei'], kennung, f'Beschreibung „{pfad}“ hat keine Überschrift für: {", ".join(fehlt)}.')

    def _faktoren(self):
        pfad = self.ordner / 'faktoren.toml'
        if not pfad.is_file():
            self._warnung(self._rel(pfad), '', 'Datei fehlt (Faktoren stehen in discovery/faktoren.toml).')
            return
        datei = self._rel(pfad)
        daten, text = self._toml(pfad)
        if daten is None:
            return
        for nr, f in enumerate(self._tabellen(daten, 'faktor', datei), 1):
            fid = self._text(f, 'id', datei, f'Faktor Nr. {nr}')
            kennung = fid or f'Faktor Nr. {nr}'
            self._unbekannt(f, FAKTOR_FELDER, datei, kennung)
            art = self._text(f, 'art', datei, kennung)
            if art is not None and art not in ARTEN:
                self._fehler(datei, kennung, f'Art „{art}“ ist nicht erlaubt. Erlaubt: {_liste(ARTEN)}.')
            name = self._text(f, 'name', datei, kennung)
            quelle = self._text(f, 'quelle', datei, kennung, pflicht=False)
            stichtag, hinweis = None, ''
            wert = f.get('aendert_sich')
            if wert is not None:
                try:
                    stichtag, hinweis = _stichtag(wert)
                except ValueError:
                    self._fehler(datei, kennung, f'„aendert_sich“ ({wert!r}) ist kein Datum; erwartet TT.MM.JJJJ, JJJJ-MM-TT '
                                 'oder ein Text ohne Zahlenreihe.')
            schon = [v for v in self.faktoren if fid and v['id'] == fid]
            zeile = _zeile_von(text, fid, len(schon))
            for vorhanden in schon:
                self._fehler(datei, fid, f'Faktor-ID „{fid}“ kommt zweimal vor: {_ort(vorhanden)} und '
                             f'{_ort({"datei": datei, "zeile": zeile, "nr": nr})}.')
            self.faktoren.append({'id': fid, 'art': art, 'name': name or '', 'quelle': quelle, 'datei': datei,
                                  'zeile': zeile, 'nr': nr, 'stichtag': stichtag, 'hinweis': hinweis})

    def _fragen(self):
        pfad = self.ordner / 'fragen.md'
        if not pfad.is_file():
            self._warnung(self._rel(pfad), '', 'Datei fehlt (offene Fragen stehen in discovery/fragen.md).')
            return
        datei = self._rel(pfad)
        try:
            text = pfad.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError) as e:
            self._fehler(datei, '', f'nicht lesbar ({e})')
            return
        # HTML-Kommentare (Beispiele in den Vorlagen) zählen nicht; Zeilenzahl bleibt für die Meldungen gleich
        zeilen = re.sub(r'<!--.*?-->', lambda m: '\n' * m.group().count('\n'), text, flags=re.S).splitlines()
        for nr, z in enumerate(zeilen, 1):
            m = _FRAGE.match(z)
            if m:
                self.fragen.append((m.group(1) == ' ', m.group(2).strip()))
                if m.group(1) == ' ' and self.adressat(m.group(2))[1] is None:
                    self._hinweis(datei, f'Zeile {nr}', 'offene Frage ohne Adressat; am Ende „(an <Rolle>)“ ergänzen, '
                                  'damit klar ist, wen man fragen soll.')
            elif _FRAGE_KOPF.match(z):
                self._fehler(datei, f'Zeile {nr}', 'Frage ohne gültiges Kästchen; erwartet „- [ ] Text“ (offen) oder „- [x] Text“ (beantwortet).')

    def _verweise(self):
        """Eindeutigkeit und Verweise über alle Dateien."""
        erste = {}
        for p in self.prozesse:
            if not p['id']:
                continue
            if p['id'] in erste:
                a = erste[p['id']]
                self._fehler(p['datei'], p['id'], f'ID „{p["id"]}“ kommt zweimal vor: {_ort(a)} und {_ort(p)}.')
            else:
                erste[p['id']] = p
        faktoren = {f['id'] for f in self.faktoren if f['id']}
        for p in self.prozesse:
            kennung = p['id'] or f'Prozess Nr. {p["nr"]}'
            for fid in p['faktoren']:
                if fid not in faktoren:
                    self._fehler(p['datei'], kennung, f'Faktor „{fid}“ gibt es nicht in discovery/faktoren.toml.')
            ziel = p['zusammen']
            if ziel:
                if ziel == p['id']:
                    self._fehler(p['datei'], kennung, 'kann nicht mit sich selbst zusammengelegt sein.')
                elif ziel not in erste:
                    self._fehler(p['datei'], kennung, f'zusammengelegt mit „{ziel}“, aber diesen Prozess gibt es nicht.')
                elif erste[ziel]['zusammen']:
                    self._fehler(p['datei'], kennung, f'zusammengelegt mit „{ziel}“, das selbst mit „{erste[ziel]["zusammen"]}“ '
                                 'zusammengelegt ist; bitte auf das endgültige Ziel verweisen.')
            stufe = STAENDE.index(p['stand']) if p['stand'] else -1
            if stufe >= _QUELLEN_AB and not p['quellen']:
                self._warnung(p['datei'], kennung, f'Stand „{p["stand"]}“, aber keine Quellen (z. B. Interviewnummer).')
            if stufe >= _SOLL_AB and p['soll'] == 'offen':
                self._warnung(p['datei'], kennung, f'Stand „{p["stand"]}“, aber Soll steht noch auf „offen“.')
        genutzt = {fid for p in self.prozesse for fid in p['faktoren']}
        for f in self.faktoren:
            if f['id'] and f['id'] not in genutzt:
                self._warnung(f['datei'], f['id'], 'wird von keinem Prozess genannt.')

    # --- Auswertung
    def betroffene(self):
        """Faktor-ID -> IDs der Prozesse, die ihn nennen (in Katalogreihenfolge)."""
        ergebnis = {f['id']: [] for f in self.faktoren if f['id']}
        for p in self.prozesse:
            for fid in p['faktoren']:
                if fid in ergebnis and (p['id'] or '?') not in ergebnis[fid]:
                    ergebnis[fid].append(p['id'] or '?')
        return ergebnis

    def offene_fragen(self):
        return [t for offen, t in self.fragen if offen]

    @staticmethod
    def adressat(text):
        """(Frage ohne Adressat-Zusatz, Rolle oder None); die Rolle steht am Ende als „(an <Rolle>)“."""
        m = _ADRESSAT.search(text)
        if m and m.group(1):
            return text[:m.start()].rstrip(), m.group(1)
        return text.strip(), None

    def offene_nach_adressat(self):
        """[(Rolle oder None, [Fragen ohne Adressat-Zusatz])]: Rollen alphabetisch, „ohne Adressat“ (None) zuletzt."""
        gruppen = {}
        for t in self.offene_fragen():
            frage, rolle = self.adressat(t)
            gruppen.setdefault(rolle, []).append(frage)
        rollen = sorted((r for r in gruppen if r), key=lambda r: (r.casefold(), r))
        return [(r, gruppen[r]) for r in rollen] + ([(None, gruppen[None])] if None in gruppen else [])

    def frage_bereich(self, text):
        """Bereichs-ID der ersten Prozess- oder Bereichskennung in der Frage, sonst None."""
        prozesse = {p['id']: p['bereich'] for p in self.prozesse if p['id']}
        bereiche = {b['id'] for b in self.bereiche}
        for k in _KENNUNG.findall(self.adressat(text)[0]):
            if k in prozesse:
                return prozesse[k]
            if k in bereiche:
                return k
        return None


def _zeile_von(text, kennung, n=0):
    """Zeile des n-ten (ab 0) `id = "<kennung>"` im Text; None, wenn es nicht zu finden ist."""
    if not kennung:
        return None
    treffer = list(re.finditer(r'^[ \t]*id[ \t]*=[ \t]*["\']' + re.escape(kennung) + r'["\']', text, re.M))
    return text.count('\n', 0, treffer[n].start()) + 1 if n < len(treffer) else None


def _ort(p):
    return f'{p["datei"]}, ' + (f'Zeile {p["zeile"]}' if p.get('zeile') else f'Eintrag Nr. {p["nr"]}')


def ordner(pfad=None):
    """Der Ordner discovery/ zu einem Projektpfad (oder der Ordner selbst)."""
    p = Path(pfad or '.').resolve()
    if (p / 'discovery').is_dir():
        return p / 'discovery'
    if p.name == 'discovery' and p.is_dir():
        return p
    raise Fehler(f'{pfad or "."}: kein Ordner discovery/ gefunden (erwartet discovery/katalog/<bereich>.toml, '
                 'discovery/faktoren.toml und discovery/fragen.md)')


def laden(pfad=None):
    return Katalog(ordner(pfad))


def pruefen(pfad=None):
    """Prüft den Katalog; gibt (Katalog, Text, Exitcode) zurück."""
    k = laden(pfad)
    zeilen = [Katalog.zeile(m) for m in k.meldungen]
    n_f, n_w, n_h = len(k.fehler()), len(k.warnungen()), len(k.hinweise())
    kopf = (f'{_zahl(len(k.bereiche), "Bereich", "Bereiche")}, {_zahl(len(k.prozesse), "Prozess", "Prozesse")}, '
            f'{_zahl(len(k.faktoren), "Faktor", "Faktoren")}, {_zahl(len(k.offene_fragen()), "offene Frage", "offene Fragen")}')
    hinweise = f', {_zahl(n_h, "Hinweis", "Hinweise")}' if n_h else ''
    if n_f:
        zeilen.append(f'FEHLER: {n_f} Fehler, {_zahl(n_w, "Warnung", "Warnungen")}{hinweise} ({kopf}).')
    else:
        zeilen.append(f'OK: keine Fehler, {_zahl(n_w, "Warnung", "Warnungen")}{hinweise} ({kopf}).')
    return k, '\n'.join(zeilen), 1 if n_f else 0


def _soll_gruppe(p):
    if p['zusammen']:
        return 'zusammengelegt'
    return p['soll']


def _tabelle(kopf, zeilen):
    z = ['| ' + ' | '.join(kopf) + ' |', '|' + '|'.join(['---'] + ['--:'] * (len(kopf) - 1)) + '|']
    z += ['| ' + ' | '.join(str(c) for c in zeile) + ' |' for zeile in zeilen]
    return z


def stand(pfad=None, heute=None):
    """Übersicht als Markdown: Bereich × Stand, Soll, offene Fragen, Faktoren mit Stichtag, betroffene Prozesse."""
    heute = heute or datetime.date.today()
    k = laden(pfad)
    out = [f'# Discovery-Stand · {k.wurzel.name}', '']
    if k.fehler():
        out += [f'**Achtung:** Der Katalog hat {len(k.fehler())} Fehler, die Zahlen können abweichen. '
                'Details mit `python -m devflow2 discovery pruefen`.', '']
    frage_je = {}
    ohne = 0
    for t in k.offene_fragen():
        b = k.frage_bereich(t)
        if b is None:
            ohne += 1
        else:
            frage_je[b] = frage_je.get(b, 0) + 1
    offen_gesamt = len(k.offene_fragen())

    out += ['## Bereich × Stand', '']
    zeilen = []
    # nur Stände mit mindestens einem Prozess werden Spalten (Reihenfolge bleibt): schmaler, lesbar auf dem Handy
    staende = [s for s in STAENDE if any(p['stand'] == s for p in k.prozesse)]
    for b in k.bereiche:
        ps = [p for p in k.prozesse if p['datei'] == b['datei']]
        zeilen.append([f'{b["id"]} {b["name"]}'] + [sum(1 for p in ps if p['stand'] == s) for s in staende]
                      + [len(ps), frage_je.get(b['id'], 0)])
    if ohne:
        zeilen.append(['(ohne Bereich)'] + [0] * len(staende) + [0, ohne])
    summe = [sum(z[i] for z in zeilen) for i in range(1, len(staende) + 3)]
    zeilen.append(['**Summe**'] + summe)
    out += _tabelle(['Bereich'] + staende + ['Prozesse', 'Fragen offen'], zeilen) + ['']

    out += ['## Bereich × Soll', '']
    soll_namen = ['offen', 'neu', 'bleibt', 'ändert sich', 'entfällt', 'zusammengelegt', 'außerhalb']
    zeilen = []
    for b in k.bereiche:
        ps = [p for p in k.prozesse if p['datei'] == b['datei']]
        zeilen.append([f'{b["id"]} {b["name"]}'] + [sum(1 for p in ps if _soll_gruppe(p) == s) for s in soll_namen])
    zeilen.append(['**Summe**'] + [sum(z[i] for z in zeilen) for i in range(1, len(soll_namen) + 1)])
    out += _tabelle(['Bereich'] + soll_namen, zeilen) + ['']

    out += [f'## Offene Fragen ({offen_gesamt})', '']
    if not offen_gesamt:
        out += ['_Keine._', '']
    for rolle, fragen in k.offene_nach_adressat():
        out += [f'### {"an " + rolle if rolle else "ohne Adressat"} ({len(fragen)})', '']
        out += [f'- {t}' for t in fragen] + ['']

    betroffen = k.betroffene()
    out += ['## Faktoren mit Stichtag', '']
    mit = sorted((f for f in k.faktoren if f['stichtag']), key=lambda f: (f['stichtag'], f['id'] or ''))
    for f in mit:
        pr = betroffen.get(f['id'], [])
        marke = ' (überschritten)' if f['stichtag'] < heute else ''
        out.append(f'- {_datum(f["stichtag"])}{marke} · {f["id"]} · {f["name"]} ({f["art"]}) · '
                   + (f'betrifft {", ".join(pr)}' if pr else 'betrifft keinen Prozess'))
    out += ['_Keine._'] if not mit else []
    out += ['']

    out += ['## Betroffene Prozesse je Faktor', '']
    for f in k.faktoren:
        pr = betroffen.get(f['id'], [])
        zusatz = f' · Änderung: {f["hinweis"]}' if f['hinweis'] else ''
        out.append(f'- {f["id"]} · {f["name"]} ({f["art"]}){zusatz} · {_zahl(len(pr), "Prozess", "Prozesse")}' + (f': {", ".join(pr)}' if pr else ''))
    out += ['_Keine Faktoren erfasst._'] if not k.faktoren else []
    return '\n'.join(out)


def main(argv):
    """discovery pruefen [<pfad>] | stand [<pfad>]. Rückgabe: Exitcode."""
    if argv[:1] in (['pruefen'], ['stand']) and len(argv) <= 2:
        pfad = argv[1] if len(argv) == 2 else None
        if argv[0] == 'pruefen':
            _, text, code = pruefen(pfad)
            print(text)
            return code
        print(stand(pfad))
        return 0
    raise SystemExit('Aufruf: python -m devflow2 discovery pruefen [<pfad>] | stand [<pfad>]')
