"""Eigenanteil: Eingaben lesen, Regeln anwenden, Summen und API."""
import json
import os
import sys
import tempfile

T = tempfile.mkdtemp(prefix="ausgaben_test_")
json.dump({"ki_aktiv": False, "pytr_verzeichnis": T, "zugriffs_token": "test"}, open(T + "/opt.json", "w"))
os.environ.update(AUSGABEN_DB=T + "/t.db", AUSGABEN_OPTIONEN=T + "/opt.json")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ausgaben"))

from app import anteile, auswertung, db  # noqa: E402


def pruef(name, bed):
    print(("OK   " if bed else "FEHL ") + name)
    assert bed, name


db.verbindung()
kid = db.q1("SELECT id FROM kategorien LIMIT 1")["id"]


def eintrag(betrag, haendler, bez, datum="2026-11-05T10:00:00", quelle="tr"):
    eid, _ = db.x("INSERT INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, haendler_roh, "
                  "konto_id, quelle, pruefen, erstellt, geaendert) VALUES (?,?, 'EUR', 1, ?, ?, ?, ?, 1, ?, 0, 'x', 'x')",
                  (datum, betrag, betrag, kid, bez, haendler, quelle))
    return eid


# Eingaben
pruef("1/6 als Bruch", anteile.lesen("1/6") == ("bruch", 1 / 6))
pruef("50 % als Bruch", anteile.lesen("50 %") == ("bruch", 0.5))
pruef("2,67 € als Betrag", anteile.lesen("2,67 €") == ("betrag", 2.67))
pruef("Leer = kein Anteil", anteile.lesen("  ") is None)
for falsch in ("7/6", "0", "abc", "0/5", "150%"):
    try:
        anteile.lesen(falsch)
        pruef(f"'{falsch}' abgelehnt", False)
    except ValueError:
        pruef(f"'{falsch}' abgelehnt", True)
pruef("Text 1/6", anteile.text("bruch", 1 / 6) == "1/6")
pruef("Text 2,67 €", anteile.text("betrag", 2.67) == "2,67 €")
pruef("Fester Betrag nie ueber 100 %", anteile.faktor("betrag", 20, 15.99) == 1.0)

# Bestand: zwei alte Disney-Abbuchungen, ein Netflix
alt1 = eintrag(15.99, "DISNEY PLUS", "Disney+", "2026-10-05T10:00:00")
alt2 = eintrag(15.99, "Disney Plus Monthly", "Disney+", "2026-09-05T10:00:00")
netflix = eintrag(13.99, "NETFLIX.COM", "Netflix", "2026-10-07T10:00:00")
anteile.neue_anwenden()
pruef("Ohne Regel: voller Betrag", db.q1("SELECT anteil FROM eintraege WHERE id=?", (alt1,))["anteil"] == 1.0)

# Regel 1/6 rueckwirkend
anteile.regel_setzen(db.q1("SELECT * FROM eintraege WHERE id=?", (alt1,)), "bruch", 1 / 6, rueckwirkend=True)
a = {r["id"]: r["anteil"] for r in db.q("SELECT id, anteil FROM eintraege")}
pruef("Rueckwirkend auf beide Disney-Eintraege", abs(a[alt1] - 1 / 6) < 1e-9 and abs(a[alt2] - 1 / 6) < 1e-9)
pruef("Netflix unberuehrt", a[netflix] == 1.0)

# Teilnehmer steigt aus: ab Oktober 1/5, September bleibt 1/6
anteile.regel_setzen(db.q1("SELECT * FROM eintraege WHERE id=?", (alt1,)), "bruch", 1 / 5)
a = {r["id"]: r["anteil"] for r in db.q("SELECT id, anteil FROM eintraege")}
pruef("Neuer Faktor ab Oktober-Eintrag", abs(a[alt1] - 1 / 5) < 1e-9)
pruef("September behaelt 1/6", abs(a[alt2] - 1 / 6) < 1e-9)
anteile.regel_setzen(db.q1("SELECT * FROM eintraege WHERE id=?", (alt1,)), "bruch", 1 / 6)

# Neuer automatischer Eintrag bekommt den Anteil
neu = eintrag(15.99, "DISNEY PLUS", "Disney+", "2026-11-05T10:00:00")
pruef("Neuer Eintrag noch ungeprueft", db.q1("SELECT anteil FROM eintraege WHERE id=?", (neu,))["anteil"] is None)
pruef("neue_anwenden meldet 1 geteilten", anteile.neue_anwenden() == 1)
pruef("Neuer Eintrag 1/6", abs(db.q1("SELECT anteil FROM eintraege WHERE id=?", (neu,))["anteil"] - 1 / 6) < 1e-9)

# Summen rechnen mit Eigenanteil
s = auswertung.summe("2026-11-01", "2026-11-30")
pruef("Monatssumme nur Eigenanteil (2,67)", abs(s[0] - 15.99 / 6) < 0.01)

# Fester Betrag, Preiserhoehung
anteile.regel_setzen(db.q1("SELECT * FROM eintraege WHERE id=?", (neu,)), "betrag", 2.67, rueckwirkend=False)
teurer = eintrag(17.99, "DISNEY PLUS", "Disney+", "2026-12-05T10:00:00")
anteile.neue_anwenden()
f = db.q1("SELECT anteil FROM eintraege WHERE id=?", (teurer,))["anteil"]
pruef("Fester Betrag bleibt 2,67 bei 17,99", abs(17.99 * f - 2.67) < 0.005)
pruef("Nur eine Regel je Haendler", db.q1("SELECT COUNT(*) n FROM anteil_regeln")["n"] == 1)

# Loeschen rueckwirkend
rid = db.q1("SELECT id FROM anteil_regeln")["id"]
anteile.regel_loeschen(rid, rueckwirkend=True)
pruef("Nach Loeschen wieder volle Betraege", db.q1("SELECT COUNT(*) n FROM eintraege WHERE anteil < 1")["n"] == 0)

# API: Eintrag mit Anteil und Regel anlegen
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

with TestClient(app) as c:
    H = {"X-Ausgaben-Token": "test"}
    r = c.post("/api/eintraege", headers=H, json={"datum": "2026-11-20T12:00", "betrag": 15.99, "waehrung": "EUR",
                                                   "kategorie_id": kid, "bezeichnung": "Spotify Familie", "konto_id": 1,
                                                   "anteil_text": "1/6", "anteil_regel": True})
    pruef("API: Anlegen mit 1/6", r.status_code == 200 and abs(r.json()["anteil"] - 1 / 6) < 1e-9)
    pruef("API: Regel angelegt", any(x["text"] == "1/6" for x in c.get("/api/anteile", headers=H).json()))
    r = c.put(f"/api/eintraege/{r.json()['id']}", headers=H, json={"anteil_text": "9/6"})
    pruef("API: Unsinn abgelehnt (422)", r.status_code == 422)
    r = c.post("/api/eintraege", headers=H, json={"datum": "2026-11-21T12:00", "betrag": 15.99, "waehrung": "EUR",
                                                   "kategorie_id": kid, "bezeichnung": "Spotify Familie", "konto_id": 1})
    pruef("API: Folgeeintrag erbt Anteil per Regel", abs(r.json()["anteil"] - 1 / 6) < 1e-9)
    rid = c.get("/api/anteile", headers=H).json()[0]["id"]
    pruef("API: Regel loeschen", c.delete(f"/api/anteile/{rid}?rueckwirkend=true", headers=H).json()["ok"])

print("ALLE TESTS OK")
