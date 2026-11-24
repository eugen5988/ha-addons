# Changelog

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
