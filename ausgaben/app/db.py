"""SQLite-Datenbank des Ausgaben-Add-ons (liegt in /data, ist damit Teil der HA-Backups)."""
import os
import sqlite3
import threading
from datetime import datetime

DB_PFAD = os.environ.get("AUSGABEN_DB", "/data/ausgaben.db")
_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS kategorien (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    icon TEXT DEFAULT '🏷️',
    farbe TEXT DEFAULT '#6B7A8F',
    beschreibung TEXT DEFAULT '',
    sortierung INTEGER DEFAULT 100,
    archiviert INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS konten (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    typ TEXT DEFAULT 'manuell',
    icon TEXT DEFAULT '💳',
    archiviert INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS eintraege (
    id INTEGER PRIMARY KEY,
    datum TEXT NOT NULL,
    betrag REAL NOT NULL,
    waehrung TEXT NOT NULL DEFAULT 'EUR',
    kurs REAL,
    betrag_eur REAL,
    kategorie_id INTEGER REFERENCES kategorien(id),
    bezeichnung TEXT NOT NULL DEFAULT '',
    notiz TEXT DEFAULT '',
    konto_id INTEGER REFERENCES konten(id),
    quelle TEXT DEFAULT 'manuell',
    quelle_ref TEXT UNIQUE,
    quelle_status TEXT,
    haendler_roh TEXT,
    pruefen INTEGER DEFAULT 0,
    ki_sicherheit REAL,
    kategorisiert_durch TEXT,
    erstellt TEXT,
    geaendert TEXT
);
CREATE INDEX IF NOT EXISTS ix_eintraege_datum ON eintraege(datum);
CREATE INDEX IF NOT EXISTS ix_eintraege_kat ON eintraege(kategorie_id);
CREATE INDEX IF NOT EXISTS ix_eintraege_pruefen ON eintraege(pruefen);
CREATE TABLE IF NOT EXISTS anteil_regeln (
    id INTEGER PRIMARY KEY,
    muster TEXT NOT NULL UNIQUE,
    name TEXT,
    art TEXT NOT NULL,
    wert REAL NOT NULL,
    geaendert TEXT
);
CREATE TABLE IF NOT EXISTS regeln (
    id INTEGER PRIMARY KEY,
    muster TEXT NOT NULL UNIQUE,
    kategorie_id INTEGER REFERENCES kategorien(id),
    bezeichnung TEXT,
    mehrdeutig INTEGER DEFAULT 0,
    treffer INTEGER DEFAULT 0,
    quelle TEXT DEFAULT 'nutzer',
    geaendert TEXT
);
CREATE TABLE IF NOT EXISTS kurse (
    datum TEXT NOT NULL,
    waehrung TEXT NOT NULL,
    kurs REAL NOT NULL,
    PRIMARY KEY (datum, waehrung)
);
CREATE TABLE IF NOT EXISTS einstellungen (
    schluessel TEXT PRIMARY KEY,
    wert TEXT
);
"""

STANDARD_KONTEN = [
    ("Trade Republic", "auto_tr", "🟦"),
    ("Bargeld", "manuell", "💶"),
    ("Kreditkarte", "manuell", "💳"),
    ("Sonstiges Konto", "manuell", "🏦"),
]

# Fallback, falls noch kein Next-Import gelaufen ist
STANDARD_KATEGORIEN = [
    ("Lebensmittel & Drogerie", "🛒", "#3C8D5A"),
    ("Essen & Snacks", "🥨", "#D08A2E"),
    ("Wohnen & Nebenkosten", "🏠", "#4C6EAD"),
    ("Kleidung", "👕", "#B5527D"),
    ("Auto", "🚗", "#5B6770"),
    ("Tanken & Laden", "⚡", "#2E9AA6"),
    ("Abos & Streaming", "📺", "#7A5BC4"),
    ("Freizeit & Sport", "🏸", "#5E9E2F"),
    ("Urlaub", "🏖️", "#E0A21B"),
    ("Geschenke & Spenden", "🎁", "#C8463D"),
    ("Sonstiges", "📦", "#8A8F98"),
]


def jetzt_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def verbindung():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PFAD) or ".", exist_ok=True)
        _conn = sqlite3.connect(DB_PFAD, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _spalten_ergaenzen(_conn)
        _initialbefuellung(_conn)
    return _conn


def _spalten_ergaenzen(c):
    """Spalten, die nach der ersten Version dazugekommen sind."""
    vorhanden = {r[1] for r in c.execute("PRAGMA table_info(eintraege)")}
    if "anteil" not in vorhanden:
        c.execute("ALTER TABLE eintraege ADD COLUMN anteil REAL")


def _initialbefuellung(c):
    if c.execute("SELECT COUNT(*) FROM konten").fetchone()[0] == 0:
        c.executemany("INSERT INTO konten(name, typ, icon) VALUES (?,?,?)", STANDARD_KONTEN)
    if c.execute("SELECT COUNT(*) FROM kategorien").fetchone()[0] == 0:
        c.executemany(
            "INSERT INTO kategorien(name, icon, farbe, sortierung) VALUES (?,?,?,?)",
            [(n, i, f, k * 10) for k, (n, i, f) in enumerate(STANDARD_KATEGORIEN)],
        )


def q(sql, params=()):
    with _lock:
        return [dict(r) for r in verbindung().execute(sql, params).fetchall()]


def q1(sql, params=()):
    with _lock:
        r = verbindung().execute(sql, params).fetchone()
        return dict(r) if r else None


def x(sql, params=()):
    with _lock:
        cur = verbindung().execute(sql, params)
        return cur.lastrowid, cur.rowcount


def transaktion(func):
    """Fuehrt func(conn) atomar aus."""
    with _lock:
        c = verbindung()
        c.execute("BEGIN")
        try:
            erg = func(c)
            c.execute("COMMIT")
            return erg
        except Exception:
            c.execute("ROLLBACK")
            raise


def einstellung(schluessel, standard=None):
    r = q1("SELECT wert FROM einstellungen WHERE schluessel=?", (schluessel,))
    return r["wert"] if r else standard


def setze_einstellung(schluessel, wert):
    x(
        "INSERT INTO einstellungen(schluessel, wert) VALUES (?,?) "
        "ON CONFLICT(schluessel) DO UPDATE SET wert=excluded.wert",
        (schluessel, None if wert is None else str(wert)),
    )


def konto_id(typ_oder_name):
    r = q1("SELECT id FROM konten WHERE typ=? OR name=? ORDER BY id LIMIT 1", (typ_oder_name, typ_oder_name))
    return r["id"] if r else None
