"""Eigenanteil: Ausgaben, von denen nur ein Teil auf einen selbst entfaellt (z. B. geteiltes Abo).

Je Eintrag steht in eintraege.anteil der Faktor (0..1) des eigenen Anteils; NULL heisst
"noch nicht geprueft", 1 heisst voller Betrag. Auswertungen rechnen mit betrag_eur * anteil.
Regeln je Haendler (Tabelle anteil_regeln) setzen den Anteil fuer neue Eintraege automatisch,
entweder als Bruchteil (1/6, 50 %) oder als fester Betrag (2,67).
"""
import re
from fractions import Fraction

from . import db, kategorisierung


def lesen(text):
    """'1/6' -> ('bruch', 0.1667); '50 %' -> ('bruch', 0.5); '2,67' -> ('betrag', 2.67); '' -> None."""
    t = (text or "").strip().replace("€", "").replace(" ", "").replace(",", ".")
    if not t:
        return None
    m = re.fullmatch(r"(\d+)/(\d+)", t)
    if m:
        z, n = int(m.group(1)), int(m.group(2))
        if n == 0 or z > n or z == 0:
            raise ValueError("Bruch zwischen 1/n und 1/1 angeben, z. B. 1/6")
        return "bruch", z / n
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", t)
    if m:
        p = float(m.group(1))
        if not 0 < p <= 100:
            raise ValueError("Prozent zwischen 1 und 100 angeben")
        return "bruch", p / 100
    try:
        w = float(t)
    except ValueError:
        raise ValueError("Anteil als Bruch (1/6), Prozent (50 %) oder Betrag (2,67) angeben")
    if w <= 0:
        raise ValueError("Betrag muss größer als 0 sein")
    return "betrag", round(w, 2)


def faktor(art, wert, betrag):
    if art == "bruch":
        return min(1.0, max(0.0, wert))
    if not betrag:
        return 1.0
    return min(1.0, round(wert, 2) / abs(betrag))


def text(art, wert):
    if art == "betrag":
        return f"{wert:.2f}".replace(".", ",") + " €"
    f = Fraction(wert).limit_denominator(12)
    if abs(float(f) - wert) < 1e-6 and f.denominator > 1:
        return f"{f.numerator}/{f.denominator}"
    return f"{wert * 100:g} %".replace(".", ",")


def schluessel(e):
    return kategorisierung.normalisiere(e.get("haendler_roh") or e.get("bezeichnung") or "")


def _passt(muster, s):
    return bool(s) and (s == muster or s.startswith(muster + " "))


def regel_fuer(e, regeln=None):
    s = schluessel(e)
    for r in regeln if regeln is not None else alle_regeln():
        if _passt(r["muster"], s):
            return r
    return None


def alle_regeln():
    return db.q("SELECT * FROM anteil_regeln ORDER BY LENGTH(muster) DESC")


def neue_anwenden():
    """Setzt den Anteil fuer alle noch ungeprueften Eintraege. Gibt die Zahl geaenderter Eintraege zurueck."""
    offen = db.q("SELECT id, betrag, haendler_roh, bezeichnung FROM eintraege WHERE anteil IS NULL")
    if not offen:
        return 0
    regeln = alle_regeln()
    werte = []
    for e in offen:
        r = regel_fuer(e, regeln) if regeln else None
        werte.append((faktor(r["art"], r["wert"], e["betrag"]) if r else 1.0, e["id"]))
    db.transaktion(lambda c: c.executemany("UPDATE eintraege SET anteil=? WHERE id=?", werte))
    return sum(1 for f, _ in werte if f < 1)


def regel_setzen(e, art, wert, rueckwirkend=True):
    """Regel fuer den Haendler dieses Eintrags anlegen oder aendern."""
    muster = schluessel(e)
    if not muster:
        raise ValueError("Für diesen Eintrag lässt sich kein Händler erkennen")
    name = e.get("bezeichnung") or e.get("haendler_roh") or muster
    db.x("INSERT INTO anteil_regeln(muster, name, art, wert, geaendert) VALUES (?,?,?,?,?) "
         "ON CONFLICT(muster) DO UPDATE SET art=excluded.art, wert=excluded.wert, name=excluded.name, "
         "geaendert=excluded.geaendert", (muster, name, art, wert, db.jetzt_iso()))
    if rueckwirkend:
        werte = [(faktor(art, wert, x["betrag"]), x["id"])
                 for x in db.q("SELECT id, betrag, haendler_roh, bezeichnung FROM eintraege")
                 if _passt(muster, schluessel(x))]
        db.transaktion(lambda c: c.executemany("UPDATE eintraege SET anteil=? WHERE id=?", werte))
    return muster


def regel_loeschen(rid, rueckwirkend=False):
    r = db.q1("SELECT * FROM anteil_regeln WHERE id=?", (rid,))
    if not r:
        return
    db.x("DELETE FROM anteil_regeln WHERE id=?", (rid,))
    if rueckwirkend:
        ids = [(x["id"],) for x in db.q("SELECT id, haendler_roh, bezeichnung FROM eintraege WHERE anteil < 1")
               if _passt(r["muster"], schluessel(x))]
        db.transaktion(lambda c: c.executemany("UPDATE eintraege SET anteil=1 WHERE id=?", ids))


def regeln_liste():
    aus = [{**r, "text": text(r["art"], r["wert"])} for r in alle_regeln()]
    return sorted(aus, key=lambda r: (r["name"] or r["muster"]).lower())
