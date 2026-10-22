"""Generate descriptive charts relating NASA POWER climate to dengue cases."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
FIGURES = ROOT / "figures"
START_DATE = "2015-01-04"
END_DATE = "2025-11-30"
MIN_POPULATION = 20_000
LOOKAHEAD_WEEKS = 4
CLIMATE_VARIABLES = {
    "precipitation_mm": ("Precipitação semanal", "Precipitação (mm/semana)", "#2E86AB", "precipitacao"),
    "temperature_c": ("Temperatura média", "Temperatura (°C)", "#F26419", "temperatura"),
    "relative_humidity_pct": ("Umidade relativa média", "Umidade relativa (%)", "#1E6962", "umidade"),
}


def load_panel() -> pd.DataFrame:
    """Load versioned source tables and calculate each municipality's next four weeks."""
    weather = pd.read_csv(
        PROCESSED / "municipality_week_weather_nasa_power.csv.gz",
        usecols=["municipality_code", "week_start", *CLIMATE_VARIABLES],
        parse_dates=["week_start"],
    )
    cases = pd.read_csv(
        PROCESSED / "municipality_week_cases.csv.gz",
        usecols=["municipality_code", "week_start", "cases"],
        parse_dates=["week_start"],
    )
    population = pd.read_csv(
        PROCESSED / "ibge_municipalities_2022.csv",
        usecols=["municipality_code", "population_2022"],
    )

    panel = weather.merge(population, on="municipality_code", validate="many_to_one")
    panel = panel.loc[
        panel["population_2022"].ge(MIN_POPULATION)
        & panel["week_start"].between(START_DATE, END_DATE)
    ].dropna(subset=list(CLIMATE_VARIABLES)).copy()

    case_counts = cases.groupby(["municipality_code", "week_start"])["cases"].sum()
    future_cases = np.zeros(len(panel), dtype=float)
    for lead in range(1, LOOKAHEAD_WEEKS + 1):
        future_index = pd.MultiIndex.from_arrays(
            [
                panel["municipality_code"],
                panel["week_start"] + pd.Timedelta(days=7 * lead),
            ]
        )
        future_cases += case_counts.reindex(future_index).fillna(0).to_numpy()

    panel["incidence_next4_per_100k"] = (
        future_cases / panel["population_2022"].to_numpy() * 100_000
    )
    return panel


def _format_pt_br(value: float, _position: int) -> str:
    return f"{value:g}".replace(".", ",")


def save_charts(panel: pd.DataFrame) -> None:
    """Save one equal-frequency exposure chart for each weekly climate measure."""
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({"font.size": 12, "axes.titlesize": 15, "axes.labelsize": 11})
    FIGURES.mkdir(parents=True, exist_ok=True)
    municipality_count = panel["municipality_code"].nunique()

    for variable, (title, x_label, color, filename) in CLIMATE_VARIABLES.items():
        data = panel.assign(exposure_bin=pd.qcut(panel[variable], q=8, duplicates="drop"))
        summary = data.groupby("exposure_bin", observed=True).agg(
            exposure=(variable, "mean"),
            incidence=("incidence_next4_per_100k", "mean"),
        )

        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        ax.plot(
            summary["exposure"],
            summary["incidence"],
            marker="o",
            markersize=6,
            color=color,
            linewidth=2,
        )
        ax.set_title(title, loc="left", pad=12, fontweight="bold")
        ax.set_xlabel(x_label)
        ax.set_ylabel("Casos por 100 mil habitantes\n(média municipal)")
        ax.set_ylim(bottom=0)
        ax.xaxis.set_major_formatter(FuncFormatter(_format_pt_br))
        ax.yaxis.set_major_formatter(FuncFormatter(_format_pt_br))
        ax.grid(axis="y", alpha=0.25)
        ax.grid(axis="x", visible=False)
        ax.spines[["top", "right"]].set_visible(False)

        municipality_text = f"{municipality_count:,}".replace(",", ".")
        fig.text(
            0.5,
            0.015,
            f"2015–2025 · {municipality_text} municípios · faixas por quantil",
            ha="center",
            fontsize=9,
            color="#66777a",
        )
        fig.tight_layout(rect=(0, 0.05, 1, 1))
        path = FIGURES / f"clima_casos_{filename}.png"
        fig.savefig(path, dpi=180, facecolor="white")
        plt.close(fig)
        print(f"Salvo: {path.relative_to(ROOT)}")


def main() -> None:
    save_charts(load_panel())


if __name__ == "__main__":
    main()
