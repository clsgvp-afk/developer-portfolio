# Lagerverwaltung

Windows-Anwendung zur Bestandsführung. Artikel nach Nummer oder Name suchen, Mindestbestand sehen, Wareneingänge und Warenausgänge buchen und die letzten 100 Bewegungen je Artikel nachvollziehen. Portfolio-Projekt von Oleksandr Lezhnov, mit KI-Unterstützung entwickelt.

[Fallstudie im Portfolio](https://clsgvp-afk.github.io/projekte/lagerverwaltung/)

## Lokal starten

Benötigt Windows, .NET 8 SDK und Docker mit Compose im Linux-Container-Modus. Die Anwendung verwendet Beispieldaten und einen nur am Rechner erreichbaren API-Port.

```powershell
docker compose up --build -d
dotnet run --project desktop
```

Für den Entwicklungsbetrieb ohne Docker braucht die API PHP 8.4 mit `pdo_pgsql` und eine lokale PostgreSQL-Datenbank. Führe `db/01-schema.sql` und danach `db/02-example.sql` einmal in einer leeren Datenbank aus, setze `DB_DSN`, `DB_USER` und `DB_PASSWORD` und starte `php -S 127.0.0.1:8077 api/index.php`. Der Client verwendet standardmäßig `http://127.0.0.1:8077/`. Mit `LAGER_API_URL` kann ein anderer Endpunkt eingestellt werden. Das Beispiel ist für eine lokale Entwicklungsumgebung vorgesehen.

## Fall aus der Demo

Artikel **EL-1040** startet mit 5 Stück. Zwei Clients versuchen gleichzeitig, 4 bzw. 3 Stück zu buchen. Die erste Transaktion sperrt den Artikel mit `SELECT … FOR UPDATE`, aktualisiert seinen Bestand und schreibt den Bewegungseintrag. Der zweite Client prüft den neuen Bestand nach Freigabe der Sperre. Er erhält HTTP `409` und den noch verfügbaren Bestand. Je nach Reihenfolge bleibt 1 oder 2 Stück.

Jede Buchung erhält eine UUID. Bei unklarem Ergebnis kann der Client denselben Auftrag aus dem geöffneten Fenster erneut senden. Bereits gespeicherte Buchungen liefern ihren ursprünglichen Eintrag, auch nachdem sich der Bestand erneut geändert hat. Dieselbe UUID mit anderen Daten wird abgewiesen.

## Integrationstest

Auf einer frischen Beispieldatenbank mit 5 Stück auf `EL-1040` ausführen. Der Test bucht dauerhaft eine Bestandsbewegung in dieser Datenbank.

```powershell
python tests/check.py
```

Compose startet mehrere PHP-Worker. Alternativ akzeptiert der Test zwei API-Adressen, wenn auf Windows zwei PHP-Prozesse dieselbe PostgreSQL-Datenbank verwenden.

| Ebene | Umsetzung |
| --- | --- |
| Windows-Client | C# / WPF, Artikelsuche, Buchungsformular, Verlauf und Fehlermeldungen |
| HTTP-API | PHP / PDO, validierte JSON-Eingaben, getrennte Statuscodes für Konflikt und Eingabefehler |
| Datenbank | PostgreSQL, gemeinsame Transaktion für Bestandsänderung und Bewegungsprotokoll |
