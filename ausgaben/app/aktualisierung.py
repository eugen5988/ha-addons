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
RAW_CHANGELOG = "https://raw.githubusercontent.com/{owner}/{repo}/main/{ordner}/CHANGELOG.md"

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


def _changelog_neu(installiert, neueste):
    """Aenderungen zwischen installierter und neuester Version aus dem GitHub-Repo (beste Muehe)."""
    try:
        ordner = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        konfig = open(os.path.join(ordner, "config.yaml"), encoding="utf-8").read()
        m = re.search(r"github\.com/([^/\s]+)/([^/\s]+)/tree/[^/\s]+/([^\s]+)", konfig)
        if not m:
            return []
        url = RAW_CHANGELOG.format(owner=m.group(1), repo=m.group(2), ordner=m.group(3).strip("/"))
        text = httpx.get(url, timeout=10).text
    except Exception:  # noqa: BLE001
        return []
    eintraege, aktuell = [], None
    for zeile in text.splitlines():
        if zeile.startswith("## "):
            v = zeile[3:].strip()
            if v == installiert:
                break
            aktuell = {"version": v, "punkte": []}
            eintraege.append(aktuell)
        elif aktuell and zeile.startswith("- "):
            aktuell["punkte"].append(zeile[2:].strip())
    return eintraege if neueste else []


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
            if neu_laden:
                try:
                    _core("POST", "services/homeassistant/update_entity", {"entity_id": eid}, timeout=60)
                except httpx.TimeoutException:
                    pass
            s = _core("GET", f"states/{eid}")
            a = s.get("attributes", {})
            installiert, neueste = a.get("installed_version"), a.get("latest_version")
            verfuegbar = s.get("state") == "on" and bool(neueste) and neueste != installiert
            alt_neueste = stand.get("neueste")
            stand.update(geprueft=time.time(), installiert=installiert, neueste=neueste, verfuegbar=verfuegbar,
                         laeuft=bool(a.get("in_progress")), meldung="")
            if verfuegbar and (neueste != alt_neueste or not stand["neu"]):
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
