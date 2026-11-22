"""Verbrauch der Claude-API durch dieses Add-on.

Jede Anfrage wird mit den Tokenzahlen aus der API-Antwort (Feld "usage") gespeichert.
Die Kosten werden beim Auswerten aus Listenpreis x Tokens berechnet und mit dem
EZB-Kurs in Euro umgerechnet. Verbindlich ist die Abrechnung in der Anthropic Console.
"""
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import db, waehrung
from .einstellungen import optionen

log = logging.getLogger("ausgaben.ki_nutzung")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ki_nutzung (
    id INTEGER PRIMARY KEY,
    zeit TEXT NOT NULL,
    zweck TEXT NOT NULL,
    modell TEXT,
    eingabe INTEGER DEFAULT 0,
    ausgabe INTEGER DEFAULT 0,
    cache_lesen INTEGER DEFAULT 0,
    cache_schreiben INTEGER DEFAULT 0,
    anzahl INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 1,
    meldung TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_ki_nutzung_zeit ON ki_nutzung(zeit);
"""

# Listenpreise von Anthropic in USD je 1 Mio. Tokens (Stand November 2026), Eingabe / Ausgabe.
# Zuordnung ueber den laengsten passenden Anfang der Modell-ID.
STANDARD_PREISE = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-fable-5-2": (8.0, 40.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-opus-4-5": (5.0, 25.0),
}
# Haiku 5.5: Anfragen mit mehr als 100.000 Eingabe-Tokens kosten das Fuenffache (fuer alle Tokens).
STAFFEL = {"claude-haiku-5-5": (100_000, 5.0)}

ZWECKE = {"kategorisierung": "Kategorisierung", "beleg": "Kassenbons", "test": "Tests"}


_schema_da = False


def schema_anlegen():
    global _schema_da
    db.verbindung().executescript(SCHEMA)
    _schema_da = True


def erfassen(zweck, modell, usage=None, anzahl=0, ok=True, meldung=""):
    """Darf den eigentlichen Ablauf nie stoeren."""
    try:
        if not _schema_da:
            schema_anlegen()
        _erfassen(zweck, modell, usage, anzahl, ok, meldung)
    except Exception:  # noqa: BLE001
        log.exception("KI-Verbrauch konnte nicht gespeichert werden")


def _erfassen(zweck, modell, usage, anzahl, ok, meldung):
    u = usage or {}
    db.x("INSERT INTO ki_nutzung(zeit, zweck, modell, eingabe, ausgabe, cache_lesen, cache_schreiben, anzahl, ok, meldung) "
         "VALUES (?,?,?,?,?,?,?,?,?,?)",
         (db.jetzt_iso(), zweck, modell or "", int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
          int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0),
          int(anzahl or 0), 1 if ok else 0, (meldung or "")[:300]))


# ---------------------------------------------------------------- Preise

def _eigene_preise():
    try:
        return json.loads(db.einstellung("ki_preise") or "{}")
    except ValueError:
        return {}


def _schluessel(modell):
    treffer = [k for k in STANDARD_PREISE if (modell or "").startswith(k)]
    return max(treffer, key=len) if treffer else None


def preis(modell):
    """(eingabe, ausgabe, quelle) in USD je 1 Mio. Tokens, oder None."""
    eigen = _eigene_preise().get(modell)
    if eigen:
        return float(eigen[0]), float(eigen[1]), "eigen"
    k = _schluessel(modell)
    if k:
        return (*STANDARD_PREISE[k], "standard")
    return None


def preis_setzen(modell, eingabe, ausgabe):
    preise = _eigene_preise()
    if eingabe is None and ausgabe is None:
        preise.pop(modell, None)
    else:
        if float(eingabe) < 0 or float(ausgabe) < 0:
            raise ValueError("Preise dürfen nicht negativ sein")
        preise[modell] = [float(eingabe), float(ausgabe)]
    db.setze_einstellung("ki_preise", json.dumps(preise))


def kosten_usd(z, cache=None):
    cache = {} if cache is None else cache
    if z["modell"] not in cache:
        cache[z["modell"]] = preis(z["modell"])
    p = cache[z["modell"]]
    if not p:
        return None
    ein, aus = p[0], p[1]
    k = _schluessel(z["modell"])
    if p[2] == "standard" and k in STAFFEL:
        grenze, faktor = STAFFEL[k]
        if z["eingabe"] + z["cache_lesen"] + z["cache_schreiben"] > grenze:
            ein, aus = ein * faktor, aus * faktor
    return (z["eingabe"] * ein + z["cache_schreiben"] * ein * 1.25 + z["cache_lesen"] * ein * 0.1
            + z["ausgabe"] * aus) / 1_000_000


# ---------------------------------------------------------------- Auswertung

def _summe(zeilen, cache):
    s = {"anfragen": 0, "fehler": 0, "anzahl": 0, "eingabe": 0, "ausgabe": 0, "usd": 0.0, "ohne_preis": 0}
    for z in zeilen:
        s["anfragen"] += 1
        s["fehler"] += 0 if z["ok"] else 1
        s["anzahl"] += z["anzahl"] or 0
        s["eingabe"] += z["eingabe"] + z["cache_lesen"] + z["cache_schreiben"]
        s["ausgabe"] += z["ausgabe"]
        k = kosten_usd(z, cache)
        if k is None:
            s["ohne_preis"] += 1 if (z["eingabe"] or z["ausgabe"]) else 0
        else:
            s["usd"] += k
    return s


def _usd_eur():
    try:
        return waehrung.kurs(date.today().isoformat(), "USD")
    except Exception:  # noqa: BLE001
        return None


def monat_eur():
    """Kosten im laufenden Monat in EUR (fuer das Mehr-Menue), oder None."""
    erster = date.today().replace(day=1).isoformat()
    zeilen = db.q("SELECT * FROM ki_nutzung WHERE zeit >= ?", (erster,))
    if not zeilen:
        return 0.0
    kurs = db.q1("SELECT kurs FROM kurse WHERE waehrung='USD' ORDER BY datum DESC LIMIT 1")
    return _summe(zeilen, {})["usd"] * kurs["kurs"] if kurs else None


def uebersicht():
    heute = date.today()
    erster = heute.replace(day=1)
    alle = db.q("SELECT * FROM ki_nutzung ORDER BY zeit")
    cache = {}
    kurs = _usd_eur()

    def mit_eur(s):
        s["eur"] = round(s["usd"] * kurs, 6) if kurs else None
        s["usd"] = round(s["usd"], 6)
        return s

    zeitraeume = [
        {"name": "Heute", **mit_eur(_summe([z for z in alle if z["zeit"][:10] == heute.isoformat()], cache))},
        {"name": "Dieser Monat", **mit_eur(_summe([z for z in alle if z["zeit"] >= erster.isoformat()], cache))},
        {"name": "Gesamt", **mit_eur(_summe(alle, cache))},
    ]
    zwecke = []
    for zweck, name in ZWECKE.items():
        zeilen = [z for z in alle if z["zweck"] == zweck and z["zeit"] >= erster.isoformat()]
        if zeilen:
            zwecke.append({"zweck": zweck, "name": name, **mit_eur(_summe(zeilen, cache))})
    je_tag = defaultdict(list)
    start = heute - timedelta(days=29)
    for z in alle:
        if z["zeit"][:10] >= start.isoformat():
            je_tag[z["zeit"][:10]].append(z)
    tage = []
    for i in range(30):
        t = (start + timedelta(days=i)).isoformat()
        s = _summe(je_tag.get(t, []), cache)
        tage.append({"tag": t, "anfragen": s["anfragen"], "usd": s["usd"], "eur": s["usd"] * kurs if kurs else None})
    modell = optionen().get("claude_modell") or "claude-haiku-5-5"
    p = preis(modell)
    letzter_fehler = db.q1("SELECT zeit, meldung FROM ki_nutzung WHERE ok=0 ORDER BY id DESC LIMIT 1")
    return {
        "seit": alle[0]["zeit"] if alle else None,
        "usd_eur": kurs,
        "zeitraeume": zeitraeume,
        "zwecke_monat": zwecke,
        "tage": tage,
        "modell": modell,
        "preis": {"eingabe": p[0], "ausgabe": p[1], "quelle": p[2]} if p else None,
        "standard_preis": STANDARD_PREISE.get(_schluessel(modell)) if _schluessel(modell) else None,
        "letzter_fehler": letzter_fehler,
        "stand_preise": "November 2026",
    }
