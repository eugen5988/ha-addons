import json, os, sys, time, importlib
import tempfile
T = tempfile.mkdtemp(prefix="ausgaben_test_")
os.makedirs(T + "/pytr/.pytr", exist_ok=True)
for f in os.listdir(T):
    if f.startswith("t.db"): os.remove(os.path.join(T, f))
open(T + "/pytr/.pytr/credentials", "w").write("+491701234567\n1234\n")
json.dump({"ki_aktiv": False, "pytr_verzeichnis": T + "/pytr", "tr_aktiv": False}, open(T + "/opt.json", "w"))
os.environ.update(AUSGABEN_DB=T + "/t.db", AUSGABEN_OPTIONEN=T + "/opt.json")
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER); sys.path.insert(0, os.path.join(HIER, "..", "ausgaben"))
import fake_pytr, pytr.api
pytr.api.TradeRepublicApi = fake_pytr.TradeRepublicApi
from app import db, tr_sync, fints_sync
db.verbindung(); fints_sync.schema_anlegen()
Z = fake_pytr.ZUSTAND

def pruef(name, bed):
    print(("OK   " if bed else "FEHL ") + name); assert bed, name

# 1 Migration aus der pytr-Datei
tr_sync.migrieren()
k = tr_sync.konfig()
pruef("Migration: Telefon uebernommen", k["telefon"] == "+491701234567" and k["eingerichtet"])
pruef("Migration: PIN gespeichert", k["pin_gespeichert"] and "pin" not in k)
pruef("Migration: aktiv aus alter Option (false)", k["aktiv"] is False)
db.setze_einstellung("tr_aktiv", "1")
pruef("Anzeige maskiert", k["telefon_anzeige"] == "+49170 … 567")

# 2 Automatik aus -> kein Abruf, manuell schon
db.setze_einstellung("tr_aktiv", "0")
pruef("Automatik aus: kein Abruf", tr_sync.synchronisiere() == [] and not Z["logins"])
r = tr_sync.synchronisiere(manuell=True)
pruef("Manuell ohne Sitzung: login_noetig", r == [] and tr_sync.status["zustand"] == "login_noetig")
db.setze_einstellung("tr_aktiv", "1")

# 3 Validierung
try:
    tr_sync.konfig_speichern({"pin": "12345"}); pruef("PIN 5-stellig abgelehnt", False)
except ValueError as e:
    pruef("PIN 5-stellig abgelehnt", "4 Ziffern" in str(e))
try:
    tr_sync.konfig_speichern({"telefon": "123"}); pruef("Telefon zu kurz abgelehnt", False)
except ValueError:
    pruef("Telefon zu kurz abgelehnt", True)
k = tr_sync.konfig_speichern({"telefon": "0170 123 4567", "intervall": 2, "import_ab": "2026-11-01T00:00:00"})
pruef("Telefon normalisiert, unveraendert", k["telefon"] == "+491701234567")
pruef("Intervall auf mind. 5 begrenzt", k["intervall"] == 5)

# 4 PIN geloescht -> Login fragt PIN ab
tr_sync.konfig_speichern({"pin": ""})
s = tr_sync.login_starten()
pruef("Ohne PIN: pin_noetig", s["zustand"] == "pin_noetig" and not Z["logins"])

# 5 Falsche PIN
Z["pin_falsch"] = True
s = dict(tr_sync.login_starten("9999"))
pruef("Falsche PIN: verstaendliche Meldung", s["zustand"] == "fehler" and "PIN" in s["meldung"])
Z["pin_falsch"] = False

# 6 Login mit PIN + speichern -> warte -> ok -> Abruf
gemeldet = []
tr_sync.nach_abruf = lambda ids: gemeldet.append(ids)
s = dict(tr_sync.login_starten("4321", True))
pruef("Login: warte auf App", s["zustand"] == "warte" and Z["logins"][-1] == ("+491701234567", "4321"))
pruef("Login: PIN gespeichert", db.einstellung("tr_pin") == "4321")
for _ in range(50):
    time.sleep(0.1)
    if gemeldet: break
pruef("Nach Bestaetigung: ok", tr_sync.login_status["zustand"] == "ok")
pruef("Abruf nach Login: 2 neue Eintraege, Rueckruf", gemeldet and len(gemeldet[0]) == 2)
pruef("Cookie-Datei angelegt", os.path.exists(T + "/pytr/.pytr/cookies.+491701234567.txt"))

# 7 Sitzung gueltig -> Login ohne PIN sofort ok
tr_sync.konfig_speichern({"pin": ""})
s = tr_sync.login_starten()
pruef("Gueltige Sitzung: ok ohne PIN", s["zustand"] == "ok")
pruef("Automatischer Abruf mit Sitzung, ohne PIN", tr_sync.synchronisiere() == [] and tr_sync.status["zustand"] == "ok")

# 8 Neue Telefonnummer -> login_noetig
tr_sync.konfig_speichern({"telefon": "+491709999999"})
pruef("Neue Nummer: login_noetig", tr_sync.konfig()["zustand"] == "login_noetig")
tr_sync.konfig_speichern({"telefon": "+491701234567"})

# 9 Entfernen
tr_sync.entfernen()
k = tr_sync.konfig()
pruef("Entfernt: nicht eingerichtet", not k["eingerichtet"] and k["zustand"] == "nicht_eingerichtet")
pruef("Entfernt: Cookie geloescht", not os.path.exists(T + "/pytr/.pytr/cookies.+491701234567.txt"))
tr_sync.migrieren()
pruef("Nach Entfernen keine erneute Migration", not tr_sync.eingerichtet())
pruef("Eintraege bleiben", db.q1("SELECT COUNT(*) n FROM eintraege WHERE quelle='tr'")["n"] == 2)
s = tr_sync.login_starten("1234")
pruef("Login ohne Einrichtung abgewiesen", s["zustand"] == "zugangsdaten_fehlen")
print("ALLE TESTS OK")
