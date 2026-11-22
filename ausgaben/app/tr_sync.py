"""Abruf der Trade-Republic-Umsaetze ueber pytr (inoffizielle WebSocket-API).

Zugangsdaten (Telefonnummer, optional PIN), Startdatum, Intervall und Aktiv-Schalter liegen wie
bei den FinTS-Banken in der Add-on-Datenbank und werden in der App gepflegt (Mehr -> Banken).
Die Sitzungs-Cookies legt pytr im pytr-Verzeichnis ab (Standard /homeassistant/pytr/.pytr).
"""
import asyncio
import fnmatch
import json
import logging
import os
import re
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import db, kategorisierung, waehrung
from .einstellungen import optionen

log = logging.getLogger("ausgaben.tr")

STANDARD_TYPEN = ["CARD_*", "BANK_TRANSACTION_OUTGOING", "*DIRECT_DEBIT*", "PAYMENT_OUTBOUND*"]
STANDARD_IGNORIEREN = ["CARD_VERIFICATION"]
ABBRUCH_STATUS = {"CANCELED", "CANCELLED", "FAILED", "DECLINED", "REJECTED", "REVERSED"}
MAX_SEITEN = 60

_sync_lock = threading.Lock()
status = {"zustand": "unbekannt", "letzter_abruf": None, "letzte_meldung": "", "neu_letzter_abruf": 0,
          "typen_letzter_abruf": {}}
login_status = {"zustand": "inaktiv", "meldung": ""}
_login_api = None
nach_abruf = None  # Rueckruf aus main.py: nach_abruf(liste_neuer_eintrag_ids)


def _telefon_normalisieren(tel):
    tel = re.sub(r"[^\d+]", "", tel or "")
    if tel.startswith("00"):
        tel = "+" + tel[2:]
    elif tel.startswith("0"):
        tel = "+49" + tel[1:]
    elif tel and not tel.startswith("+"):
        tel = "+" + tel
    return tel


def _pytr_dir():
    return os.path.join(optionen()["pytr_verzeichnis"], ".pytr")


def migrieren():
    """Einmalig beim Start: vorhandene Zugangsdaten aus der pytr-Datei (bisheriger Sensor bzw.
    Version 1.1.0 mit Add-on-Optionen) in die App uebernehmen."""
    if db.einstellung("tr_telefon") is not None:
        return
    opt = optionen()
    tel, pin = _telefon_normalisieren(opt.get("tr_telefon")), (opt.get("tr_pin") or "").strip()
    if not tel:
        try:
            with open(os.path.join(_pytr_dir(), "credentials")) as f:
                zeilen = [z.strip() for z in f.read().splitlines()]
            tel, pin = zeilen[0], (zeilen[1] if len(zeilen) > 1 else "")
        except (OSError, IndexError):
            return
    if not tel:
        return
    db.setze_einstellung("tr_telefon", tel)
    db.setze_einstellung("tr_pin", pin)
    if db.einstellung("tr_aktiv") is None:
        db.setze_einstellung("tr_aktiv", "1" if opt.get("tr_aktiv", True) else "0")
    if db.einstellung("tr_intervall") is None:
        db.setze_einstellung("tr_intervall", opt.get("tr_intervall_minuten") or 15)
    log.info("TR-Zugangsdaten aus %s in die App uebernommen", _pytr_dir())


def eingerichtet():
    return bool(db.einstellung("tr_telefon"))


def aktiv():
    return eingerichtet() and db.einstellung("tr_aktiv", "1") == "1"


def intervall():
    try:
        return min(1440, max(5, int(db.einstellung("tr_intervall") or 15)))
    except ValueError:
        return 15


def _maskiert(tel):
    return tel[:6] + " … " + tel[-3:] if len(tel) > 9 else tel


def konfig():
    """Fuer die Web-App, ohne PIN."""
    tel = db.einstellung("tr_telefon") or ""
    zustand = status["zustand"] if tel else "nicht_eingerichtet"
    return {**status, "zustand": zustand, "eingerichtet": bool(tel),
            "letzter_abruf": status["letzter_abruf"] or db.einstellung("tr_letzter_abruf"), "telefon": tel, "telefon_anzeige": _maskiert(tel),
            "pin_gespeichert": bool(db.einstellung("tr_pin")), "aktiv": aktiv(), "intervall": intervall(),
            "import_ab": db.einstellung("tr_import_ab"), "login": login_status,
            "typen": _typen()[0], "ignorieren": _typen()[1]}


def konfig_speichern(d):
    if "telefon" in d:
        tel = _telefon_normalisieren(d["telefon"])
        if len(tel) < 8:
            raise ValueError("Telefonnummer mit Ländervorwahl eingeben, z. B. +49 170 1234567")
        if tel != db.einstellung("tr_telefon"):
            status.update(zustand="login_noetig", letzte_meldung="Neue Telefonnummer - bitte anmelden")
            login_status.update(zustand="inaktiv", meldung="")
        db.setze_einstellung("tr_telefon", tel)
    if "pin" in d:
        pin = re.sub(r"\s", "", d["pin"] or "")
        if pin and not re.fullmatch(r"\d{4}", pin):
            raise ValueError("Die PIN von Trade Republic hat 4 Ziffern")
        db.setze_einstellung("tr_pin", pin)
    if "aktiv" in d:
        db.setze_einstellung("tr_aktiv", "1" if d["aktiv"] else "0")
    if "intervall" in d:
        db.setze_einstellung("tr_intervall", min(1440, max(5, int(d["intervall"] or 15))))
    if "import_ab" in d:
        ab = (d["import_ab"] or "").strip()
        if ab:
            datetime.fromisoformat(ab)  # ValueError bei Unsinn
        db.setze_einstellung("tr_import_ab", ab or None)
    for schluessel in ("typen", "ignorieren"):
        if schluessel in d:
            db.setze_einstellung(f"tr_{schluessel}", json.dumps([s.strip() for s in d[schluessel] if s.strip()]))
    return konfig()


def entfernen():
    """Zugang trennen: Zugangsdaten und Sitzung loeschen. Uebernommene Eintraege bleiben."""
    tel = db.einstellung("tr_telefon") or ""
    if tel:
        try:
            os.remove(os.path.join(_pytr_dir(), f"cookies.{tel}.txt"))
        except OSError:
            pass
    db.setze_einstellung("tr_telefon", "")
    db.setze_einstellung("tr_pin", "")
    status.update(zustand="unbekannt", letzter_abruf=None, letzte_meldung="", neu_letzter_abruf=0,
                  typen_letzter_abruf={})
    login_status.update(zustand="inaktiv", meldung="")


def _api(pin=None):
    os.environ["HOME"] = optionen()["pytr_verzeichnis"]
    os.makedirs(_pytr_dir(), exist_ok=True)
    from pytr.api import TradeRepublicApi  # nach HOME, weil pytr den Pfad beim Import festlegt
    tel = db.einstellung("tr_telefon") or ""
    # Ohne PIN reicht ein Platzhalter: Fuer das Fortsetzen der Sitzung braucht pytr nur die Cookies.
    return TradeRepublicApi(phone_no=tel, pin=pin or db.einstellung("tr_pin") or "0000",
                            save_cookies=True, use_v2_login=True)


def _typen():
    roh_t, roh_i = db.einstellung("tr_typen"), db.einstellung("tr_ignorieren")
    return (json.loads(roh_t) if roh_t else STANDARD_TYPEN,
            json.loads(roh_i) if roh_i else STANDARD_IGNORIEREN)


def _passt(typ, muster):
    return any(fnmatch.fnmatchcase(typ, m) for m in muster)


async def _timeline(tr, grenze):
    items, after = [], None
    for _ in range(MAX_SEITEN):
        sub_id = await tr.timeline_transactions(after)
        while True:
            _sid, sub, resp = await asyncio.wait_for(tr.recv(), 30)
            if sub.get("type") == "timelineTransactions":
                break
        await tr.unsubscribe(sub_id)
        seite = resp.get("items", [])
        items.extend(seite)
        after = (resp.get("cursors") or {}).get("after")
        if not after or not seite:
            break
        if _zeit(seite[-1]["timestamp"]) < grenze:
            break
    try:
        await tr.close()
    except Exception:  # noqa: BLE001
        pass
    return items


def _zeit(ts):
    ts = ts.replace("Z", "+00:00")
    if len(ts) > 5 and ts[-5] in "+-" and ts[-3] != ":":
        ts = ts[:-2] + ":" + ts[-2:]
    return datetime.fromisoformat(ts)


def _grenze(voll=False):
    ab = db.einstellung("tr_import_ab")
    lokal_tz = datetime.now().astimezone().tzinfo
    import_ab = datetime.fromisoformat(ab).replace(tzinfo=lokal_tz) if ab else datetime.now(timezone.utc) - timedelta(days=60)
    if voll:
        return import_ab
    letzter = db.q1("SELECT MAX(datum) d FROM eintraege WHERE quelle='tr'")
    if letzter and letzter["d"]:
        return max(import_ab, datetime.fromisoformat(letzter["d"]).replace(tzinfo=lokal_tz) - timedelta(days=10))
    return import_ab


def synchronisiere(voll=False, manuell=False):
    """Blockierend. Gibt Liste neuer Eintrags-IDs zurueck."""
    if not eingerichtet() or not (manuell or aktiv()):
        return []
    if not _sync_lock.acquire(blocking=False):
        return []
    try:
        try:
            tr = _api()
            if not tr.resume_websession():
                status.update(zustand="login_noetig", letzte_meldung="Sitzung abgelaufen - bitte neu anmelden")
                return []
        except ValueError as e:
            status.update(zustand="login_noetig", letzte_meldung=str(e)[:200])
            return []
        grenze = _grenze(voll)
        items = asyncio.run(asyncio.wait_for(_timeline(tr, grenze), 300))
        neue_ids = _verarbeiten(items, grenze)
        status.update(zustand="ok", letzter_abruf=db.jetzt_iso(), neu_letzter_abruf=len(neue_ids),
                      letzte_meldung=f"{len(items)} Ereignisse gelesen, {len(neue_ids)} neu",
                      typen_letzter_abruf=dict(Counter(i.get("eventType") for i in items)))
        db.setze_einstellung("tr_letzter_abruf", status["letzter_abruf"])
        return neue_ids
    except Exception as e:  # noqa: BLE001
        log.exception("TR-Abruf fehlgeschlagen")
        status.update(zustand="fehler", letzte_meldung=str(e)[:300])
        return []
    finally:
        _sync_lock.release()


def _handeintrag_gleich(betrag, datum):
    tag = datetime.fromisoformat(datum).date()
    r = db.q1("SELECT id FROM eintraege WHERE quelle='manuell' AND ABS(betrag - ?) < 0.005 AND datum >= ? AND datum < ? "
              "ORDER BY ABS(julianday(datum) - julianday(?)) LIMIT 1",
              (betrag, (tag - timedelta(days=2)).isoformat(), (tag + timedelta(days=3)).isoformat(), datum))
    return r["id"] if r else None


def _verarbeiten(items, grenze):
    typen, ignorieren = _typen()
    konto = db.konto_id("auto_tr")
    neue = []
    for it in items:
        typ = it.get("eventType") or ""
        if not _passt(typ, typen) or _passt(typ, ignorieren):
            continue
        ref = f'tr:{it.get("id")}'
        st = (it.get("status") or "").upper()
        vorhanden = db.q1("SELECT id, kategorisiert_durch FROM eintraege WHERE quelle_ref=?", (ref,))
        if it.get("deleted") or it.get("hidden") or st in ABBRUCH_STATUS:
            if vorhanden:
                db.x("DELETE FROM eintraege WHERE id=?", (vorhanden["id"],))
            continue
        if not vorhanden and db.einstellung(f"geloescht:{ref}"):
            continue
        if vorhanden:
            db.x("UPDATE eintraege SET quelle_status=? WHERE id=?", (st, vorhanden["id"]))
            continue
        zeit = _zeit(it["timestamp"])
        if zeit < grenze:
            continue
        menge = it.get("amount") or {}
        wert = menge.get("value")
        if not isinstance(wert, (int, float)) or wert == 0:
            continue
        neue.append({
            "datum": zeit.astimezone().replace(tzinfo=None, microsecond=0).isoformat(),
            "betrag": round(-float(wert), 2),  # Ausgaben positiv, Erstattungen negativ
            "waehrung": menge.get("currency") or "EUR",
            "haendler_roh": (it.get("title") or "").strip(),
            "notiz": (it.get("subtitle") or "") if typ != "CARD_TRANSACTION" else "",
            "typ": typ,
            "quelle_ref": ref,
            "quelle_status": st,
        })
    if not neue:
        return []
    kategorisierung.kategorisiere(neue)
    ids = []
    jetzt = db.jetzt_iso()
    for e in neue:
        dup = _handeintrag_gleich(e["betrag"], e["datum"])
        if dup:
            # z. B. Kassenbon schon von Hand erfasst: nicht doppelt zaehlen lassen, sondern zur Pruefung vorlegen
            e["pruefen"] = 1
            e["notiz"] = f"Mögliches Duplikat von Eintrag {dup} (von Hand erfasst). " + (e.get("notiz") or "")
        waehrung.umrechnen(e)
        neu_id, anz = db.x(
            "INSERT OR IGNORE INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, "
            "notiz, konto_id, quelle, quelle_ref, quelle_status, haendler_roh, pruefen, ki_sicherheit, "
            "kategorisiert_durch, erstellt, geaendert) VALUES (?,?,?,?,?,?,?,?,?,'tr',?,?,?,?,?,?,?,?)",
            (e["datum"], e["betrag"], e["waehrung"], e["kurs"], e["betrag_eur"], e.get("kategorie_id"),
             e.get("bezeichnung") or e["haendler_roh"], e.get("notiz", ""), konto, e["quelle_ref"],
             e["quelle_status"], e["haendler_roh"], e.get("pruefen", 1), e.get("ki_sicherheit"),
             e.get("kategorisiert_durch"), jetzt, jetzt),
        )
        if anz:
            ids.append(neu_id)
    return ids


# ---------------------------------------------------------------- Anmeldung aus der App heraus

def login_starten(pin=None, pin_speichern=False):
    """Startet die v2-Anmeldung. Bestaetigung erfolgt in der Trade-Republic-App."""
    global _login_api
    if not eingerichtet():
        login_status.update(zustand="zugangsdaten_fehlen", meldung="Zuerst Trade Republic einrichten")
        return login_status
    pin = re.sub(r"\s", "", pin or "")
    if pin and pin_speichern:
        konfig_speichern({"pin": pin})
    pin = pin or db.einstellung("tr_pin") or ""
    api = _api(pin)
    try:
        if api.resume_websession():
            login_status.update(zustand="ok", meldung="Sitzung ist noch gültig")
            status.update(zustand="ok")
            return login_status
    except Exception:  # noqa: BLE001
        log.debug("Sitzung nicht fortsetzbar", exc_info=True)
    if not pin:
        login_status.update(zustand="pin_noetig", meldung="PIN eingeben")
        return login_status
    try:
        api.initiate_weblogin()
    except Exception as e:  # noqa: BLE001
        login_status.update(zustand="fehler", meldung=_login_fehlertext(e))
        return login_status
    _login_api = api
    if api.weblogin_needs_authenticator:
        login_status.update(zustand="code_noetig", meldung="Code aus der Authenticator-App eingeben")
    else:
        login_status.update(zustand="warte", meldung="Anmeldung in der Trade-Republic-App bestätigen")
        threading.Thread(target=_login_abschliessen, daemon=True).start()
    return login_status


def _login_fehlertext(e):
    t = str(e) or e.__class__.__name__
    if "PIN" in t.upper() or "401" in t or "INVALID" in t.upper():
        return f"Anmeldung abgelehnt - Telefonnummer oder PIN prüfen ({t[:120]})"
    return t[:300]


def _login_abschliessen(code=None):
    try:
        _login_api.complete_weblogin(code)
        login_status.update(zustand="ok", meldung="Angemeldet")
        status.update(zustand="ok", letzte_meldung="Angemeldet - Abruf startet")
        threading.Thread(target=_abruf_nach_login, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        login_status.update(zustand="fehler", meldung=_login_fehlertext(e))


def _abruf_nach_login():
    ids = synchronisiere(manuell=True)
    if nach_abruf:
        try:
            nach_abruf(ids)
        except Exception:  # noqa: BLE001
            log.exception("Rueckruf nach TR-Abruf")


def login_code(code):
    if login_status["zustand"] != "code_noetig" or _login_api is None:
        return login_status
    _login_abschliessen(code)
    return login_status
