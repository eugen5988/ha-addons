# Hinweise für Änderungen an diesem Repository

Home-Assistant-Add-on-Repository. Add-ons: `ausgaben/` (Haushaltsbuch) und `whatsapp_archiv/` (Medien-Viewer).
Der Nutzer spricht Deutsch; Oberfläche, Texte, Commit-Nachrichten und Changelog auf Deutsch.

## Feste Regeln

- **Jede Änderung bekommt eine neue Version** in `<addon>/config.yaml` und einen Eintrag oben in
  `<addon>/CHANGELOG.md` (nur beim Add-on, das sich ändert). Der Nutzer aktualisiert über den App Store („Aktualisieren“).
- **Slug `ausgaben` nie ändern.** Daran hängen Datenspeicher (`/data`) und Panel-Adresse.
  Der Anzeigename (`name`, `panel_title`, `manifest.json`, `<title>`) darf sich ändern.
- **Cache-Busting:** Bei Änderungen an `app/static/*` die Zahl `?v=N` in `app/static/index.html`
  erhöhen (beide Vorkommen).
- Keine persönlichen Daten in den Code (Namen, Orte, Telefonnummern, IBANs). Solche Dinge sind
  Einstellungen in der App.
- Zugangsdaten (Banken, PINs) liegen in der SQLite-Datenbank, API-Key und Token in den Add-on-Optionen.

## Aufbau `ausgaben/app`

- `main.py` – FastAPI, Endpunkte, Hintergrundschleifen (TR, Banken, Sensoren), Zugriffsschutz
  (Ingress-IP frei, sonst Token)
- `db.py` – SQLite-Schema und Hilfsfunktionen (`q`, `q1`, `x`, `einstellung`, `setze_einstellung`)
- `kategorisierung.py` – Regel → Verlauf → Claude; `frage_claude`
- `beleg.py` – Kassenbon-Scan (Claude Vision)
- `tls.py` – eigene CA und Serverzertifikat (Port 8443), lernt aufgerufene Adressen
- `static/sw.js` – Service Worker (Offline-Start, letzte API-Antworten); Warteschlange für Offline-Einträge in `app.js`
- `anteile.py` – Eigenanteil je Eintrag (`eintraege.anteil`) und Regeln je Händler; Summen über `auswertung.BETRAG`
- `ki_nutzung.py` – Tokenzählung und Kostenrechnung (Listenpreise in `STANDARD_PREISE`)
- `tr_sync.py` – Trade Republic über pytr; Zugangsdaten in der DB, Sitzung im pytr-Verzeichnis
- `fints_sync.py` – DKB, comdirect, andere über python-fints (Jobs mit TAN/App-Freigabe, Nachholen)
- Mehr → Info → „Version & Änderungen“ liest `config.yaml` und `CHANGELOG.md` (im Image per Dockerfile kopiert); Changelog-Punkte als `- ` unter `## Version`.
- `aktualisierung.py` – Updates aus der App über Core (`homeassistant.update_entity` = Store-Reload, `update.install`); der Supervisor verbietet Selbst-Updates direkt
- `next_import.py`, `export.py`, `auswertung.py`, `waehrung.py`, `einstellungen.py`
- `static/` – Web-App ohne Build-Schritt (`index.html`, `app.js`, `app.css`, Symbole)

## Aufbau `whatsapp_archiv/app`

- `server.py` – aiohttp, liefert ein vorhandenes Archiv aus (Mac-App „WhatsApp-Archiv“ erzeugt es aus dem
  iPhone-Backup). Archivsuche unter `/media` und `/share`, Zugriffsschutz wie bei Ausgaben (Ingress-IP frei,
  sonst Token per `?t=` oder Cookie `wa_token`). Range-Anfragen für Videos auf iOS.
- `viewer.html` – Viewer-Oberfläche; ersetzt beim Ausliefern die `viewer/index.html` aus dem Archiv, damit
  Verbesserungen per Add-on-Update ankommen. Daten (`viewer/data/*.js`, `viewer/thumbs/`) kommen aus dem Archiv.
- **Slug `whatsapp_archiv` nie ändern.**

## Tests

```bash
pip install -r ausgaben/requirements.txt -r whatsapp_archiv/requirements.txt pillow
python tests/test_tr.py
python tests/test_beleg.py
python tests/test_ki.py
python tests/test_anteile.py
python tests/test_offline.py
python tests/test_update.py
python tests/test_whatsapp_archiv.py
```

Die Tests laufen ohne Netz: pytr und die Claude-API werden nachgebaut (`tests/fake_pytr.py`).
GitHub Actions führt sie bei jedem Push aus. Änderungen an der Oberfläche zusätzlich im
iPhone-Format (390×844) prüfen.
