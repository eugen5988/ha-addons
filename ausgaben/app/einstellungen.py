"""Liest die Add-on-Optionen aus /data/options.json (von Home Assistant geschrieben)."""
import json
import os

OPTIONEN_PFAD = os.environ.get("AUSGABEN_OPTIONEN", "/data/options.json")
STANDARD = {
    "anthropic_api_key": "",
    "claude_modell": "claude-haiku-5-5",
    "ki_aktiv": True,
    "pytr_verzeichnis": "/homeassistant/pytr",
    "zugriffs_token": "",
    "sensor_praefix": "allgemein_finanzen_ausgaben",
}


def optionen():
    daten = dict(STANDARD)
    try:
        with open(OPTIONEN_PFAD, encoding="utf-8") as f:
            daten.update(json.load(f))
    except FileNotFoundError:
        pass
    # Beim Einfuegen rutschen leicht Leerzeichen oder Zeilenumbrueche mit hinein
    for k in ("anthropic_api_key", "claude_modell", "zugriffs_token", "sensor_praefix"):
        if isinstance(daten.get(k), str):
            daten[k] = daten[k].strip()
    return daten
