"""Export der (gefilterten) Eintraege nach Excel oder CSV."""
import csv
import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from . import auswertung, db

SPALTEN = [
    ("Datum", 12), ("Uhrzeit", 8), ("Bezeichnung", 30), ("Kategorie", 24), ("Betrag", 11),
    ("Währung", 9), ("Kurs (EUR)", 11), ("Betrag EUR", 12), ("Eigenanteil EUR", 14), ("Konto", 18), ("Notiz", 30),
    ("Quelle", 9), ("Händler (Rohdaten)", 30),
]


def _zeilen(p):
    where, args = auswertung.filter_sql(p)
    rows = db.q(auswertung.BASIS_SELECT + f"WHERE {where} ORDER BY e.datum, e.id", args)
    for e in rows:
        dt = datetime.fromisoformat(e["datum"])
        yield [dt.date(), dt.strftime("%H:%M"), e["bezeichnung"], e["kategorie"] or "", e["betrag"], e["waehrung"],
               e["kurs"], e["betrag_eur"],
               round(e["betrag_eur"] * (e["anteil"] if e["anteil"] is not None else 1), 2) if e["betrag_eur"] is not None else None,
               e["konto"] or "", e["notiz"] or "", e["quelle"], e["haendler_roh"] or ""]


def xlsx(p):
    wb = Workbook()
    ws = wb.active
    ws.title = "Ausgaben"
    kopf_fill = PatternFill("solid", fgColor="1E3A5F")
    ws.append([s for s, _ in SPALTEN])
    for i, (_, breite) in enumerate(SPALTEN, 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = kopf_fill
        ws.column_dimensions[get_column_letter(i)].width = breite
    n = 1
    for z in _zeilen(p):
        ws.append(z)
        n += 1
        ws.cell(row=n, column=1).number_format = "DD.MM.YYYY"
        ws.cell(row=n, column=5).number_format = "#,##0.00"
        ws.cell(row=n, column=7).number_format = "0.0000"
        ws.cell(row=n, column=8).number_format = '#,##0.00 "€"'
        ws.cell(row=n, column=9).number_format = '#,##0.00 "€"'
    if n > 1:
        ws.append([])
        ws.append(["Summe", None, None, None, None, None, None, f"=SUBTOTAL(9,H2:H{n})", f"=SUBTOTAL(9,I2:I{n})"])
        ws.cell(row=n + 2, column=1).font = Font(bold=True)
        for sp in (8, 9):
            ws.cell(row=n + 2, column=sp).font = Font(bold=True)
            ws.cell(row=n + 2, column=sp).number_format = '#,##0.00 "€"'
        ws.auto_filter.ref = f"A1:{get_column_letter(len(SPALTEN))}{n}"
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("Nach Kategorie")
    ws2.append(["Kategorie", "Summe EUR", "Anzahl", "Anteil"])
    for i in range(1, 5):
        ws2.cell(row=1, column=i).font = Font(bold=True, color="FFFFFF")
        ws2.cell(row=1, column=i).fill = kopf_fill
    for z in auswertung.nach_kategorien(p)["kategorien"]:
        ws2.append([z["name"], z["summe"], z["anzahl"], (z["anteil"] or 0) / 100])
        ws2.cell(row=ws2.max_row, column=2).number_format = '#,##0.00 "€"'
        ws2.cell(row=ws2.max_row, column=4).number_format = "0.0%"
    ws2.column_dimensions["A"].width = 28
    ws2.column_dimensions["B"].width = 14

    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


def csv_text(p):
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";")
    w.writerow([s for s, _ in SPALTEN])
    for z in _zeilen(p):
        z[0] = z[0].strftime("%d.%m.%Y")
        for i in (4, 6, 7):
            if z[i] is not None:
                z[i] = f"{z[i]:.4f}".rstrip("0").rstrip(".").replace(".", ",") if i == 6 else f"{z[i]:.2f}".replace(".", ",")
        w.writerow(z)
    return "\ufeff" + puffer.getvalue()
