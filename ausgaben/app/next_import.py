"""Import des Excel-Exports der iOS-App "Next" (Spalten: Category ID, Category, Date, Amount, Note).

Der Export enthaelt oft keine Kategorienamen, nur IDs. Die Namen unten sind Vorschlaege fuer die
Standard-IDs von Next und koennen in der App jederzeit umbenannt werden.
"""
import io
from datetime import datetime

import xlrd

from . import db

# Next-Kategorie-ID -> (Name, Icon, Farbe, Beschreibung fuer die KI)
NEXT_KATEGORIEN = {
    1: ("Lebensmittel & Drogerie", "🛒", "#3C8D5A", "Supermarkt, Drogerie, Getraenke"),
    2: ("Kleidung", "👕", "#B5527D", "Kleidung und Schuhe, auch fuer die Kinder, Vinted"),
    3: ("Abos & Streaming", "📺", "#7A5BC4", "iCloud, Netflix, Spotify, Disney, App-Abos"),
    4: ("Essen & Snacks", "🥨", "#D08A2E", "Baecker, Kantine, Restaurant, Fast Food, Lieferdienst"),
    5: ("Privat", "🔒", "#6E6A86", "persoenliche Ausgaben"),
    6: ("Wohnen & Nebenkosten", "🏠", "#4C6EAD", "Miete, Strom, Internet, Rundfunkbeitrag, Baumarkt, Haushalt"),
    10: ("Bahn, Bus & Taxi", "🚆", "#3E8FC2", "oeffentlicher Verkehr, Taxi, Uber"),
    14: ("Kinder & Spielzeug", "🧸", "#E07A9B", "Spielzeug, Kinderbuecher, Babysachen"),
    15: ("Tanken & Laden", "⚡", "#2E9AA6", "Tankstelle, Wallbox-Strom, Supercharger, Ladekarte"),
    23: ("Urlaub", "🏖️", "#E0A21B", "alles waehrend einer Reise (Next-Notizen: Et = Essen/Trinken, Tr = Transport)"),
    24: ("Auto", "🚗", "#5B6770", "Parken, Waschen, Versicherung, Leasing, Rate, Reifen, Werkstatt"),
    25: ("Katzen", "🐈", "#9C6B3E", "Katzenfutter, Tierarzt, Streu"),
    27: ("Geschenke & Spenden", "🎁", "#C8463D", "Geschenke, Blumen, Spenden"),
    28: ("Handy", "📱", "#46617A", "Handyvertraege, Zubehoer"),
    36: ("Friseur", "💇", "#B07AA1", "Friseur"),
    38: ("Freizeit & Sport", "🏸", "#5E9E2F", "Badminton, Sauna, Ausfluege, Tierpark, Camping"),
    39: ("Sonstiges", "📦", "#8A8F98", "Kleinkram, Gebuehren ohne eigene Kategorie"),
    50: ("Hochzeit", "💍", "#D4A5A5", "Hochzeit 2021"),
    52: ("Gesundheit", "💊", "#D9534F", "Apotheke, Arzt, Zahnarzt, Medikamente"),
    54: ("Elektronik & Technik", "🔌", "#3B5998", "Kabel, Smart Home, Computerzubehoer"),
    56: ("Körperpflege", "🧴", "#C98BB9", "Parfum, Kosmetik, Rasierer"),
    59: ("Kino", "🎬", "#8E44AD", "Kino"),
    62: ("Bank & Gebühren", "🏦", "#607D8B", "Kontofuehrung, Kartengebuehren, Ausweise"),
    66: ("Versicherungen & Beiträge", "🛡️", "#2C7873", "Versicherungen, Gewerkschaft, ADAC"),
    69: ("Brillen & Accessoires", "🕶️", "#A3824B", "Sonnenbrille, Brille"),
    75: ("Schmuck & Zeitschriften", "💎", "#B8860B", "Schmuck, Zeitschriften"),
    82: ("Porto & Versand", "✉️", "#8D6E63", "Briefmarken, Pakete, Versand"),
}
NEXT_ZUSAMMENLEGEN = {0: 23}  # ID 0 (ein einzelner Urlaubs-Eintrag) -> Urlaub


def _zeilen_lesen(inhalt):
    buch = xlrd.open_workbook(file_contents=inhalt)
    blatt = buch.sheet_by_index(0)
    kopf_zeile, spalten = None, {}
    for r in range(min(blatt.nrows, 30)):
        werte = [str(v).strip() for v in blatt.row_values(r)]
        if "Amount" in werte and "Date" in werte:
            kopf_zeile = r
            spalten = {name: werte.index(name) for name in werte if name}
            break
    if kopf_zeile is None:
        raise ValueError("Keine Kopfzeile mit 'Date' und 'Amount' gefunden - ist das ein Next-Export?")
    for r in range(kopf_zeile + 1, blatt.nrows):
        z = blatt.row_values(r)
        datum = z[spalten["Date"]]
        if datum in ("", None):
            continue
        if isinstance(datum, float):
            datum = xlrd.xldate_as_datetime(datum, buch.datemode)
        else:
            datum = datetime.fromisoformat(str(datum))
        kid = z[spalten.get("Category ID", -1)] if "Category ID" in spalten else None
        kname = str(z[spalten["Category"]]).strip() if "Category" in spalten else ""
        yield {
            "kat_id": int(kid) if kid not in ("", None) else None,
            "kat_name": kname,
            "datum": datum.replace(microsecond=0).isoformat(),
            "betrag": float(z[spalten["Amount"]] or 0),
            "notiz": str(z[spalten["Note"]]).strip() if "Note" in spalten else "",
        }


def importiere(inhalt, bis=None, tr_folgetag=False):
    """bis: 'YYYY-MM-DD' - nur Zeilen bis einschliesslich diesem Tag uebernehmen.
    tr_folgetag: Trade Republic ab dem Tag nach 'bis' uebernehmen."""
    zeilen = list(_zeilen_lesen(inhalt))
    in_datei = len(zeilen)
    if bis:
        zeilen = [z for z in zeilen if z["datum"][:10] <= bis[:10]]
    konto = db.konto_id("Altdaten (Next)")
    if konto is None:
        konto, _ = db.x("INSERT INTO konten(name, typ, icon, archiviert) VALUES ('Altdaten (Next)','manuell','🗂️',1)")

    def ausfuehren(c):
        kat_map = {}
        vorhandene = {r["name"]: r["id"] for r in (dict(x) for x in c.execute("SELECT id, name FROM kategorien"))}

        def kat_fuer(next_id, next_name):
            next_id = NEXT_ZUSAMMENLEGEN.get(next_id, next_id)
            schluessel = (next_id, next_name)
            if schluessel in kat_map:
                return kat_map[schluessel]
            if next_name:
                name, icon, farbe, beschr = next_name, "🏷️", "#6B7A8F", ""
            elif next_id in NEXT_KATEGORIEN:
                name, icon, farbe, beschr = NEXT_KATEGORIEN[next_id]
            else:
                name, icon, farbe, beschr = f"Next {next_id}", "🏷️", "#6B7A8F", ""
            if name in vorhandene:
                kid = vorhandene[name]
                if beschr:
                    c.execute("UPDATE kategorien SET icon=?, farbe=?, beschreibung=? WHERE id=? AND beschreibung=''",
                              (icon, farbe, beschr, kid))
            else:
                kid = c.execute(
                    "INSERT INTO kategorien(name, icon, farbe, beschreibung, sortierung) VALUES (?,?,?,?,?)",
                    (name, icon, farbe, beschr, 100),
                ).lastrowid
                vorhandene[name] = kid
            kat_map[schluessel] = kid
            return kid

        neu = doppelt = 0
        gesehen = {}
        jetzt = db.jetzt_iso()
        for z in zeilen:
            basis = f'next:{z["datum"]}:{z["betrag"]:.2f}:{z["notiz"]}'
            gesehen[basis] = gesehen.get(basis, 0) + 1
            ref = f"{basis}:{gesehen[basis]}"
            kid = kat_fuer(z["kat_id"], z["kat_name"])
            cur = c.execute(
                "INSERT OR IGNORE INTO eintraege(datum, betrag, waehrung, kurs, betrag_eur, kategorie_id, bezeichnung, "
                "konto_id, quelle, quelle_ref, pruefen, kategorisiert_durch, erstellt, geaendert) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,0,'next',?,?)",
                (z["datum"], z["betrag"], "EUR", 1.0, z["betrag"], kid, z["notiz"], konto, "next", ref, jetzt, jetzt),
            )
            if cur.rowcount:
                neu += 1
            else:
                doppelt += 1
        # Ordnung: Kategorien nach Haeufigkeit sortieren
        for pos, r in enumerate(c.execute(
            "SELECT kategorie_id, COUNT(*) n FROM eintraege GROUP BY kategorie_id ORDER BY n DESC").fetchall()):
            c.execute("UPDATE kategorien SET sortierung=? WHERE id=?", (pos * 10, r[0]))
        # Ungenutzte Standard-Kategorien entfernen
        standard = [k[0] for k in db.STANDARD_KATEGORIEN]
        c.execute(f"DELETE FROM kategorien WHERE name IN ({','.join('?' * len(standard))}) "
                  "AND id NOT IN (SELECT DISTINCT kategorie_id FROM eintraege WHERE kategorie_id IS NOT NULL) "
                  "AND id NOT IN (SELECT kategorie_id FROM regeln WHERE kategorie_id IS NOT NULL)", standard)
        return neu, doppelt

    neu, doppelt = db.transaktion(ausfuehren)
    letzter = max((z["datum"] for z in zeilen), default=None)
    if bis and tr_folgetag:
        from datetime import date, timedelta
        folgetag = date.fromisoformat(bis[:10]) + timedelta(days=1)
        db.setze_einstellung("tr_import_ab", folgetag.isoformat() + "T00:00:00")
    elif letzter and not db.einstellung("tr_import_ab"):
        # TR erst ab dem Tag nach dem letzten Next-Eintrag importieren -> keine Doppelungen
        db.setze_einstellung("tr_import_ab", letzter[:10] + "T23:59:59")
    return {"gelesen": len(zeilen), "nach_stichtag": in_datei - len(zeilen), "neu": neu, "schon_vorhanden": doppelt, "letzter_eintrag": letzter,
            "tr_import_ab": db.einstellung("tr_import_ab")}


def importiere_datei(pfad):
    with open(pfad, "rb") as f:
        return importiere(f.read())


if __name__ == "__main__":
    import sys
    print(importiere(io.BytesIO(open(sys.argv[1], "rb").read()).getvalue()))
