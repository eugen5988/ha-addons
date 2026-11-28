# WhatsApp-Archiv

Zeigt ein vorhandenes WhatsApp-Archiv (Ordner `WhatsApp-Archiv` mit Unterordner `viewer`) in
Home Assistant an. Das Add-on liest nur – es verändert nichts am Archiv.

## Erste Schritte

1. **NAS einbinden:** Einstellungen → System → Speicher → **Netzwerkspeicher hinzufügen**.
   - Name z. B. `nas`, Verwendung **Medien**, Protokoll Samba/CIFS
   - Server (IP des NAS), Freigabe (z. B. die Freigabe, in der das Archiv liegt), Benutzer und Passwort
   - Die Freigabe erscheint danach im Add-on unter `/media/nas/…`.
2. Add-on installieren und starten, in der Info-Ansicht **„In Seitenleiste anzeigen“** einschalten.
3. Das Add-on sucht das Archiv beim Start selbst unter `/media` und `/share`. Wird es nicht gefunden
   oder liegen mehrere Archive dort, in der **Konfiguration** den Pfad eintragen, z. B.
   `/media/nas/Unterordner/WhatsApp-Archiv`.

## Optionen

| Option | Bedeutung |
|---|---|
| Pfad zum Archiv | Ordner `WhatsApp-Archiv` (oder der Ordner darüber). Leer = automatische Suche. |
| Zugriffs-Token | Nur für den direkten Aufruf über den Port. Leer = direkter Zugriff gesperrt. |

## Auf dem iPhone

- **Empfohlen:** über die Home-Assistant-App, Eintrag „WhatsApp-Archiv“ in der Seitenleiste. Funktioniert
  überall, wo die HA-App funktioniert, und ist durch den HA-Login geschützt.
- **Als eigene Web-App (optional):** Zugriffs-Token setzen und im Reiter **Netzwerk** einen Port
  eintragen (z. B. 8188). Dann in Safari einmal `http://<HA-IP>:8188/viewer/index.html?t=<TOKEN>`
  öffnen → Teilen → **Zum Home-Bildschirm**. Unterwegs nur über VPN (z. B. Tailscale), den Port
  **nicht** ins Internet freigeben.

## Bedienung

- Chat antippen → Reiter **Fotos / Videos / Audio / Dokumente**, darunter Filter nach Jahr.
- Bild antippen → Vollbild, wischen zum Blättern, nach unten wischen schließt.
- 💬 oben rechts zeigt den kompletten Chatverlauf.
- Sprachnachrichten (`.opus`) spielt nicht jeder Browser ab; dann über „Öffnen“ herunterladen.

## Archiv aktualisieren

Neue Chats und Medien kommen über die Mac-App „WhatsApp-Archiv“ ins Archiv (neues iPhone-Backup →
„Archiv erstellen“ in denselben Zielordner). Das Add-on zeigt den neuen Stand sofort, ein Neustart ist
nicht nötig.

## Fehlersuche

- **„Archiv-Daten nicht erreichbar“:** NAS offline oder Netzwerkspeicher in HA getrennt
  (Einstellungen → System → Speicher → neu verbinden).
- **„Kein Archiv gefunden“:** Pfad in der Konfiguration prüfen; im Protokoll steht, wo gesucht wurde.
