import os
import warnings
from pathlib import Path

import joblib
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

import train  # load_data, build_features, MODEL_PATH, METRICS_PATH, BASE_DIR

warnings.filterwarnings("ignore", message="Skipping features without any observed values")

CHUNKSIZE = 2000  # kleine Pakete, damit PostgreSQL nicht zu viele Parameter bekommt


def _split_label(service_date: pd.Series, paket: dict) -> pd.Series:
    """train / test / neu je Betriebstag, anhand der im Modell gespeicherten Tage."""
    if paket.get("split_typ") == "fahrtweise":
        return pd.Series("gemischt", index=service_date.index)
    train_tage = set(paket.get("train_tage", []))
    test_tage = set(paket.get("test_tage", []))
    if not train_tage and not test_tage:
        return pd.Series("unbekannt", index=service_date.index)
    return service_date.astype(str).map(
        lambda t: "train" if t in train_tage else ("test" if t in test_tage else "neu")
    )


def berechne_prognosen(df: pd.DataFrame, paket: dict):
    """df = Ergebnis von build_features, paket = geladene joblib-Datei.
    Gibt (delay_predictions, ml_predictions) zurueck."""
    spalten = paket["num_features"] + paket["cat_features"]
    pred = paket["model"].predict(df[spalten])
    jetzt = pd.Timestamp.now(tz="UTC")

    arrival = pd.to_datetime(df["realtime_arrival_time"], utc=True, errors="coerce")
    departure = pd.to_datetime(df["realtime_departure_time"], utc=True, errors="coerce")
    ts = arrival.fillna(departure)
    service_date = (
        df["service_date"] if "service_date" in df.columns
        else ts.dt.tz_convert("Europe/Berlin").dt.strftime("%Y%m%d")
    )
    version = paket.get("model_version") or paket["model_name"]

    komfort = pd.DataFrame({
        "ts": ts,
        "service_date": service_date,
        "trip_id": df["trip_id"],
        "stop_id": df["stop_id"],
        "stop_name": df["stop_name"],
        "route_short_name": df["route_short_name"],
        "stop_sequence": df["stop_sequence"],
        "delay_min": df["verspaetung_min"],
        "prediction_min": pred,
    })
    komfort["error_min"] = komfort["prediction_min"] - komfort["delay_min"]
    komfort["model_name"] = paket["model_name"]
    komfort["predicted_at"] = jetzt

    ml = pd.DataFrame({
        "route_id": df["route_id"] if "route_id" in df.columns else df["route_short_name"],
        "trip_id": df["trip_id"],
        "stop_id": df["stop_id"],
        "service_date": pd.to_datetime(
            service_date.astype(str), format="%Y%m%d", errors="coerce"
        ).dt.date,
        "target_time": ts,
        "prediction_created_at": jetzt,
        "predicted_delay_seconds": (pred * 60).round(1),
        "reference_delay_seconds": pd.to_numeric(df["delay_seconds"], errors="coerce"),
        "model_version": version,
        "dataset_split": _split_label(service_date, paket),
    })
    return komfort, ml


def schreibe_in_db(engine, komfort: pd.DataFrame, ml: pd.DataFrame, ordner: Path):
    ml.to_sql("ml_predictions", engine, if_exists="replace",
              index=False, chunksize=CHUNKSIZE, method="multi")
    print(f"ml_predictions: {len(ml)} Rows geschrieben "
          f"({ml['dataset_split'].value_counts().to_dict()}).")

    komfort.to_sql("delay_predictions", engine, if_exists="replace",
                   index=False, chunksize=CHUNKSIZE, method="multi")
    print(f"delay_predictions: {len(komfort)} Rows geschrieben.")

    metriken_pfad = ordner / "modell_metriken.csv"
    if metriken_pfad.exists():
        metriken = pd.read_csv(metriken_pfad).rename(columns={
            "Modell": "model", "MAE (min)": "mae_min",
            "RMSE (min)": "rmse_min", "R2": "r2",
        })
        metriken.to_sql("model_metrics", engine, if_exists="replace", index=False)
        print(f"model_metrics: {len(metriken)} Rows geschrieben.")
    else:
        print(f"Hinweis: {metriken_pfad} nicht gefunden, model_metrics uebersprungen.")

    wichtigkeit_pfad = ordner / "feature_wichtigkeit.csv"
    if wichtigkeit_pfad.exists():
        wichtigkeit = pd.read_csv(wichtigkeit_pfad, index_col=0).reset_index()
        wichtigkeit.columns = ["feature", "importance"]
        wichtigkeit.to_sql("feature_importance", engine, if_exists="replace", index=False)
        print(f"feature_importance: {len(wichtigkeit)} Rows geschrieben.")
    else:
        print(f"Hinweis: {wichtigkeit_pfad} nicht gefunden, feature_importance uebersprungen.")


def main():
    paket = joblib.load(train.MODEL_PATH)
    print(f"Modell geladen: {paket['model_name']} ({paket.get('model_version', 'ohne Version')})")

    df = train.build_features(train.load_data())
    komfort, ml = berechne_prognosen(df, paket)

    load_dotenv(dotenv_path=train.BASE_DIR / ".env")
    engine = create_engine(os.environ["DATABASE_URL"])
    schreibe_in_db(engine, komfort, ml, train.METRICS_PATH.parent)
    engine.dispose()
    


if __name__ == "__main__":
    main()