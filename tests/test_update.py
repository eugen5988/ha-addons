"""Add-on-Update aus der App: Pruefen und Installieren ueber einen nachgebauten Home Assistant Core."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

AUFRUFE = []
ZUSTAND = {"installiert": "1.0.0", "neueste": "1.0.0"}


class FakeCore(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _antwort(self, daten, code=200):
        body = json.dumps(daten).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _entitaet(self):
        return {"entity_id": "update.ausgaben_update",
                "state": "on" if ZUSTAND["neueste"] != ZUSTAND["installiert"] else "off",
                "attributes": {"title": "Ausgaben", "entity_picture": "/api/hassio/addons/91883541_ausgaben/icon",
                               "installed_version": ZUSTAND["installiert"], "latest_version": ZUSTAND["neueste"],
                               "in_progress": False}}

    def do_GET(self):
        AUFRUFE.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/states":
            self._antwort([{"entity_id": "update.anderes", "state": "off",
                            "attributes": {"title": "Anderes", "entity_picture": "/api/hassio/addons/core_ssh/icon"}},
                           self._entitaet()])
        elif self.path == "/states/update.ausgaben_update":
            if ZUSTAND.get("verzoegert"):
                ZUSTAND["verzoegert"] -= 1
                if not ZUSTAND["verzoegert"]:
                    ZUSTAND["neueste"] = "1.1.0"  # Store-Reload hat eine neue Version gefunden
            self._antwort(self._entitaet())
        else:
            self._antwort({}, 404)

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length") or 0)
        daten = json.loads(self.rfile.read(laenge) or b"{}")
        AUFRUFE.append(("POST", self.path, daten))
        if self.path == "/services/homeassistant/update_entity":
            ZUSTAND["verzoegert"] = 2  # wie Core: Store-Reload erst nach kurzer Verzoegerung
        self._antwort([])


server = HTTPServer(("127.0.0.1", 0), FakeCore)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ.update(AUSGABEN_CORE=f"http://127.0.0.1:{server.server_port}", SUPERVISOR_TOKEN="geheim",
                  HOSTNAME="91883541-ausgaben")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ausgaben"))

from app import aktualisierung  # noqa: E402

aktualisierung.github_version = lambda: "1.1.0"  # ohne Netz
aktualisierung._changelog_neu = lambda i, n: [{"version": n, "punkte": ["Neu"]}]
aktualisierung.time = type("T", (), {"time": staticmethod(time.time), "sleep": staticmethod(lambda s: None)})


def pruef(name, bed):
    print(("OK   " if bed else "FEHL ") + name)
    assert bed, name


pruef("Slug aus Hostname", aktualisierung.eigener_slug() == "91883541_ausgaben")

s = aktualisierung.pruefen(False)
pruef("Entität über das Add-on-Symbol gefunden", s["entitaet"] == "update.ausgaben_update")
pruef("Ohne Neuladen kein Update", s["verfuegbar"] is False and s["installiert"] == "1.0.0")
pruef("Token wird mitgeschickt", all(a[2] == "Bearer geheim" for a in AUFRUFE if a[0] == "GET"))

s = aktualisierung.pruefen(True)
pruef("Neuladen ruft homeassistant.update_entity", any(a[1] == "/services/homeassistant/update_entity"
                                                       and a[2] == {"entity_id": "update.ausgaben_update"} for a in AUFRUFE))
pruef("Danach Update verfügbar (auch bei verzögerter Auffrischung)", s["verfuegbar"] is True and s["neueste"] == "1.1.0")
pruef("Neuerungen dabei", s["neu"] == [{"version": "1.1.0", "punkte": ["Neu"]}])
pruef("Öffentlicher Stand ohne Entität", "entitaet" not in aktualisierung.oeffentlich())

aktualisierung.installieren(mit_backup=True)
for _ in range(50):
    if any(a[1] == "/services/update/install" for a in AUFRUFE):
        break
    time.sleep(0.05)
pruef("Installieren ruft update.install mit Backup", any(
    a[1] == "/services/update/install" and a[2] == {"entity_id": "update.ausgaben_update", "backup": True} for a in AUFRUFE))
pruef("Stand: läuft", aktualisierung.stand["laeuft"] is True)

server.shutdown()
print("Alle Update-Tests bestanden")
