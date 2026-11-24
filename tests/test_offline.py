"""HTTPS-Zertifikat und Offline-Warteschlange (Server-Seite)."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

T = tempfile.mkdtemp(prefix="ausgaben_test_")
json.dump({"ki_aktiv": False, "pytr_verzeichnis": T, "zugriffs_token": "test"}, open(T + "/opt.json", "w"))
os.environ.update(AUSGABEN_DB=T + "/t.db", AUSGABEN_OPTIONEN=T + "/opt.json", AUSGABEN_TLS=T + "/tls")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ausgaben"))

from cryptography import x509  # noqa: E402

from app import db, tls  # noqa: E402


def pruef(name, bed):
    print(("OK   " if bed else "FEHL ") + name)
    assert bed, name


db.verbindung()
crt, key = tls.sicherstellen()
ca = x509.load_pem_x509_certificate(open(tls.CA_CRT, "rb").read())
srv = x509.load_pem_x509_certificate(open(crt, "rb").read())
pruef("CA ist CA", ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
pruef("Server von CA signiert", srv.issuer == ca.subject)
pruef("Server max. 825 Tage", srv.not_valid_after_utc - srv.not_valid_before_utc <= timedelta(days=825))
pruef("EKU serverAuth", x509.ExtendedKeyUsageOID.SERVER_AUTH in srv.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)

tls.adresse_lernen("192.168.0.193")
import time  # noqa: E402
time.sleep(1.5)
san = x509.load_pem_x509_certificate(open(crt, "rb").read()).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
pruef("Gelernte IP im Zertifikat", "192.168.0.193" in [str(n.value) for n in san])
tls.eigene_setzen(["homeassistant.tail-test.ts.net"])
san = x509.load_pem_x509_certificate(open(crt, "rb").read()).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
pruef("Eigener Name im Zertifikat", "homeassistant.tail-test.ts.net" in [str(n.value) for n in san])
ca_vorher = open(tls.CA_CRT, "rb").read()
tls.sicherstellen()
pruef("CA bleibt bei Erneuerung gleich", open(tls.CA_CRT, "rb").read() == ca_vorher)
pruef("Status liefert Port 8443", tls.status()["port"] == 8443)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

with TestClient(app) as c:
    r = c.get("/zertifikat.crt")
    pruef("CA-Download ohne Token", r.status_code == 200 and b"BEGIN CERTIFICATE" in r.content)
    r = c.get("/sw.js")
    pruef("Service Worker mit Version", r.status_code == 200 and "__VERSION__" not in r.text)
    H = {"X-Ausgaben-Token": "test"}
    kid = db.q1("SELECT id FROM kategorien LIMIT 1")["id"]
    body = {"datum": "2026-11-24T09:00", "betrag": 4.5, "waehrung": "EUR", "kategorie_id": kid,
            "bezeichnung": "Bäcker", "konto_id": 1, "client_ref": "abc-123"}
    a = c.post("/api/eintraege", headers=H, json=body).json()
    b = c.post("/api/eintraege", headers=H, json=body).json()
    pruef("Doppelt gesendet = ein Eintrag", a["id"] == b["id"] and db.q1("SELECT COUNT(*) n FROM eintraege")["n"] == 1)
    e = c.post("/api/eintraege", headers=H, json={**body, "client_ref": "bon-1", "pruefen": True}).json()
    pruef("Offline-Kassenbon landet in Prüfen", e["pruefen"] == 1)
print("ALLE TESTS OK")
