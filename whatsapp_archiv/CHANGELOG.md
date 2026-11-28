# Changelog

## 1.0.1

- Behoben: Auf breiten Bildschirmen wurden nur die ersten Bilder eines Chats geladen (z. B. nur der letzte Monat); der Viewer lädt jetzt so lange nach, bis das Fenster gefüllt ist, und beim Scrollen zuverlässig weiter.

## 1.0.0

- Erste Version: Medien-Viewer für das WhatsApp-Archiv in der Seitenleiste von Home Assistant.
- Archiv wird automatisch unter `/media` und `/share` gefunden (NAS als Netzwerkspeicher), Pfad optional einstellbar.
- Fotos, Videos, Sprachnachrichten und Dokumente je Chat, Filter nach Jahr, Vollbild mit Wischen, Chatverlauf im Overlay.
- Direkter Aufruf als iPhone-Web-App über einen Port, nur mit Zugriffs-Token.
