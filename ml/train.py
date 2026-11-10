import os
import re
import warnings
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GridSearchCV, GroupShuffleSplit, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore", message="Skipping features without any observed values")


# Einstellungen
BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"   
MODELS_DIR = BASE_DIR / "models"
MODEL_PATH = MODELS_DIR / "verspaetung_modell.joblib"
METRICS_PATH = RESULTS_DIR / "modell_metriken.csv"
PLOT_PATH = RESULTS_DIR / "vorhersage_vs_ist.png"

RANDOM_STATE = 42
TEST_ANTEIL = 0.2             # ca. 20 % der Tage bzw. Fahrten als Test
MAX_ABS_VERSPAETUNG_MIN = 60  # Extremwerte darueber werden entfernt
KEIN_EVENT_DISTANZ_KM = 10.0  # Ersatzwert, wenn kein Ereignis in der Naehe
LAG_BUCKET_MIN = 10           # Zeitraster fuer die Lage-Features
LAG_BUCKETS = 6               # 6 x 10 min = die letzte Stunde vor der Beobachtung
TUNING = True                 # False = schneller, ohne Parameter-Suche
VORGAENGER_MAX_MIN = 90       # Vorgaenger-Fahrt zaehlt nur, wenn sie hoechstens so weit zurueckliegt
BACKTEST = True               # Bewertung ueber mehrere einzelne Tage (dauert etwas laenger)
BACKTEST_TAGE = 4             # so viele der letzten Tage werden einzeln nachgerechnet
BACKTEST_MODELLE = [
    "Baseline: Median", "Baseline: Mittelwert", "Lineare Regression",
    "Gradient Boosting (MAE)", "Gradient Boosting (RMSE)",
]
WOCHENTAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

NUM_FEATURES = [
    "stunde",
    "stunde_sin",
    "stunde_cos",
    "wochentag",
    "ist_wochenende",
    "ist_hauptverkehrszeit",
    "stop_sequence",
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "roadwork_count",
    "has_roadwork",
    "nearest_roadwork_distance_km",
    "traffic_event_count",
    "has_traffic_event",
    "nearest_traffic_distance_km",
    "alert_count",
    "lag_linie_min",
    "lag_linie_n",
    "lag_netz_min",
    "lag_netz_n",
    "vorgaenger_min",
    "vorgaenger_abstand_min",
]
CAT_FEATURES = ["route_short_name", "route_type", "stop_name"]



# 1. Daten laden
def load_data() -> pd.DataFrame:
    load_dotenv(dotenv_path=BASE_DIR / ".env")
    engine = create_engine(os.environ["DATABASE_URL"])
    df = pd.read_sql("SELECT * FROM traffic_data", engine)
    engine.dispose()
    print(f"{len(df)} Rows aus traffic_data geladen.")
    return df



# 2. Features bauen
def _vorlauf(df: pd.DataFrame, keys: list):
    """Summe und Anzahl der Verspaetungen in den LAG_BUCKETS Zeitfenstern VOR dem
    eigenen Zeitfenster, gruppiert nach `keys` (leere Liste = ganzes Netz)."""
    agg = (
        df.groupby(keys + ["bucket"])["verspaetung_min"]
        .agg(s="sum", n="count")
        .reset_index()
    )
    basis = df[keys + ["bucket"]]
    summe = np.zeros(len(df))
    anzahl = np.zeros(len(df))
    for k in range(1, LAG_BUCKETS + 1):
        tmp = agg.copy()
        tmp["bucket"] = tmp["bucket"] + k  # Fenster k Schritte vorher -> zur Row "verschoben"
        m = basis.merge(tmp, on=keys + ["bucket"], how="left")
        summe += m["s"].fillna(0).to_numpy()
        anzahl += m["n"].fillna(0).to_numpy()
    return summe, anzahl


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Aktuelle Lage: mittlere Verspaetung ANDERER Fahrten (gleiche Linie bzw. ganzes
    Netz) in der Stunde vor der Beobachtung. Die eigene Fahrt wird ausgeschlossen,
    es werden nur frueher liegende Zeitfenster benutzt."""
    df = df.copy()
    df["bucket"] = (
        (df["ts_soll"] - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(minutes=LAG_BUCKET_MIN)
    ).astype("int64")

    linie = "route_short_name"
    s_l, n_l = _vorlauf(df, [linie])
    s_lt, n_lt = _vorlauf(df, [linie, "trip_id"])
    s_n, n_n = _vorlauf(df, [])
    s_nt, n_nt = _vorlauf(df, ["trip_id"])

    def mittel(summe, anzahl):
        return np.where(anzahl > 0, summe / np.where(anzahl > 0, anzahl, 1), np.nan)

    df["lag_linie_n"] = n_l - n_lt
    df["lag_linie_min"] = mittel(s_l - s_lt, n_l - n_lt)
    df["lag_netz_n"] = n_n - n_nt
    df["lag_netz_min"] = mittel(s_n - s_nt, n_n - n_nt)
    return df


def add_vorgaenger_features(df: pd.DataFrame) -> pd.DataFrame:
    """Verspaetung der Vorgaenger-Fahrt: die letzte frueher liegende Fahrt derselben
    Linie an DERSELBEN Haltestelle (hoechstens VORGAENGER_MAX_MIN Minuten davor).
    Verspaetungen halten sich oft von Fahrt zu Fahrt.
    Reihenfolge und Abstand beruhen auf der SOLLzeit (Fahrplan-Takt). Mit der Realtime-
    Zeit waere der Abstand ein Leck, weil er die aktuelle Verspaetung enthaelt."""
    df = df.copy()
    sortiert = df.sort_values(["route_short_name", "stop_id", "ts_soll"])
    gruppe = sortiert.groupby(["route_short_name", "stop_id"], sort=False)
    prev_delay = gruppe["verspaetung_min"].shift(1)
    prev_ts = gruppe["ts_soll"].shift(1)
    prev_trip = gruppe["trip_id"].shift(1)
    abstand = (sortiert["ts_soll"] - prev_ts).dt.total_seconds() / 60.0
    gueltig = (abstand <= VORGAENGER_MAX_MIN) & (prev_trip != sortiert["trip_id"])
    df["vorgaenger_min"] = prev_delay.where(gueltig).reindex(df.index)
    df["vorgaenger_abstand_min"] = abstand.where(gueltig).reindex(df.index)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Zielvariable in Minuten
    df["verspaetung_min"] = pd.to_numeric(df["delay_seconds"], errors="coerce") / 60.0

    # Beobachtungszeitpunkt (UTC -> Europe/Berlin)
    arrival = pd.to_datetime(df["realtime_arrival_time"], utc=True, errors="coerce")
    departure = pd.to_datetime(df["realtime_departure_time"], utc=True, errors="coerce")
    df["ts"] = arrival.fillna(departure)  # Realtime-Zeitpunkt (enthaelt die Verspaetung)
    # Sollzeit = Realtime minus Verspaetung. Alle Zeit-Features nutzen die Sollzeit, damit
    # die Verspaetung (das Ziel) nicht indirekt ueber die Uhrzeit in die Features rutscht.
    df["ts_soll"] = df["ts"] - pd.to_timedelta(df["verspaetung_min"], unit="min")
    zeit = df["ts_soll"].dt.tz_convert("Europe/Berlin")

    df["stunde"] = zeit.dt.hour
    df["wochentag"] = zeit.dt.day_of_week
    df["ist_wochenende"] = (df["wochentag"] >= 5).astype(float)
    dezimal = zeit.dt.hour + zeit.dt.minute / 60.0
    df["stunde_sin"] = np.sin(2 * np.pi * dezimal / 24)
    df["stunde_cos"] = np.cos(2 * np.pi * dezimal / 24)
    df["ist_hauptverkehrszeit"] = (
        (df["wochentag"] < 5)
        & (((df["stunde"] >= 7) & (df["stunde"] < 9)) | ((df["stunde"] >= 16) & (df["stunde"] < 18)))
    ).astype(float)
    if "service_date" not in df.columns:
        df["service_date"] = zeit.dt.strftime("%Y%m%d")

    # Ereignis-Flags + Distanz: NULL = kein Ereignis (nicht 0!)
    df["has_roadwork"] = (df["roadwork_count"].fillna(0) > 0).astype(float)
    df["has_traffic_event"] = (df["traffic_event_count"].fillna(0) > 0).astype(float)
    df["nearest_roadwork_distance_km"] = df["nearest_roadwork_distance_km"].fillna(KEIN_EVENT_DISTANZ_KM)
    df["nearest_traffic_distance_km"] = df["nearest_traffic_distance_km"].fillna(KEIN_EVENT_DISTANZ_KM)

    # Kategorische Spalten als Text
    for col in CAT_FEATURES:
        df[col] = df[col].astype(str)

    # Zeilen ohne Ziel/Zeitpunkt oder mit Extremwerten entfernen
    df = df.dropna(subset=["verspaetung_min", "ts", "ts_soll"])
    df = df[df["verspaetung_min"].abs() <= MAX_ABS_VERSPAETUNG_MIN].reset_index(drop=True)

    # Lage-Features (brauchen die bereinigten Rows)
    df = add_lag_features(df)
    df = add_vorgaenger_features(df)

    # Numerische Spalten sicher in Zahlen umwandeln
    for col in NUM_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    print(f"{len(df)} Rows nach Bereinigung (Ziel vorhanden, |Verspaetung| <= "
          f"{MAX_ABS_VERSPAETUNG_MIN} min).")
    return df



# 3. Train/Test-Split (ohne Leakage)
def split_data(df: pd.DataFrame):
    """Zeitlich nach service_date, wenn genug Tage da sind,
    sonst fahrtweise (alle Rows einer trip_id bleiben zusammen)."""
    tage = sorted(df["service_date"].dropna().unique())

    if len(tage) >= 5:
        n_test = max(1, int(round(len(tage) * TEST_ANTEIL)))
        test_tage = set(tage[-n_test:])
        test_mask = df["service_date"].isin(test_tage)
        train, test = df[~test_mask], df[test_mask]
        print(f"Zeitlicher Split: Test = letzte {n_test} Tage ({sorted(test_tage)}).")
    else:
        gruppen = df["service_date"].astype(str) + "_" + df["trip_id"].astype(str)
        splitter = GroupShuffleSplit(n_splits=1, test_size=TEST_ANTEIL, random_state=RANDOM_STATE)
        train_idx, test_idx = next(splitter.split(df, groups=gruppen))
        train, test = df.iloc[train_idx], df.iloc[test_idx]
        print("Wenige Tage vorhanden -> fahrtweiser Split (GroupShuffleSplit).")

    print(f"Train: {len(train)} Rows, Test: {len(test)} Rows.")
    return train, test



# 4. Modelle
def make_preprocessor() -> ColumnTransformer:
    numerisch = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
    ])
    kategorisch = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        # Kategorien mit weniger als 100 Rows werden zu "selten" gebuendelt
        ("onehot", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=100)),
    ])
    # sparse_threshold=0: immer dichte Matrix (Gradient Boosting braucht das)
    return ColumnTransformer(
        [("num", numerisch, NUM_FEATURES), ("cat", kategorisch, CAT_FEATURES)],
        sparse_threshold=0,
    )


def _pipe(regressor) -> Pipeline:
    return Pipeline([("pre", make_preprocessor()), ("model", regressor)])


def _hgb(loss: str) -> Pipeline:
    return _pipe(HistGradientBoostingRegressor(
        loss=loss, max_iter=300, l2_regularization=1.0, random_state=RANDOM_STATE,
    ))


def make_models() -> dict:
    return {
        "Baseline: Mittelwert": _pipe(DummyRegressor(strategy="mean")),
        "Baseline: Median": _pipe(DummyRegressor(strategy="median")),
        "Lineare Regression": _pipe(LinearRegression()),
        "Ridge Regression": _pipe(Ridge(alpha=1.0)),
        "Random Forest": _pipe(RandomForestRegressor(
            n_estimators=150, min_samples_leaf=20, max_features=0.5,
            n_jobs=-1, random_state=RANDOM_STATE,
        )),
        "Gradient Boosting (MAE)": _hgb("absolute_error"),
        "Gradient Boosting (RMSE)": _hgb("squared_error"),
    }


# welche Modelle per Zeitreihen-Kreuzvalidierung abgestimmt werden: Name -> Scoring
TUNE = {
    "Gradient Boosting (MAE)": "neg_mean_absolute_error",
    "Gradient Boosting (RMSE)": "r2",
}
TUNE_GRID = {
    "model__learning_rate": [0.05, 0.1],
    "model__max_leaf_nodes": [15, 31],
    "model__min_samples_leaf": [50, 200],
}


def tune(pipeline: Pipeline, X: pd.DataFrame, y: pd.Series, scoring: str) -> Pipeline:
    """Parameter-Suche NUR auf den Trainingstagen (zeitlich geordnete Folds)."""
    suche = GridSearchCV(pipeline, TUNE_GRID, scoring=scoring,
                         cv=TimeSeriesSplit(n_splits=3), n_jobs=1, refit=True)
    suche.fit(X, y)
    print(f"   beste Parameter: {suche.best_params_}")
    return suche.best_estimator_


def bewerte(y_true, y_pred) -> dict:
    return {
        "MAE (min)": mean_absolute_error(y_true, y_pred),
        "RMSE (min)": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "R2": r2_score(y_true, y_pred),
    }


def backtest(df: pd.DataFrame, vorlagen: dict, n_tage: int) -> pd.DataFrame:
    """Fuer jeden der letzten n_tage Tage: auf allen frueheren Tagen trainieren und
    genau diesen Tag testen. Robuster als nur die letzten 2 Testtage, weil ein
    einzelnes (Wochenend-)Ergebnis nicht alles entscheidet."""
    feat = NUM_FEATURES + CAT_FEATURES
    tage = sorted(df["service_date"].dropna().unique())
    zeilen = []
    print(f"\nBacktest ueber die letzten {n_tage} Tage ...")
    for tag in tage[-n_tage:]:
        tr = df[df["service_date"] < tag].sort_values("ts")
        te = df[df["service_date"] == tag]
        if len(tr) < 1000 or len(te) < 200:
            continue
        wt = WOCHENTAGE_KURZ[pd.Timestamp(str(tag)).weekday()]
        for name, vorlage in vorlagen.items():
            m = clone(vorlage).fit(tr[feat], tr["verspaetung_min"])
            zeilen.append({
                "Tag": f"{tag} ({wt})", "Rows": len(te), "Modell": name,
                **bewerte(te["verspaetung_min"], m.predict(te[feat])),
            })
    bt = pd.DataFrame(zeilen)
    if bt.empty:
        print("Backtest uebersprungen (zu wenig Tage oder Rows).")
        return bt
    for metrik in ("MAE (min)", "R2"):
        tab = bt.pivot(index="Modell", columns="Tag", values=metrik)
        tab["Mittel"] = tab.mean(axis=1)
        print(f"\nBacktest {metrik} je Tag:")
        print(tab.round(3).to_string())
    bt.round(4).to_csv(RESULTS_DIR / "backtest_ergebnisse.csv", index=False)
    return bt



# 5. Hauptprogramm
def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    df = build_features(load_data())
    if len(df) < 50:
        print("Zu wenig Daten zum Trainieren. Erst mehr Snapshots sammeln lassen.")
        return

    train, test = split_data(df)
    train_tage = sorted(train["service_date"].dropna().unique())
    test_tage = sorted(test["service_date"].dropna().unique())
    split_typ = "zeitlich" if not (set(train_tage) & set(test_tage)) else "fahrtweise"
    train = train.sort_values("ts")  # Reihenfolge der Zeit fuer die Kreuzvalidierung
    X_train, y_train = train[NUM_FEATURES + CAT_FEATURES], train["verspaetung_min"]
    X_test, y_test = test[NUM_FEATURES + CAT_FEATURES], test["verspaetung_min"]

    print(f"\nVerspaetung im Train: Mittel {y_train.mean():.2f} min, "
          f"Median {y_train.median():.2f} min, Std {y_train.std():.2f} min")

    ergebnisse = []
    fitted = {}
    for name, modell in make_models().items():
        print(f"Trainiere: {name} ...")
        if TUNING and name in TUNE:
            modell = tune(modell, X_train, y_train, TUNE[name])
        else:
            modell.fit(X_train, y_train)
        pred = modell.predict(X_test)
        ergebnisse.append({"Modell": name, **bewerte(y_test, pred)})
        fitted[name] = (modell, pred)

    # Einfache lineare Regression: nur EIN Feature (staerkste Korrelation im Training)
    korr = train[NUM_FEATURES].corrwith(y_train).abs().dropna()
    einfach_feature = korr.idxmax()
    einfach = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("model", LinearRegression()),
    ])
    einfach.fit(train[[einfach_feature]], y_train)
    ergebnisse.append({
        "Modell": f"Einfache Regression ({einfach_feature})",
        **bewerte(y_test, einfach.predict(test[[einfach_feature]])),
    })
    print(f"\nEinfache Regression: y = {einfach.named_steps['model'].intercept_:.3f} "
          f"+ {einfach.named_steps['model'].coef_[0]:.3f} * {einfach_feature}")

    tabelle = pd.DataFrame(ergebnisse).set_index("Modell").round(3)
    print("\nErgebnisse auf dem Testset:")
    print(tabelle.to_string())
    tabelle.to_csv(METRICS_PATH)

    # Backtest ueber mehrere einzelne Tage
    bt = None
    if BACKTEST:
        bt = backtest(df, {n: fitted[n][0] for n in BACKTEST_MODELLE if n in fitted},
                      BACKTEST_TAGE)

    # Bestes echtes Modell (ohne Baselines und einfache Regression) nach MAE
    kandidaten = tabelle.loc[~tabelle.index.str.startswith(("Baseline", "Einfache"))]
    bestes = kandidaten["MAE (min)"].idxmin()
    if bt is not None and not bt.empty:
        mae_bt = bt.groupby("Modell")["MAE (min)"].mean()
        kand_bt = [m for m in kandidaten.index if m in mae_bt.index]
        if kand_bt:
            bestes = mae_bt.loc[kand_bt].idxmin()
            print(f"\nAuswahl nach mittlerem MAE im Backtest: {bestes}")
    bestes_r2 = kandidaten["R2"].idxmax()
    baseline_mae = tabelle.loc["Baseline: Median", "MAE (min)"]
    print(f"\nBestes Modell nach MAE: {bestes} (MAE {tabelle.loc[bestes, 'MAE (min)']} min, "
          f"Median-Baseline {baseline_mae} min)")
    print(f"Bestes Modell nach R2:  {bestes_r2} (R2 {tabelle.loc[bestes_r2, 'R2']})")
    if tabelle.loc[bestes, "MAE (min)"] >= baseline_mae:
        print("Hinweis: Das Modell schlaegt die Baseline nicht. Das ist ein "
              "ehrliches Ergebnis und gehoert in die Diskussion eurer Grenzen.")

    # Koeffizienten der linearen Regression (Interpretation)
    lin = fitted["Lineare Regression"][0]
    namen = lin.named_steps["pre"].get_feature_names_out()
    koeff = pd.Series(lin.named_steps["model"].coef_, index=namen)
    print("\nStaerkste Einfluesse (Lineare Regression, standardisierte Features):")
    print(koeff.reindex(koeff.abs().sort_values(ascending=False).index).head(15).round(3))

    # Feature-Wichtigkeit des Random Forest
    rf = fitted["Random Forest"][0]
    rf_namen = rf.named_steps["pre"].get_feature_names_out()
    wichtigkeit = pd.Series(
        rf.named_steps["model"].feature_importances_, index=rf_namen
    ).sort_values(ascending=False)
    print("\nFeature-Wichtigkeit (Random Forest, Summe = 1):")
    print(wichtigkeit.head(15).round(3))
    wichtigkeit.round(4).to_csv(METRICS_PATH.parent / "feature_wichtigkeit.csv",
                                header=["wichtigkeit"])

    # Modell speichern (Pipeline + Feature-Liste + Split-Info fuer das Dashboard)
    name_sauber = re.sub(r"[^A-Za-z0-9]+", "_", bestes).strip("_")
    version = f"{name_sauber}_{datetime.now():%Y%m%d_%H%M}"
    print(f"\nModell-Version: {version}")
    joblib.dump(
        {
            "model": fitted[bestes][0],
            "model_name": bestes,
            "model_version": version,
            "split_typ": split_typ,
            "train_tage": train_tage,
            "test_tage": test_tage,
            "num_features": NUM_FEATURES,
            "cat_features": CAT_FEATURES,
            "kein_event_distanz_km": KEIN_EVENT_DISTANZ_KM,
            "metriken": tabelle.loc[bestes].to_dict(),
        },
        MODEL_PATH,
    )
    print(f"\nModell gespeichert: {MODEL_PATH}")
    print(f"Metriken gespeichert: {METRICS_PATH}")

    # Plot Vorhersage vs. Ist
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pred = fitted[bestes][1]
        grenze = [min(y_test.min(), pred.min()), max(y_test.max(), pred.max())]
        plt.figure(figsize=(6, 6))
        plt.scatter(y_test, pred, s=8, alpha=0.4)
        plt.plot(grenze, grenze, "r--", label="perfekte Vorhersage")
        plt.xlabel("Tatsaechliche Verspaetung (min)")
        plt.ylabel("Vorhergesagte Verspaetung (min)")
        plt.title(f"{bestes}: Vorhersage vs. Ist")
        plt.legend()
        plt.tight_layout()
        plt.savefig(PLOT_PATH, dpi=150)
        plt.close()
        print(f"Plot gespeichert: {PLOT_PATH}")
    except ImportError:
        print("matplotlib nicht installiert, Plot uebersprungen.")


if __name__ == "__main__":
    main()