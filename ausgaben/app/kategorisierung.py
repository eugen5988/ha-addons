"""Kategorisierung neuer Eintraege.

Reihenfolge:
  1. Regel (vom Nutzer gelernt oder angelegt)       -> sicher
  2. Verlauf (Next-Altdaten + eigene Korrekturen)  -> sicher, wenn eindeutig
  3. Claude (kleines Modell)                        -> mit Sicherheitswert
Mehrdeutige Haendler (z. B. PayPal ohne Zielhaendler, Amazon) landen immer in "Pruefen".

Vertrauliche Kategorien (Name "Privat" oder "vertraulich" in der Beschreibung) gehen nur
mit ihrem Namen an Claude: ohne Beschreibung, ohne Beispiele, ohne Korrekturen.
"""
import json
import logging
import re
import unicodedata
from collections import Counter, defaultdict

import httpx

from . import db, ki_nutzung
from .einstellungen import optionen

log = logging.getLogger("ausgaben.kategorisierung")

STANDARD_MEHRDEUTIG = ["paypal", "amazon", "amzn", "klarna", "sumup", "zettle", "tricount", "ueberweisung"]
SICHERHEIT_OHNE_PRUEFEN = 0.8
VERTRAULICH_NAMEN = {"privat"}
VERTRAULICH_MARKER = "vertraulich"

_VORSILBEN = re.compile(r"^(paypal|pp|sumup|sq|zettle|izettle|sp)\s*[\*\.]\s*", re.I)
_RAUSCHEN = re.compile(
    r"\b(gmbh|co|kg|ag|ug|se|e\.?k|inc|ltd|llc|sarl|bv|nv|filiale|store|shop|online|"
    r"www|de|com|net|eu|nl|fr|it|es|marketplace|mktp|zahlung|payment)\b"
)


def normalisiere(text):
    """'PayPal *Vinted 1234' -> 'vinted', 'McDonald’s 0815' -> 'mcdonalds'."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    t = t.replace("’", "").replace("'", "")
    t = _VORSILBEN.sub("", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    t = _RAUSCHEN.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def mehrdeutige_muster():
    roh = db.einstellung("mehrdeutig")
    return json.loads(roh) if roh else STANDARD_MEHRDEUTIG


def ist_mehrdeutig(haendler_roh):
    roh = (haendler_roh or "").lower()
    norm = normalisiere(haendler_roh)
    for m in mehrdeutige_muster():
        if m == "paypal":
            # PayPal nur mehrdeutig, wenn kein Zielhaendler dahinter steht
            if "paypal" in roh:
                rest = normalisiere(re.sub(r"paypal\s*(europe)?\s*[\*\.]?", " ", roh))
                rest = re.sub(r"\b(s a r l|et cie|s c a|europe)\b", " ", rest).strip()
                if not rest:
                    return True
            continue
        if m in norm.split() or norm.startswith(m):
            return True
    return False


def ist_vertraulich(kategorie):
    name = (kategorie.get("name") or "").strip().lower()
    beschreibung = (kategorie.get("beschreibung") or "").lower()
    return name in VERTRAULICH_NAMEN or VERTRAULICH_MARKER in beschreibung


# ---------------------------------------------------------------- Verlauf

class Verlauf:
    """Index ueber bisherige, vom Menschen vergebene Kategorien."""

    def __init__(self):
        self.voll = defaultdict(Counter)   # normalisierte Bezeichnung -> Kategorie-Zaehler
        self.wort = defaultdict(Counter)   # erstes Wort -> Kategorie-Zaehler
        self.name = {}                     # normalisiert -> schoenste Bezeichnung
        zeilen = db.q(
            "SELECT bezeichnung, haendler_roh, kategorie_id FROM eintraege "
            "WHERE kategorie_id IS NOT NULL AND pruefen=0 "
            "AND kategorisiert_durch IN ('next','nutzer','regel','verlauf')"
        )
        for z in zeilen:
            for text in {z["bezeichnung"], z["haendler_roh"]}:
                n = normalisiere(text)
                if not n:
                    continue
                self.voll[n][z["kategorie_id"]] += 1
                self.wort[n.split()[0]][z["kategorie_id"]] += 1
                if text == z["bezeichnung"]:
                    self.name.setdefault(n, z["bezeichnung"])

    def vorschlag(self, haendler_roh):
        n = normalisiere(haendler_roh)
        if not n:
            return None
        for zaehler in (self.voll.get(n), self.wort.get(n.split()[0])):
            if not zaehler:
                continue
            gesamt = sum(zaehler.values())
            kat, anz = zaehler.most_common(1)[0]
            anteil = anz / gesamt
            if anz >= 2 and anteil >= 0.75:
                bez = self.name.get(n) or self._bezeichnung_fuer_wort(n.split()[0], kat)
                return {"kategorie_id": kat, "bezeichnung": bez, "sicherheit": round(min(0.99, anteil), 2)}
        return None

    def _bezeichnung_fuer_wort(self, wort, kat):
        for n, name in self.name.items():
            if n.split()[0] == wort:
                return name.split()[0] if len(name.split()) > 1 else name
        return wort.capitalize()


# ---------------------------------------------------------------- Regeln

def regel_fuer(haendler_roh):
    n = normalisiere(haendler_roh)
    if not n:
        return None
    r = db.q1("SELECT * FROM regeln WHERE muster=?", (n,))
    if not r:
        r = db.q1("SELECT * FROM regeln WHERE muster=?", (n.split()[0],))
    if r:
        db.x("UPDATE regeln SET treffer=treffer+1 WHERE id=?", (r["id"],))
    return r


def lerne_aus_korrektur(eintrag):
    """Nach einer Nutzerkorrektur Regel anlegen/aktualisieren (nicht fuer mehrdeutige Haendler)."""
    roh = eintrag.get("haendler_roh")
    if not roh or eintrag.get("kategorie_id") is None or ist_mehrdeutig(roh):
        return
    n = normalisiere(roh)
    if not n:
        return
    db.x(
        "INSERT INTO regeln(muster, kategorie_id, bezeichnung, quelle, geaendert) VALUES (?,?,?,?,?) "
        "ON CONFLICT(muster) DO UPDATE SET kategorie_id=excluded.kategorie_id, "
        "bezeichnung=excluded.bezeichnung, geaendert=excluded.geaendert",
        (n, eintrag["kategorie_id"], eintrag.get("bezeichnung"), "nutzer", db.jetzt_iso()),
    )


# ---------------------------------------------------------------- Claude

def _kategorien_kontext():
    kats = db.q("SELECT id, name, beschreibung FROM kategorien WHERE archiviert=0 ORDER BY sortierung, name")
    vertraulich = {k["id"] for k in kats if ist_vertraulich(k)}
    beispiele = db.q(
        "SELECT kategorie_id, bezeichnung, COUNT(*) n FROM eintraege "
        "WHERE kategorie_id IS NOT NULL AND pruefen=0 AND bezeichnung<>'' "
        "GROUP BY kategorie_id, bezeichnung ORDER BY n DESC"
    )
    je_kat = defaultdict(list)
    for b in beispiele:
        if b["kategorie_id"] in vertraulich:
            continue
        if len(je_kat[b["kategorie_id"]]) < 12:
            je_kat[b["kategorie_id"]].append(b["bezeichnung"])
    zeilen = []
    for k in kats:
        txt = f'- id {k["id"]}: {k["name"]}'
        if k["id"] not in vertraulich:
            if k["beschreibung"]:
                txt += f' ({k["beschreibung"]})'
            bsp = ", ".join(je_kat.get(k["id"], []))
            if bsp:
                txt += f" | typische Eintraege: {bsp}"
        zeilen.append(txt)
    return "\n".join(zeilen), {k["id"] for k in kats}, vertraulich


def _korrekturen_kontext(vertraulich):
    rows = db.q(
        "SELECT e.kategorie_id, haendler_roh, bezeichnung, k.name kat FROM eintraege e "
        "JOIN kategorien k ON k.id=e.kategorie_id "
        "WHERE e.kategorisiert_durch='nutzer' AND e.haendler_roh IS NOT NULL "
        "ORDER BY e.geaendert DESC LIMIT 60"
    )
    rows = [r for r in rows if r["kategorie_id"] not in vertraulich][:30]
    return "\n".join(f'- "{r["haendler_roh"]}" -> {r["kat"]} / "{r["bezeichnung"]}"' for r in rows)


SYSTEM_PROMPT = """Du ordnest Ausgaben eines Familienhaushalts in Deutschland{wohnort} Kategorien zu.
Die Rohdaten kommen meist von Kartenzahlungen (Haendlername wie auf dem Kontoauszug).
Antworte ausschliesslich mit einem JSON-Array, ohne Erklaerung und ohne Markdown.
Jedes Element: {"nr": <nr>, "kategorie_id": <id aus der Liste>, "bezeichnung": "<kurzer, sauberer Name>", "sicherheit": <0..1>}
Regeln:
- bezeichnung im Stil der typischen Eintraege: kurz, ohne Ort, Filialnummer oder Rechtsform (z. B. "Wez", "Bäcker", "Netflix").
- Wenn der Haendler mehrdeutig ist (Zahlungsdienst, Kaufhaus-Marktplatz, Privatperson), waehle die wahrscheinlichste Kategorie und setze sicherheit unter 0.6.
- Nutze nur kategorie_id-Werte aus der Liste."""


def system_prompt():
    ort = (db.einstellung("ki_wohnort") or "").strip()
    return SYSTEM_PROMPT.replace("{wohnort}", f" (Wohnort: {ort})" if ort else "")


def frage_claude(eintraege, zweck="kategorisierung"):
    """eintraege: Liste von dicts mit nr, haendler, betrag, waehrung, datum, typ. Gibt dict nr->Vorschlag."""
    opt = optionen()
    if not (opt.get("ki_aktiv") and opt.get("anthropic_api_key")) or not eintraege:
        return {}
    kat_text, gueltige_ids, vertraulich = _kategorien_kontext()
    korrekturen = _korrekturen_kontext(vertraulich)
    nutzer = f"Kategorien:\n{kat_text}\n\n"
    if korrekturen:
        nutzer += f"Zuletzt vom Nutzer korrigiert (daran orientieren):\n{korrekturen}\n\n"
    nutzer += "Einzuordnen:\n" + json.dumps(eintraege, ensure_ascii=False)
    modell = opt.get("claude_modell") or "claude-haiku-5-5"
    usage = None
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": opt["anthropic_api_key"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": modell,
                "max_tokens": 2000,
                "system": system_prompt(),
                "messages": [{"role": "user", "content": nutzer}],
            },
            timeout=60,
        )
        r.raise_for_status()
        usage = r.json().get("usage")
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        text = re.sub(r"```(json)?", "", text).strip()
        start, ende = text.find("["), text.rfind("]")
        daten = json.loads(text[start : ende + 1])
    except httpx.HTTPStatusError as e:
        meldung = {401: "API-Key ungültig oder abgelaufen - in der Add-on-Konfiguration prüfen",
                   403: "API-Key hat keine Berechtigung für dieses Modell",
                   404: f"Modell {modell} unbekannt - in der Add-on-Konfiguration prüfen",
                   429: "Zu viele Anfragen oder Guthaben aufgebraucht",
                   529: "Claude ist gerade überlastet, nächster Versuch beim nächsten Abruf"}.get(
            e.response.status_code, f"Anfrage abgelehnt ({e.response.status_code})")
        log.warning("Claude-Anfrage fehlgeschlagen: %s", meldung)
        db.setze_einstellung("ki_letzter_fehler", f"{db.jetzt_iso()}: {meldung}")
        ki_nutzung.erfassen(zweck, modell, usage, len(eintraege), ok=False, meldung=meldung)
        return {}
    except Exception as e:  # noqa: BLE001
        log.warning("Claude-Anfrage fehlgeschlagen: %s", e)
        db.setze_einstellung("ki_letzter_fehler", f"{db.jetzt_iso()}: {str(e)[:300]}")
        ki_nutzung.erfassen(zweck, modell, usage, len(eintraege), ok=False, meldung=str(e))
        return {}
    db.setze_einstellung("ki_letzter_fehler", "")
    ki_nutzung.erfassen(zweck, modell, usage, len(eintraege))
    ergebnis = {}
    for d in daten:
        try:
            kid = int(d["kategorie_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if kid in gueltige_ids:
            ergebnis[d.get("nr")] = {
                "kategorie_id": kid,
                "bezeichnung": (d.get("bezeichnung") or "").strip()[:80],
                "sicherheit": float(d.get("sicherheit") or 0.5),
            }
    return ergebnis


def kategorisiere(neue):
    """neue: Liste von dicts (haendler_roh, betrag, waehrung, datum, typ). Ergaenzt die Felder
    kategorie_id, bezeichnung, pruefen, ki_sicherheit, kategorisiert_durch in-place."""
    verlauf = None
    offen = []
    for e in neue:
        roh = e.get("haendler_roh") or ""
        mehrdeutig = ist_mehrdeutig(roh)
        r = regel_fuer(roh)
        if r and not r["mehrdeutig"]:
            e.update(kategorie_id=r["kategorie_id"], bezeichnung=r["bezeichnung"] or roh,
                     kategorisiert_durch="regel", ki_sicherheit=1.0, pruefen=1 if mehrdeutig else 0)
            continue
        if verlauf is None:
            verlauf = Verlauf()
        v = None if mehrdeutig else verlauf.vorschlag(roh)
        if v:
            e.update(kategorie_id=v["kategorie_id"], bezeichnung=v["bezeichnung"],
                     kategorisiert_durch="verlauf", ki_sicherheit=v["sicherheit"],
                     pruefen=0 if v["sicherheit"] >= SICHERHEIT_OHNE_PRUEFEN else 1)
            continue
        e.update(bezeichnung=e.get("bezeichnung") or roh, pruefen=1, kategorisiert_durch=None)
        e["_mehrdeutig"] = mehrdeutig
        offen.append(e)

    for start in range(0, len(offen), 25):
        teil = offen[start : start + 25]
        anfrage = [
            {"nr": i, "haendler": e.get("haendler_roh"), "betrag": e.get("betrag"),
             "waehrung": e.get("waehrung", "EUR"), "datum": (e.get("datum") or "")[:10], "typ": e.get("typ")}
            for i, e in enumerate(teil)
        ]
        antworten = frage_claude(anfrage)
        for i, e in enumerate(teil):
            a = antworten.get(i)
            if a:
                sicher = a["sicherheit"]
                e.update(kategorie_id=a["kategorie_id"], bezeichnung=a["bezeichnung"] or e["bezeichnung"],
                         kategorisiert_durch="ki", ki_sicherheit=sicher,
                         pruefen=1 if (e["_mehrdeutig"] or sicher < SICHERHEIT_OHNE_PRUEFEN) else 0)
    for e in neue:
        e.pop("_mehrdeutig", None)
        if e.get("kategorie_id") is None:
            e["pruefen"] = 1
    return neue
