import warnings
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

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
        "route_short_name": str(linie),
        "route_type": np.nan if route_type is None else str(route_type),
        "stop_name": np.nan if haltestelle is None else str(haltestelle),
    }

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
    ]
    for text, eingabe in beispiele:
        print(f"{text:<52} -> {prognose(**eingabe):+.1f} min")