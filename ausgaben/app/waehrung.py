"""Waehrungsumrechnung in EUR ueber EZB-Referenzkurse (Frankfurter-API), mit Cache in der DB."""
import logging
from datetime import date

import httpx

from . import db

log = logging.getLogger("ausgaben.waehrung")
BASIS = "EUR"
WAEHRUNGEN = ["EUR", "USD", "GBP", "CHF", "DKK", "SEK", "NOK", "PLN", "CZK", "HUF", "TRY",
              "JPY", "CAD", "AUD", "THB", "AED", "MAD", "EGP", "ISK", "HRK", "MXN", "ZAR"]
_QUELLEN = ["https://api.frankfurter.dev/v1/{d}?base={w}&symbols=EUR",
            "https://api.frankfurter.app/{d}?from={w}&to=EUR"]


def kurs(datum_iso, waehrung):
    """EUR je 1 Einheit Fremdwaehrung am Datum, oder None wenn (noch) nicht ermittelbar."""
    waehrung = (waehrung or BASIS).upper()
    if waehrung == BASIS:
        return 1.0
    tag = (datum_iso or date.today().isoformat())[:10]
    if tag > date.today().isoformat():
        tag = date.today().isoformat()
    r = db.q1("SELECT kurs FROM kurse WHERE datum=? AND waehrung=?", (tag, waehrung))
    if r:
        return r["kurs"]
    for url in _QUELLEN:
        try:
            antwort = httpx.get(url.format(d=tag, w=waehrung), timeout=10)
            antwort.raise_for_status()
            wert = float(antwort.json()["rates"]["EUR"])
            db.x("INSERT OR REPLACE INTO kurse(datum, waehrung, kurs) VALUES (?,?,?)", (tag, waehrung, wert))
            return wert
        except Exception as e:  # noqa: BLE001
            log.info("Kursabruf %s %s ueber %s fehlgeschlagen: %s", waehrung, tag, url.split("/")[2], e)
    return None


def umrechnen(eintrag):
    """Setzt kurs und betrag_eur in-place. Ein manuell gesetzter Kurs bleibt erhalten."""
    w = (eintrag.get("waehrung") or BASIS).upper()
    eintrag["waehrung"] = w
    k = eintrag.get("kurs") if eintrag.get("kurs_manuell") else None
    if k is None:
        k = kurs(eintrag.get("datum"), w)
    eintrag["kurs"] = k
    eintrag["betrag_eur"] = round(eintrag["betrag"] * k, 2) if k is not None else None
    eintrag.pop("kurs_manuell", None)
    return eintrag


def offene_kurse_nachholen():
    for e in db.q("SELECT id, datum, waehrung, betrag FROM eintraege WHERE betrag_eur IS NULL LIMIT 200"):
        k = kurs(e["datum"], e["waehrung"])
        if k is not None:
            db.x("UPDATE eintraege SET kurs=?, betrag_eur=? WHERE id=?", (k, round(e["betrag"] * k, 2), e["id"]))
