# Ausgaben

Haushaltsbuch für Home Assistant, als Ersatz für die iOS-App „Next“.

## Erste Schritte

1. Add-on installieren und starten, in der Info-Ansicht **„In Seitenleiste anzeigen“** einschalten.
2. Optional in der **Konfiguration**: Anthropic-API-Key für die KI und einen Zugriffs-Token für die
   iPhone-Web-App eintragen.
3. In der App unter **Mehr → Banken** Trade Republic, DKB, comdirect oder andere Banken verbinden.
4. Optional unter **Mehr → Import** die Daten aus Next übernehmen.

## Optionen

| Option | Bedeutung |
|---|---|
| Anthropic API-Key | Für KI-Kategorisierung und Kassenbon-Scan (console.anthropic.com). Leer = nur Regeln und Verlauf. |
| Claude-Modell | Standard `claude-haiku-5-5`. |
| KI-Kategorisierung | Unbekannte Händler von Claude einordnen lassen. |
| pytr-Verzeichnis | Ordner für die Trade-Republic-Sitzung (Standard `/homeassistant/pytr`). |
| Zugriffs-Token | Für die Web-App über Port 8199. Leer = direkter Zugriff gesperrt. |
| Sensor-Präfix | Basis der Entity-IDs, Standard `allgemein_finanzen_ausgaben`. |

## Banken

Alle Zugangsdaten werden in der App eingerichtet (Mehr → Banken):

- **Trade Republic:** Telefonnummer und PIN, Bestätigung in der TR-App. Abruf im eingestellten Intervall.
- **DKB, comdirect, andere (FinTS):** Anmeldename und PIN. Nötig ist eine kostenlose
  FinTS-Produkt-ID (fints.org → Hersteller → Produktregistrierung). Die DKB verlangt bei jedem
  Abruf eine Freigabe in der App.
- Die PIN kann gespeichert werden. Sie liegt dann unverschlüsselt in der Datenbank und damit auch
  in Backups.
- Überweisungen an eigene Konten erkennt die App an der IBAN und an den Umbuchungsbegriffen
  (Mehr → Banken). Den eigenen Namen dort eintragen, damit Überträge nicht als Ausgabe zählen.

## Kategorien

Reihenfolge: gelernte Zuordnung → eigener Verlauf → Claude. Unsichere Fälle und mehrdeutige Händler
(PayPal ohne Ziel, Amazon …) landen in **Prüfen**. Bestätigte Vorschläge werden zu Regeln, sodass
Claude mit der Zeit seltener gefragt wird. Verbrauch und Kosten der KI stehen unter
Mehr → KI-Kategorisierung.

## Eigenanteil

Zahlst du etwas komplett, bekommst aber Geld von anderen zurück (z. B. ein geteiltes Abo), trägst du
im Eintrag unter **„Mein Anteil“** z. B. `1/6`, `50 %` oder `2,67` ein. Mit dem Häkchen gilt das ab diesem Eintrag
für alle Einträge des Händlers; frühere behalten ihren Anteil, außer du hakst „Auch alle bisherigen“ an. Auswertungen und Sensoren rechnen dann nur mit deinem Anteil.

## Web-App auf dem iPhone

1. In der Konfiguration einen Zugriffs-Token setzen (beliebige lange Zeichenfolge).
2. Unter **Netzwerk** ist Port 8199 vorbelegt (bei Bedarf ändern).
3. `http://<HA-Adresse>:8199` in Safari öffnen, Token eingeben, **Teilen → Zum Home-Bildschirm**.
4. Unterwegs über Tailscale mit freigegebener Subnetz-Route erreichbar.

## Offline-Betrieb (HTTPS)

Offline startet die Web-App nur über HTTPS. Das Add-on bringt dafür ein eigenes Zertifikat mit (Port 8443):

1. In Safari `http://<HA-Adresse>:8199/zertifikat.crt` öffnen und das Profil laden.
2. Einstellungen → Allgemein → VPN & Geräteverwaltung → „Ausgaben Add-on CA“ installieren.
3. Einstellungen → Allgemein → Info → Zertifikatsvertrauenseinstellungen → „Ausgaben Add-on CA“ einschalten.
4. `https://<HA-Adresse>:8443` in Safari öffnen, Token eingeben, zum Home-Bildschirm hinzufügen.

Die Schritte stehen mit den passenden Adressen auch in der App unter Mehr → Offline & HTTPS.

## Sensoren und Ereignis

- `sensor.<präfix>_heute`, `_woche`, `_monat`, `_jahr`, `_zu_prufen`
- Ereignis `ausgaben_neuer_eintrag` bei jedem neuen automatischen Umsatz (mit Betrag, Kategorie,
  Bezeichnung).

Beispiel für eine Kachel, die zur App führt (`<slug>` ist der Teil der Adresse in der Seitenleiste,
z. B. `abcd1234_ausgaben`):

```yaml
type: tile
entity: sensor.allgemein_finanzen_ausgaben_monat
name: Ausgaben Monat
tap_action:
  action: navigate
  navigation_path: /<slug>
```

## Daten

Die Datenbank liegt unter `/data/ausgaben.db` und ist Teil jedes Home-Assistant-Backups.
Exporte landen auf Wunsch unter `/share/ausgaben`.
