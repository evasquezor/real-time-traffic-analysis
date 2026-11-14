# Real-time traffic analysis

# Data collection - Gökhan

Pipeline zur Analyse von Verspaetungen im Raum Heilbronn. Die Pipeline
erzeugt eine bereinigte Row pro konkreter Fahrt, Haltestelle und
Realtime-Beobachtung. Fahrplan, Echtzeitdaten, Wetter, Baustellen,
Verkehrsmeldungen und Service-Alerts werden dabei in derselben Row
zusammengefuehrt.

## 1. Voraussetzungen

- Python 3.13 oder kompatible Python-Version
- PostgreSQL-Datenbank, zum Beispiel Neon
- Internetzugriff auf die externen Datenquellen

### Virtuelle Umgebung unter Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Datenquellen und Endpoints

### GTFS Static

Der statische Feed kommt von GTFS.de:

```text
https://download.gtfs.de/germany/nv_free/latest.zip
```

Verwendete GTFS-Dateien:

| Datei                | Bedeutung                             |
| -------------------- | ------------------------------------- |
| `stops.txt`          | Haltestellen und Koordinaten          |
| `stop_times.txt`     | geplante Ankunfts- und Abfahrtszeiten |
| `trips.txt`          | konkrete Fahrten                      |
| `routes.txt`         | Linien und Verkehrsmittel             |
| `shapes.txt`         | geografischer Verlauf einer Fahrt     |
| `calendar.txt`       | regulaere Betriebstage                |
| `calendar_dates.txt` | Ausnahmen und Feiertage               |

Die Pipeline laedt den deutschlandweiten Feed, filtert die Haltestellen
ueber die offizielle Heilbronn-Grenze und behaelt danach nur Trips, die
mindestens eine Heilbronner Haltestelle bedienen.

### GTFS-Realtime

```text
https://realtime.gtfs.de/realtime-free.pb
```

Der Feed ist ein Protobuf-Snapshot und enthaelt unter anderem:

- aktuelle oder prognostizierte Ankunfts- und Abfahrtszeiten
- Verspaetungen in Sekunden
- `TripUpdate`-Daten pro Fahrt und Halt
- `Alert`-Entities fuer passende Stoerungsinformationen

Der Feed ist kein historisches Archiv. Fuer historische Trainingsdaten muss
der Snapshot regelmaessig gespeichert werden.

### Heilbronn-Grenze

```text
https://opendata.heilbronn.de/sites/default/files/Heilbronn_Stadtkreisgrenzen_Gen_0.geojson
```

Die Koordinaten der GTFS-Haltestellen werden gegen dieses Polygon geprueft.

### Baustellen

```text
https://api.mobidata-bw.de/datasets/traffic/roadworks/roadworks_geojson.json
```

Die Antwort ist GeoJSON. Die Pipeline filtert nach Zeitraum und ordnet
Baustellen anschliessend einer konkreten Fahrt zu, wenn sie maximal etwa
500 Meter von der Fahrtstrecke entfernt liegen.

### Verkehrsmeldungen

```text
https://api.mobidata-bw.de/datasets/traffic/incidents-bw/TIC3-Meldungen.xml
```

Die TIC3-Antwort ist XML. Meldungen werden anhand ihrer Koordinaten und
ihres bekannten Zeitraums mit der Fahrtstrecke verbunden. Meldungen ohne
Startzeit werden nicht automatisch verworfen, ein fehlendes Ende oder ein
fehlender Beginn wird als unbekannt behandelt.

### Wetter

Historische Daten:

```text
https://archive-api.open-meteo.com/v1/archive
```

Forecast-Fallback fuer aktuelle oder zukuenftige Tage:

```text
https://api.open-meteo.com/v1/forecast
```

Verwendete Variablen:

- `temperature_2m`
- `precipitation`
- `wind_speed_10m`

### TRIAS

TRIAS unter `https://efa-bw.de/trias` ist eine Anfrage-Schnittstelle fuer
konkrete Fahrplanauskuenfte und Verbindungen. Sie wird derzeit nicht fuer
die Massendatenerzeugung verwendet. GTFS Static und GTFS-Realtime sind fuer
die aktuelle Row-basierte Verspaetungsanalyse geeigneter.

## 3. GTFS-Verkehrsmittel (`route_type`)

| Wert | Verkehrsmittel    |
| ---: | ----------------- |
|    0 | Strassenbahn/Tram |
|    1 | U-Bahn/Metro      |
|    2 | Bahn/Zug          |
|    3 | Bus               |
|    4 | Faehre            |
|    5 | Seilbahn          |
|    6 | Gondelbahn        |
|    7 | Standseilbahn     |
|   11 | Oberleitungsbus   |
|   12 | Monorail          |

## 4. Verarbeitung und Row-Modell

Die zentralen Funktionen sind:

1. `fetch_static_heilbronn()`
   - laedt den GTFS-Static-Feed
   - filtert Heilbronner Haltestellen
   - verbindet Stops, Stop-Times, Trips, Routes und Shapes
   - baut ein wiederverwendbares statisches Mapping
2. `fetch_realtime_heilbronn(static_data=...)`
   - laedt den aktuellen GTFS-Realtime-Snapshot
   - verbindet Realtime-Daten mit `trip_id`, `stop_id` und `service_date`
   - verwirft Updates ohne echte Realtime-Zeit oder ohne Verspaetungswert
   - uebernimmt passende Service-Alerts
3. `prepare_data()`
   - laedt Wetter, Baustellen und TIC3-Meldungen
   - ordnet Einfluesse raeumlich und zeitlich der konkreten Fahrt zu
   - gibt die finalen Rows zurueck

Die Grundidentitaet einer Row ist:

```text
service_date + trip_id + stop_id/stop_sequence
```

Eine Fahrt kann mehrere Rows haben, eine pro Realtime-Stop-Update. Die
Fahrtstrecke wird ueber `shape_id` bestimmt. Falls keine verwertbare Shape
vorhanden ist, wird die Haltestellenfolge als Fallback verwendet.

Baustellen und Verkehr werden nicht nur direkt an der Haltestelle gesucht.
Eine Meldung wird der Row zugeordnet, wenn sie nahe an der gesamten
Fahrtstrecke liegt und zum beobachteten Realtime-Zeitpunkt aktiv ist.

## 5. Wichtige Ausgabefelder

### Zielvariable

- `delay_seconds`
- `arrival_delay_seconds`
- `departure_delay_seconds`

`delay_seconds` verwendet bevorzugt die Ankunftsverspaetung und faellt,
wenn diese fehlt, auf die Abfahrtsverspaetung zurueck. Die Rohfelder bleiben
separat erhalten. Fehlende Werte werden nicht kuenstlich erraten.

### Einflussvariablen

- `temperature_2m`
- `precipitation`
- `wind_speed_10m`
- `roadwork_count`
- `nearest_roadwork_distance_km`
- `traffic_event_count`
- `nearest_traffic_distance_km`
- `has_service_alert`
- `alert_count`
- `has_technical_alert`
- `alert_causes`
- `alert_effects`
- `alert_ids`
- `alert_headers`

Ein `NULL` bei einer Distanz bedeutet, dass kein passendes Ereignis
gefunden wurde. Das ist nicht dasselbe wie eine Distanz von `0`.

Service-Alerts werden nur angehaengt, wenn `trip_id`, `route_id` oder
`stop_id` passt und der Alert zum Realtime-Zeitpunkt aktiv ist. Ein Alert
muss nicht technisch sein, auch Unfall, Umleitung, Baustelle, Ausfall oder
andere Stoerungen werden als Einflussfaktor beruecksichtigt.

## 6. Lokaler Test

Nur importieren startet die Pipeline noch nicht:

```python
import prepare_data
```

Die Pipeline wird explizit ausgefuehrt:

```python
rows = prepare_data.prepare_data()
print(len(rows))
print(rows[:1])
```

Als Skript:

```powershell
.\.venv\Scripts\python.exe prepare_data.py
```

Dabei ruft `main()` die Funktion `prepare_data()` auf und schreibt die
Rows in PostgreSQL.

## 7. PostgreSQL und Neon

Die Pipeline verwendet:

```text
pandas -> SQLAlchemy -> psycopg -> PostgreSQL/Neon
```

Die Datei `.env` muss lokal eine `DATABASE_URL` enthalten. Die `.env`-Datei
ist in `.gitignore` eingetragen und darf nicht committed werden.

Beispielstruktur ohne echte Zugangsdaten:

```text
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DATABASE?sslmode=require
```

Benoetigte Pakete:

- `pandas`
- `sqlalchemy`
- `psycopg[binary]`
- `python-dotenv`

Vor dem ersten Lauf kann die Verbindung getestet werden:

```powershell
@'
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path.cwd() / ".env")
engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as connection:
    print(connection.execute(text("SELECT 1")).scalar())

engine.dispose()
'@ | .\.venv\Scripts\python.exe -
```

Der erwartete Wert ist:

```text
1
```

`prepare_data.py` schreibt die Daten in die Tabelle `traffic_data`.
Die Speicherung verwendet einen PostgreSQL-Upsert. Als fachlicher
Schluessel gilt:

```text
service_date + trip_id + stop_sequence + stop_id
```

Ist dieser Schluessel noch nicht vorhanden, wird eine Row eingefuegt. Wenn
er bereits vorhanden ist, wird die Row mit den aktuellen Realtime-, Wetter-,
Baustellen-, Verkehrs- und Alert-Werten aktualisiert. Der `feed_timestamp`
wird dabei ebenfalls aktualisiert; er ist bewusst kein Bestandteil des
fachlichen Schluessels, weil ein neuer Feed-Snapshot keine neue Fahrt
darstellt.

Beim ersten Upsert-Lauf werden bereits vorhandene Mehrfachzeilen pro
fachlichem Schluessel auf die neueste Row reduziert und eine PostgreSQL
Unique-Constraint angelegt. Dadurch koennen spaetere GitHub-Action-Laeufe
dieselbe Row nicht mehrfach einfuegen.

Beispiele fuer SQLTools oder den Neon SQL Editor:

```sql
SELECT COUNT(*) AS row_count
FROM traffic_data;
```

```sql
SELECT
    service_date,
    trip_id,
    stop_id,
    stop_sequence,
    delay_seconds,
    roadwork_count,
    traffic_event_count,
    alert_count,
    has_service_alert
FROM traffic_data
ORDER BY service_date DESC
LIMIT 20;
```

## 8. GitHub Actions

Ein Action-Lauf startet das Skript einmalig:

```text
python prepare_data.py
```

Dafuer muss `DATABASE_URL` als GitHub-Repository-Secret hinterlegt werden.
Sie darf nicht im Workflow-Text stehen.

Beispiel fuer den relevanten Workflow-Teil:

```yaml
name: Collect traffic data

on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:

jobs:
  collect:
    runs-on: ubuntu-latest
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements.txt
      - run: python prepare_data.py
```

GitHub Actions bietet fuer geplante Workflows keine verlässlich garantierte
Ausfuehrung alle zwei Minuten. Das kleinste sinnvolle Cron-Intervall ist
typischerweise fuenf Minuten, und die Ausfuehrung kann bei hoher Auslastung
verzoegert werden. Fuer echte Zwei-Minuten-Abstaende ist ein dauerhafter
Worker, eine VM oder ein anderer Scheduler geeigneter.

Da der Realtime-Feed ein Snapshot ist, sollte fuer historische Analysen der
Zeitpunkt jedes Laufs gespeichert werden. `feed_timestamp` und
`retrieved_at` sind dafuer vorgesehen. Bei regelmaessigen Laeufen darf der
Duplikat-Schluessel den Snapshot-Zeitpunkt nicht ignorieren, sonst werden
unterschiedliche Realtime-Beobachtungen derselben Fahrt zusammengelegt.

## 9. Regressionsmodell

### Einflussfaktoren:

- Temperatur, Regen, Wind
- Baustellen
- Verkehrsbehinderungen/Engpässe (bpsw. überlastete Straßen)
- GTFS-Realtime-Service-Alerts, z. B. technische Probleme, Unfälle,
  Baustellen, Umleitungen oder Ausfälle

**Kontextfrage:**

_War diese konkrete Fahrt zum beobachteten **Zeitpunkt** wahrscheinlich von einer nahegelegenen **Baustelle**, **Wetterbedingungen**, **Störungsmeldung** oder **Verkehrsmeldung** betroffen?_

### Zielvariable:

- Verspätung

### Regressionsmodell

`y = β₀ + β₁X₁ + β₂X₂ + β₃X₃ + β₄X + ε`

(ε ist der Fehlerterm, Teil der Regression die nicht
erklärt werden konnte, z.B.:

Tatsächliche Verspätung ist 6min und Modell ermittelt 4min: ε = y - ŷ = 6 - 4 = 2. Eine Abweichung von 2min somit.)

**Terme**:

- y: tatsächliche Verspätung
- β₀: Grundwert bzw. Achsenabschnitt
- β₁, β₂, β₃, β₄: geschätzte Zusammenhänge der Einflussfaktoren mit der Verspätung
- X₁, X₂, X₃, X₄: Wetter, Baustellen, Verkehrsbehinderungen und Service Alerts
- ε: nicht durch das Modell erklärte Abweichung

Die Pipeline behauptet nicht, dass ein einzelnes Ereignis sicher die
Verspaetung verursacht hat. Sie liefert reproduzierbare Kontextvariablen,
die anschliessend statistisch oder mit einem Machine-Learning-Modell
ausgewertet werden koennen.

## 10. Bekannte Grenzen

- Der GTFS-Realtime-Feed ist kein historisches Archiv.
- Der Static-Feed wird aktuell pro Pipeline-Lauf erneut geladen.
- Verkehrsmeldungen koennen fehlende Start- oder Endzeiten haben.
- Die Ereignisdistanz ist eine Naeherung anhand von Routenpunkten und einem
  Schwellenwert von etwa 500 Metern.
- Ein Realtime-Snapshot enthaelt nur die vom Anbieter aktuell veroeffentlichten
  Fahrten und Updates.
- TRIAS wird derzeit nicht fuer die Row-Erzeugung benoetigt.

# KI - Erik

Verspätungsprognose für den Raum Heilbronn mit Regression (scikit-learn). Der Teil liest die
bereinigten Rows aus `traffic_data`, untersucht Muster und Abhängigkeiten, trainiert und
vergleicht Regressionsmodelle und stellt die Prognosen in der Datenbank für das Dashboard
(Grafana) bereit. Alle Skripte liegen im Ordner `ml/`. Stand aller Zahlen: 14.11.2026.

## 1. Überblick

| Datei | Aufgabe |
| --- | --- |
| `ml/train.py` | Features bauen, Modelle trainieren, vergleichen und bewerten, bestes Modell speichern |
| `ml/analyse.py` | Muster in den Verspätungen untersuchen (Tabellen und Diagramme) |
| `ml/predict.py` | Prognose für eine einzelne Fahrt (Funktion `prognose()`, braucht `train.py` im selben Ordner) |
| `ml/predict_batch.py` | Prognosen für alle Rows berechnen und in die Datenbank schreiben |
| `ml/models/verspaetung_modell.joblib` | trainiertes Modell inkl. Feature-Listen, Version, Trainings- und Testtagen |
| `ml/results/` | Ergebnisse: Metriken, Backtest, Feature-Wichtigkeit, Analyse-Tabellen und -Diagramme |

**Zielvariable:** `delay_seconds` aus `traffic_data`, umgerechnet in Minuten (Ankunfts-, sonst
Abfahrtsverspätung). Das ist die **Prognose des Anbieters** (GTFS-Realtime) und keine
unabhängig gemessene Ist-Zeit. Das Modell lernt also, diese Anbieterwerte zu erklären.

**Fragestellung:** Welche Muster und Einflussfaktoren bestimmen die Verspätung, und wie genau
lässt sie sich vor der Fahrt aus Uhrzeit, Linie, Haltestelle, Wetter und aktueller Lage im Netz
vorhersagen? Es ist eine **Vorab-Prognose**: Die Verspätung der eigenen Fahrt an früheren
Haltestellen wird bewusst nicht benutzt (siehe Abschnitt 7).

## 2. Ausführen

Im Ordner `ml/` mit aktivierter virtueller Umgebung. Die `.env` mit `DATABASE_URL` liegt in
`ml/` und wird nicht committet.

```powershell
pip install -r requirements.txt   # scikit-learn (ab 1.3), joblib, matplotlib, numpy
pip install lightgbm              # optional, wird sonst im Vergleich übersprungen
python train.py                   # Training, Bewertung, Backtest, Modell speichern
python analyse.py                 # Tabellen und Diagramme in results/
python predict_batch.py           # schreibt die Prognose-Tabellen in die Datenbank
python predict.py                 # optional: Beispielprognosen im Terminal
```

`train.py` braucht wegen Parameter-Suche, vielen Modellen und Backtest bis zu etwa einer Stunde.
Für einen schnellen Test: `TUNING = False` und `BACKTEST = False` oben in der Datei, oder
`TUNING_ITERATIONEN` verkleinern. Der Zufall ist über `RANDOM_STATE = 42` festgelegt. Die
Ergebnisse hängen vom Datenstand ab, weil `traffic_data` laufend wächst. `predict_batch.py` nach
jedem neuen Training erneut ausführen, damit die Tabellen zum gespeicherten Modell passen.

## 3. Daten und Bereinigung

- Quelle: Tabelle `traffic_data`. Stand der Analyse: 116 404 Rows, nach der Bereinigung
  116 372. Das Training lief am 14.11. abends mit 115 542 bereinigten Rows.
- Entfernt werden Rows ohne Verspätungswert oder Zeitpunkt und Verspätungen über ±60 Minuten
  (wenige Ausreißer, rund 30 Rows).
- Beim Lernen werden Verspätungen zusätzlich auf ±30 Minuten begrenzt (`Y_BEGRENZEN`), damit
  einzelne Extremwerte das Modell nicht verzerren. Bewertet wird mit den echten Werten.
- Ein leerer Wert bei den Ereignis-Distanzen heißt "kein Ereignis in der Nähe" und wird durch
  10 km ersetzt (nicht durch 0). Dazu gibt es die Flags `has_roadwork` und `has_traffic_event`.
- Alle Zeit-Features (Stunde, Wochentag usw.) werden aus der **Sollzeit** abgeleitet
  (Realtime-Zeit minus Verspätung, Berliner Zeit). Mit der Realtime-Zeit würde die Verspätung
  indirekt in den Features stecken (Data Leakage).
- Die Spalten `arrival_delay_seconds`, `departure_delay_seconds` und `delay_seconds` sowie die
  Realtime-Zeiten sind keine Features. Alle Features werden im Arbeitsspeicher berechnet,
  `traffic_data` bleibt unverändert.

## 4. Features

| Gruppe | Features |
| --- | --- |
| Zeit (aus Sollzeit) | `stunde`, `stunde_sin`, `stunde_cos`, `wochentag`, `ist_wochenende`, `ist_hauptverkehrszeit` (Mo–Fr 7–9 und 16–18 Uhr) |
| Fahrt | `stop_sequence`; Kategorien `route_short_name`, `route_type`, `stop_name` (Kategorien mit weniger als 100 Rows werden gebündelt) |
| Position in der Fahrt | `linie_laenge` (typische Anzahl Halte der Linie), `pos_rel` (`stop_sequence` geteilt durch Linienlänge), `ist_erster_halt`, `ist_letzter_halt` |
| Wetter | `temperature_2m`, `precipitation`, `wind_speed_10m` |
| Baustellen, Verkehr, Alerts | `roadwork_count`, `has_roadwork`, `nearest_roadwork_distance_km`, `traffic_event_count`, `has_traffic_event`, `nearest_traffic_distance_km`, `alert_count` |
| Aktuelle Lage | `lag_linie_min`, `lag_linie_n`, `lag_netz_min`, `lag_netz_n`: mittlere Verspätung **anderer** Fahrten derselben Linie bzw. des ganzen Netzes in der Stunde vor der Beobachtung (10-Minuten-Raster, die eigene Fahrt ist ausgeschlossen) und die Anzahl der Rows dahinter |
| Lage kurz und lokal | `lag_linie_kurz_min`, `lag_linie_kurz_n`, `lag_netz_kurz_min` (letzte ca. 20 Minuten), `lag_halt_min`, `lag_halt_n` (alle Linien an derselben Haltestelle, letzte ca. 30 Minuten), `trend_linie_min`, `trend_netz_min` (kurze Lage minus Lage der Stunde: wird es schlimmer?) |
| Vorgänger-Fahrten | `vorgaenger_min`, `vorgaenger_abstand_min`: Verspätung der vorherigen Fahrt derselben Linie an derselben Haltestelle (höchstens 90 Minuten davor) und der Fahrplan-Abstand dazu; `vorgaenger3_min`, `vorgaenger3_n`: Mittel der letzten drei Vorgänger-Fahrten |
| Takt | `naechster_abstand_min`: Fahrplan-Abstand zur nächsten Fahrt der Linie an derselben Haltestelle |
| Historische Muster | vier Schlüssel `k_linie_halt`, `k_linie_tag_std`, `k_halt_tag_std`, `k_linie_halt_hvz` (Kombinationen aus Linie, Haltestelle, Tagtyp Mo–Fr/Sa/So, Stunde und Hauptverkehrszeit). Das Modell ersetzt jeden Schlüssel durch die mittlere Verspätung dieser Kombination im Training (Target Encoding) |

Das Target Encoding läuft im Training mit 5-facher Kreuz-Ziehung, damit die eigene Verspätung
einer Row nicht in ihr eigenes Feature rutscht. Seltene Kombinationen werden zum Gesamtmittel
gezogen, unbekannte bekommen den Gesamtmittelwert.

Die Lage-, Vorgänger- und Takt-Features nutzen die vom Anbieter gemeldeten Verspätungen
**anderer** Fahrten und Fahrplan-Zeiten. Sie enthalten keine Information der eigenen Fahrt. Das
haben wir mit Tests geprüft: Ändert man die Verspätung einer Row, bleiben ihre eigenen Features
gleich, und mit zufällig gemischtem Ziel ist das Modell nicht besser als die Baseline.
Baustellen- und Verkehrs-Features sind konstant (im Beobachtungszeitraum kein einziger Fall)
und haben deshalb keinen Einfluss.

## 5. Modelle und Bewertung

- **Baselines:** konstante Schätzung mit Mittelwert bzw. Median der Trainingsdaten. Sie lernen
  nichts und sind nur der Vergleichsmaßstab.
- **Einfache Regression:** nur ein Feature, das mit der stärksten Korrelation im Training
  (aktuell `vorgaenger3_min`).
- **Multiple Regression:** Lineare Regression mit allen Features.
- **Baum-Modelle:** Random Forest, Extra Trees, Gradient Boosting mit MAE-Verlust und mit
  RMSE-Verlust, Gradient Boosting mit nativen Kategorien (Linie und Haltestelle direkt als
  Kategorie statt One-Hot), optional LightGBM.
- **Mix:** Mittel aus dem getunten Gradient Boosting (MAE) und dem Random Forest.
- **Tuning:** Die Parameter der Gradient-Boosting-Modelle (außer LightGBM) werden per Zufallssuche
  (12 Kombinationen) mit Zeitreihen-Kreuzvalidierung (`TimeSeriesSplit`) nur auf den
  Trainingstagen gesucht.
- **Split:** zeitlich. Die letzten ca. 20 % der Betriebstage sind der Test (12., 13. und
  14.11.), alle früheren Tage das Training (98 925 Rows, Test 16 617 Rows).
- **Backtest:** Für jeden der letzten 4 Tage (11. bis 14.11.) wird auf allen früheren Tagen
  trainiert und genau dieser Tag getestet. Das ist robuster als ein einzelner Testzeitraum. Er
  läuft für die vier besten Modelle nach Test-MAE sowie die Baselines und die Lineare
  Regression.
- **Kennzahlen:** MAE (mittlerer Fehler in Minuten), RMSE (große Fehler zählen stärker) und R²
  (Anteil der erklärten Schwankung, 0 = nicht besser als der Mittelwert).
- **Modellauswahl:** Gespeichert wird automatisch das Modell mit dem kleinsten mittleren MAE im
  Backtest (Baselines und einfache Regression zählen nicht).

## 6. Ergebnisse (Stand 14.11.2026)

Testset (3 Tage, 16 617 Rows):

| Modell | MAE (min) | RMSE (min) | R² |
| --- | ---: | ---: | ---: |
| Baseline Mittelwert | 2,650 | 5,145 | −0,000 |
| Baseline Median | 2,483 | 5,251 | −0,042 |
| Einfache Regression (`vorgaenger3_min`) | 2,583 | 5,099 | 0,018 |
| Lineare Regression | 2,341 | 4,582 | 0,207 |
| Random Forest | 2,059 | 4,128 | 0,356 |
| Gradient Boosting (RMSE) | 2,113 | 4,067 | 0,375 |
| Gradient Boosting (MAE) | 2,035 | 4,550 | 0,218 |
| Extra Trees | 2,033 | 4,154 | 0,348 |
| Mix: Gradient Boosting (MAE) + Random Forest | 2,019 | 4,288 | 0,305 |
| LightGBM (MAE) | 2,006 | 4,482 | 0,241 |
| **Gradient Boosting nativ (MAE)** | **1,976** | 4,457 | 0,250 |

Backtest, MAE je Tag (Minuten):

| Modell | Mi 11.11. | Do 12.11. | Fr 13.11. | Sa 14.11. | Mittel |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline Median | 2,522 | 2,760 | 2,408 | 1,978 | 2,417 |
| Baseline Mittelwert | 2,769 | 2,846 | 2,617 | 2,291 | 2,631 |
| Lineare Regression | 2,461 | 2,584 | 2,351 | 1,816 | 2,303 |
| Extra Trees | 2,019 | 2,189 | 2,061 | 1,662 | 1,983 |
| Mix | 1,987 | 2,174 | 2,046 | 1,665 | 1,968 |
| LightGBM (MAE) | 1,967 | 2,180 | 2,015 | 1,642 | 1,951 |
| **Gradient Boosting nativ (MAE)** | **1,961** | **2,128** | **1,983** | **1,624** | **1,924** |

Das gespeicherte Modell ist **Gradient Boosting nativ (MAE)** (Version
`Gradient_Boosting_nativ_MAE_20261114_1719`). Es liegt auf dem Testset 20,4 % und im
Backtest-Mittel ebenfalls 20,4 % unter dem MAE der Median-Baseline und ist an jedem der vier
Tage etwa 18 bis 23 % besser. Der mittlere Fehler beträgt **etwa 1,95 Minuten** (Test 1,98,
Backtest 1,92).

**Einordnung**

- Das Modell erkennt Muster (Tageszeit, Linie, Haltestelle, aktuelle Lage), kann die Verspätung
  einer einzelnen Fahrt aber nur grob schätzen. Der Fehler von rund 2 Minuten ist fast so groß
  wie die typische Verspätung (Median 1,6 Minuten).
- Die Unterschiede zwischen den besten Modellen sind klein (zwischen 1,92 und 1,98 im Backtest)
  und liegen innerhalb der Schwankung zwischen einzelnen Tagen. Das spricht dafür, dass die
  Grenze in den Daten liegt und nicht im Algorithmus. Auch zusätzliche Features (Lage an der
  Haltestelle, Takt, Position in der Fahrt) brachten nur noch Bruchteile.
- **MAE und R² laufen auseinander:** Modelle mit RMSE-Verlust oder Random Forest sagen eher den
  Mittelwert voraus (2,7 Minuten), der wegen der Ausreißer über dem typischen Wert (Median
  1,6 Minuten) liegt. Sie haben deshalb das bessere R² (bis 0,38), aber einen schlechteren MAE.
  Das MAE-Modell trifft den typischen Fall besser und erfasst große Ausschläge kaum.
- **Wichtigste Einflüsse** (Random Forest): die typische Verspätung der Linie nach Tagtyp und
  Stunde (`k_linie_tag_std`, 23 %), die der Haltestelle nach Tagtyp und Stunde (10 %),
  Linie-Haltestelle in der Hauptverkehrszeit (5 %), dann Uhrzeit, aktuelle Lage von Netz und
  Linie, Position in der Fahrt und Vorgänger-Fahrten (je 2 bis 4 %). Kein Wetter-Feature gehört
  zu den 15 wichtigsten.
- Die Koeffizienten der linearen Regression werden von einzelnen seltenen Linien und
  Haltestellen dominiert. Für Aussagen zu Linien und Haltestellen eignen sich die Tabellen aus
  `analyse.py`.

**Muster aus der Analyse** (116 372 Rows, `ml/results/analyse_*.csv` und `.png`):

- Verteilung: Mittel 2,7 Minuten, Median 1,6 Minuten. 29,3 % der Rows haben mindestens 3,
  14,6 % mindestens 5 und 4,0 % mindestens 10 Minuten Verspätung, 12,0 % sind zu früh.
- Tageszeit: am höchsten zur Hauptverkehrszeit am Nachmittag (16 Uhr 4,2 Minuten, 17 Uhr
  4,0 Minuten) und morgens (7 und 8 Uhr: 3,1 bis 3,3 Minuten), nachts und spät abends am
  niedrigsten (0,7 bis 1,5 Minuten).
- Wochentag: Montag 3,4 Minuten, Dienstag 3,1, Donnerstag 3,0, Freitag 2,8, Mittwoch 2,6,
  Samstag 2,1 und Sonntag 1,2 Minuten.
- Linien: Einzelne Linien liegen deutlich über dem Durchschnitt (694: 7,2, 683: 5,8, 648HN: 5,2,
  682: 5,1 und 8: 5,1 Minuten), allerdings meist mit wenigen Rows (100 bis 950). Die größte Linie
  61 (22 726 Rows) hat im Mittel 4,4 Minuten.
- Haltestellen: Böckingen (Buchenhof 6,0, Derfflingerstraße 5,7, Schanz Nord 5,4 Minuten) und
  der Busbahnhof am Hauptbahnhof (5,6) liegen vorn.
- Wetter: Starker Regen (über 2 mm) geht mit 4,3 Minuten einher, aber nur 577 Rows (0,5 %). Bei
  leichtem und mittlerem Regen liegen die Werte unter dem Durchschnitt, wahrscheinlich weil
  Regen mit Wochenende oder Nachtzeit zusammenfällt. Die Korrelation zwischen Regen und
  Verspätung ist praktisch null (0,02). Bei Wind (2,6 Minuten bis 10 km/h, 3,2 über 20 km/h)
  und Temperatur (10 bis 20 °C: 3,0, bis 0 °C: 1,6) gibt es leichte Unterschiede, die sich mit
  Tageszeit und Jahreszeit vermischen. Das sind Korrelationen und kein Beleg für Ursachen.
- Baustellen und Verkehrsmeldungen: im Zeitraum keine Fälle, ein Vergleich war nicht möglich.
- Korrelation mit der Verspätung: `vorgaenger3_min` 0,21, `lag_linie_min` 0,20,
  `lag_linie_kurz_min` und `lag_halt_min` je 0,18, `vorgaenger_min` 0,17, Hauptverkehrszeit
  0,15, `stop_sequence` 0,15, Wochenende −0,10. Wetter-Features liegen unter 0,1.

## 7. Verworfene Variante: Fahrtverlauf

Zusätzlich haben wir ein Modell getestet, das die Verspätung der vorherigen Haltestellen
**derselben Fahrt** als Feature bekommt (`FAHRT_FEATURES = True` in `train.py`). Es erreichte
eine MAE von 0,74 Minuten (Test, Stand 13.11.). Wir nutzen es nicht, weil es im Wesentlichen die
aktuelle Verspätung fortschreibt: Die einfache Regel `y = 0,178 + 0,983 · Verspätung am
vorherigen Halt` kommt allein schon auf 0,95 Minuten, und im Random Forest haben die
Fahrtverlauf-Features rund 90 % der Wichtigkeit, Uhrzeit, Linie, Wetter und Lage zählen kaum
noch. Das Modell sagt dann "wie verspätet ist der Bus am nächsten Halt, wenn er am letzten
3 Minuten spät war", erklärt aber keine Muster und Ursachen. Für die Aufgabe nutzen wir deshalb
die Vorab-Prognose ohne Fahrtverlauf.

## 8. Tabellen für das Dashboard (Grafana)

`predict_batch.py` schreibt vier eigene Tabellen in dieselbe Datenbank (`traffic_data` bleibt
unverändert). Sie werden bei jedem Lauf komplett neu geschrieben, damit alle Prognosen vom
aktuellen Modell stammen. Letzter Lauf: 116 506 Rows (davon 98 925 `train`, 17 581 `test`).

**`ml_predictions`** (eine Row pro Fahrt und Halt):

| Spalte | Bedeutung |
| --- | --- |
| `route_id`, `trip_id`, `stop_id` | Linie, Fahrt, Haltestelle (GTFS-IDs wie in `traffic_data`) |
| `service_date` | Betriebstag (Datum) |
| `target_time` | Zeitpunkt, für den die Prognose gilt (Ankunfts-, sonst Abfahrtszeit, UTC) |
| `prediction_created_at` | Zeitpunkt der Berechnung (UTC) |
| `predicted_delay_seconds` | vorhergesagte Verspätung des Modells in Sekunden |
| `reference_delay_seconds` | vom Anbieter gemeldete Verspätung (GTFS-Realtime), keine unabhängig gemessene Ist-Zeit |
| `model_version` | Modellname und Trainingszeitpunkt, z. B. `Gradient_Boosting_nativ_MAE_20261114_1719` |
| `dataset_split` | `train` (Tag im Training), `test` (Testtag), `neu` (Tag nach dem Training) |

Für eine faire Bewertung nur Rows mit `dataset_split <> 'train'` verwenden, weil das Modell die
Trainingstage kennt:

```sql
SELECT dataset_split,
       COUNT(*)                                                         AS anzahl,
       AVG(ABS(predicted_delay_seconds - reference_delay_seconds)) / 60 AS mae_min,
       AVG(predicted_delay_seconds - reference_delay_seconds) / 60      AS bias_min
FROM ml_predictions
GROUP BY dataset_split;
```

Weitere Tabellen:

- `delay_predictions`: dieselben Prognosen, zusätzlich mit `stop_name`, `route_short_name`,
  `delay_min`, `prediction_min` und `error_min` (Minuten statt Sekunden, `service_date` als Text,
  ohne `dataset_split`).
- `model_metrics`: Modellvergleich (`model`, `mae_min`, `rmse_min`, `r2`), 11 Modelle.
- `feature_importance`: Feature-Wichtigkeit des Random Forest (`feature`, `importance`). Sie
  enthält alle Features inklusive der One-Hot-Spalten der Linien und Haltestellen.

Linie und Haltestellenname zu `ml_predictions` dazuholen:

```sql
SELECT m.*, d.route_short_name, d.stop_name
FROM ml_predictions m
JOIN delay_predictions d
  ON d.trip_id = m.trip_id AND d.stop_id = m.stop_id
 AND d.service_date = to_char(m.service_date, 'YYYYMMDD');
```

Die Prognosen sind nur so aktuell wie der letzte Lauf von `predict_batch.py`. Sie bilden eine
Prognose pro Row aus `traffic_data`. Dadurch wächst die Tabelle mit den Daten.

## 9. Einzelne Prognose in Python

```python
from predict import prognose

minuten = prognose(linie="61", stunde=8, wochentag=0, stop_sequence=5,
                   haltestelle="Hauptbahnhof Busbahnhof, Heilbronn",
                   lag_linie_min=3.5)   # Wochentag: 0 = Montag
```

Nicht angegebene Werte (z. B. Wetter oder Lage) füllt die Pipeline mit dem Median aus dem
Training auf. Je mehr Werte angegeben sind, desto genauer die Prognose. Die Lage-Werte lassen
sich aus der Datenbank berechnen (siehe `add_lag_features()` und `add_vorgaenger_features()` in
`train.py`). `predict.py` importiert aus `train.py` und muss im selben Ordner liegen.

## 10. Grenzen

- **Zielvariable:** Das Modell lernt die Prognose des Anbieters, keine gemessene Ist-Zeit.
- **Kurzer Zeitraum:** Rund drei Wochen Daten. Einzelne Tage (Störung, Feiertag) beeinflussen das
  Ergebnis stark, deshalb gibt es den Backtest. Der letzte Tag im Test (14.11.) war beim Training
  noch nicht vollständig.
- **Fehlende Variation:** Baustellen und Verkehrsmeldungen kamen nicht vor, starker Regen nur
  in 0,5 % der Rows. Ob sie die Verspätung beeinflussen, konnte nicht bewertet werden.
  Zusätzliche Datenquellen wurden nicht ergänzt, es werden nur die Daten aus der API genutzt.
- **Geringe Erklärungskraft:** R² zwischen 0,2 und 0,4 je nach Modell. Viele Verspätungen
  entstehen durch Einzelereignisse (Störung, Unfall, Fahrzeugausfall), die in keiner Spalte
  stehen. Mit den vorhandenen Daten ist eine deutlich kleinere MAE als etwa 2 Minuten nicht
  zu erwarten: Alle getesteten Modelle und Feature-Varianten liegen eng beieinander.
- **Seltene Kombinationen** (z. B. selten befahrene Linie spät abends) sind unsicher, weil das
  Modell dafür wenige Beispiele gesehen hat. Die historischen Muster beruhen auf wenigen Wochen.
- **Lage- und Vorgänger-Features** nutzen Anbieterwerte anderer Fahrten. Im Live-Betrieb müssen
  die aktuellen Rows dafür in der Datenbank vorliegen.
- Die Ergebnisse ändern sich mit mehr Daten. Nach längerer Datenerfassung (mehr Wochen, Ferien,
  Regen, Baustellen) sollte neu trainiert werden.

# Visualisierung - Mohamad
