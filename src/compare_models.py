#!/usr/bin/env python3
"""Compare dengue count models with temporal tuning and regional metrics."""

from __future__ import annotations

import json
import time
from pathlib import Path

import lightgbm as lgb
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import ParameterSampler
from sklearn.preprocessing import OneHotEncoder

from src import analyze, environmental_features


BASE_FEATURES = [
    "municipality_code", "uf", "lag_0", "lag_1", "lag_2", "lag_3", "lag_4",
    "lag_13", "lag_51", "lag_52", "mean_4w", "week_sin", "week_cos",
]
ENVIRONMENT_FEATURES = [
    "precipitation_mm", "temperature_c", "relative_humidity_pct", "spatial_lag_cases",
]
FEATURES = BASE_FEATURES + ENVIRONMENT_FEATURES
NUMERIC_FEATURES = [feature for feature in FEATURES if feature not in {"municipality_code", "uf"}]
RANDOM_FOREST_SAMPLE_SIZE = 300_000
TUNING_SEED = 42


def score_predictions(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.maximum(0, np.asarray(predicted, dtype=float))
    error = actual - predicted
    return {
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.mean(error**2) ** 0.5),
        "wape": float(np.abs(error).sum() / max(1.0, actual.sum())),
    }


def select_best_config(results: list[dict[str, object]]) -> dict[str, object]:
    return min(results, key=lambda result: result["validation_wape"])


def _prediction_rows(frame: pd.DataFrame, prediction: np.ndarray, model: str) -> pd.DataFrame:
    output = frame[["municipality_code", "uf", "target_week", "target_year", "target"]].copy()
    output["prediction"] = np.maximum(0, prediction)
    output["model"] = model
    return output


def _metrics(rows: pd.DataFrame, period: int, *, geography: str, code: str) -> dict[str, object]:
    frame = rows.loc[rows["target_year"].eq(period)]
    if geography == "UF":
        frame = frame.loc[frame["uf"].eq(code)]
    scores = score_predictions(frame["target"].to_numpy(), frame["prediction"].to_numpy())
    capture = 0.0
    total = float(frame["target"].sum())
    for _, week in frame.groupby("target_week"):
        n_alerts = max(1, int(np.ceil(len(week) * 0.10)))
        capture += float(week.nlargest(n_alerts, "prediction")["target"].sum())
    return {
        "period": period,
        "model": str(rows["model"].iloc[0]),
        "geography_level": geography,
        "geography_code": code,
        "municipalities": int(frame["municipality_code"].nunique()),
        "rows": int(len(frame)),
        "actual_cases": int(total),
        **scores,
        "top_10pct_case_capture": capture / max(total, 1.0),
    }


def _metrics_for_model(rows: pd.DataFrame, period: int) -> list[dict[str, object]]:
    rows = rows.loc[rows["target_year"].eq(period)]
    output = [_metrics(rows, period, geography="Brasil", code="BR")]
    for uf in sorted(rows["uf"].dropna().astype(str).unique()):
        output.append(_metrics(rows, period, geography="UF", code=uf))
    return output


def _save_comparison_figures(predictions: pd.DataFrame, metrics: pd.DataFrame) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({"font.size": 12, "axes.titlesize": 17, "axes.labelsize": 13})
    test = predictions.loc[predictions["target_year"].isin([2024, 2025])].copy()
    test["target_week"] = pd.to_datetime(test["target_week"], format="mixed")
    weekly = test.groupby(["model", "target_week"], as_index=False)[["target", "prediction"]].sum()
    observed = weekly.loc[weekly["model"].eq("Persistência")]
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(observed["target_week"], observed["target"], label="Observado", color="#222222", linewidth=2.4)
    for model, label, color in (
        ("Persistência", "Persistência", "#2E86AB"),
        ("LightGBM clima+espacial", "LightGBM clima+espacial", "#F26419"),
    ):
        frame = weekly.loc[weekly["model"].eq(model)]
        ax.plot(frame["target_week"], frame["prediction"], label=label, color=color, alpha=0.9)
    ax.set(title="Previsão semanal agregada: teste temporal 2024–2025", xlabel="Semana de destino", ylabel="Notificações prováveis")
    ax.ticklabel_format(axis="y", style="plain")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.legend(frameon=False, ncol=3, loc="upper left")
    fig.tight_layout()
    fig.savefig(analyze.FIGURES / "teste_previsao_semanal.png", dpi=180)
    plt.close(fig)

    selected = ["Persistência", "Random Forest (amostra)", "LightGBM base", "LightGBM clima+espacial"]
    brazil = metrics.loc[
        metrics["geography_level"].eq("Brasil")
        & metrics["period"].isin([2024, 2025])
        & metrics["model"].isin(selected)
    ]
    labels = {
        "Persistência": "Persistência",
        "Random Forest (amostra)": "Random Forest",
        "LightGBM base": "LightGBM base",
        "LightGBM clima+espacial": "LightGBM clima+espacial",
    }
    fig, ax = plt.subplots(figsize=(13, 7))
    table = brazil.assign(model=brazil["model"].map(labels)).pivot(index="period", columns="model", values="wape")[list(labels.values())] * 100
    table.plot(kind="bar", ax=ax, color=["#2E86AB", "#9C89B8", "#F6AE2D", "#F26419"])
    for container in ax.containers:
        ax.bar_label(container, fmt="%.1f%%", padding=3, fontsize=10)
    ax.set(title="Erro de previsão por modelo", xlabel="Ano de teste", ylabel="WAPE (%) — menor é melhor")
    ax.legend(title="Modelo", frameon=False)
    ax.tick_params(axis="x", rotation=0)
    fig.tight_layout()
    fig.savefig(analyze.FIGURES / "metricas_teste.png", dpi=180)
    plt.close(fig)

    capture = brazil.assign(model=brazil["model"].map(labels)).pivot(
        index="period", columns="model", values="top_10pct_case_capture",
    )[list(labels.values())] * 100
    fig, ax = plt.subplots(figsize=(13, 7))
    capture.plot(kind="bar", ax=ax, color=["#2E86AB", "#9C89B8", "#F6AE2D", "#F26419"])
    for container in ax.containers:
        ax.bar_label(container, fmt="%.1f%%", padding=3, fontsize=10)
    ax.set(title="Casos capturados ao priorizar 10% dos municípios", xlabel="Ano de teste", ylabel="Casos observados capturados (%)")
    ax.set_ylim(0, 100)
    ax.legend(title="Ordenação por previsão", frameon=False)
    ax.tick_params(axis="x", rotation=0)
    fig.tight_layout()
    fig.savefig(analyze.FIGURES / "captura_top_10pct.png", dpi=180)
    plt.close(fig)


def _fit_lgbm(
    train: pd.DataFrame,
    frame: pd.DataFrame,
    params: dict[str, object],
    features: list[str],
    *,
    poisson: bool = False,
) -> np.ndarray:
    model = lgb.LGBMRegressor(**params)
    target = train["target"] if poisson else np.log1p(train["target"])
    model.fit(train[features], target, categorical_feature=["municipality_code", "uf"])
    prediction = model.predict(frame[features])
    return np.maximum(0, prediction if poisson else np.expm1(prediction))


def _complete_feature_rows(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.dropna(subset=NUMERIC_FEATURES)


def _fit_random_forest(
    train: pd.DataFrame,
    frame: pd.DataFrame,
    municipality_categories: list[str],
    uf_categories: list[str],
) -> tuple[pd.DataFrame, np.ndarray]:
    train = _complete_feature_rows(train)
    frame = _complete_feature_rows(frame)
    sample = train.sample(n=min(RANDOM_FOREST_SAMPLE_SIZE, len(train)), random_state=TUNING_SEED)
    categorical = ColumnTransformer(
        [
            ("municipality", OneHotEncoder(categories=[municipality_categories], handle_unknown="ignore", sparse_output=True, dtype=np.float32), ["municipality_code"]),
            ("uf", OneHotEncoder(categories=[uf_categories], handle_unknown="ignore", sparse_output=True, dtype=np.float32), ["uf"]),
            ("numeric", "passthrough", NUMERIC_FEATURES),
        ],
        sparse_threshold=1.0,
    )
    x_train = categorical.fit_transform(sample[FEATURES])
    x_predict = categorical.transform(frame[FEATURES])
    model = RandomForestRegressor(
        n_estimators=60, max_depth=16, min_samples_leaf=25, max_features=0.5,
        n_jobs=-1, random_state=TUNING_SEED,
    )
    model.fit(x_train, sample["target"])
    return frame, np.maximum(0, model.predict(x_predict))


def _sample_configs() -> list[dict[str, object]]:
    space = {
        "num_leaves": [15, 31, 63],
        "min_child_samples": [20, 40, 80, 160],
        "n_estimators": [200, 400, 600],
        "learning_rate": [0.02, 0.05, 0.08],
        "max_depth": [-1, 8, 12],
        "reg_lambda": [0.5, 2.0, 8.0],
    }
    return list(ParameterSampler(space, n_iter=12, random_state=TUNING_SEED))


def _tune(panel: pd.DataFrame, base_params: dict[str, object]) -> tuple[dict[str, object], pd.DataFrame]:
    folds = [(2020, 2021), (2021, 2022), (2022, 2023)]
    records: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    for candidate, config in enumerate(_sample_configs(), start=1):
        fold_scores = []
        for train_through, validate_year in folds:
            train = panel.loc[panel["target_year"].le(train_through)]
            validation = panel.loc[panel["target_year"].eq(validate_year)]
            predicted = _fit_lgbm(train, validation, {**base_params, **config}, FEATURES)
            wape = score_predictions(validation["target"], predicted)["wape"]
            fold_scores.append(wape)
            records.append({
                "candidate": candidate,
                "train_through_year": train_through,
                "validation_year": validate_year,
                "validation_wape": wape,
                "params": json.dumps(config, sort_keys=True),
            })
        results.append({
            "candidate": candidate,
            "params": config,
            "validation_wape": float(np.mean(fold_scores)),
        })
        print(f"LightGBM candidato {candidate}/12: WAPE média={np.mean(fold_scores):.4f}", flush=True)
    return select_best_config(results), pd.DataFrame(records)


def run_comparison() -> tuple[pd.DataFrame, pd.DataFrame]:
    started = time.time()
    processed = analyze.PROCESSED
    counts = pd.read_csv(processed / "municipality_week_cases.csv.gz", dtype={"municipality_code": str})
    counts["week_start"] = pd.to_datetime(counts["week_start"])
    reference = pd.read_csv(processed / "ibge_municipalities_2022.csv", dtype={"municipality_code": str})
    reference, weather, neighbors = environmental_features.load_environmental_data(reference, processed)
    panel, _ = analyze.build_feature_panel(counts, reference, weather, neighbors)

    base_params: dict[str, object] = {
        "objective": "regression_l1", "max_bin": 63, "n_jobs": 8,
        "verbosity": -1, "random_state": TUNING_SEED,
    }
    best, tuning = _tune(panel, base_params)
    params = {**base_params, **best["params"]}
    final_train = panel.loc[panel["target_year"].le(2023)]
    validation = panel.loc[panel["target_year"].eq(2023)]
    test = panel.loc[panel["target_year"].isin([2024, 2025])]

    old_validation = pd.read_csv(processed / "validation_2023_predictions.csv.gz", dtype={"municipality_code": str})
    old_test = pd.read_csv(processed / "test_2024_2025_predictions.csv.gz", dtype={"municipality_code": str})
    validation_rows: list[pd.DataFrame] = []
    test_rows: list[pd.DataFrame] = []
    for data in (old_validation, old_test):
        output_rows = validation_rows if data["target_year"].eq(2023).all() else test_rows
        for model_name, column in (
            ("Persistência", "pred_last_week"),
            ("Sazonal (52 semanas)", "pred_seasonal"),
        ):
            output_rows.append(_prediction_rows(data, data[column].to_numpy(), model_name))

    base_validation = _fit_lgbm(
        panel.loc[panel["target_year"].le(2022)], validation, params, BASE_FEATURES,
    )
    base_test = _fit_lgbm(final_train, test, params, BASE_FEATURES)
    enriched_validation = _fit_lgbm(
        panel.loc[panel["target_year"].le(2022)], validation, params, FEATURES,
    )
    enriched_test = _fit_lgbm(final_train, test, params, FEATURES)
    validation_rows.extend([
        _prediction_rows(validation, base_validation, "LightGBM base"),
        _prediction_rows(validation, enriched_validation, "LightGBM clima+espacial"),
    ])
    test_rows.extend([
        _prediction_rows(test, base_test, "LightGBM base"),
        _prediction_rows(test, enriched_test, "LightGBM clima+espacial"),
    ])

    poisson_params = {**params, "objective": "poisson"}
    poisson_validation = _fit_lgbm(
        panel.loc[panel["target_year"].le(2022)], validation, poisson_params, FEATURES, poisson=True,
    )
    poisson_test = _fit_lgbm(final_train, test, poisson_params, FEATURES, poisson=True)
    validation_rows.append(_prediction_rows(validation, poisson_validation, "LightGBM Poisson (enriquecido)"))
    test_rows.append(_prediction_rows(test, poisson_test, "LightGBM Poisson (enriquecido)"))

    categories = [str(value) for value in panel["municipality_code"].cat.categories]
    uf_categories = [str(value) for value in panel["uf"].cat.categories]
    print("Random Forest: ajustando amostra fixa de até 300.000 linhas...", flush=True)
    rf_validation_frame, rf_validation = _fit_random_forest(
        panel.loc[panel["target_year"].le(2022)], validation, categories, uf_categories,
    )
    rf_test_frame, rf_test = _fit_random_forest(
        final_train, test, categories, uf_categories,
    )
    validation_rows.append(_prediction_rows(rf_validation_frame, rf_validation, "Random Forest (amostra)"))
    test_rows.append(_prediction_rows(rf_test_frame, rf_test, "Random Forest (amostra)"))

    predictions = pd.concat(validation_rows + test_rows, ignore_index=True)
    metrics = pd.DataFrame([
        metric
        for model in predictions["model"].unique()
        for year in (2023, 2024, 2025)
        for metric in _metrics_for_model(predictions.loc[predictions["model"].eq(model)], year)
    ])
    _save_comparison_figures(predictions, metrics)
    metrics.to_csv(processed / "model_comparison_metrics.csv", index=False)
    tuning.to_csv(processed / "model_tuning_validation.csv", index=False)
    analyze.write_csv_atomic(predictions, processed / "model_comparison_predictions.csv.gz")
    analyze.write_json_atomic({
        "run_seconds": round(time.time() - started, 1),
        "random_forest_train_sample_rows": RANDOM_FOREST_SAMPLE_SIZE,
        "tuning_seed": TUNING_SEED,
        "tuning_candidates": len(_sample_configs()),
        "validation_folds": [2021, 2022, 2023],
        "selected_candidate": best["candidate"],
        "selected_params": params,
        "selection_metric": "mean WAPE over expanding validation folds 2021-2023",
        "test_years": [2024, 2025],
        "weather_source": "NASA POWER Daily API (MERRA-2/POWER)",
        "features_enriched": ENVIRONMENT_FEATURES,
        "weather_municipalities": int(weather["municipality_code"].nunique()),
        "weather_missing_panel_rows": int(panel["relative_humidity_pct"].isna().sum()),
    }, processed / "model_comparison_summary.json")
    return metrics, tuning


if __name__ == "__main__":
    model_metrics, tuning_metrics = run_comparison()
    print("Comparação de modelos:")
    print(model_metrics.to_string(index=False))
    print("Busca de parâmetros (validação temporal 2021–2023):")
    print(tuning_metrics.to_string(index=False))
