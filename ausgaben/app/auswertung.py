"""Summen, Zeitreihen, Kategorien-Auswertung und die Sensoren fuer Home Assistant."""
import logging
import os
from datetime import date, timedelta

import httpx

from . import db
from .einstellungen import optionen

log = logging.getLogger("ausgaben.ha")
BETRAG = "COALESCE(e.betrag_eur, 0)"


def filter_sql(p):
    """Baut WHERE-Klausel aus Filterparametern (dict)."""
    w, a = ["1=1"], []
    if p.get("von"):
        w.append("e.datum >= ?"); a.append(p["von"][:10])
    if p.get("bis"):
        w.append("e.datum < date(?, '+1 day')"); a.append(p["bis"][:10])
    if p.get("kategorie"):
        ids = [int(i) for i in str(p["kategorie"]).split(",") if i]
        w.append(f"e.kategorie_id IN ({','.join('?' * len(ids))})"); a += ids
    if p.get("konto"):
        w.append("e.konto_id = ?"); a.append(int(p["konto"]))
    if p.get("pruefen") in ("1", 1, True, "true"):
        w.append("e.pruefen = 1")
    if p.get("ohne_kategorie") in ("1", 1, True, "true"):
        w.append("e.kategorie_id IS NULL")
    if p.get("q"):
        for wort in str(p["q"]).split():
            muster = f"%{wort}%"
            teil = "(e.bezeichnung LIKE ? OR e.notiz LIKE ? OR e.haendler_roh LIKE ? OR k.name LIKE ?"
            werte = [muster] * 4
            try:
                zahl = float(wort.replace(",", "."))
                teil += " OR ABS(e.betrag - ?) < 0.005"
                werte.append(zahl)
            except ValueError:
                pass
            w.append(teil + ")"); a += werte
    if p.get("min") not in (None, ""):
        w.append(f"{BETRAG} >= ?"); a.append(float(p["min"]))
    if p.get("max") not in (None, ""):
        w.append(f"{BETRAG} <= ?"); a.append(float(p["max"]))
    if p.get("waehrung"):
        w.append("e.waehrung = ?"); a.append(p["waehrung"])
    return " AND ".join(w), a


BASIS_SELECT = (
    "SELECT e.*, k.name kategorie, k.icon kategorie_icon, k.farbe kategorie_farbe, ko.name konto "
    "FROM eintraege e LEFT JOIN kategorien k ON k.id=e.kategorie_id LEFT JOIN konten ko ON ko.id=e.konto_id "
)


def summe(von, bis, extra=""):
    r = db.q1(f"SELECT COALESCE(SUM({BETRAG}),0) s, COUNT(*) n FROM eintraege e WHERE e.datum >= ? "
              f"AND e.datum < date(?, '+1 day') {extra}", (von, bis))
    return round(r["s"], 2), r["n"]


def zeitreihe(p, einheit):
    where, args = filter_sql(p)
    gruppe = {
        "tag": "date(e.datum)",
        "woche": "date(e.datum, 'weekday 0', '-6 days')",
        "monat": "strftime('%Y-%m', e.datum)",
        "jahr": "strftime('%Y', e.datum)",
    }[einheit]
    return db.q(
        f"SELECT {gruppe} periode, ROUND(SUM({BETRAG}),2) summe, COUNT(*) anzahl "
        f"FROM eintraege e LEFT JOIN kategorien k ON k.id=e.kategorie_id WHERE {where} "
        f"GROUP BY periode ORDER BY periode", args)


def nach_kategorien(p):
    where, args = filter_sql(p)
    zeilen = db.q(
        f"SELECT e.kategorie_id id, COALESCE(k.name,'Ohne Kategorie') name, COALESCE(k.icon,'❔') icon, "
        f"COALESCE(k.farbe,'#9AA3AE') farbe, ROUND(SUM({BETRAG}),2) summe, COUNT(*) anzahl "
        f"FROM eintraege e LEFT JOIN kategorien k ON k.id=e.kategorie_id WHERE {where} "
        f"GROUP BY e.kategorie_id ORDER BY summe DESC", args)
    gesamt = sum(z["summe"] or 0 for z in zeilen)
    for z in zeilen:
        z["anteil"] = round((z["summe"] or 0) / gesamt * 100, 1) if gesamt else 0
    return {"gesamt": round(gesamt, 2), "kategorien": zeilen}


def kennzahlen():
    heute = date.today()
    wochenstart = heute - timedelta(days=heute.weekday())
    monatsstart = heute.replace(day=1)
    vormonat_ende = monatsstart - timedelta(days=1)
    vormonat_start = vormonat_ende.replace(day=1)
    vormonat_bis_tag = min(heute.day, vormonat_ende.day)
    jahresstart = heute.replace(month=1, day=1)
    i = date.isoformat
    return {
        "heute": summe(i(heute), i(heute))[0],
        "woche": summe(i(wochenstart), i(heute))[0],
        "monat": summe(i(monatsstart), i(heute)),
        "vormonat": summe(i(vormonat_start), i(vormonat_ende))[0],
        "vormonat_bis_heute": summe(i(vormonat_start), i(vormonat_start.replace(day=vormonat_bis_tag)))[0],
        "jahr": summe(i(jahresstart), i(heute))[0],
        "pruefen": db.q1("SELECT COUNT(*) n FROM eintraege WHERE pruefen=1")["n"],
        "monatsstart": i(monatsstart),
        "heute_datum": i(heute),
    }


# ---------------------------------------------------------------- Home Assistant

def _ha(pfad, json_daten):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return
    try:
        httpx.post(f"http://supervisor/core/api/{pfad}", json=json_daten,
                   headers={"Authorization": f"Bearer {token}"}, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.info("HA-Aufruf %s fehlgeschlagen: %s", pfad, e)


def sensoren_senden():
    praefix = optionen().get("sensor_praefix") or "allgemein_finanzen_ausgaben"
    k = kennzahlen()
    monat_summe, monat_anzahl = k["monat"]
    top = nach_kategorien({"von": k["monatsstart"], "bis": k["heute_datum"]})["kategorien"][:6]
    letzte = db.q(BASIS_SELECT + "ORDER BY e.datum DESC, e.id DESC LIMIT 10")
    basis = {"unit_of_measurement": "€", "device_class": "monetary", "icon": "mdi:cash-multiple"}

    def sende(suffix, wert, name, **attr):
        _ha(f"states/sensor.{praefix}_{suffix}", {"state": wert, "attributes": {**basis, "friendly_name": name, **attr}})

    sende("heute", k["heute"], "Allgemein Finanzen Ausgaben heute")
    sende("woche", k["woche"], "Allgemein Finanzen Ausgaben Woche")
    sende("monat", monat_summe, "Allgemein Finanzen Ausgaben Monat",
          anzahl=monat_anzahl, vormonat=k["vormonat"], vormonat_bis_heute=k["vormonat_bis_heute"],
          differenz_vormonat=round(monat_summe - k["vormonat_bis_heute"], 2),
          kategorien=[{"name": t["name"], "icon": t["icon"], "summe": t["summe"]} for t in top],
          letzte=[{"datum": e["datum"][:16].replace("T", " "), "bezeichnung": e["bezeichnung"],
                   "kategorie": e["kategorie"], "betrag": e["betrag_eur"], "pruefen": bool(e["pruefen"])}
                  for e in letzte])
    sende("jahr", k["jahr"], "Allgemein Finanzen Ausgaben Jahr")
    _ha(f"states/sensor.{praefix}_zu_prufen",
        {"state": k["pruefen"], "attributes": {"friendly_name": "Allgemein Finanzen Ausgaben zu prüfen",
                                              "icon": "mdi:tag-search", "unit_of_measurement": "Einträge"}})


def ereignis_neue_ausgabe(eintrag):
    _ha("events/ausgaben_neuer_eintrag", {
        "id": eintrag["id"], "bezeichnung": eintrag["bezeichnung"], "kategorie": eintrag.get("kategorie"),
        "betrag": eintrag["betrag"], "waehrung": eintrag["waehrung"], "betrag_eur": eintrag["betrag_eur"],
        "pruefen": bool(eintrag["pruefen"]), "haendler": eintrag.get("haendler_roh"),
    })
