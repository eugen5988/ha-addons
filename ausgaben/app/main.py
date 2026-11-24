"""Ausgaben - Webserver (Ingress in Home Assistant + optional direkter Port fuer die iPhone-Web-App)."""
import asyncio
import hmac
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import anteile, auswertung, beleg, db, export, fints_sync, kategorisierung, ki_nutzung, next_import, tr_sync, waehrung
from .einstellungen import optionen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ausgaben")
STATIC = os.path.join(os.path.dirname(__file__), "static")
INGRESS_IP = "172.30.32.2"
_sensor_flag = asyncio.Event()
_loop = None


def sensoren_aktualisieren():
    """Aus Request-Handlern aufrufen (auch aus Worker-Threads): Sensoren zeitnah neu senden."""
    if _loop is not None:
        _loop.call_soon_threadsafe(_sensor_flag.set)


# ---------------------------------------------------------------- Hintergrund

async def _tr_schleife():
    await asyncio.sleep(20)
    while True:
        try:
            neue = await asyncio.to_thread(tr_sync.synchronisiere)
            for i in neue:
                e = db.q1(auswertung.BASIS_SELECT + "WHERE e.id=?", (i,))
                await asyncio.to_thread(auswertung.ereignis_neue_ausgabe, e)
            if neue:
                sensoren_aktualisieren()
        except Exception:  # noqa: BLE001
            log.exception("TR-Schleife")
        await asyncio.sleep(tr_sync.intervall() * 60)


async def _sensor_schleife():
    while True:
        try:
            await asyncio.to_thread(anteile.neue_anwenden)
            await asyncio.to_thread(auswertung.sensoren_senden)
            await asyncio.to_thread(waehrung.offene_kurse_nachholen)
        except Exception:  # noqa: BLE001
            log.exception("Sensor-Schleife")
        _sensor_flag.clear()
        try:
            await asyncio.wait_for(_sensor_flag.wait(), 300)
            await asyncio.sleep(2)  # kurz sammeln
        except asyncio.TimeoutError:
            pass


def _nach_bankabruf(ids):
    """Wird aus dem Bank-Thread aufgerufen: Ereignisse senden, Sensoren auffrischen."""
    for i in ids:
        e = db.q1(auswertung.BASIS_SELECT + "WHERE e.id=?", (i,))
        if e:
            auswertung.ereignis_neue_ausgabe(e)
    sensoren_aktualisieren()


async def _bank_schleife():
    await asyncio.sleep(60)
    while True:
        try:
            for bank_id in await asyncio.to_thread(fints_sync.faellige_abrufe):
                log.info("Taeglicher Bankabruf fuer Bank %s", bank_id)
                fints_sync.job_starten(bank_id, "abruf")
        except Exception:  # noqa: BLE001
            log.exception("Bank-Schleife")
        await asyncio.sleep(60)


@asynccontextmanager
async def lebenszyklus(_app):
    global _loop
    _loop = asyncio.get_running_loop()
    db.verbindung()
    fints_sync.schema_anlegen()
    ki_nutzung.schema_anlegen()
    fints_sync.nach_abruf = _nach_bankabruf
    tr_sync.nach_abruf = _nach_bankabruf
    tr_sync.migrieren()
    anteile.neue_anwenden()
    aufgaben = [asyncio.create_task(_tr_schleife()), asyncio.create_task(_sensor_schleife()),
                asyncio.create_task(_bank_schleife())]
    yield
    for a in aufgaben:
        a.cancel()


app = FastAPI(title="Ausgaben", lifespan=lebenszyklus, docs_url=None, redoc_url=None)


@app.middleware("http")
async def zugriff(request: Request, call_next):
    pfad = request.url.path
    offen = not pfad.startswith("/api/") or pfad == "/api/auth"
    ingress = request.client and request.client.host == INGRESS_IP
    if not offen and not ingress:
        token = optionen().get("zugriffs_token") or ""
        mitgegeben = request.headers.get("x-ausgaben-token") or request.cookies.get("ausgaben_token") or ""
        if not token or not hmac.compare_digest(token, mitgegeben):
            return JSONResponse({"detail": "token"}, status_code=401)
    return await call_next(request)


@app.post("/api/auth")
def auth(daten: dict = Body(...)):
    token = optionen().get("zugriffs_token") or ""
    if not token or not hmac.compare_digest(token, str(daten.get("token", ""))):
        raise HTTPException(401, "Token falsch")
    antwort = JSONResponse({"ok": True})
    antwort.set_cookie("ausgaben_token", token, max_age=3600 * 24 * 365, httponly=True, samesite="strict")
    return antwort


# ---------------------------------------------------------------- Meta & Status

@app.get("/api/meta")
def meta():
    opt = optionen()
    kats = db.q("SELECT k.*, (SELECT COUNT(*) FROM eintraege e WHERE e.kategorie_id=k.id) anzahl "
                "FROM kategorien k ORDER BY archiviert, sortierung, name")
    return {
        "kategorien": kats,
        "konten": db.q("SELECT * FROM konten ORDER BY archiviert, id"),
        "waehrungen": waehrung.WAEHRUNGEN,
        "kennzahlen": auswertung.kennzahlen(),
        "ki": {"aktiv": bool(opt.get("ki_aktiv") and opt.get("anthropic_api_key")),
               "modell": opt.get("claude_modell"), "letzter_fehler": db.einstellung("ki_letzter_fehler", ""),
               "kosten_monat_eur": ki_nutzung.monat_eur()},
        "tr": tr_sync.konfig(),
        "einstellungen": {"mehrdeutig": kategorisierung.mehrdeutige_muster(), "wohnort": db.einstellung("ki_wohnort") or ""},
        "anzahl_gesamt": db.q1("SELECT COUNT(*) n FROM eintraege")["n"],
        "darstellung": {"design": db.einstellung("design", "salbei"), "modus": db.einstellung("design_modus", "auto"),
                        "startseite": db.einstellung("startseite", "uebersicht")},
        "banken": {"anzahl": db.q1("SELECT COUNT(*) n FROM banken")["n"],
                   "offen_nachholen": db.q1("SELECT COUNT(*) n FROM bank_nachholen WHERE status='offen'")["n"],
                   "fehler": [r["name"] for r in db.q("SELECT name FROM banken WHERE meldung<>''")]},
    }


@app.get("/api/kennzahlen")
def kennzahlen():
    return auswertung.kennzahlen()


# ---------------------------------------------------------------- Eintraege

FELDER = ("datum", "betrag", "waehrung", "kategorie_id", "bezeichnung", "notiz", "konto_id")


def _eintrag_laden(eid):
    e = db.q1(auswertung.BASIS_SELECT + "WHERE e.id=?", (eid,))
    if not e:
        raise HTTPException(404, "Eintrag nicht gefunden")
    return e


def _validieren(d, teilweise=False):
    if not teilweise or "betrag" in d:
        try:
            d["betrag"] = round(float(str(d.get("betrag")).replace(",", ".")), 2)
        except (TypeError, ValueError):
            raise HTTPException(422, "Betrag fehlt oder ist keine Zahl")
    if not teilweise or "datum" in d:
        roh = d.get("datum") or datetime.now().replace(second=0, microsecond=0).isoformat()
        try:
            d["datum"] = datetime.fromisoformat(roh).replace(microsecond=0).isoformat()
        except ValueError:
            raise HTTPException(422, "Datum ungueltig")
    if "bezeichnung" in d:
        d["bezeichnung"] = (d["bezeichnung"] or "").strip()[:120]
    return d


@app.get("/api/eintraege")
def eintraege(request: Request):
    p = dict(request.query_params)
    where, args = auswertung.filter_sql(p)
    limit = min(int(p.get("limit", 200)), 2000)
    offset = int(p.get("offset", 0))
    reihenfolge = "e.datum ASC, e.id ASC" if p.get("sortierung") == "alt" else "e.datum DESC, e.id DESC"
    if p.get("sortierung") == "betrag":
        reihenfolge = f"{auswertung.BETRAG} DESC"
    zeilen = db.q(auswertung.BASIS_SELECT + f"WHERE {where} ORDER BY {reihenfolge} LIMIT ? OFFSET ?",
                  args + [limit, offset])
    summe = db.q1(f"SELECT COALESCE(SUM({auswertung.BETRAG}),0) s, COUNT(*) n FROM eintraege e "
                  f"LEFT JOIN kategorien k ON k.id=e.kategorie_id WHERE {where}", args)
    return {"eintraege": zeilen, "summe": round(summe["s"], 2), "anzahl": summe["n"]}


_KEIN_ANTEIL = object()


def _anteil_lesen(d):
    """Liest d['anteil_text'] vor dem Speichern (Fehler sollen nichts halb speichern)."""
    if "anteil_text" not in d:
        return _KEIN_ANTEIL
    try:
        return anteile.lesen(d.get("anteil_text"))
    except ValueError as e:
        raise HTTPException(422, str(e))


def _anteil_uebernehmen(eid, anteil, d):
    e = _eintrag_laden(eid)
    if anteil is _KEIN_ANTEIL:
        anteile.neue_anwenden()
        return
    f = anteile.faktor(anteil[0], anteil[1], e["betrag"]) if anteil else 1.0
    db.x("UPDATE eintraege SET anteil=? WHERE id=?", (f, eid))
    if d.get("anteil_regel"):
        if anteil:
            anteile.regel_setzen(e, anteil[0], anteil[1], rueckwirkend=True)
        else:
            r = anteile.regel_fuer(e)
            if r:
                anteile.regel_loeschen(r["id"], rueckwirkend=True)


@app.get("/api/anteile")
def anteile_liste():
    return anteile.regeln_liste()


@app.delete("/api/anteile/{rid}")
def anteil_loeschen(rid: int, rueckwirkend: bool = False):
    anteile.regel_loeschen(rid, rueckwirkend)
    sensoren_aktualisieren()
    return {"ok": True}


@app.post("/api/eintraege")
def eintrag_anlegen(d: dict = Body(...)):
    roh, anteil = d, _anteil_lesen(d)
    d = _validieren({k: d.get(k) for k in FELDER + ("kurs",) if k in d})
    if d.get("kurs"):
        d["kurs_manuell"] = True
    waehrung.umrechnen(d)
    jetzt = db.jetzt_iso()
    eid, _ = db.x(
        "INSERT INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, notiz, "
        "konto_id, quelle, pruefen, kategorisiert_durch, erstellt, geaendert) "
        "VALUES (?,?,?,?,?,?,?,?,?,'manuell',0,'nutzer',?,?)",
        (d["datum"], d["betrag"], d["waehrung"], d["kurs"], d["betrag_eur"], d.get("kategorie_id"),
         d.get("bezeichnung", ""), d.get("notiz", ""), d.get("konto_id") or db.konto_id("Bargeld"), jetzt, jetzt))
    _anteil_uebernehmen(eid, anteil, roh)
    sensoren_aktualisieren()
    return _eintrag_laden(eid)


@app.put("/api/eintraege/{eid}")
def eintrag_aendern(eid: int, d: dict = Body(...)):
    anteil = _anteil_lesen(d)
    alt = _eintrag_laden(eid)
    neu = _validieren({k: d[k] for k in FELDER + ("kurs",) if k in d}, teilweise=True)
    zusammen = {**alt, **neu}
    if "kurs" in neu and neu["kurs"]:
        zusammen["kurs_manuell"] = True
    if any(k in neu for k in ("betrag", "waehrung", "datum", "kurs")):
        if not ("kurs" in neu and neu["kurs"]) and ("waehrung" in neu or "datum" in neu):
            zusammen["kurs_manuell"] = False
        elif "kurs" not in neu:
            zusammen["kurs_manuell"] = True  # nur Betrag geaendert -> bisherigen Kurs behalten
        waehrung.umrechnen(zusammen)
    kategorie_geaendert = "kategorie_id" in neu and neu["kategorie_id"] != alt["kategorie_id"]
    bez_geaendert = "bezeichnung" in neu and neu["bezeichnung"] != alt["bezeichnung"]
    db.x(
        "UPDATE eintraege SET datum=?, betrag=?, waehrung=?, kurs=?, betrag_eur=?, kategorie_id=?, bezeichnung=?, "
        "notiz=?, konto_id=?, pruefen=0, kategorisiert_durch=?, geaendert=? WHERE id=?",
        (zusammen["datum"], zusammen["betrag"], zusammen["waehrung"], zusammen["kurs"], zusammen["betrag_eur"],
         zusammen["kategorie_id"], zusammen["bezeichnung"], zusammen.get("notiz") or "", zusammen["konto_id"],
         "nutzer" if (kategorie_geaendert or bez_geaendert or alt["quelle"] == "manuell") else alt["kategorisiert_durch"],
         db.jetzt_iso(), eid))
    if (kategorie_geaendert or bez_geaendert) and d.get("regel_lernen", True):
        kategorisierung.lerne_aus_korrektur(zusammen)
    _anteil_uebernehmen(eid, anteil, d)
    sensoren_aktualisieren()
    return _eintrag_laden(eid)


@app.delete("/api/eintraege/{eid}")
def eintrag_loeschen(eid: int):
    e = _eintrag_laden(eid)
    if e["quelle_ref"] and e["quelle"] in ("tr", "bank"):
        # Merken, damit der naechste Abruf den Umsatz nicht wieder anlegt
        db.setze_einstellung(f"geloescht:{e['quelle_ref']}", db.jetzt_iso())
    db.x("DELETE FROM eintraege WHERE id=?", (eid,))
    sensoren_aktualisieren()
    return {"ok": True}


@app.post("/api/eintraege/{eid}/bestaetigen")
def eintrag_bestaetigen(eid: int):
    e = _eintrag_laden(eid)
    db.x("UPDATE eintraege SET pruefen=0, geaendert=? WHERE id=?", (db.jetzt_iso(), eid))
    if e["kategorisiert_durch"] in ("ki", "verlauf"):
        kategorisierung.lerne_aus_korrektur(e)
    sensoren_aktualisieren()
    return _eintrag_laden(eid)


@app.post("/api/eintraege/{eid}/aufteilen")
def eintrag_aufteilen(eid: int, d: dict = Body(...)):
    """d = {"teile": [{"betrag", "kategorie_id", "bezeichnung"}, ...]} - Summe muss dem Original entsprechen."""
    e = _eintrag_laden(eid)
    teile = d.get("teile") or []
    if len(teile) < 2:
        raise HTTPException(422, "Mindestens zwei Teile angeben")
    try:
        betraege = [round(float(str(t["betrag"]).replace(",", ".")), 2) for t in teile]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Betrag in einem Teil ungueltig")
    if abs(sum(betraege) - e["betrag"]) > 0.009:
        raise HTTPException(422, f"Die Teile ergeben {sum(betraege):.2f}, das Original {e['betrag']:.2f}")

    def ausfuehren(c):
        jetzt = db.jetzt_iso()
        kurs = e["kurs"]
        ids = []
        for n, (t, b) in enumerate(zip(teile, betraege)):
            werte = (b, round(b * kurs, 2) if kurs else None, t.get("kategorie_id"),
                     (t.get("bezeichnung") or e["bezeichnung"])[:120])
            if n == 0:
                c.execute("UPDATE eintraege SET betrag=?, betrag_eur=?, kategorie_id=?, bezeichnung=?, pruefen=0, "
                          "kategorisiert_durch='nutzer', geaendert=? WHERE id=?", (*werte, jetzt, eid))
                ids.append(eid)
            else:
                ref = f'{e["quelle_ref"]}#{n}' if e["quelle_ref"] else None
                ids.append(c.execute(
                    "INSERT INTO eintraege(datum, betrag, betrag_eur, kategorie_id, bezeichnung, waehrung, kurs, notiz, "
                    "konto_id, quelle, quelle_ref, haendler_roh, pruefen, kategorisiert_durch, erstellt, geaendert) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,'nutzer',?,?)",
                    (e["datum"], *werte, e["waehrung"], kurs, e["notiz"], e["konto_id"], e["quelle"], ref,
                     e["haendler_roh"], jetzt, jetzt)).lastrowid)
        return ids

    ids = db.transaktion(ausfuehren)
    sensoren_aktualisieren()
    return {"ids": ids}


@app.post("/api/pruefen/alle_bestaetigen")
def alle_bestaetigen(d: dict = Body(default={})):
    ids = d.get("ids")
    if ids:
        db.x(f"UPDATE eintraege SET pruefen=0 WHERE id IN ({','.join('?' * len(ids))}) AND kategorie_id IS NOT NULL",
             [int(i) for i in ids])
    else:
        db.x("UPDATE eintraege SET pruefen=0 WHERE pruefen=1 AND kategorie_id IS NOT NULL")
    sensoren_aktualisieren()
    return {"ok": True}


@app.post("/api/pruefen/neu_kategorisieren")
def neu_kategorisieren():
    offen = db.q("SELECT id, haendler_roh, bezeichnung, betrag, waehrung, datum FROM eintraege "
                 "WHERE pruefen=1 AND haendler_roh IS NOT NULL")
    kategorisierung.kategorisiere(offen)
    for e in offen:
        db.x("UPDATE eintraege SET kategorie_id=?, bezeichnung=?, pruefen=?, ki_sicherheit=?, kategorisiert_durch=? "
             "WHERE id=?", (e.get("kategorie_id"), e.get("bezeichnung"), e.get("pruefen", 1),
                            e.get("ki_sicherheit"), e.get("kategorisiert_durch"), e["id"]))
    sensoren_aktualisieren()
    return {"bearbeitet": len(offen)}


@app.get("/api/vorschlaege")
def vorschlaege(q: str = ""):
    """Autovervollstaendigung fuer die Bezeichnung inkl. ueblicher Kategorie und letztem Betrag."""
    if len(q.strip()) < 1:
        return []
    return db.q(
        "SELECT bezeichnung, kategorie_id, COUNT(*) n, MAX(datum) zuletzt, "
        "(SELECT betrag FROM eintraege x WHERE x.bezeichnung=e.bezeichnung ORDER BY datum DESC LIMIT 1) letzter_betrag "
        "FROM eintraege e WHERE bezeichnung LIKE ? AND kategorie_id IS NOT NULL "
        "GROUP BY bezeichnung, kategorie_id ORDER BY (bezeichnung LIKE ?) DESC, n DESC LIMIT 8",
        (f"%{q.strip()}%", f"{q.strip()}%"))


# ---------------------------------------------------------------- Auswertung

@app.get("/api/auswertung/zeitreihe")
def zeitreihe(request: Request):
    p = dict(request.query_params)
    einheit = p.get("einheit", "tag")
    if einheit not in ("tag", "woche", "monat", "jahr"):
        raise HTTPException(422, "einheit: tag|woche|monat|jahr")
    return auswertung.zeitreihe(p, einheit)


@app.get("/api/auswertung/kategorien")
def kategorien_auswertung(request: Request):
    return auswertung.nach_kategorien(dict(request.query_params))


@app.get("/api/auswertung/kategorie_verlauf")
def kategorie_verlauf(kategorie: int, monate: int = 13):
    von = (date.today().replace(day=1) - timedelta(days=31 * (monate - 1))).replace(day=1)
    return auswertung.zeitreihe({"von": von.isoformat(), "kategorie": kategorie}, "monat")


# ---------------------------------------------------------------- Kategorien, Konten, Regeln

@app.post("/api/kategorien")
def kategorie_anlegen(d: dict = Body(...)):
    name = (d.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Name fehlt")
    if db.q1("SELECT id FROM kategorien WHERE name=?", (name,)):
        raise HTTPException(409, "Kategorie gibt es schon")
    kid, _ = db.x("INSERT INTO kategorien(name, icon, farbe, beschreibung, sortierung) VALUES (?,?,?,?,?)",
                  (name, d.get("icon") or "🏷️", d.get("farbe") or "#6B7A8F", d.get("beschreibung") or "",
                   int(d.get("sortierung") or 500)))
    return db.q1("SELECT * FROM kategorien WHERE id=?", (kid,))


@app.put("/api/kategorien/{kid}")
def kategorie_aendern(kid: int, d: dict = Body(...)):
    alt = db.q1("SELECT * FROM kategorien WHERE id=?", (kid,))
    if not alt:
        raise HTTPException(404, "Kategorie nicht gefunden")
    neu = {**alt, **{k: d[k] for k in ("name", "icon", "farbe", "beschreibung", "sortierung", "archiviert") if k in d}}
    db.x("UPDATE kategorien SET name=?, icon=?, farbe=?, beschreibung=?, sortierung=?, archiviert=? WHERE id=?",
         (neu["name"].strip(), neu["icon"], neu["farbe"], neu["beschreibung"], int(neu["sortierung"]),
          int(bool(neu["archiviert"])), kid))
    sensoren_aktualisieren()
    return db.q1("SELECT * FROM kategorien WHERE id=?", (kid,))


@app.post("/api/kategorien/{kid}/zusammenfuehren")
def kategorie_zusammenfuehren(kid: int, d: dict = Body(...)):
    ziel = int(d["ziel"])
    if ziel == kid:
        raise HTTPException(422, "Ziel ist dieselbe Kategorie")

    def ausfuehren(c):
        c.execute("UPDATE eintraege SET kategorie_id=? WHERE kategorie_id=?", (ziel, kid))
        c.execute("UPDATE regeln SET kategorie_id=? WHERE kategorie_id=?", (ziel, kid))
        c.execute("DELETE FROM kategorien WHERE id=?", (kid,))

    db.transaktion(ausfuehren)
    sensoren_aktualisieren()
    return {"ok": True}


@app.post("/api/konten")
def konto_anlegen(d: dict = Body(...)):
    name = (d.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Name fehlt")
    kid, _ = db.x("INSERT INTO konten(name, typ, icon) VALUES (?, 'manuell', ?)", (name, d.get("icon") or "💳"))
    return db.q1("SELECT * FROM konten WHERE id=?", (kid,))


@app.put("/api/konten/{kid}")
def konto_aendern(kid: int, d: dict = Body(...)):
    alt = db.q1("SELECT * FROM konten WHERE id=?", (kid,))
    if not alt:
        raise HTTPException(404, "Konto nicht gefunden")
    neu = {**alt, **{k: d[k] for k in ("name", "icon", "archiviert") if k in d}}
    db.x("UPDATE konten SET name=?, icon=?, archiviert=? WHERE id=?",
         (neu["name"], neu["icon"], int(bool(neu["archiviert"])), kid))
    return db.q1("SELECT * FROM konten WHERE id=?", (kid,))


@app.get("/api/regeln")
def regeln():
    return db.q("SELECT r.*, k.name kategorie, k.icon kategorie_icon FROM regeln r "
                "LEFT JOIN kategorien k ON k.id=r.kategorie_id ORDER BY r.geaendert DESC")


@app.put("/api/regeln/{rid}")
def regel_aendern(rid: int, d: dict = Body(...)):
    alt = db.q1("SELECT * FROM regeln WHERE id=?", (rid,))
    if not alt:
        raise HTTPException(404, "Regel nicht gefunden")
    neu = {**alt, **{k: d[k] for k in ("kategorie_id", "bezeichnung", "mehrdeutig") if k in d}}
    db.x("UPDATE regeln SET kategorie_id=?, bezeichnung=?, mehrdeutig=?, geaendert=? WHERE id=?",
         (neu["kategorie_id"], neu["bezeichnung"], int(bool(neu["mehrdeutig"])), db.jetzt_iso(), rid))
    return {"ok": True}


@app.delete("/api/regeln/{rid}")
def regel_loeschen(rid: int):
    db.x("DELETE FROM regeln WHERE id=?", (rid,))
    return {"ok": True}


@app.put("/api/einstellungen")
def einstellungen_speichern(d: dict = Body(...)):
    if "mehrdeutig" in d:
        db.setze_einstellung("mehrdeutig", json.dumps([s.strip().lower() for s in d["mehrdeutig"] if s.strip()]))
    if "design" in d:
        db.setze_einstellung("design", re.sub(r"[^a-z0-9_-]", "", str(d["design"]).lower())[:30] or "salbei")
    if d.get("design_modus") in ("auto", "hell", "dunkel"):
        db.setze_einstellung("design_modus", d["design_modus"])
    if d.get("startseite") in ("uebersicht", "eintraege", "verlauf"):
        db.setze_einstellung("startseite", d["startseite"])
    if "wohnort" in d:
        db.setze_einstellung("ki_wohnort", str(d["wohnort"] or "").strip()[:60])
    return meta()


# ---------------------------------------------------------------- Trade Republic

@app.get("/api/tr")
def tr_konfig():
    return tr_sync.konfig()


@app.put("/api/tr")
def tr_speichern(d: dict = Body(...)):
    try:
        return tr_sync.konfig_speichern(d)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/tr")
def tr_entfernen():
    tr_sync.entfernen()
    return {"ok": True}


@app.post("/api/tr/abrufen")
async def tr_abrufen(voll: bool = False):
    if not tr_sync.eingerichtet():
        raise HTTPException(422, "Trade Republic ist noch nicht eingerichtet")
    neue = await asyncio.to_thread(tr_sync.synchronisiere, voll, True)
    for i in neue:
        e = db.q1(auswertung.BASIS_SELECT + "WHERE e.id=?", (i,))
        await asyncio.to_thread(auswertung.ereignis_neue_ausgabe, e)
    sensoren_aktualisieren()
    return {"neu": len(neue), "status": tr_sync.konfig()}


@app.post("/api/tr/login")
async def tr_login(d: dict = Body(default={})):
    if d.get("code"):
        return await asyncio.to_thread(tr_sync.login_code, str(d["code"]))
    try:
        return await asyncio.to_thread(tr_sync.login_starten, d.get("pin"), bool(d.get("pin_speichern")))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/tr/login")
def tr_login_status():
    return tr_sync.login_status


# ---------------------------------------------------------------- KI

@app.post("/api/ki/test")
def ki_test(d: dict = Body(default={})):
    beispiel = d.get("haendler") or "BACKSTUBE SIMON GMBH 0815"
    e = [{"haendler_roh": beispiel, "betrag": 4.2, "waehrung": "EUR", "datum": date.today().isoformat()}]
    if not optionen().get("anthropic_api_key"):
        raise HTTPException(422, "Kein API-Key in den Add-on-Optionen")
    antwort = kategorisierung.frage_claude([{"nr": 0, "haendler": beispiel, "betrag": 4.2, "waehrung": "EUR",
                                             "datum": date.today().isoformat()}], zweck="test")
    if not antwort:
        raise HTTPException(502, db.einstellung("ki_letzter_fehler") or "Keine Antwort")
    a = antwort[0]
    k = db.q1("SELECT name FROM kategorien WHERE id=?", (a["kategorie_id"],))
    return {"haendler": e[0]["haendler_roh"], "kategorie": k["name"] if k else None, **a}


# ---------------------------------------------------------------- Kassenbon

@app.post("/api/beleg")
async def beleg_lesen(d: dict = Body(...)):
    try:
        return await asyncio.to_thread(beleg.lesen, d.get("bild") or "")
    except beleg.BelegFehler as e:
        raise HTTPException(422, str(e))


@app.get("/api/ki/nutzung")
async def ki_nutzung_abfragen():
    return await asyncio.to_thread(ki_nutzung.uebersicht)


@app.put("/api/ki/preise")
def ki_preise(d: dict = Body(...)):
    modell = (d.get("modell") or optionen().get("claude_modell") or "").strip()
    if not modell:
        raise HTTPException(422, "Modell fehlt")
    try:
        if d.get("standard"):
            ki_nutzung.preis_setzen(modell, None, None)
        else:
            ki_nutzung.preis_setzen(modell, float(str(d["eingabe"]).replace(",", ".")),
                                    float(str(d["ausgabe"]).replace(",", ".")))
    except (KeyError, ValueError) as e:
        raise HTTPException(422, f"Preis ungültig: {e}")
    return ki_nutzung.uebersicht()


# ---------------------------------------------------------------- Import / Export

@app.post("/api/import/next")
async def import_next(datei: UploadFile = File(...), bis: str = Form(""), tr_folgetag: str = Form("")):
    inhalt = await datei.read()
    if bis:
        try:
            date.fromisoformat(bis[:10])
        except ValueError:
            raise HTTPException(422, "Stichtag ungueltig")
    try:
        erg = await asyncio.to_thread(next_import.importiere, inhalt, bis or None, tr_folgetag in ("1", "true", "on"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Import fehlgeschlagen: {e}")
    sensoren_aktualisieren()
    return erg


# ---------------------------------------------------------------- Banken (FinTS)

def _bank_oder_404(bank_id):
    try:
        return fints_sync.bank_laden(bank_id)
    except KeyError:
        raise HTTPException(404, "Bank nicht gefunden")


@app.get("/api/banken")
def banken():
    return {"banken": fints_sync.alle_banken(), "vorlagen": fints_sync.VORLAGEN,
            "produkt_id": fints_sync.produkt_id(), "umbuchung_begriffe": fints_sync.umbuchung_begriffe()}


@app.put("/api/banken/einstellungen")
def banken_einstellungen(d: dict = Body(...)):
    if "produkt_id" in d:
        db.setze_einstellung("fints_produkt_id", (d["produkt_id"] or "").strip())
    if "umbuchung_begriffe" in d:
        db.setze_einstellung("umbuchung_begriffe",
                             json.dumps([s.strip().lower() for s in d["umbuchung_begriffe"] if s.strip()]))
    return banken()


@app.post("/api/banken")
def bank_anlegen(d: dict = Body(...)):
    try:
        b = fints_sync.bank_anlegen(d)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return fints_sync.bank_oeffentlich(b)


@app.put("/api/banken/{bank_id}")
def bank_aendern(bank_id: int, d: dict = Body(...)):
    _bank_oder_404(bank_id)
    return fints_sync.bank_oeffentlich(fints_sync.bank_aendern(bank_id, d))


@app.delete("/api/banken/{bank_id}")
def bank_loeschen(bank_id: int):
    _bank_oder_404(bank_id)
    fints_sync.bank_loeschen(bank_id)
    return {"ok": True}


@app.post("/api/banken/{bank_id}/auftrag")
def bank_auftrag(bank_id: int, d: dict = Body(...)):
    b = _bank_oder_404(bank_id)
    art = d.get("art")
    if art not in ("einrichten", "abruf", "nachholen"):
        raise HTTPException(422, "art: einrichten|abruf|nachholen")
    if art == "nachholen" and not d.get("von"):
        raise HTTPException(422, "Startdatum fuer das Nachholen fehlt")
    if not (d.get("pin") or b["pin"]):
        raise HTTPException(422, "PIN fehlt")
    if not fints_sync.produkt_id():
        raise HTTPException(422, "Zuerst die FinTS-Produkt-ID eintragen")
    return fints_sync.job_starten(bank_id, art, d.get("pin"), d.get("von"), d.get("bis")).status()


@app.get("/api/banken/{bank_id}/auftrag")
def bank_auftrag_status(bank_id: int):
    j = fints_sync.job(bank_id)
    return j.status() if j else {"zustand": "keiner"}


@app.post("/api/banken/{bank_id}/tan")
def bank_tan(bank_id: int, d: dict = Body(...)):
    j = fints_sync.job(bank_id)
    if not j or not j.is_alive():
        raise HTTPException(409, "Kein laufender Auftrag")
    if d.get("abbrechen"):
        j.abbrechen()
    else:
        j.tan_eingeben(str(d.get("tan") or ""))
    return j.status()


@app.get("/api/nachholen")
def nachholen(bank_id: int = 0):
    return fints_sync.nachholen_liste(bank_id or None)


@app.post("/api/nachholen/uebernehmen")
def nachholen_uebernehmen(d: dict = Body(...)):
    n = fints_sync.nachholen_uebernehmen(d.get("eintraege") or [])
    v = fints_sync.nachholen_verwerfen(d.get("verwerfen") or [])
    sensoren_aktualisieren()
    return {"uebernommen": n, "verworfen": v}


@app.post("/api/admin/zuruecksetzen")
def zuruecksetzen(d: dict = Body(...)):
    """Loescht alle Eintraege. Kategorien, Konten und Einstellungen bleiben erhalten."""
    if (d.get("bestaetigung") or "").strip().upper() != "LÖSCHEN":
        raise HTTPException(422, "Zur Bestaetigung LÖSCHEN eintippen")

    def ausfuehren(c):
        anzahl = c.execute("DELETE FROM eintraege").rowcount
        # Merker fuer einzeln geloeschte TR-Umsaetze verwerfen, damit ein neuer Abruf alles holt
        c.execute("DELETE FROM einstellungen WHERE schluessel LIKE 'geloescht:%'")
        if d.get("regeln_loeschen"):
            c.execute("DELETE FROM regeln")
        return anzahl

    anzahl = db.transaktion(ausfuehren)
    if "tr_import_ab" in d:
        db.setze_einstellung("tr_import_ab", d["tr_import_ab"] or None)
    log.info("Alle Eintraege geloescht (%s)", anzahl)
    sensoren_aktualisieren()
    return {"geloescht": anzahl, "tr_import_ab": db.einstellung("tr_import_ab")}


@app.get("/api/export")
def export_datei(request: Request):
    p = dict(request.query_params)
    stempel = datetime.now().strftime("%Y-%m-%d")
    if p.get("ablage"):
        # Fuer die HA-App auf dem iPhone, wo Downloads aus dem Ingress-Rahmen oft nicht gehen
        os.makedirs("/share/ausgaben", exist_ok=True)
        endung = "csv" if p.get("format") == "csv" else "xlsx"
        pfad = f"/share/ausgaben/ausgaben_{datetime.now().strftime('%Y-%m-%d_%H%M')}.{endung}"
        inhalt = export.csv_text(p).encode("utf-8") if endung == "csv" else export.xlsx(p)
        with open(pfad, "wb") as f:
            f.write(inhalt)
        return {"pfad": pfad}
    if p.get("format") == "csv":
        return Response(export.csv_text(p), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="ausgaben_{stempel}.csv"'})
    return Response(export.xlsx(p),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="ausgaben_{stempel}.xlsx"'})


@app.get("/api/kurs")
def kurs(waehrung_code: str, datum: str = ""):
    return {"kurs": waehrung.kurs(datum or date.today().isoformat(), waehrung_code)}


# ---------------------------------------------------------------- Frontend

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8099, log_level="warning", proxy_headers=False)
