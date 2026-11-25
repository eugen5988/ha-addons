# Changelog

## 1.14.0

- Neu unter Mehr → Info: **Version & Änderungen** zeigt die installierte Version und das komplette Änderungsprotokoll.
- Seiten lassen sich nicht mehr seitlich verschieben, z. B. die Bankseite bei langen Fehlermeldungen. Lange Meldungen werden umbrochen.

## 1.13.0

- Fenster (z. B. „Neue Ausgabe“) lassen sich wie unter iOS nach unten wegziehen: Ein langer Zug oder ein schneller Wisch schließt das Fenster, ein kurzer Zug federt zurück. Das gilt nur, wenn das Fenster ganz oben steht; sonst wird normal gescrollt.

## 1.12.2

- Kein Zoomen mehr auf dem iPhone: Zwei-Finger-Zoom und Doppeltippen vergrößern die App nicht mehr, auch nicht beim Antippen von Eingabefeldern.

## 1.12.1

- Ziehen zum Aktualisieren: Ist die Trade-Republic-Sitzung abgelaufen, startet die Anmeldung gleich mit (gespeicherte PIN, Bestätigung in der Trade-Republic-App). Danach werden die Umsätze abgerufen und die Anzahl neuer Einträge gemeldet.

## 1.12.0

- Neue Kategorie direkt beim Erfassen anlegen: Kachel „＋ Neue Kategorie“ am Ende des Kategorienrasters (Symbol und Name), die neue Kategorie ist danach gleich ausgewählt. Farbe und KI-Beschreibung lassen sich später unter Mehr → Kategorien ergänzen.

## 1.11.1

- Ziehen zum Aktualisieren löst erst bei deutlich längerem Zug aus (Schwelle rund 60 % länger).

## 1.11.0

- Ziehen zum Aktualisieren: In Übersicht, Einträge, Verlauf und Prüfen oben nach unten ziehen und loslassen. Trade Republic wird sofort abgerufen, Banken mit gespeicherter PIN im Hintergrund; das Ergebnis erscheint als Meldung, auch wenn eine Freigabe in der Banking-App nötig ist.
- Fühlbares Feedback (Haptik) beim Erreichen der Auslöseschwelle, auf dem iPhone ab iOS 18.

## 1.10.0

- Offline erfasste Einträge und Kassenbon-Fotos, die noch auf Übertragung warten, stehen jetzt oben in „Einträge“ und in der Übersicht (Abschnitt „Noch nicht übertragen“, Marke „wartet“).
- Wartende Einträge lassen sich per Wischen nach links verwerfen.

## 1.9.1

- Wischen zum Löschen: Der rote Knopf füllt nur noch die freigelegte Fläche und scheint nicht mehr unter der Zeile durch. „Löschen“ steht mittig im Knopf.

## 1.9.0

- Einträge per Wischen nach links löschen, wie in iOS: kurz wischen zeigt „Löschen“, ganz durchwischen
  löscht sofort.

## 1.8.2

- Offline: „Sichern“ hängt nicht mehr, wenn das iPhone keine Verbindung hat, aber ein VPN wie Tailscale
  eingeschaltet ist. Nach wenigen Sekunden wird der Eintrag auf dem Gerät gespeichert.
- Während des Sicherns zeigt der Knopf „…“ und lässt sich nicht doppelt auslösen.

## 1.8.1

- Banken: Bei einem Fehler zeigt die App die Meldungen der Bank mit Code und Erklärung
  (z. B. „9078 Produkt nicht registriert“) statt eines Folgefehlers.

## 1.8.0

- Offline-Betrieb der iPhone-Web-App: Sie startet ohne Netz mit dem letzten Stand. Neue Ausgaben und
  Kassenbon-Fotos werden auf dem Gerät gespeichert und automatisch übertragen, sobald wieder Verbindung
  besteht (Kassenbons landen dann unter „Prüfen“).
- HTTPS mit eigenem Zertifikat auf Port 8443 (Voraussetzung für den Offline-Betrieb). Einrichtung unter
  Mehr → Offline & HTTPS; das Zertifikat erneuert sich automatisch.
- Neue Einträge werden auch bei doppeltem Senden nur einmal angelegt.

## 1.7.3

- Banken: Schalter „Fehlersuche“ schreibt die Kommunikation mit der Bank ins Add-on-Protokoll (PIN ausgeblendet).

## 1.7.2

- Banken: Lehnt der FinTS-Server eine Anfrage ab, zeigt die App jetzt die Antwort der Bank statt nur „Bad status code 400“.
- FinTS-Produkt-ID wird ohne versehentlich mitkopierte Leerzeichen oder Zeilenumbrüche verwendet.

## 1.7.1

- Eigenanteil: Eine neue oder geänderte Regel gilt ab dem Datum des Eintrags; frühere Einträge behalten
  ihren Anteil (z. B. wenn jemand beim Abo aus- oder einsteigt). Alle bisherigen umstellen geht über
  ein zusätzliches Häkchen.

## 1.7.0

- Eigenanteil: Bei geteilten Ausgaben (z. B. Abo mit mehreren Teilnehmern) zählt nur dein Anteil.
  Im Eintrag unter „Mein Anteil“ als Bruch (1/6), Prozent (50 %) oder fester Betrag (2,67) angeben,
  auf Wunsch für alle Einträge des Händlers, auch künftige.
- Liste zeigt „2,67 € von 15,99 €“; Summen, Verlauf, Kategorien und Sensoren rechnen mit dem Eigenanteil.
- Übersicht und Entfernen unter Mehr → Eigenanteile; Excel-Export mit Spalte „Eigenanteil EUR“.

## 1.6.0

- Startseite wählbar: Übersicht, Einträge oder Verlauf (Mehr → Darstellung → Startseite).

## 1.5.2

- Nach einem erfolgreichen KI-Test verschwindet die alte Fehlermeldung sofort von der KI-Seite.

## 1.5.1

- API-Key und Token werden ohne versehentlich eingefügte Leerzeichen oder Zeilenumbrüche verwendet.
- Verständliche Fehlermeldungen der KI (z. B. „API-Key ungültig oder abgelaufen“ statt „401 Unauthorized“).

## 1.5.0

- Umzug in das GitHub-Repository `eugen5988/ha-addons`: Updates kommen jetzt direkt über den App Store.
- Wohnort für die KI ist eine Einstellung in der App (Mehr → KI-Kategorisierung) statt fest im Code.
- Keine persönlichen Vorgaben mehr bei den Umbuchungsbegriffen; eigene Namen trägst du unter Mehr → Banken ein.
- Direkter Port für die Web-App ist standardmäßig 8199.

## 1.4.0

- KI-Verbrauch: Anfragen, Tokens und Kosten je Tag, Monat und insgesamt, aufgeteilt nach
  Kategorisierung, Kassenbons und Tests; Preise je Modell anpassbar.
- Monatskosten der KI im Mehr-Menü.

## 1.3.0

- Kassenbon per Kamera einlesen: Händler, Datum, Summe, Zahlungsart und Positionen werden erkannt
  und ins Formular übernommen.
- Hinweis auf bereits erfasste Zahlungen mit gleichem Betrag.
- Trade-Republic-Umsätze, die schon von Hand erfasst wurden, landen als mögliches Duplikat in „Prüfen“.

## 1.2.0

- Alle Banken (Trade Republic, DKB, comdirect, andere) werden einheitlich in der App unter
  Mehr → Banken eingerichtet, inklusive Zugangsdaten, Startdatum und Abrufintervall.
- Trade-Republic-Optionen aus der Add-on-Konfiguration entfernt.

## 1.1.0

- Trade-Republic-Zugangsdaten über die Add-on-Optionen hinterlegbar.

## 1.0.0

- Erste Version: automatische Erfassung (Trade Republic, DKB, comdirect), KI-Kategorisierung,
  Import der Next-Daten, Bargeld und manuelle Einträge, Fremdwährungen, Übersichten und Verlauf,
  Suche und Filter, Export nach Excel, wiederkehrende Ausgaben, Doppelte-Einträge-Suche,
  Farbdesigns, Sensoren für das Dashboard.
