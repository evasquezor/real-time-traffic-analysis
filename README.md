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

Verspätungsprognose für den Raum Heilbronn mit Regression (scikit-learn). Der Teil
liest die bereinigten Rows aus `traffic_data`, untersucht Muster, trainiert und
vergleicht Modelle und stellt die Prognosen in der Datenbank für das Dashboard
(Grafana) bereit. Alle Skripte liegen im Ordner `ml/`.

## 1. Überblick

| Datei | Aufgabe |
| --- | --- |
| `ml/train.py` | Features bauen, Modelle trainieren und bewerten, bestes Modell speichern |
| `ml/analyse.py` | Muster in den Verspätungen untersuchen (Diagramme und Tabellen) |
| `ml/predict.py` | Prognose für eine einzelne Fahrt (Funktion `prognose()`) |
| `ml/predict_batch.py` | Prognosen für alle Rows berechnen und in die Datenbank schreiben |
| `ml/models/verspaetung_modell.joblib` | trainiertes Modell inkl. Feature-Listen, Version und Trainings-/Testtagen |
| `ml/results/` | Ergebnisse: Metriken, Backtest, Feature-Wichtigkeit, Analyse-Diagramme |

**Zielvariable:** `delay_seconds` aus `traffic_data`, umgerechnet in Minuten
(Ankunftsverspätung, sonst Abfahrtsverspätung). Das ist die **Prognose des Anbieters**
(GTFS-Realtime) und keine unabhängig gemessene Ist-Zeit. Das Modell lernt also, diese
Anbieterwerte zu erklären und vorherzusagen.

## 2. Ausführen

Im Ordner `ml/` mit aktivierter virtueller Umgebung. Die `.env` mit `DATABASE_URL`
liegt im Ordner `ml/` und wird nicht committet.

```powershell
pip install -r requirements.txt   # u. a. scikit-learn, joblib, matplotlib
python train.py                   # Training, Bewertung, Backtest, Modell speichern
python analyse.py                 # Diagramme und Tabellen in results/
python predict.py                 # Beispielprognosen im Terminal
python predict_batch.py           # schreibt die Prognose-Tabellen in die Datenbank
```

`train.py` braucht wegen Parameter-Suche und Backtest einige Minuten. Mit `TUNING = False`
und `BACKTEST = False` oben in der Datei läuft ein schneller Test. Der Zufall ist über
`RANDOM_STATE = 42` festgelegt. Die Ergebnisse hängen vom Datenstand ab, weil
`traffic_data` laufend wächst.

## 3. Daten und Bereinigung

- Quelle: Tabelle `traffic_data` (Stand dieser Auswertung: 10.11.2026, 88 252 Rows nach
  der Bereinigung, rund zwei Wochen Betriebstage).
- Rows ohne Verspätungswert oder ohne Zeitpunkt und Verspätungen über ±60 Minuten werden
  entfernt (88 258 → 88 252 Rows im Training, wenige Ausreißer).
- Ein leerer Wert bei den Ereignis-Distanzen heißt "kein Ereignis in der Nähe" und wird durch
  10 km ersetzt (nicht durch 0). Zusätzlich gibt es die Flags `has_roadwork` und
  `has_traffic_event`.
- Alle Zeit-Features (Stunde, Wochentag usw.) werden aus der **Sollzeit** abgeleitet
  (Realtime-Zeit minus Verspätung, Berliner Zeit). Würde man die Realtime-Zeit nehmen, steckte
  die Verspätung indirekt in den Features (Data Leakage).
- Die Spalten `arrival_delay_seconds`, `departure_delay_seconds` und `delay_seconds` sowie die
  Realtime-Zeiten sind keine Features.

## 4. Features

| Gruppe | Features |
| --- | --- |
| Zeit (aus Sollzeit) | `stunde`, `stunde_sin`, `stunde_cos`, `wochentag`, `ist_wochenende`, `ist_hauptverkehrszeit` (Mo–Fr 7–9 und 16–18 Uhr) |
| Fahrt | `stop_sequence`, Kategorien `route_short_name`, `route_type`, `stop_name` (Kategorien mit weniger als 100 Rows werden gebündelt) |
| Wetter | `temperature_2m`, `precipitation`, `wind_speed_10m` |
| Baustellen und Verkehr | `roadwork_count`, `has_roadwork`, `nearest_roadwork_distance_km`, `traffic_event_count`, `has_traffic_event`, `nearest_traffic_distance_km` |
| Alerts | `alert_count` |
| Aktuelle Lage | `lag_linie_min`, `lag_linie_n`, `lag_netz_min`, `lag_netz_n`: mittlere Verspätung **anderer** Fahrten derselben Linie bzw. des ganzen Netzes in der Stunde vor der Beobachtung (10-Minuten-Raster, die eigene Fahrt ist ausgeschlossen) und die Anzahl der Rows dahinter |
| Vorgänger-Fahrt | `vorgaenger_min`, `vorgaenger_abstand_min`: Verspätung der vorherigen Fahrt derselben Linie an derselben Haltestelle (höchstens 90 Minuten davor) und der Fahrplan-Abstand dazu |

Die Lage- und Vorgänger-Features nutzen die vom Anbieter gemeldeten Verspätungen anderer
Fahrten. Sie sind zum Zeitpunkt der Prognose verfügbar und enthalten keine Information der
eigenen Fahrt. In der Auswertung waren Baustellen- und Verkehrs-Features konstant (keine Fälle im
Beobachtungszeitraum) und haben deshalb keinen Einfluss.

## 5. Modelle und Bewertung

- **Baselines:** konstante Schätzung mit Mittelwert bzw. Median der Trainingsdaten.
- **Einfache Regression:** nur ein Feature (das mit der stärksten Korrelation, `lag_linie_min`).
- **Multiple Regression:** Lineare Regression und Ridge mit allen Features.
- **Random Forest:** 150 Bäume, `min_samples_leaf = 20`.
- **Gradient Boosting:** einmal mit MAE-Verlust (`absolute_error`) und einmal mit RMSE-Verlust
  (`squared_error`). Die Parameter (`learning_rate`, `max_leaf_nodes`, `min_samples_leaf`)
  werden per Zeitreihen-Kreuzvalidierung (`TimeSeriesSplit`) nur auf den Trainingstagen gesucht.
- **Split:** zeitlich. Die letzten ca. 20 % der Betriebstage sind der Test (hier die letzten
  3 Tage: 08.11., 09.11., 10.11.), alle früheren Tage sind das Training. Es gibt kein eigenes
  Validierungsset, die Parameter-Suche läuft intern auf dem Training.
- **Backtest:** Für jeden der letzten 4 Tage wird auf allen früheren Tagen trainiert und genau
  dieser Tag getestet. Das ist robuster als nur ein Testzeitraum.
- **Kennzahlen:** MAE (mittlerer Fehler in Minuten), RMSE (große Fehler zählen stärker) und
  R² (Anteil der erklärten Schwankung, 0 = nicht besser als der Mittelwert).
- **Modellauswahl:** Gespeichert wird das Modell mit dem kleinsten mittleren MAE im Backtest.

## 6. Ergebnisse (Stand 10.11.2026)

Testset (3 Tage, 15 128 Rows, Training 73 124 Rows):

| Modell | MAE (min) | RMSE (min) | R² |
| --- | ---: | ---: | ---: |
| Baseline Mittelwert | 2,897 | 5,165 | −0,001 |
| Baseline Median | 2,660 | 5,288 | −0,049 |
| Einfache Regression (`lag_linie_min`) | 2,712 | 4,995 | 0,064 |
| Lineare Regression | 2,663 | 4,753 | 0,153 |
| Random Forest | 2,662 | 4,782 | 0,142 |
| Gradient Boosting (RMSE) | 2,713 | 4,773 | 0,146 |
| **Gradient Boosting (MAE)** | **2,231** | 4,811 | 0,132 |

Backtest, MAE je Tag (Minuten):

| Modell | Sa 07.11. | So 08.11. | Mo 09.11. | Di 10.11. | Mittel |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline Median | 2,144 | 1,713 | 3,394 | 2,362 | 2,403 |
| Lineare Regression | 2,264 | 1,984 | 3,231 | 2,299 | 2,445 |
| Gradient Boosting (RMSE) | 2,661 | 2,423 | 3,119 | 2,033 | 2,559 |
| **Gradient Boosting (MAE)** | **1,882** | **1,411** | **2,884** | **1,898** | **2,019** |

Das gespeicherte Modell ist **Gradient Boosting (MAE)**. Es liegt auf dem Testset 16 % und im
Backtest-Mittel ebenfalls 16 % unter dem MAE der Median-Baseline und ist an jedem der vier
Tage besser (12 bis 20 %). Der mittlere Fehler beträgt etwa 2,0 bis 2,2 Minuten.

**Einordnung:**

- Das Modell erkennt Muster (Hauptverkehrszeit, Linie, Position in der Fahrt, aktuelle Lage), kann
  die Verspätung einer einzelnen Fahrt aber nur grob schätzen. Der Fehler von etwa 2 Minuten ist
  fast so groß wie die typische Verspätung (Median 1,6 Minuten).
- R² liegt bei etwa 0,1 bis 0,15 und schwankt von Tag zu Tag (Backtest-Mittel 0,09 bis 0,11). Die
  Features erklären nur einen kleinen Teil der Schwankung.
- **MAE und R² laufen auseinander:** Modelle mit RMSE-Verlust sagen eher den Mittelwert voraus
  (2,6 Minuten), der wegen der Ausreißer über dem typischen Wert (Median 1,6 Minuten) liegt. Sie
  haben deshalb den besseren R², aber einen schlechteren MAE als die Median-Baseline. Das
  MAE-Modell trifft den typischen Fall besser und erfasst große Ausschläge kaum.
- **Wichtigste Einflüsse** (Random Forest): aktuelle Lage der Linie (`lag_linie_min`), Uhrzeit
  (`stunde_sin`, `stunde_cos`), Lage im Netz, `stop_sequence` und Verspätung der Vorgänger-Fahrt.
  Eine einzelne Haltestelle oder Linie hat dagegen kaum Gewicht. Für Aussagen zu Linien und
  Haltestellen eignen sich die Tabellen aus `analyse.py`, weil seltene Kategorien in den
  Koeffizienten der linearen Regression stark schwanken.

**Muster aus der Analyse** (`ml/results/analyse_*.csv` und `.png`):

- Die Verspätung ist zur Hauptverkehrszeit am höchsten (7–8 Uhr und 16–17 Uhr, im Mittel 3,2 bis
  4,1 Minuten), nachts und spät abends am niedrigsten (etwa 0,6 bis 1,5 Minuten).
- Montag bis Freitag liegt der Mittelwert bei 2,6 bis 3,4 Minuten, samstags bei 2,2, sonntags
  bei 1,2 Minuten.
- Einzelne Buslinien (z. B. 694, 683, 8, 682) und Haltestellen in Böckingen liegen deutlich über
  dem Durchschnitt (5 bis 7 Minuten).
- Die Verteilung hat dicke Ränder: Median 1,6 Minuten, Mittel 2,7 Minuten, 14,5 % der Rows mit
  mindestens 5 Minuten und 4,1 % mit mindestens 10 Minuten Verspätung.
- Regen kam im Zeitraum nur in rund 2 300 Rows vor. Die niedrigeren Werte in den Regenklassen
  hängen vermutlich mit dem Wochenende oder der Nachtzeit zusammen und sind keine Aussage zum
  Einfluss von Regen.

## 7. Tabellen für das Dashboard (Grafana)

`predict_batch.py` schreibt vier eigene Tabellen in dieselbe Datenbank (`traffic_data` bleibt
unverändert). Sie werden bei jedem Lauf komplett neu geschrieben, damit alle Prognosen vom
aktuellen Modell stammen.

**`ml_predictions`** (eine Row pro Fahrt und Halt):

| Spalte | Bedeutung |
| --- | --- |
| `route_id`, `trip_id`, `stop_id` | Linie, Fahrt, Haltestelle (GTFS-IDs wie in `traffic_data`) |
| `service_date` | Betriebstag (Datum) |
| `target_time` | Zeitpunkt, für den die Prognose gilt (Ankunfts-, sonst Abfahrtszeit, UTC) |
| `prediction_created_at` | Zeitpunkt der Berechnung (UTC) |
| `predicted_delay_seconds` | vorhergesagte Verspätung des Modells in Sekunden |
| `reference_delay_seconds` | vom Anbieter gemeldete Verspätung (GTFS-Realtime), keine unabhängig gemessene Ist-Zeit |
| `model_version` | Modellname und Trainingszeitpunkt, z. B. `Gradient_Boosting_MAE_20261110_1054` |
| `dataset_split` | `train` (Tag im Training), `test` (Testtag), `neu` (Tag nach dem Training) |

Für eine faire Bewertung nur Rows mit `dataset_split <> 'train'` verwenden, weil das Modell
die Trainingstage kennt:

```sql
SELECT dataset_split,
       COUNT(*)                                                         AS anzahl,
       AVG(ABS(predicted_delay_seconds - reference_delay_seconds)) / 60 AS mae_min,
       AVG(predicted_delay_seconds - reference_delay_seconds) / 60      AS bias_min
FROM ml_predictions
GROUP BY dataset_split;
```

Weitere Tabellen:

- `delay_predictions`: wie oben, zusätzlich mit `stop_name`, `route_short_name`, `delay_min`,
  `prediction_min` und `error_min` (bequem für Diagramme).
- `model_metrics`: Modellvergleich (`model`, `mae_min`, `rmse_min`, `r2`).
- `feature_importance`: Feature-Wichtigkeit des Random Forest (`feature`, `importance`).

Linie und Haltestellenname zu `ml_predictions` dazuholen:

```sql
SELECT m.*, d.route_short_name, d.stop_name
FROM ml_predictions m
JOIN delay_predictions d
  ON d.trip_id = m.trip_id AND d.stop_id = m.stop_id
 AND d.service_date = to_char(m.service_date, 'YYYYMMDD');
```

Die Prognosen sind nur so aktuell wie der letzte Lauf von `predict_batch.py`.

## 8. Einzelne Prognose in Python

```python
from predict import prognose

minuten = prognose(linie="61", stunde=8, wochentag=0, stop_sequence=5,
                   lag_linie_min=3.5)   # Wochentag: 0 = Montag
```

Nicht angegebene Werte (z. B. Wetter oder Lage) füllt die Pipeline mit dem Median aus dem
Training auf. Je mehr Werte angegeben sind, desto genauer die Prognose. Die Lage- und
Vorgänger-Werte lassen sich aus der Datenbank berechnen (siehe `add_lag_features()` und
`add_vorgaenger_features()` in `train.py`).

## 9. Grenzen

- **Zielvariable:** Das Modell lernt die Prognose des Anbieters, keine gemessene Ist-Zeit.
- **Kurzer Zeitraum:** Rund zwei Wochen Daten, mit wenigen Wochenenden. Der Testzeitraum
  enthält mit dem aktuellen Tag einen unvollständigen Tag. Einzelne Tage (z. B. ein Montag mit
  einer Störung) beeinflussen das Ergebnis stark, deshalb gibt es den Backtest.
- **Fehlende Variation:** Baustellen und Verkehrsmeldungen kamen im Zeitraum nicht vor, Regen
  fast nicht. Ob sie die Verspätung beeinflussen, konnte nicht bewertet werden.
- **Geringe Erklärungskraft:** R² von etwa 0,1. Viele Verspätungen entstehen durch
  Einzelereignisse (Störung, Unfall, Fahrzeugausfall), die in keiner Spalte stehen.
- **Seltene Kombinationen** (z. B. selten befahrene Linie spät abends) sind unsicher, weil das Modell
  dafür wenige Beispiele gesehen hat.
- **Lage- und Vorgänger-Features** nutzen Anbieterwerte anderer Fahrten. Im Live-Betrieb müssen
  die aktuellen Rows dafür in der Datenbank vorliegen.
- Die Ergebnisse ändern sich, sobald mehr Daten vorliegen. Mit längerer Datenerfassung
  (mehr Wochen, Ferien, Regen, Baustellen) sollte neu trainiert werden.

# Visualisierung - Mohamad
