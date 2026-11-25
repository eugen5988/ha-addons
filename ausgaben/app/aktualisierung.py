"""Add-on-Updates aus der App heraus.

Der Supervisor erlaubt einem Add-on nicht, sich selbst zu aktualisieren ("can't update itself").
Deshalb geht alles ueber Home Assistant Core (homeassistant_api ist ohnehin an):
  - Pruefen:      homeassistant.update_entity auf die Update-Entitaet des Add-ons
                  -> Core laedt dabei die Add-on-Repositories neu (Store-Reload)
  - Installieren: update.install auf dieselbe Entitaet (Core ruft den Supervisor auf)
Es sind keine zusaetzlichen Supervisor-Rechte (hassio_role) noetig.
"""
import logging
import os
import re
import threading
import time

import httpx

log = logging.getLogger("ausgaben.aktualisierung")

CORE = os.environ.get("AUSGABEN_CORE", "http://supervisor/core/api")  # fuer Tests ueberschreibbar

stand = {"geprueft": None, "installiert": None, "neueste": None, "verfuegbar": False,
         "laeuft": False, "entitaet": None, "meldung": "", "neu": []}
_lock = threading.Lock()


def _token():
    return os.environ.get("SUPERVISOR_TOKEN")


def _core(methode, pfad, json_daten=None, timeout=15):
    r = httpx.request(methode, f"{CORE}/{pfad}", json=json_daten, timeout=timeout,
                      headers={"Authorization": f"Bearer {_token()}"})
    r.raise_for_status()
    return r.json() if r.content else None


def eigener_slug():
    """Im Add-on-Container ist der Hostname der Slug mit Bindestrich, z. B. 91883541-ausgaben."""
    return (os.environ.get("HOSTNAME") or "").replace("-", "_")


def _entitaet_finden():
    if stand["entitaet"]:
        return stand["entitaet"]
    slug = eigener_slug()
    kandidaten = []
    for s in _core("GET", "states") or []:
        eid = s.get("entity_id", "")
        if not eid.startswith("update."):
            continue
        a = s.get("attributes", {})
        bild = a.get("entity_picture") or ""
        if slug and bild == f"/api/hassio/addons/{slug}/icon":
            stand["entitaet"] = eid
            return eid
        if slug and bild.endswith(f"/addons/{slug}/icon"):
            kandidaten.insert(0, eid)
        elif (a.get("title") or "").strip().lower() == "ausgaben":
            kandidaten.append(eid)
    if kandidaten:
        stand["entitaet"] = kandidaten[0]
        return kandidaten[0]
    return None


def _repo_datei(name):
    """Datei aus dem GitHub-Repo des Add-ons (Adresse aus config.yaml), beste Muehe."""
    ordner = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    konfig = open(os.path.join(ordner, "config.yaml"), encoding="utf-8").read()
    m = re.search(r"github\.com/([^/\s]+)/([^/\s]+)/tree/([^/\s]+)/([^\s]+)", konfig)
    if not m:
        return None
    url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4).strip('/')}/{name}?t={int(time.time())}"
    return httpx.get(url, timeout=10, headers={"Cache-Control": "no-cache"}).text


def github_version():
    try:
        m = re.search(r'^version:\s*"?([^"\n]+)"?', _repo_datei("config.yaml") or "", re.M)
        return m.group(1).strip() if m else None
    except Exception:  # noqa: BLE001
        return None


def _vtupel(v):
    """'1.15.2' -> (1, 15, 2); Unlesbares wird ganz klein einsortiert."""
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v or ""))
    except ValueError:
        return ()


def _changelog_neu(installiert, neueste):
    """Aenderungen nach der installierten bis einschliesslich der neuesten Version (beste Muehe).
    Nur wenn der Changelog die neueste Version enthaelt; sonst [] (GitHub liefert manchmal kurz einen alten Stand)."""
    try:
        text = _repo_datei("CHANGELOG.md") or ""
    except Exception:  # noqa: BLE001
        return []
    von, bis = _vtupel(installiert), _vtupel(neueste)
    eintraege, aktuell = [], None
    for zeile in text.splitlines():
        if zeile.startswith("## "):
            v = zeile[3:].strip()
            aktuell = {"version": v, "punkte": []} if von < _vtupel(v) <= bis else None
            if aktuell:
                eintraege.append(aktuell)
        elif aktuell and zeile.startswith("- "):
            aktuell["punkte"].append(zeile[2:].strip())
    if not any(x["version"] == neueste for x in eintraege):
        return []
    return eintraege


def pruefen(neu_laden=False):
    """Fragt Core nach der Update-Entitaet. neu_laden=True laesst den Supervisor die Repos neu einlesen."""
    if not _token():
        stand.update(meldung="Nur innerhalb von Home Assistant verfügbar", geprueft=time.time())
        return stand
    with _lock:
        try:
            eid = _entitaet_finden()
            if not eid:
                stand.update(meldung="Update-Entität des Add-ons nicht gefunden (Supervisor-Integration aktiv?)",
                             geprueft=time.time())
                return stand
            s = _core("GET", f"states/{eid}")
            if neu_laden:
                # Core frischt die Add-on-Daten verzoegert auf (Debouncer, ca. 10 s) und laedt dabei die
                # Repositories neu. Deshalb danach eine Weile nachsehen, bis die neue Version ankommt.
                vorher = s.get("attributes", {}).get("latest_version")
                ziel = github_version()
                try:
                    _core("POST", "services/homeassistant/update_entity", {"entity_id": eid}, timeout=60)
                except httpx.TimeoutException:
                    pass
                ende = time.time() + 45
                while time.time() < ende:
                    s = _core("GET", f"states/{eid}")
                    neueste = s.get("attributes", {}).get("latest_version")
                    if neueste != vorher or (ziel and neueste == ziel):
                        break
                    if not ziel and time.time() > ende - 25:
                        break  # GitHub nicht erreichbar: nach ~20 s aufhoeren
                    time.sleep(3)
            a = s.get("attributes", {})
            installiert, neueste = a.get("installed_version"), a.get("latest_version")
            verfuegbar = s.get("state") == "on" and bool(neueste) and neueste != installiert
            alt_neueste = stand.get("neueste")
            stand.update(geprueft=time.time(), installiert=installiert, neueste=neueste, verfuegbar=verfuegbar,
                         laeuft=bool(a.get("in_progress")), meldung="")
            if verfuegbar and (neueste != alt_neueste or installiert != stand.get("neu_fuer") or not stand["neu"]):
                stand["neu_fuer"] = installiert
                stand["neu"] = _changelog_neu(installiert, neueste)
            elif not verfuegbar:
                stand["neu"] = []
        except Exception as e:  # noqa: BLE001
            log.info("Update-Prüfung fehlgeschlagen: %s", e)
            stand.update(meldung=f"Prüfung fehlgeschlagen: {e}", geprueft=time.time())
    return stand


def installieren(mit_backup=False):
    """Startet das Update ueber Core. Das Add-on wird dabei gestoppt und neu gestartet,
    daher wird nicht auf das Ende gewartet (Core schirmt den Aufruf gegen Abbruch ab)."""
    eid = _entitaet_finden()
    if not eid:
        raise RuntimeError("Update-Entität des Add-ons nicht gefunden")
    stand.update(laeuft=True, meldung="")

    def los():
        try:
            _core("POST", "services/update/install", {"entity_id": eid, "backup": bool(mit_backup)}, timeout=20)
        except httpx.TimeoutException:
            pass  # normal: Core arbeitet weiter, das Add-on wird gleich neu gestartet
        except Exception as e:  # noqa: BLE001
            stand.update(laeuft=False, meldung=f"Update fehlgeschlagen: {e}")
            log.warning("Update fehlgeschlagen: %s", e)

    threading.Thread(target=los, daemon=True).start()
    return stand


def oeffentlich():
    return {k: stand[k] for k in ("geprueft", "installiert", "neueste", "verfuegbar", "laeuft", "meldung", "neu")}
