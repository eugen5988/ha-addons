import base64, io, json, os, sys
import tempfile
T = tempfile.mkdtemp(prefix="ausgaben_test_")
os.makedirs(T + "/pytr", exist_ok=True)
for f in os.listdir(T):
    if f.startswith("t.db"): os.remove(os.path.join(T, f))
OPT = T + "/opt.json"
def optionen_setzen(**kw):
    json.dump({"ki_aktiv": True, "anthropic_api_key": "sk-test", "pytr_verzeichnis": T + "/pytr", **kw}, open(OPT, "w"))
optionen_setzen()
os.environ.update(AUSGABEN_DB=T + "/t.db", AUSGABEN_OPTIONEN=OPT)
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER); sys.path.insert(0, os.path.join(HIER, "..", "ausgaben"))
import httpx
from PIL import Image
from app import beleg, db, fints_sync, tr_sync
db.verbindung(); fints_sync.schema_anlegen()

def pruef(name, bed):
    print(("OK   " if bed else "FEHL ") + name); assert bed, name

buf = io.BytesIO(); Image.new("RGB", (300, 600), "white").save(buf, "JPEG")
BILD = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
kats = {k["name"]: k["id"] for k in db.q("SELECT id, name FROM kategorien")}
print("Kategorien:", list(kats)[:12])
k_leb = next(v for n, v in kats.items() if "Lebensmittel" in n or "Einkauf" in n)
k_andere = next(v for v in kats.values() if v != k_leb)

ANTWORT = {}
ANFRAGEN = []
class Antwort:
    def __init__(self, status, daten): self.status_code, self._d = status, daten
    def json(self): return self._d
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("fehler", request=httpx.Request("POST", "https://x"), response=self)
def fake_post(url, headers=None, json=None, timeout=None):
    ANFRAGEN.append(json)
    if ANTWORT.get("status", 200) != 200:
        return Antwort(ANTWORT["status"], {"error": {"message": "image too large"}})
    return Antwort(200, {"content": [{"type": "text", "text": ANTWORT["text"]}]})
beleg.httpx.post = fake_post
def claude(d, fence=False):
    ANTWORT.clear(); ANTWORT["text"] = ("```json\n" if fence else "") + json.dumps(d, ensure_ascii=False) + ("\n```" if fence else "")

BASIS = {"lesbar": True, "haendler": "REWE", "datum": "2026-11-20", "uhrzeit": "17:42", "betrag": 23.45, "waehrung": "EUR",
         "zahlungsart": "bar", "positionen": [{"text": "Milch 1,5%", "betrag": 1.19}, {"text": "Pfand", "betrag": -0.25}],
         "kategorie_id": k_leb, "bezeichnung": "Rewe", "sicherheit": 0.9, "hinweis": ""}

# 1 KI aus
optionen_setzen(ki_aktiv=False)
try: beleg.lesen(BILD); pruef("KI aus -> Fehlermeldung", False)
except beleg.BelegFehler as e: pruef("KI aus -> Fehlermeldung", "API-Key" in str(e))
optionen_setzen()

# 2 kaputtes Bild
try: beleg.lesen("data:text/plain;base64,abc"); pruef("Kein Bild abgelehnt", False)
except beleg.BelegFehler: pruef("Kein Bild abgelehnt", True)

# 3 Normalfall, Antwort in Codeblock
claude(BASIS, fence=True)
r = beleg.lesen(BILD)
a = ANFRAGEN[-1]
inhalt = a["messages"][0]["content"]
pruef("Anfrage: Bild als base64 jpeg", inhalt[0]["type"] == "image" and inhalt[0]["source"]["media_type"] == "image/jpeg")
pruef("Anfrage: Kategorienliste dabei", f"id {k_leb}:" in inhalt[1]["text"] and a["model"] == "claude-haiku-5-5")
pruef("Betrag/Datum/Uhrzeit", r["betrag"] == 23.45 and r["datum"] == "2026-11-20" and r["uhrzeit"] == "17:42")
pruef("KI-Kategorie uebernommen", r["kategorie_id"] == k_leb and r["kategorisiert_durch"] == "ki" and r["sicherheit"] == 0.9)
pruef("Bar -> Konto Bargeld", r["konto_id"] == db.konto_id("Bargeld"))
pruef("Positionen inkl. Pfand negativ", len(r["positionen"]) == 2 and r["positionen"][1]["betrag"] == -0.25)
pruef("Keine Duplikate", r["duplikate"] == [])

# 4 Gelernte Regel hat Vorrang, ohne Trefferzaehler
db.x("INSERT INTO regeln(muster, kategorie_id, bezeichnung, quelle, geaendert) VALUES ('rewe', ?, 'Supermarkt', 'nutzer', '2026-01-01')", (k_andere,))
claude(BASIS); r = beleg.lesen(BILD)
pruef("Regel vor KI", r["kategorie_id"] == k_andere and r["bezeichnung"] == "Supermarkt" and r["kategorisiert_durch"] == "regel")
pruef("Regel-Treffer nicht hochgezaehlt", db.q1("SELECT treffer FROM regeln WHERE muster='rewe'")["treffer"] in (0, None))
db.x("DELETE FROM regeln")

# 5 Verlauf vor KI
for i in range(3):
    db.x("INSERT INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, konto_id, quelle, pruefen, kategorisiert_durch, erstellt, geaendert) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (f"2026-10-0{i+1}T10:00:00", 5.0 + i, "EUR", 1, 5.0 + i, k_andere, "dm", 1, "next", 0, "next", "x", "x"))
claude({**BASIS, "haendler": "dm-drogerie markt"}); r = beleg.lesen(BILD)
pruef("Verlauf vor KI", r["kategorie_id"] == k_andere and r["kategorisiert_durch"] == "verlauf")

# 6 Duplikat: TR-Umsatz gleicher Betrag am Folgetag
db.x("INSERT INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, haendler_roh, konto_id, quelle, quelle_ref, pruefen, erstellt, geaendert) VALUES ('2026-11-21T09:00:00', 23.45, 'EUR', 1, 23.45, ?, 'Rewe', 'REWE Markt GmbH', 1, 'tr', 'tr:x1', 0, 'x', 'x')", (k_leb,))
claude({**BASIS, "zahlungsart": "karte"}); r = beleg.lesen(BILD)
pruef("Duplikat gefunden (TR, +1 Tag)", len(r["duplikate"]) == 1 and r["duplikate"][0]["quelle"] == "tr")
pruef("Karte -> kein Konto-Vorschlag", r["konto_id"] is None and r["zahlungsart"] == "karte")

# 7 unlesbar
claude({"lesbar": False, "hinweis": "Auf dem Foto ist kein Kassenbon zu sehen"}); r = beleg.lesen(BILD)
pruef("Kein Bon erkannt", r == {"lesbar": False, "hinweis": "Auf dem Foto ist kein Kassenbon zu sehen"})

# 8 fremde Waehrung, Zukunftsdatum, Komma-Betrag
claude({**BASIS, "waehrung": "XYZ", "datum": "2030-01-01", "betrag": "12,50"}); r = beleg.lesen(BILD)
pruef("Unbekannte Waehrung -> EUR + Hinweis", r["waehrung"] == "EUR" and "XYZ" in r["hinweis"])
pruef("Datum in der Zukunft verworfen", r["datum"] is None and r["uhrzeit"] is None)
pruef("Betrag mit Komma gelesen", r["betrag"] == 12.5)

# 9 API-Fehler
ANTWORT.clear(); ANTWORT["status"] = 400
try: beleg.lesen(BILD); pruef("API-Fehler verstaendlich", False)
except beleg.BelegFehler as e: pruef("API-Fehler verstaendlich", "400" in str(e) and "image too large" in str(e))

# 10 Ungueltige Kategorie der KI -> keine Kategorie
claude({**BASIS, "haendler": "Kiosk Ecke", "kategorie_id": 99999}); r = beleg.lesen(BILD)
pruef("Unbekannte Kategorie-ID verworfen", r["kategorie_id"] is None and r["bezeichnung"] == "Rewe")

# 11 TR-Import markiert Handeintrag-Doppel
db.x("INSERT INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, konto_id, quelle, pruefen, kategorisiert_durch, erstellt, geaendert) VALUES ('2026-11-19T12:00:00', 61.10, 'EUR', 1, 61.10, ?, 'Tanken', 1, 'manuell', 0, 'nutzer', 'x', 'x')", (k_andere,))
hand_id = db.q1("SELECT id FROM eintraege WHERE quelle='manuell'")["id"]
from datetime import datetime, timezone
items = [{"id": "z1", "eventType": "CARD_TRANSACTION", "timestamp": "2026-11-20T18:02:00.000+0000", "title": "Shell", "amount": {"value": -61.10, "currency": "EUR"}, "status": "EXECUTED"},
         {"id": "z2", "eventType": "CARD_TRANSACTION", "timestamp": "2026-11-20T18:05:00.000+0000", "title": "Kiosk", "amount": {"value": -3.20, "currency": "EUR"}, "status": "EXECUTED"}]
optionen_setzen(ki_aktiv=False)
ids = tr_sync._verarbeiten(items, datetime(2026, 11, 1, tzinfo=timezone.utc))
z1 = db.q1("SELECT pruefen, notiz FROM eintraege WHERE quelle_ref='tr:z1'"); z2 = db.q1("SELECT notiz FROM eintraege WHERE quelle_ref='tr:z2'")
pruef("TR: Doppel zu Handeintrag markiert", z1["pruefen"] == 1 and z1["notiz"].startswith(f"Mögliches Duplikat von Eintrag {hand_id}"))
pruef("TR: anderer Umsatz unberuehrt", "Duplikat" not in (z2["notiz"] or ""))
print("ALLE TESTS OK")
