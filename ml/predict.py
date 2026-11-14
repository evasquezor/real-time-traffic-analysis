"""
predict.py (Version 5) - Verspaetung fuer eine konkrete Fahrt vorhersagen

Laedt das trainierte Modell (verspaetung_modell.joblib) und gibt die erwartete
Verspaetung in Minuten zurueck.

Test im Terminal:
    python predict.py

Im Dashboard / in anderen Skripten:
    from predict import prognose
    minuten = prognose(linie="61", stunde=8, wochentag=0, stop_sequence=5,
                       lag_linie_min=3.5)

Nicht angegebene Werte (z. B. Wetter oder Lage) fuellt die Pipeline selbst mit
dem Median aus dem Training auf. Je mehr Werte angegeben sind, desto genauer.

Die Lage-Werte (lag_*) sind die mittlere Verspaetung ANDERER Fahrten in der
letzten Stunde (gleiche Linie bzw. ganzes Netz) und die Anzahl der Rows dahinter.
Berechnen laesst sich das aus der Datenbank, siehe add_lag_features() in train.py.
"""

import warnings
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from train import hist_kombis  # gleiche Schluessel wie im Training

warnings.filterwarnings("ignore", message="Skipping features without any observed values")

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "verspaetung_modell.joblib"  # ggf. anpassen


@lru_cache(maxsize=None)
def lade_modell(pfad: str = str(MODEL_PATH)) -> dict:
    """Laedt das gespeicherte Paket (Pipeline + Feature-Listen) nur einmal."""
    return joblib.load(pfad)


def _zahl(wert):
    return np.nan if wert is None else float(wert)


def prognose(
    linie,
    stunde,
    wochentag,
    minute=0,
    stop_sequence=None,
    haltestelle=None,
    route_type=None,
    temperature_2m=None,
    precipitation=None,
    wind_speed_10m=None,
    roadwork_count=0,
    traffic_event_count=0,
    nearest_roadwork_distance_km=None,
    nearest_traffic_distance_km=None,
    alert_count=0,
    lag_linie_min=None,
    lag_linie_n=None,
    lag_netz_min=None,
    lag_netz_n=None,
    vorgaenger_min=None,
    vorgaenger_abstand_min=None,
    vorstop_min=None,
    vorstop_abstand_min=None,
    fahrt_start_min=None,
    fahrt_mittel_min=None,
    fahrt_n_vorher=None,
    modell_pfad: str = str(MODEL_PATH),
) -> float:
    """Erwartete Verspaetung in Minuten.

    linie: Liniennummer als Text, z. B. "61" oder "S4"
    stunde: 0-23 (Berliner Zeit), minute: 0-59, wochentag: 0 = Montag ... 6 = Sonntag
    stop_sequence: Position der Haltestelle in der Fahrt (1, 2, 3, ...)
    haltestelle: Name der Haltestelle (stop_name), optional
    route_type: GTFS-Typ, z. B. 0 = Tram, 2 = Zug, 3 = Bus
    lag_linie_min / lag_netz_min: mittlere Verspaetung anderer Fahrten der Linie bzw.
        des Netzes in der letzten Stunde (Minuten); lag_*_n: Anzahl der Rows dahinter
    vorgaenger_min: Verspaetung der vorherigen Fahrt derselben Linie an derselben Haltestelle
    vorgaenger_abstand_min: so viele Minuten lag diese Vorgaenger-Fahrt davor
    vorstop_min: Verspaetung am vorherigen Halt DERSELBEN Fahrt (Minuten), vorstop_abstand_min:
        Fahrplan-Abstand zu diesem Halt; fahrt_start_min / fahrt_mittel_min / fahrt_n_vorher:
        Verspaetung am ersten gemeldeten Halt, Mittel und Anzahl der frueheren Halte der Fahrt
        (nur relevant, wenn das Modell mit FAHRT_FEATURES = True trainiert wurde)
    """
    paket = lade_modell(modell_pfad)
    kein_event = paket["kein_event_distanz_km"]

    dezimal = stunde + minute / 60.0
    werte = {
        "stunde": _zahl(stunde),
        "stunde_sin": float(np.sin(2 * np.pi * dezimal / 24)),
        "stunde_cos": float(np.cos(2 * np.pi * dezimal / 24)),
        "wochentag": _zahl(wochentag),
        "ist_wochenende": float(wochentag >= 5),
        "ist_hauptverkehrszeit": float(
            wochentag < 5 and (7 <= stunde < 9 or 16 <= stunde < 18)
        ),
        "stop_sequence": _zahl(stop_sequence),
        "temperature_2m": _zahl(temperature_2m),
        "precipitation": _zahl(precipitation),
        "wind_speed_10m": _zahl(wind_speed_10m),
        "roadwork_count": _zahl(roadwork_count),
        "has_roadwork": float(roadwork_count > 0),
        "traffic_event_count": _zahl(traffic_event_count),
        "has_traffic_event": float(traffic_event_count > 0),
        # Wie im Training: kein Ereignis in der Naehe -> Ersatzdistanz
        "nearest_roadwork_distance_km": (
            kein_event if nearest_roadwork_distance_km is None and roadwork_count == 0
            else _zahl(nearest_roadwork_distance_km)
        ),
        "nearest_traffic_distance_km": (
            kein_event if nearest_traffic_distance_km is None and traffic_event_count == 0
            else _zahl(nearest_traffic_distance_km)
        ),
        "alert_count": _zahl(alert_count),
        "lag_linie_min": _zahl(lag_linie_min),
        "lag_linie_n": _zahl(lag_linie_n),
        "lag_netz_min": _zahl(lag_netz_min),
        "lag_netz_n": _zahl(lag_netz_n),
        "vorgaenger_min": _zahl(vorgaenger_min),
        "vorgaenger_abstand_min": _zahl(vorgaenger_abstand_min),
        "vorstop_min": _zahl(vorstop_min),
        "vorstop_abstand_min": _zahl(vorstop_abstand_min),
        "fahrt_start_min": _zahl(fahrt_start_min),
        "fahrt_mittel_min": _zahl(fahrt_mittel_min),
        "fahrt_n_vorher": _zahl(fahrt_n_vorher),
        "route_short_name": str(linie),
        "route_type": np.nan if route_type is None else str(route_type),
        "stop_name": np.nan if haltestelle is None else str(haltestelle),
    }

    # Historische Muster: Schluessel aus Linie, Haltestelle, Tagtyp und Stunde (das Modell
    # wandelt sie selbst in die mittlere Verspaetung der Kombination um)
    kombis = hist_kombis(
        pd.Series([str(linie)]), pd.Series(["nan" if haltestelle is None else str(haltestelle)]),
        pd.Series([wochentag]), pd.Series([stunde]), pd.Series([werte["ist_hauptverkehrszeit"]]),
    )
    for name, wert in kombis.items():
        werte[name] = wert.iloc[0]

    spalten = paket["num_features"] + paket["cat_features"]
    fehlend = [c for c in spalten if c not in werte]
    if fehlend:
        warnings.warn(f"Feature(s) ohne Eingabe, werden mit dem Median aufgefuellt: {fehlend}")
    zeile = pd.DataFrame([{c: werte.get(c, np.nan) for c in spalten}])
    return float(paket["model"].predict(zeile)[0])


if __name__ == "__main__":
    paket = lade_modell()
    print(f"Modell: {paket['model_name']}  |  Testmetriken: "
          f"{ {k: round(v, 3) for k, v in paket['metriken'].items()} }\n")

    beispiele = [
        ("Linie 61, Mo 08:00, 5. Halt, Lage ruhig (0,5 min)",
         dict(linie="61", stunde=8, wochentag=0, stop_sequence=5, lag_linie_min=0.5)),
        ("Linie 61, Mo 08:00, 5. Halt, Lage schlecht (6 min)",
         dict(linie="61", stunde=8, wochentag=0, stop_sequence=5, lag_linie_min=6.0)),
        ("Linie 61, Mo 17:00, 15. Halt, ohne Lage-Angabe",
         dict(linie="61", stunde=17, wochentag=0, stop_sequence=15)),
        ("Linie 8, Fr 16:00, 10. Halt, Lage 3 min",
         dict(linie="8", stunde=16, wochentag=4, stop_sequence=10, lag_linie_min=3.0)),
        ("Linie 61, Mo 08:00, 5. Halt, Fahrt war zuvor +1 min spaet",
         dict(linie="61", stunde=8, wochentag=0, stop_sequence=5, vorstop_min=1.0,
              vorstop_abstand_min=2.0, fahrt_start_min=1.0, fahrt_mittel_min=1.0, fahrt_n_vorher=4)),
        ("Linie 61, Mo 08:00, 5. Halt, Fahrt war zuvor +8 min spaet",
         dict(linie="61", stunde=8, wochentag=0, stop_sequence=5, vorstop_min=8.0,
              vorstop_abstand_min=2.0, fahrt_start_min=7.0, fahrt_mittel_min=7.5, fahrt_n_vorher=4)),
    ]
    for text, eingabe in beispiele:
        print(f"{text:<52} -> {prognose(**eingabe):+.1f} min")