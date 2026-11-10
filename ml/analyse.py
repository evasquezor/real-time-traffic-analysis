from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from train import NUM_FEATURES, build_features, load_data

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"  
MIN_ANZAHL = 30                     
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
ZIEL = "verspaetung_min"



# Hilfsfunktionen
def gruppen_tabelle(df, spalte, min_anzahl=MIN_ANZAHL):
    """Anzahl, Mittelwert, Median und Anteil >= 3 min Verspaetung je Gruppe."""
    tab = (
        df.groupby(spalte, observed=True)[ZIEL]
        .agg(anzahl="count", mittelwert="mean", median="median",
             anteil_ab_3min=lambda s: (s >= 3).mean())
        .round(3)
    )
    return tab[tab["anzahl"] >= min_anzahl]


def balken(tab, titel, dateiname, xlabel, horizontal=False, wert="mittelwert"):
    """Balkendiagramm der durchschnittlichen Verspaetung je Gruppe."""
    if tab.empty:
        print(f"  (uebersprungen: {titel} - zu wenig Daten)")
        return
    fig, ax = plt.subplots(figsize=(8, max(4, 0.35 * len(tab))) if horizontal else (8, 4.5))
    labels = [str(i) for i in tab.index]
    if horizontal:
        ax.barh(labels, tab[wert])
        ax.invert_yaxis()
        ax.set_xlabel("Durchschnittliche Verspaetung (min)")
        ax.set_ylabel(xlabel)
    else:
        ax.bar(labels, tab[wert])
        ax.set_ylabel("Durchschnittliche Verspaetung (min)")
        ax.set_xlabel(xlabel)
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    ax.set_title(titel)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / dateiname, dpi=150)
    plt.close(fig)


def speichere(tab, dateiname):
    tab.to_csv(RESULTS_DIR / dateiname)



# Einzelne Analysen
def analyse_verteilung(df):
    print("\n1. Verteilung der Verspaetung (Minuten)")
    print(df[ZIEL].describe().round(2).to_string())
    for grenze in (1, 3, 5, 10):
        print(f"   Anteil mit >= {grenze} min Verspaetung: {(df[ZIEL] >= grenze).mean():.1%}")
    print(f"   Anteil zu frueh (< 0 min): {(df[ZIEL] < 0).mean():.1%}")

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(df[ZIEL], bins=60)
    ax.axvline(df[ZIEL].median(), color="red", linestyle="--", label="Median")
    ax.set_xlabel("Verspaetung (min)")
    ax.set_ylabel("Anzahl Rows")
    ax.set_title("Verteilung der Verspaetungen")
    ax.legend()
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "analyse_verteilung.png", dpi=150)
    plt.close(fig)


def analyse_zeit(df):
    print("\n2. Verspaetung nach Zeit")
    nach_stunde = gruppen_tabelle(df, "stunde")
    print(nach_stunde.to_string())
    speichere(nach_stunde, "analyse_stunde.csv")
    balken(nach_stunde, "Verspaetung nach Uhrzeit", "analyse_stunde.png", "Stunde (Berliner Zeit)")

    nach_tag = gruppen_tabelle(df, "wochentag")
    nach_tag.index = [WOCHENTAGE[int(i)] for i in nach_tag.index]
    print(nach_tag.to_string())
    speichere(nach_tag, "analyse_wochentag.csv")
    balken(nach_tag, "Verspaetung nach Wochentag", "analyse_wochentag.png", "Wochentag")


def analyse_ort(df):
    print("\n3. Verspaetung nach Linie und Haltestelle")
    nach_linie = gruppen_tabelle(df, "route_short_name").sort_values("mittelwert", ascending=False)
    print(nach_linie.head(15).to_string())
    speichere(nach_linie, "analyse_linie.csv")
    balken(nach_linie.head(15), "Verspaetung nach Linie (Top 15)",
           "analyse_linie.png", "Linie", horizontal=True)

    if "stop_name" in df.columns:
        nach_halt = gruppen_tabelle(df, "stop_name").sort_values("mittelwert", ascending=False)
        print(nach_halt.head(15).to_string())
        speichere(nach_halt, "analyse_haltestelle.csv")
        balken(nach_halt.head(15), "Verspaetung nach Haltestelle (Top 15)",
               "analyse_haltestelle.png", "Haltestelle", horizontal=True)


def analyse_wetter(df):
    print("\n4. Verspaetung nach Wetter")
    df = df.copy()
    df["regen"] = pd.cut(
        df["precipitation"], bins=[-0.01, 0, 0.5, 2, np.inf],
        labels=["kein Regen", "leicht (<=0,5 mm)", "mittel (<=2 mm)", "stark (>2 mm)"],
    )
    df["temperatur"] = pd.cut(
        df["temperature_2m"], bins=[-np.inf, 0, 10, 20, np.inf],
        labels=["<= 0 C", "0-10 C", "10-20 C", "> 20 C"],
    )
    df["wind"] = pd.cut(
        df["wind_speed_10m"], bins=[-np.inf, 10, 20, 30, np.inf],
        labels=["<= 10 km/h", "10-20 km/h", "20-30 km/h", "> 30 km/h"],
    )
    for spalte, titel in [("regen", "Niederschlag"), ("temperatur", "Temperatur"), ("wind", "Wind")]:
        tab = gruppen_tabelle(df, spalte)
        print(f"\n{titel}:")
        print(tab.to_string())
        speichere(tab, f"analyse_{spalte}.csv")
        balken(tab, f"Verspaetung nach {titel}", f"analyse_{spalte}.png", titel)


def analyse_ereignisse(df):
    print("\n5. Verspaetung mit und ohne Baustelle / Verkehrsmeldung / Alert")
    zeilen = []
    for spalte, name in [("has_roadwork", "Baustelle"),
                         ("has_traffic_event", "Verkehrsmeldung"),
                         ("has_service_alert", "Service-Alert")]:
        if spalte not in df.columns:
            continue
        for wert, text in [(0.0, "nein"), (1.0, "ja")]:
            teil = df[df[spalte] == wert][ZIEL]
            zeilen.append({
                "Faktor": name, "vorhanden": text, "anzahl": len(teil),
                "mittelwert": round(teil.mean(), 3) if len(teil) else np.nan,
                "median": round(teil.median(), 3) if len(teil) else np.nan,
            })
    tab = pd.DataFrame(zeilen)
    print(tab.to_string(index=False))
    tab.to_csv(RESULTS_DIR / "analyse_ereignisse.csv", index=False)
    print("   Hinweis: Bei kleiner Anzahl 'ja' ist der Vergleich nicht aussagekraeftig.")

    if not tab.empty:
        pivot = tab.pivot(index="Faktor", columns="vorhanden", values="mittelwert")
        fig, ax = plt.subplots(figsize=(7, 4.5))
        pivot.plot(kind="bar", ax=ax)
        ax.set_ylabel("Durchschnittliche Verspaetung (min)")
        ax.set_title("Verspaetung mit (ja) und ohne (nein) Einflussfaktor")
        plt.setp(ax.get_xticklabels(), rotation=0)
        fig.tight_layout()
        fig.savefig(RESULTS_DIR / "analyse_ereignisse.png", dpi=150)
        plt.close(fig)


def analyse_korrelation(df):
    print("\n6. Korrelation der Features mit der Verspaetung")
    zahlen = df[[c for c in NUM_FEATURES if c in df.columns] + [ZIEL]]
    korr = zahlen.corr(numeric_only=True)[ZIEL].drop(ZIEL).dropna()
    korr = korr.reindex(korr.abs().sort_values(ascending=False).index).round(3)
    print(korr.to_string())
    korr.to_csv(RESULTS_DIR / "analyse_korrelation.csv", header=["korrelation"])

    fig, ax = plt.subplots(figsize=(8, max(4, 0.35 * len(korr))))
    ax.barh(korr.index, korr.values)
    ax.invert_yaxis()
    ax.set_xlabel("Korrelation mit Verspaetung (Pearson)")
    ax.set_title("Zusammenhang der Features mit der Verspaetung")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "analyse_korrelation.png", dpi=150)
    plt.close(fig)
    print("   Hinweis: Korrelation ist nur ein linearer Zusammenhang und kein Beweis fuer Ursache.")



def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    df = build_features(load_data())
    if len(df) < 50:
        print("Zu wenig Daten fuer eine Analyse.")
        return

    analyse_verteilung(df)
    analyse_zeit(df)
    analyse_ort(df)
    analyse_wetter(df)
    analyse_ereignisse(df)
    analyse_korrelation(df)
    print(f"\nFertig. Diagramme und Tabellen liegen in: {RESULTS_DIR}")


if __name__ == "__main__":
    main()