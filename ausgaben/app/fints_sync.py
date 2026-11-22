"""Bankanbindung per FinTS (DKB, comdirect und andere deutsche Banken) ueber python-fints.

Ablauf
- Jede Bank ist ein Zugang (Tabelle banken) mit Anmeldename, optional gespeicherter PIN,
  gewaehltem TAN-Verfahren und ausgewaehlten Konten.
- Ein Auftrag (Job) laeuft in einem eigenen Thread. Verlangt die Bank eine Freigabe,
  wartet der Thread: bei App-Freigabe (decoupled) fragt er alle 3 s nach, bei TAN-Eingabe
  (z. B. photoTAN) wartet er auf die Eingabe aus der Web-App. Die Bank-Verbindung bleibt
  dabei offen.
- Laufender Abruf: Ausgaben ab "import_ab" werden direkt als Eintraege angelegt
  (wie bei Trade Republic). Umbuchungen und Gutschriften werden ausgelassen.
- Nachholen: Umsaetze eines frei gewaehlten Zeitraums landen in bank_nachholen und werden
  in der Web-App durchgesehen, bevor sie zu Eintraegen werden.
"""
import base64
import hashlib
import json
import logging
import queue
import re
import threading
import time
from datetime import date, datetime, timedelta

from . import db, kategorisierung, waehrung

log = logging.getLogger("ausgaben.fints")

VORLAGEN = {
    "dkb": {"name": "DKB", "blz": "12030000", "url": "https://fints.dkb.de/fints", "app": "DKB-App"},
    "comdirect": {"name": "comdirect", "blz": "20041111", "url": "https://fints.comdirect.de/fints",
                  "app": "comdirect photoTAN-App"},
}
STANDARD_UMBUCHUNG = ["trade republic", "kreditkartenabrechnung",
                      "abrechnung kreditkarte", "umbuchung", "uebertrag", "übertrag", "eigenes konto"]
TAN_WARTEZEIT = 300  # Sekunden fuer Freigabe in der App bzw. TAN-Eingabe
DUPLIKAT_TAGE = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS banken (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    vorlage TEXT,
    blz TEXT NOT NULL,
    url TEXT NOT NULL,
    login TEXT NOT NULL,
    kunden_id TEXT DEFAULT '',
    pin TEXT DEFAULT '',
    tan_verfahren TEXT DEFAULT '',
    tan_verfahren_liste TEXT DEFAULT '[]',
    tan_medium TEXT,
    zustand BLOB,
    konten TEXT DEFAULT '[]',
    konto_id INTEGER REFERENCES konten(id),
    import_ab TEXT,
    abruf_uhrzeit TEXT DEFAULT '',
    letzter_abruf TEXT,
    letzter_auto_tag TEXT,
    meldung TEXT DEFAULT '',
    aktiv INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS bank_nachholen (
    id INTEGER PRIMARY KEY,
    bank_id INTEGER,
    ref TEXT UNIQUE,
    konto TEXT,
    datum TEXT,
    betrag REAL,
    waehrung TEXT DEFAULT 'EUR',
    gegenpartei TEXT,
    zweck TEXT,
    buchungstext TEXT,
    iban_gegen TEXT,
    art TEXT,
    duplikat_id INTEGER,
    kategorie_id INTEGER,
    bezeichnung TEXT,
    ki_sicherheit REAL,
    kategorisiert_durch TEXT,
    status TEXT DEFAULT 'offen',
    erstellt TEXT
);
CREATE INDEX IF NOT EXISTS ix_nachholen_status ON bank_nachholen(status);
"""

_jobs = {}            # bank_id -> Job
_jobs_lock = threading.Lock()
ClientKlasse = None   # fuer Tests austauschbar
nach_abruf = None     # Rueckruf aus main.py: nach_abruf(liste_neuer_eintrag_ids)


def schema_anlegen():
    db.verbindung().executescript(SCHEMA)


def _client_klasse():
    if ClientKlasse is not None:
        return ClientKlasse
    from fints.client import FinTS3PinTanClient
    return FinTS3PinTanClient


def _need_tan_klasse():
    if ClientKlasse is not None and hasattr(ClientKlasse, "NeedTANResponse"):
        return ClientKlasse.NeedTANResponse
    from fints.client import NeedTANResponse
    return NeedTANResponse


# ---------------------------------------------------------------- Einstellungen

def produkt_id():
    return (db.einstellung("fints_produkt_id") or "").strip()


def umbuchung_begriffe():
    roh = db.einstellung("umbuchung_begriffe")
    return json.loads(roh) if roh else STANDARD_UMBUCHUNG


def bank_laden(bank_id):
    b = db.q1("SELECT * FROM banken WHERE id=?", (bank_id,))
    if not b:
        raise KeyError("Bank nicht gefunden")
    b["konten"] = json.loads(b["konten"] or "[]")
    b["tan_verfahren_liste"] = json.loads(b["tan_verfahren_liste"] or "[]")
    return b


def bank_oeffentlich(b):
    """Ohne PIN und Client-Zustand, fuer die Web-App."""
    d = {k: v for k, v in b.items() if k not in ("pin", "zustand")}
    d["pin_gespeichert"] = bool(b.get("pin"))
    job = _jobs.get(b["id"])
    d["job"] = job.status() if job else None
    d["offen_nachholen"] = db.q1("SELECT COUNT(*) n FROM bank_nachholen WHERE bank_id=? AND status='offen'",
                                 (b["id"],))["n"]
    return d


def alle_banken():
    return [bank_oeffentlich(bank_laden(r["id"])) for r in db.q("SELECT id FROM banken ORDER BY id")]


def bank_anlegen(d):
    vorlage = VORLAGEN.get(d.get("vorlage") or "", {})
    name = (d.get("name") or vorlage.get("name") or "Bank").strip()
    blz = re.sub(r"\D", "", d.get("blz") or vorlage.get("blz") or "")
    url = (d.get("url") or vorlage.get("url") or "").strip()
    login = (d.get("login") or "").strip()
    if not (blz and url and login):
        raise ValueError("BLZ, Server-Adresse und Anmeldename sind noetig")
    konto = db.q1("SELECT id FROM konten WHERE name=?", (name,))
    konto_id = konto["id"] if konto else db.x("INSERT INTO konten(name, typ, icon) VALUES (?, 'auto_fints', '🏦')",
                                              (name,))[0]
    bid, _ = db.x(
        "INSERT INTO banken(name, vorlage, blz, url, login, kunden_id, pin, konto_id, import_ab, abruf_uhrzeit) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, d.get("vorlage") or "", blz, url, login, (d.get("kunden_id") or "").strip(),
         (d.get("pin") or "") if d.get("pin_speichern") else "", konto_id,
         d.get("import_ab") or date.today().isoformat(), d.get("abruf_uhrzeit") or ""))
    return bank_laden(bid)


def bank_aendern(bank_id, d):
    b = bank_laden(bank_id)
    felder = {}
    for k in ("name", "blz", "url", "login", "kunden_id", "tan_verfahren", "import_ab", "abruf_uhrzeit", "aktiv"):
        if k in d:
            felder[k] = d[k]
    if "konten" in d:
        aktiv = {k["schluessel"]: bool(k.get("aktiv")) for k in d["konten"]}
        for k in b["konten"]:
            if k["schluessel"] in aktiv:
                k["aktiv"] = aktiv[k["schluessel"]]
        felder["konten"] = json.dumps(b["konten"])
    if "pin" in d:
        felder["pin"] = d["pin"] or ""
    if "tan_verfahren" in felder and felder["tan_verfahren"] != b["tan_verfahren"]:
        felder["tan_medium"] = None
    if felder:
        db.x(f"UPDATE banken SET {', '.join(k + '=?' for k in felder)} WHERE id=?", (*felder.values(), bank_id))
    if "name" in felder and b["konto_id"]:
        db.x("UPDATE konten SET name=? WHERE id=?", (felder["name"], b["konto_id"]))
    return bank_laden(bank_id)


def bank_loeschen(bank_id):
    db.x("DELETE FROM bank_nachholen WHERE bank_id=? AND status='offen'", (bank_id,))
    db.x("DELETE FROM banken WHERE id=?", (bank_id,))


# ---------------------------------------------------------------- Job

class Abbruch(Exception):
    pass


class Job(threading.Thread):
    def __init__(self, bank_id, art, pin=None, von=None, bis=None):
        super().__init__(daemon=True)
        self.bank_id, self.art, self.pin_einmal, self.von, self.bis = bank_id, art, pin, von, bis
        self.zustand = "laeuft"
        self.meldung = "Verbinde mit der Bank …"
        self.tan_art = None
        self.tan_text = ""
        self.tan_bild = None
        self.ergebnis = {}
        self.gestartet = db.jetzt_iso()
        self.beendet = None
        self._tan_queue = queue.Queue()
        self._abbrechen = threading.Event()

    def status(self):
        return {"art": self.art, "zustand": self.zustand, "meldung": self.meldung, "tan_art": self.tan_art,
                "tan_text": self.tan_text, "tan_bild": self.tan_bild, "ergebnis": self.ergebnis,
                "gestartet": self.gestartet, "beendet": self.beendet}

    def tan_eingeben(self, tan):
        self._tan_queue.put(tan)

    def abbrechen(self):
        self._abbrechen.set()
        self._tan_queue.put(None)

    # ---- Freigaben
    def _freigabe(self, f, r):
        NeedTAN = _need_tan_klasse()
        bank = bank_laden(self.bank_id)
        app = VORLAGEN.get(bank["vorlage"] or "", {}).get("app", "Banking-App")
        while isinstance(r, NeedTAN):
            if getattr(r, "decoupled", False):
                self.zustand, self.tan_art = "tan_noetig", "app"
                self.tan_text = _text(getattr(r, "challenge", "")) or f"Bitte in der {app} bestätigen."
                self.meldung = f"Warte auf Freigabe in der {app} …"
                frist = time.time() + TAN_WARTEZEIT
                while True:
                    if self._abbrechen.wait(3):
                        raise Abbruch("Abgebrochen")
                    r = f.send_tan(r, "")
                    if not (isinstance(r, NeedTAN) and getattr(r, "decoupled", False)):
                        break
                    if time.time() > frist:
                        raise Abbruch("Keine Freigabe innerhalb von 5 Minuten")
            else:
                self.zustand, self.tan_art = "tan_noetig", "code"
                self.tan_text = _text(getattr(r, "challenge", "")) or "Bitte TAN eingeben."
                matrix = getattr(r, "challenge_matrix", None)
                self.tan_bild = (f"data:{matrix[0]};base64,{base64.b64encode(matrix[1]).decode()}"
                                 if matrix else None)
                self.meldung = "Warte auf TAN-Eingabe …"
                try:
                    tan = self._tan_queue.get(timeout=TAN_WARTEZEIT)
                except queue.Empty:
                    raise Abbruch("Keine TAN innerhalb von 5 Minuten")
                if tan is None:
                    raise Abbruch("Abgebrochen")
                self.tan_bild = None
                r = f.send_tan(r, tan.strip())
        self.zustand, self.tan_art, self.tan_text, self.tan_bild = "laeuft", None, "", None
        return r

    # ---- Ablauf
    def run(self):
        try:
            bank = bank_laden(self.bank_id)
            pin = self.pin_einmal or bank["pin"]
            if not pin:
                raise Abbruch("PIN fehlt")
            if not produkt_id():
                raise Abbruch("FinTS-Produkt-ID fehlt (Mehr → Banken)")
            Klasse = _client_klasse()
            f = Klasse(bank["blz"], bank["login"], pin, bank["url"], customer_id=bank["kunden_id"] or None,
                       product_id=produkt_id(), from_data=bank["zustand"])
            self._verfahren_waehlen(f, bank)
            with f:
                if getattr(f, "init_tan_response", None):
                    self._freigabe(f, f.init_tan_response)
                if self.art == "einrichten" or not bank["konten"]:
                    self.meldung = "Lade Kontenliste …"
                    self._konten_speichern(self._freigabe(f, f.get_sepa_accounts()))
                    bank = bank_laden(self.bank_id)
                if self.art in ("abruf", "nachholen"):
                    self._umsaetze(f, bank)
            db.x("UPDATE banken SET zustand=?, meldung='' WHERE id=?", (f.deconstruct(including_private=True),
                                                                          self.bank_id))
            self.zustand = "fertig"
            if self.art == "einrichten":
                self.meldung = f"Verbunden, {len(bank_laden(self.bank_id)['konten'])} Konten gefunden"
        except Abbruch as e:
            self.zustand, self.meldung = "fehler", str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("FinTS-Auftrag fehlgeschlagen")
            self.zustand, self.meldung = "fehler", _fehlertext(e)
        finally:
            self.tan_art, self.tan_bild = None, None
            self.beendet = db.jetzt_iso()
            db.x("UPDATE banken SET meldung=? WHERE id=?",
                 (self.meldung if self.zustand == "fehler" else "", self.bank_id))

    def _verfahren_waehlen(self, f, bank):
        if bank["tan_verfahren"] and not f.get_current_tan_mechanism():
            f.set_tan_mechanism(bank["tan_verfahren"])
        if not f.get_current_tan_mechanism() or not bank["tan_verfahren_liste"]:
            self.meldung = "Frage TAN-Verfahren ab …"
            f.fetch_tan_mechanisms()
            liste = [{"code": code, "name": str(getattr(p, "name", code))}
                     for code, p in f.get_tan_mechanisms().items() if code != "999"]
            wahl = bank["tan_verfahren"] if any(v["code"] == bank["tan_verfahren"] for v in liste) else None
            if not wahl and liste:
                bevorzugt = [v for v in liste if re.search(r"app|push|decoupled|securego|best", v["name"], re.I)]
                wahl = (bevorzugt or liste)[0]["code"]
            if wahl:
                f.set_tan_mechanism(wahl)
            db.x("UPDATE banken SET tan_verfahren=?, tan_verfahren_liste=? WHERE id=?",
                 (wahl or "", json.dumps(liste), self.bank_id))
        if bank.get("tan_medium") is not None:
            f.selected_tan_medium = bank["tan_medium"]
        elif f.get_current_tan_mechanism() not in (None, "999") and f.is_tan_media_required():
            medien = f.get_tan_media()[1]
            if medien:
                f.set_tan_medium(medien[0])
            else:
                f.selected_tan_medium = ""
            db.x("UPDATE banken SET tan_medium=? WHERE id=?", (f.selected_tan_medium, self.bank_id))

    def _konten_speichern(self, konten):
        bank = bank_laden(self.bank_id)
        alt = {k["schluessel"]: k for k in bank["konten"]}
        neu = []
        for a in konten or []:
            schluessel = a.iban or f"{a.blz}:{a.accountnumber}:{a.subaccount or ''}"
            neu.append({
                "schluessel": schluessel, "iban": a.iban, "bic": a.bic, "accountnumber": a.accountnumber,
                "subaccount": a.subaccount, "blz": a.blz,
                "name": _konto_name(a),
                "aktiv": alt.get(schluessel, {}).get("aktiv", True),
            })
        db.x("UPDATE banken SET konten=? WHERE id=?", (json.dumps(neu), self.bank_id))

    def _umsaetze(self, f, bank):
        from collections import namedtuple
        SEPA = namedtuple("SEPAAccount", "iban bic accountnumber subaccount blz")
        konten = [k for k in bank["konten"] if k.get("aktiv")]
        if not konten:
            raise Abbruch("Kein Konto ausgewählt")
        heute = date.today()
        if self.art == "nachholen":
            von = date.fromisoformat(self.von[:10])
            bis = date.fromisoformat((self.bis or heute.isoformat())[:10])
        else:
            von = date.fromisoformat((bank["import_ab"] or heute.isoformat())[:10])
            letzter = db.q1("SELECT MAX(datum) d FROM eintraege WHERE quelle='bank' AND konto_id=?",
                            (bank["konto_id"],))["d"]
            if letzter:
                von = max(von, date.fromisoformat(letzter[:10]) - timedelta(days=10))
            bis = heute
        roh = []
        for i, k in enumerate(konten, 1):
            self.meldung = f"Lade Umsätze {k['name']} ({i}/{len(konten)}) …"
            konto = SEPA(k["iban"], k["bic"], k["accountnumber"], k["subaccount"], k["blz"])
            erg = self._freigabe(f, f.get_transactions(konto, von, bis))
            for t in erg or []:
                u = _umsatz(t, k)
                if u:
                    roh.append(u)
        self.meldung = f"Verarbeite {len(roh)} Umsätze …"
        if self.art == "nachholen":
            self.ergebnis = _nachholen_speichern(bank, roh, self)
        else:
            self.ergebnis = _laufend_speichern(bank, roh, von)
            db.x("UPDATE banken SET letzter_abruf=? WHERE id=?", (db.jetzt_iso(), self.bank_id))
            if nach_abruf:
                try:
                    nach_abruf(self.ergebnis.get("ids", []))
                except Exception:  # noqa: BLE001
                    log.exception("Rueckruf nach Bankabruf")
        self.meldung = _ergebnis_text(self.art, self.ergebnis)


def job_starten(bank_id, art, pin=None, von=None, bis=None):
    with _jobs_lock:
        alt = _jobs.get(bank_id)
        if alt and alt.is_alive():
            return alt
        job = Job(bank_id, art, pin, von, bis)
        _jobs[bank_id] = job
        job.start()
        return job


def job(bank_id):
    return _jobs.get(bank_id)


# ---------------------------------------------------------------- Umsaetze

def _text(s):
    return re.sub(r"<[^>]+>", " ", str(s or "")).replace("&nbsp;", " ").strip()


def _konto_name(a):
    if a.iban:
        return f"{a.iban[:4]} … {a.iban[-4:]}" + (f" ({a.subaccount})" if a.subaccount else "")
    return f"Konto {a.accountnumber}" + (f"/{a.subaccount}" if a.subaccount else "")


def _fehlertext(e):
    t = str(e) or e.__class__.__name__
    name = e.__class__.__name__
    if "PIN" in name or "9910" in t or "9931" in t or "9942" in t:
        return "Anmeldung abgelehnt - Anmeldename oder PIN prüfen"
    if "Connection" in name or "Max retries" in t or "Name or service" in t:
        return f"Bank nicht erreichbar ({t[:150]})"
    if "9078" in t or "Produkt" in t or "product" in t.lower():
        return f"Bank lehnt die Produkt-ID ab - ist die Registrierung schon freigeschaltet? ({t[:150]})"
    return t[:300]


def _feld(d, *namen):
    for n in namen:
        v = d.get(n)
        if v not in (None, ""):
            return v
    return None


def _umsatz(t, konto):
    d = t.data if hasattr(t, "data") else t
    menge = d.get("amount")
    wert = getattr(menge, "amount", menge)
    if wert is None:
        return None
    wert = float(wert)
    if wert == 0:
        return None
    wae = getattr(menge, "currency", None) or d.get("currency") or "EUR"
    tag = _feld(d, "date", "entry_date", "booking_date", "valuta_date")
    if hasattr(tag, "isoformat"):
        tag = tag.isoformat()
    tag = str(tag)[:10]
    zweck = " ".join(str(x) for x in [d.get("purpose"), d.get("additional_purpose")] if x).strip()
    gegen = (_feld(d, "applicant_name", "recipient_name", "creditor_name", "debtor_name") or "").strip()
    return {
        "konto": konto["schluessel"], "datum": tag, "betrag_konto": wert, "waehrung": wae,
        "gegenpartei": gegen, "zweck": re.sub(r"\s+", " ", zweck)[:300],
        "buchungstext": (d.get("posting_text") or "").strip(),
        "iban_gegen": (d.get("applicant_iban") or "").replace(" ", "").upper(),
    }


def _referenzen(bank, umsaetze):
    zaehler = {}
    for u in umsaetze:
        basis = "|".join(str(x) for x in (bank["id"], u["konto"], u["datum"], f"{u['betrag_konto']:.2f}",
                                           u["gegenpartei"], u["zweck"]))
        zaehler[basis] = zaehler.get(basis, 0) + 1
        u["ref"] = f"bank{bank['id']}:" + hashlib.sha1(f"{basis}|{zaehler[basis]}".encode()).hexdigest()[:20]


def _eigene_ibans():
    ibans = set()
    for b in db.q("SELECT konten FROM banken"):
        for k in json.loads(b["konten"] or "[]"):
            if k.get("iban"):
                ibans.add(k["iban"].upper())
    return ibans


def _art(u, eigene, begriffe):
    text = f"{u['gegenpartei']} {u['zweck']} {u['buchungstext']}".lower()
    if u["iban_gegen"] and u["iban_gegen"] in eigene:
        return "umbuchung"
    if any(b and b in text for b in begriffe):
        return "umbuchung"
    return "gutschrift" if u["betrag_konto"] > 0 else "ausgabe"


def _duplikat(betrag, datum, ausschliessen_ref=None):
    von = (date.fromisoformat(datum) - timedelta(days=DUPLIKAT_TAGE)).isoformat()
    bis = (date.fromisoformat(datum) + timedelta(days=DUPLIKAT_TAGE + 1)).isoformat()
    r = db.q1("SELECT id FROM eintraege WHERE ABS(betrag - ?) < 0.005 AND datum >= ? AND datum < ? "
              "AND quelle <> 'bank' ORDER BY ABS(julianday(datum) - julianday(?)) LIMIT 1",
              (betrag, von, bis, datum))
    return r["id"] if r else None


def _haendler(u):
    name = u["gegenpartei"]
    if not name:
        name = re.split(r"\s{2,}|/|//", u["zweck"])[0][:60]
    return name.strip() or u["buchungstext"] or "Bankumsatz"


def _laufend_speichern(bank, roh, von):
    _referenzen(bank, roh)
    eigene, begriffe = _eigene_ibans(), umbuchung_begriffe()
    neue, uebersprungen = [], {"umbuchung": 0, "gutschrift": 0}
    for u in roh:
        if u["datum"] < von.isoformat():
            continue
        if db.q1("SELECT 1 FROM eintraege WHERE quelle_ref=?", (u["ref"],)) or db.einstellung(f"geloescht:{u['ref']}"):
            continue
        if db.q1("SELECT 1 FROM bank_nachholen WHERE ref=?", (u["ref"],)):
            continue
        art = _art(u, eigene, begriffe)
        if art != "ausgabe":
            uebersprungen[art] += 1
            continue
        betrag = round(-u["betrag_konto"], 2)
        neue.append({"datum": u["datum"] + "T00:00:00", "betrag": betrag, "waehrung": u["waehrung"],
                     "haendler_roh": _haendler(u), "notiz": u["zweck"][:160], "quelle_ref": u["ref"],
                     "typ": u["buchungstext"], "dup": _duplikat(betrag, u["datum"])})
    if neue:
        kategorisierung.kategorisiere(neue)
    jetzt = db.jetzt_iso()
    ids = []
    for e in neue:
        waehrung.umrechnen(e)
        pruefen = 1 if e["dup"] else e.get("pruefen", 1)
        notiz = (f"Mögliches Duplikat von Eintrag {e['dup']}. " if e["dup"] else "") + e["notiz"]
        eid, anz = db.x(
            "INSERT OR IGNORE INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, "
            "notiz, konto_id, quelle, quelle_ref, haendler_roh, pruefen, ki_sicherheit, kategorisiert_durch, "
            "erstellt, geaendert) VALUES (?,?,?,?,?,?,?,?,?,'bank',?,?,?,?,?,?,?)",
            (e["datum"], e["betrag"], e["waehrung"], e["kurs"], e["betrag_eur"], e.get("kategorie_id"),
             e.get("bezeichnung") or e["haendler_roh"], notiz, bank["konto_id"], e["quelle_ref"], e["haendler_roh"],
             pruefen, e.get("ki_sicherheit"), e.get("kategorisiert_durch"), jetzt, jetzt))
        if anz:
            ids.append(eid)
    return {"neu": len(ids), "ids": ids, "umbuchungen": uebersprungen["umbuchung"],
            "gutschriften": uebersprungen["gutschrift"], "gelesen": len(roh)}


def _nachholen_speichern(bank, roh, job=None):
    _referenzen(bank, roh)
    eigene, begriffe = _eigene_ibans(), umbuchung_begriffe()
    neu = []
    for u in roh:
        if db.q1("SELECT 1 FROM eintraege WHERE quelle_ref=?", (u["ref"],)):
            continue
        if db.q1("SELECT 1 FROM bank_nachholen WHERE ref=?", (u["ref"],)):
            continue
        u["art"] = _art(u, eigene, begriffe)
        u["betrag"] = round(-u["betrag_konto"], 2)
        u["duplikat_id"] = _duplikat(u["betrag"], u["datum"]) if u["art"] == "ausgabe" else None
        u["haendler_roh"] = _haendler(u)
        neu.append(u)
    einordnen = [u for u in neu if u["art"] == "ausgabe" and not u["duplikat_id"]]
    if job:
        job.meldung = f"Ordne {len(einordnen)} Umsätze ein …"
    if einordnen:
        kategorisierung.kategorisiere(einordnen)
    jetzt = db.jetzt_iso()
    for u in neu:
        db.x("INSERT OR IGNORE INTO bank_nachholen(bank_id, ref, konto, datum, betrag, waehrung, gegenpartei, zweck, "
             "buchungstext, iban_gegen, art, duplikat_id, kategorie_id, bezeichnung, ki_sicherheit, "
             "kategorisiert_durch, status, erstellt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'offen',?)",
             (bank["id"], u["ref"], u["konto"], u["datum"], u["betrag"], u["waehrung"], u["gegenpartei"], u["zweck"],
              u["buchungstext"], u["iban_gegen"], u["art"], u["duplikat_id"], u.get("kategorie_id"),
              u.get("bezeichnung") or u["haendler_roh"], u.get("ki_sicherheit"), u.get("kategorisiert_durch"), jetzt))
    zaehl = {a: sum(1 for u in neu if u["art"] == a) for a in ("ausgabe", "gutschrift", "umbuchung")}
    return {"gelesen": len(roh), "neu": len(neu), "duplikate": sum(1 for u in neu if u["duplikat_id"]), **zaehl}


def _ergebnis_text(art, e):
    if art == "nachholen":
        return (f"{e['neu']} Umsätze zur Durchsicht bereit ({e['ausgabe']} Ausgaben, davon {e['duplikate']} "
                f"vermutlich schon erfasst; {e['umbuchung']} Umbuchungen, {e['gutschrift']} Gutschriften)")
    return (f"{e['neu']} neue Ausgaben übernommen ({e['umbuchungen']} Umbuchungen und "
            f"{e['gutschriften']} Gutschriften ausgelassen)")


# ---------------------------------------------------------------- Nachholen: Durchsicht

def nachholen_liste(bank_id=None):
    w, a = "n.status='offen'", []
    if bank_id:
        w += " AND n.bank_id=?"
        a.append(int(bank_id))
    return db.q(
        "SELECT n.*, b.name bank, d.bezeichnung dup_bezeichnung, d.datum dup_datum, d.quelle dup_quelle "
        f"FROM bank_nachholen n LEFT JOIN banken b ON b.id=n.bank_id LEFT JOIN eintraege d ON d.id=n.duplikat_id "
        f"WHERE {w} ORDER BY n.datum DESC, n.id DESC", a)


def nachholen_uebernehmen(eintraege):
    """eintraege: [{id, kategorie_id, bezeichnung}] - legt Eintraege an und markiert die Zeilen."""
    jetzt = db.jetzt_iso()
    angelegt = 0
    for wunsch in eintraege:
        n = db.q1("SELECT n.*, b.konto_id FROM bank_nachholen n JOIN banken b ON b.id=n.bank_id "
                  "WHERE n.id=? AND n.status='offen'", (int(wunsch["id"]),))
        if not n:
            continue
        kat = wunsch.get("kategorie_id") or n["kategorie_id"]
        bez = (wunsch.get("bezeichnung") or n["bezeichnung"] or n["gegenpartei"] or "Bankumsatz")[:120]
        e = {"datum": n["datum"] + "T00:00:00", "betrag": n["betrag"], "waehrung": n["waehrung"]}
        waehrung.umrechnen(e)
        eid, anz = db.x(
            "INSERT OR IGNORE INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, "
            "notiz, konto_id, quelle, quelle_ref, haendler_roh, pruefen, kategorisiert_durch, erstellt, geaendert) "
            "VALUES (?,?,?,?,?,?,?,?,?,'bank',?,?,?,?,?,?)",
            (e["datum"], e["betrag"], e["waehrung"], e["kurs"], e["betrag_eur"], kat, bez, (n["zweck"] or "")[:160],
             n["konto_id"], n["ref"], n["gegenpartei"] or bez, 0 if kat else 1,
             "nutzer" if wunsch.get("kategorie_id") else n["kategorisiert_durch"], jetzt, jetzt))
        db.x("UPDATE bank_nachholen SET status='uebernommen' WHERE id=?", (n["id"],))
        if anz:
            angelegt += 1
            if wunsch.get("kategorie_id") and wunsch.get("kategorie_id") != n["kategorie_id"]:
                kategorisierung.lerne_aus_korrektur({"haendler_roh": n["gegenpartei"], "kategorie_id": kat,
                                                     "bezeichnung": bez})
    return angelegt


def nachholen_verwerfen(ids):
    if not ids:
        return 0
    _, n = db.x(f"UPDATE bank_nachholen SET status='verworfen' WHERE status='offen' AND id IN "
                f"({','.join('?' * len(ids))})", [int(i) for i in ids])
    return n


# ---------------------------------------------------------------- Zeitplan

def faellige_abrufe():
    """Gibt Banken zurueck, deren taeglicher Abruf jetzt faellig ist."""
    jetzt = datetime.now()
    faellig = []
    for b in db.q("SELECT id, abruf_uhrzeit, letzter_auto_tag, pin, aktiv FROM banken"):
        uhr = (b["abruf_uhrzeit"] or "").strip()
        if not (b["aktiv"] and b["pin"] and re.match(r"^\d{1,2}:\d{2}$", uhr)):
            continue
        h, m = map(int, uhr.split(":"))
        if (jetzt.hour, jetzt.minute) >= (h, m) and b["letzter_auto_tag"] != jetzt.date().isoformat():
            db.x("UPDATE banken SET letzter_auto_tag=? WHERE id=?", (jetzt.date().isoformat(), b["id"]))
            faellig.append(b["id"])
    return faellig
