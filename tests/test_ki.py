import base64, io, json, os, sys
from datetime import date, timedelta
import tempfile
T = tempfile.mkdtemp(prefix="ausgaben_test_")
os.makedirs(T, exist_ok=True)
for f in os.listdir(T):
    if f.startswith("t.db"): os.remove(os.path.join(T, f))
OPT = T + "/opt.json"
json.dump({"ki_aktiv": True, "anthropic_api_key": "sk-test", "pytr_verzeichnis": T}, open(OPT, "w"))
os.environ.update(AUSGABEN_DB=T + "/t.db", AUSGABEN_OPTIONEN=OPT)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ausgaben"))
import httpx
from PIL import Image
from app import beleg, db, kategorisierung, ki_nutzung
db.verbindung(); ki_nutzung.schema_anlegen()
db.x("INSERT OR REPLACE INTO kurse(datum, waehrung, kurs) VALUES (?, 'USD', 0.9)", (date.today().isoformat(),))
def pruef(name, bed):
    print(("OK   " if bed else "FEHL ") + name); assert bed, name
nah = lambda a, b: a is not None and abs(a - b) < 1e-12

class A:
    def __init__(s, st, d): s.status_code, s._d = st, d
    def json(s): return s._d
    def raise_for_status(s):
        if s.status_code >= 400: raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "https://x"), response=s)
PLAN = []
def fake_post(url, headers=None, json=None, timeout=None):
    return PLAN.pop(0)
kategorisierung.httpx.post = fake_post
beleg.httpx.post = fake_post
kid = db.q1("SELECT id FROM kategorien LIMIT 1")["id"]

# 1 Kategorisierung: 3 Umsaetze, Tokens erfasst
PLAN.append(A(200, {"content": [{"type": "text", "text": json.dumps([{"nr": i, "kategorie_id": kid, "bezeichnung": "X", "sicherheit": 0.9} for i in range(3)])}],
                    "usage": {"input_tokens": 1000, "output_tokens": 200}}))
kategorisierung.frage_claude([{"nr": i, "haendler": f"H{i}"} for i in range(3)])
z = db.q1("SELECT * FROM ki_nutzung ORDER BY id DESC")
pruef("Kategorisierung erfasst", z["zweck"] == "kategorisierung" and z["eingabe"] == 1000 and z["ausgabe"] == 200 and z["anzahl"] == 3 and z["ok"] == 1)
pruef("Kosten Haiku 5.5: 0,0002 $", nah(ki_nutzung.kosten_usd(z), 0.0002))

# 2 Antwort unbrauchbar -> Tokens trotzdem gezaehlt, ok=0
PLAN.append(A(200, {"content": [{"type": "text", "text": "kein json"}], "usage": {"input_tokens": 500, "output_tokens": 10}}))
kategorisierung.frage_claude([{"nr": 0, "haendler": "Y"}])
z = db.q1("SELECT * FROM ki_nutzung ORDER BY id DESC")
pruef("Kaputte Antwort: Tokens gezaehlt, als Fehler markiert", z["ok"] == 0 and z["eingabe"] == 500)

# 3 HTTP-Fehler -> 0 Tokens, Fehler
PLAN.append(A(529, {"error": {"message": "overloaded"}}))
kategorisierung.frage_claude([{"nr": 0, "haendler": "Y"}])
z = db.q1("SELECT * FROM ki_nutzung ORDER BY id DESC")
pruef("HTTP-Fehler: 0 Tokens, Fehler", z["ok"] == 0 and z["eingabe"] == 0)

# 4 Kassenbon
buf = io.BytesIO(); Image.new("RGB", (50, 80), "white").save(buf, "JPEG")
bild = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
PLAN.append(A(200, {"content": [{"type": "text", "text": json.dumps({"lesbar": True, "haendler": "REWE", "betrag": 9.46, "kategorie_id": kid})}],
                    "usage": {"input_tokens": 2400, "output_tokens": 300}}))
beleg.lesen(bild)
z = db.q1("SELECT * FROM ki_nutzung ORDER BY id DESC")
pruef("Kassenbon erfasst", z["zweck"] == "beleg" and z["anzahl"] == 1 and z["eingabe"] == 2400)

# 5 Test-Zweck
PLAN.append(A(200, {"content": [{"type": "text", "text": json.dumps([{"nr": 0, "kategorie_id": kid, "bezeichnung": "B", "sicherheit": 0.9}])}], "usage": {"input_tokens": 800, "output_tokens": 30}}))
kategorisierung.frage_claude([{"nr": 0, "haendler": "Backstube"}], zweck="test")
pruef("Test separat gezaehlt", db.q1("SELECT zweck FROM ki_nutzung ORDER BY id DESC")["zweck"] == "test")

# 6 Uebersicht
u = ki_nutzung.uebersicht()
heute, monat, gesamt = u["zeitraeume"]
erwartet_usd = (1000*0.1 + 200*0.5 + 500*0.1 + 10*0.5 + 2400*0.1 + 300*0.5 + 800*0.1 + 30*0.5) / 1e6
pruef("Heute: 5 Anfragen, 2 Fehler", heute["anfragen"] == 5 and heute["fehler"] == 2)
pruef("Summe USD stimmt", nah(gesamt["usd"], round(erwartet_usd, 6)))
pruef("EUR mit Kurs 0,9", nah(gesamt["eur"], round(erwartet_usd * 0.9, 6)))
zw = {z["zweck"]: z for z in u["zwecke_monat"]}
pruef("Aufteilung: Kategorisierung 3 Anfragen / 5 Umsaetze", zw["kategorisierung"]["anfragen"] == 3 and zw["kategorisierung"]["anzahl"] == 5)
pruef("Aufteilung: 1 Bon", zw["beleg"]["anzahl"] == 1)
pruef("30 Tage, heute zuletzt", len(u["tage"]) == 30 and u["tage"][-1]["anfragen"] == 5)
pruef("Monatskosten fuer Mehr-Menue", nah(round(ki_nutzung.monat_eur(), 9), round(erwartet_usd * 0.9, 9)))

# 7 Haiku-Staffel > 100k
z = {"modell": "claude-haiku-5-5", "eingabe": 150_000, "ausgabe": 1000, "cache_lesen": 0, "cache_schreiben": 0}
pruef("Staffel ueber 100k: Fuenffach", nah(ki_nutzung.kosten_usd(z), (150_000*0.5 + 1000*2.5) / 1e6))

# 8 Modell-ID mit Datum, unbekanntes Modell, eigener Preis
pruef("Haiku 4.5 mit Datum erkannt", ki_nutzung.preis("claude-haiku-4-5-20251001")[:2] == (1.0, 5.0))
pruef("Sonnet 5.5 nicht als Sonnet 5", ki_nutzung.preis("claude-sonnet-5-5")[:2] == (2.0, 10.0))
pruef("Unbekanntes Modell ohne Preis", ki_nutzung.preis("claude-neu-9") is None)
ki_nutzung.preis_setzen("claude-haiku-5-5", 0.2, 1.0)
pruef("Eigener Preis gilt", ki_nutzung.preis("claude-haiku-5-5") == (0.2, 1.0, "eigen"))
pruef("Eigener Preis: keine Staffel", nah(ki_nutzung.kosten_usd(z), (150_000*0.2 + 1000*1.0) / 1e6))
ki_nutzung.preis_setzen("claude-haiku-5-5", None, None)
pruef("Zurueck auf Listenpreis", ki_nutzung.preis("claude-haiku-5-5")[2] == "standard")
try: ki_nutzung.preis_setzen("x", -1, 1); pruef("Negativer Preis abgelehnt", False)
except ValueError: pruef("Negativer Preis abgelehnt", True)
print("ALLE TESTS OK")
