"""Kassenbon per Foto einlesen.

Das Bild (im Browser bereits verkleinert) geht zusammen mit der Kategorienliste an Claude.
Claude liefert Haendler, Datum, Summe, Waehrung, Zahlungsart, Positionen und einen
Kategorie-Vorschlag. Danach gilt dieselbe Reihenfolge wie bei automatischen Umsaetzen:
gelernte Zuordnung vor eigenem Verlauf vor KI-Vorschlag.
Das Foto wird nicht gespeichert.
"""
import base64
import json
import logging
import re
from datetime import date, datetime, timedelta

import httpx

from . import auswertung, db, kategorisierung, ki_nutzung, waehrung
from .einstellungen import optionen

log = logging.getLogger("ausgaben.beleg")

MAX_BILD_BYTES = 4_500_000   # Anthropic erlaubt 5 MB je Bild
DUPLIKAT_TAGE = 2

SYSTEM_PROMPT = """Du liest Kassenbons, Quittungen und Rechnungen aus Fotos aus, fuer ein Haushaltsbuch in Deutschland.
Antworte ausschliesslich mit einem JSON-Objekt, ohne Erklaerung und ohne Markdown:
{"lesbar": true, "haendler": "...", "datum": "JJJJ-MM-TT", "uhrzeit": "HH:MM", "betrag": 0.00, "waehrung": "EUR",
 "zahlungsart": "bar|karte|unbekannt", "positionen": [{"text": "...", "betrag": 0.00}],
 "kategorie_id": 0, "bezeichnung": "...", "sicherheit": 0.0, "hinweis": ""}
Regeln:
- betrag: die tatsaechlich bezahlte Gesamtsumme (SUMME, Gesamt, Zu zahlen, Total) nach Rabatten und Pfand.
  Nicht das gegebene Bargeld, nicht das Rueckgeld, nicht eine Zwischensumme. Unlesbar: null.
- haendler: Name des Geschaefts, wie man ihn im Alltag nennt, ohne Rechtsform, Adresse und Filialnummer
  (z. B. "REWE", "dm", "Aldi", "Baeckerei Mueller").
- datum und uhrzeit: Kaufzeitpunkt vom Bon. Zweistellige Jahre als 20xx lesen. Nicht raten: unlesbar -> null.
- waehrung: ISO-Code, in Deutschland meist EUR.
- zahlungsart: "karte" bei EC, girocard, Visa, Mastercard, Maestro, kontaktlos, Kartenzahlung, Terminal-Beleg;
  "bar" bei BAR, Gegeben, Rueckgeld; sonst "unbekannt".
- positionen: Artikelzeilen mit ihrem Endpreis, hoechstens 40, Text so kurz wie auf dem Bon.
  Keine MwSt.-Zeilen, Zwischensummen oder Zahlungszeilen. Rabatte und Pfandrueckgabe als negative Betraege.
- kategorie_id: aus der Liste die Kategorie, die am besten zum Einkauf passt. Die Positionen zaehlen mehr als der
  Name des Geschaefts (Drogerieartikel im Supermarkt sind trotzdem Lebensmitteleinkauf, wenn diese ueberwiegen).
  bezeichnung im Stil der typischen Eintraege, sicherheit 0..1 fuer die Kategorie.
- hinweis: kurz auf Deutsch, nur wenn etwas unsicher ist (z. B. "Summe teilweise verdeckt"), sonst "".
- Ist kein Kassenbon zu erkennen: lesbar false, hinweis mit dem Grund, alle anderen Felder null."""


class BelegFehler(Exception):
    pass


def _bild_aus_daten_url(daten_url):
    m = re.match(r"^data:(image/(?:jpeg|png|webp|gif));base64,(.+)$", (daten_url or "").strip(), re.S)
    if not m:
        raise BelegFehler("Kein Bild erhalten")
    medientyp, b64 = m.group(1), re.sub(r"\s", "", m.group(2))
    try:
        groesse = len(base64.b64decode(b64, validate=True))
    except ValueError:
        raise BelegFehler("Bild beschädigt")
    if groesse > MAX_BILD_BYTES:
        raise BelegFehler("Bild zu groß")
    return medientyp, b64


def _frage_claude(medientyp, b64):
    opt = optionen()
    kat_text, gueltige_ids, _vertraulich = kategorisierung._kategorien_kontext()
    text = (f"Heute ist der {date.today().isoformat()}.\n\nKategorien:\n{kat_text}\n\n"
            "Lies den Kassenbon auf dem Foto aus.")
    modell = opt.get("claude_modell") or "claude-haiku-5-5"
    usage = None
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": opt["anthropic_api_key"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": modell,
                "max_tokens": 2000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": medientyp, "data": b64}},
                    {"type": "text", "text": text},
                ]}],
            },
            timeout=90,
        )
        r.raise_for_status()
        usage = r.json().get("usage")
        antwort = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        antwort = re.sub(r"```(json)?", "", antwort).strip()
        start, ende = antwort.find("{"), antwort.rfind("}")
        daten = json.loads(antwort[start: ende + 1])
    except httpx.HTTPStatusError as e:
        meldung = f"KI-Anfrage abgelehnt ({e.response.status_code})"
        try:
            meldung += ": " + e.response.json()["error"]["message"][:200]
        except Exception:  # noqa: BLE001
            pass
        db.setze_einstellung("ki_letzter_fehler", f"{db.jetzt_iso()}: {meldung}")
        ki_nutzung.erfassen("beleg", modell, None, 1, ok=False, meldung=meldung)
        raise BelegFehler(meldung)
    except Exception as e:  # noqa: BLE001
        log.warning("Bon-Erkennung fehlgeschlagen: %s", e)
        db.setze_einstellung("ki_letzter_fehler", f"{db.jetzt_iso()}: Bon: {str(e)[:300]}")
        ki_nutzung.erfassen("beleg", modell, usage, 1, ok=False, meldung=str(e))
        raise BelegFehler("Der Bon konnte nicht gelesen werden. Bitte noch einmal versuchen.")
    db.setze_einstellung("ki_letzter_fehler", "")
    ki_nutzung.erfassen("beleg", modell, usage, 1)
    return daten, gueltige_ids


def _zahl(v):
    if v is None or v == "":
        return None
    try:
        return round(float(str(v).replace(",", ".")), 2)
    except ValueError:
        return None


def _datum(v):
    try:
        d = date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None
    if d > date.today() + timedelta(days=1) or d.year < 2000:
        return None
    return d.isoformat()


def _uhrzeit(v):
    m = re.match(r"^(\d{1,2}):(\d{2})", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _regel_ohne_zaehlen(haendler):
    n = kategorisierung.normalisiere(haendler)
    if not n:
        return None
    return (db.q1("SELECT * FROM regeln WHERE muster=?", (n,))
            or db.q1("SELECT * FROM regeln WHERE muster=?", (n.split()[0],)))


def duplikate(betrag, datum):
    """Vorhandene Eintraege mit gleichem Betrag in +-2 Tagen (egal aus welcher Quelle)."""
    if not betrag:
        return []
    tag = date.fromisoformat(datum) if datum else date.today()
    von = (tag - timedelta(days=DUPLIKAT_TAGE)).isoformat()
    bis = (tag + timedelta(days=DUPLIKAT_TAGE + 1)).isoformat()
    return db.q(auswertung.BASIS_SELECT + "WHERE ABS(e.betrag - ?) < 0.005 AND e.datum >= ? AND e.datum < ? "
                "ORDER BY ABS(julianday(e.datum) - julianday(?)) LIMIT 3", (betrag, von, bis, tag.isoformat()))


def lesen(daten_url):
    opt = optionen()
    if not (opt.get("ki_aktiv") and opt.get("anthropic_api_key")):
        raise BelegFehler("Zum Einlesen von Kassenbons wird die KI gebraucht: API-Key in den Add-on-Optionen "
                          "eintragen und die KI-Kategorisierung einschalten.")
    medientyp, b64 = _bild_aus_daten_url(daten_url)
    roh, gueltige_ids = _frage_claude(medientyp, b64)

    if not roh.get("lesbar", True):
        return {"lesbar": False, "hinweis": (roh.get("hinweis") or "Kein Kassenbon erkannt").strip()[:200]}

    haendler = (roh.get("haendler") or "").strip()[:80]
    betrag = _zahl(roh.get("betrag"))
    datum = _datum(roh.get("datum"))
    wae = (roh.get("waehrung") or "EUR").strip().upper()[:3]
    hinweise = [(roh.get("hinweis") or "").strip()[:200]]
    if wae not in waehrung.WAEHRUNGEN:
        hinweise.append(f"Währung {wae} ist nicht in der Liste, bitte prüfen")
        wae = "EUR"
    zahlungsart = roh.get("zahlungsart") if roh.get("zahlungsart") in ("bar", "karte") else "unbekannt"
    positionen = []
    for p in (roh.get("positionen") or [])[:40]:
        if isinstance(p, dict) and (p.get("text") or "").strip():
            positionen.append({"text": str(p["text"]).strip()[:60], "betrag": _zahl(p.get("betrag"))})

    # Kategorie: gelernte Zuordnung > Verlauf > KI
    kategorie_id, bezeichnung, durch, sicherheit = None, "", None, None
    r = _regel_ohne_zaehlen(haendler)
    if r and not r["mehrdeutig"]:
        kategorie_id, bezeichnung, durch, sicherheit = r["kategorie_id"], r["bezeichnung"] or haendler, "regel", 1.0
    else:
        v = kategorisierung.Verlauf().vorschlag(haendler) if haendler else None
        if v:
            kategorie_id, bezeichnung, durch, sicherheit = v["kategorie_id"], v["bezeichnung"], "verlauf", v["sicherheit"]
    if kategorie_id is None:
        try:
            kid = int(roh.get("kategorie_id"))
        except (TypeError, ValueError):
            kid = None
        if kid in gueltige_ids:
            kategorie_id, durch = kid, "ki"
            sicherheit = _zahl(roh.get("sicherheit"))
        bezeichnung = (roh.get("bezeichnung") or "").strip()[:80] or haendler

    konto_id = db.konto_id("Bargeld") if zahlungsart == "bar" else None
    return {
        "lesbar": True, "haendler": haendler, "datum": datum, "uhrzeit": _uhrzeit(roh.get("uhrzeit")) if datum else None,
        "betrag": betrag, "waehrung": wae, "zahlungsart": zahlungsart, "positionen": positionen,
        "kategorie_id": kategorie_id, "bezeichnung": bezeichnung or haendler, "kategorisiert_durch": durch,
        "sicherheit": sicherheit, "konto_id": konto_id, "hinweis": " · ".join(h for h in hinweise if h),
        "duplikate": duplikate(betrag, datum),
    }
