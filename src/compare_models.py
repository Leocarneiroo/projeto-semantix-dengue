#!/usr/bin/env python3
"""Compare count models on the existing dengue municipality-week panel."""

from __future__ import annotations

import json
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import OneHotEncoder

from src import analyze


FEATURES = [
    "municipality_code", "uf", "lag_0", "lag_1", "lag_2", "lag_3", "lag_4",
    "lag_13", "lag_51", "lag_52", "mean_4w", "week_sin", "week_cos",
]
NUMERIC_FEATURES = [feature for feature in FEATURES if feature not in {"municipality_code", "uf"}]
RANDOM_FOREST_SAMPLE_SIZE = 300_000


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
    output = frame[["target_week", "target_year", "target"]].copy()
    output["prediction"] = np.maximum(0, prediction)
    output["model"] = model
    return output


def _metrics(rows: pd.DataFrame, period: int) -> dict[str, object]:
    frame = rows.loc[rows["target_year"].eq(period)]
    scores = score_predictions(frame["target"].to_numpy(), frame["prediction"].to_numpy())
    capture = 0.0
    total = float(frame["target"].sum())
    for _, week in frame.groupby("target_week"):
        n_alerts = max(1, int(np.ceil(len(week) * 0.10)))
        capture += float(week.nlargest(n_alerts, "prediction")["target"].sum())
    return {
        "period": period,
        "model": str(rows["model"].iloc[0]),
        "rows": int(len(frame)),
        "actual_cases": int(frame["target"].sum()),
        **scores,
        "top_10pct_case_capture": capture / max(total, 1.0),
    }


def _fit_lgbm(
    train: pd.DataFrame,
    frame: pd.DataFrame,
    params: dict[str, object],
    *,
    poisson: bool = False,
) -> np.ndarray:
    model = lgb.LGBMRegressor(**params)
    target = train["target"] if poisson else np.log1p(train["target"])
    model.fit(train[FEATURES], target, categorical_feature=["municipality_code", "uf"])
    prediction = model.predict(frame[FEATURES])
    return np.maximum(0, prediction if poisson else np.expm1(prediction))


def _complete_feature_rows(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.dropna(subset=NUMERIC_FEATURES)


def _fit_random_forest(
    train: pd.DataFrame,
    frame: pd.DataFrame,
    municipality_categories: list[str],
    uf_categories: list[str],
) -> np.ndarray:
    train = _complete_feature_rows(train)
    frame = _complete_feature_rows(frame)
    # Amostra fixa limita CPU/memória e torna a comparação reproduzível.
    sample = train.sample(n=min(RANDOM_FOREST_SAMPLE_SIZE, len(train)), random_state=42)
    # One-hot evita tratar códigos municipais como números com ordem natural.
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
        n_estimators=60,
        max_depth=16,
        min_samples_leaf=25,
        max_features=0.5,
        n_jobs=-1,
        random_state=42,
    )
    model.fit(x_train, sample["target"])
    return np.maximum(0, model.predict(x_predict))


def run_comparison() -> tuple[pd.DataFrame, pd.DataFrame]:
    started = time.time()
    processed = analyze.PROCESSED
    counts = pd.read_csv(processed / "municipality_week_cases.csv.gz", dtype={"municipality_code": str})
    counts["week_start"] = pd.to_datetime(counts["week_start"])
    reference = pd.read_csv(processed / "ibge_municipalities_2022.csv", dtype={"municipality_code": str})
    panel, _ = analyze.build_feature_panel(counts, reference)
    train = panel.loc[panel["target_year"].le(2022)]
    validation = panel.loc[panel["target_year"].eq(2023)]
    final_train = panel.loc[panel["target_year"].le(2023)]
    test = panel.loc[panel["target_year"].isin([2024, 2025])]

    # Ajustar só em 2023; não usar 2024–25 para escolher parâmetros.
    base = {
        "objective": "regression_l1", "learning_rate": 0.05, "max_bin": 63,
        "reg_lambda": 2.0, "n_jobs": 8, "verbosity": -1, "random_state": 42,
    }
    configs = [
        {"num_leaves": 15, "min_child_samples": 40, "n_estimators": 200},
        {"num_leaves": 31, "min_child_samples": 100, "n_estimators": 200},
        {"num_leaves": 31, "min_child_samples": 40, "n_estimators": 400},
    ]
    tuning = []
    for index, config in enumerate(configs, start=1):
        params = {**base, **config}
        predicted = _fit_lgbm(train, validation, params)
        tuning.append({
            "candidate": index,
            "params": params,
            "validation_wape": score_predictions(validation["target"], predicted)["wape"],
        })
        print(f"LightGBM config {index}: validação WAPE={tuning[-1]['validation_wape']:.4f}", flush=True)
    best = select_best_config(tuning)

    # 0.05 was unstable on the full Poisson fit; compare smaller learning rates on validation.
    poisson_tuning = []
    for index, learning_rate in enumerate((0.02, 0.01), start=1):
        params = {
            **base, "objective": "poisson", "learning_rate": learning_rate,
            "num_leaves": 31, "min_child_samples": 40, "n_estimators": 200,
        }
        predicted = _fit_lgbm(train, validation, params, poisson=True)
        poisson_tuning.append({
            "candidate": index,
            "params": params,
            "validation_wape": score_predictions(validation["target"], predicted)["wape"],
        })
        print(f"LightGBM Poisson lr={learning_rate}: validação WAPE={poisson_tuning[-1]['validation_wape']:.4f}", flush=True)
    best_poisson = select_best_config(poisson_tuning)

    validation_rows = []
    test_rows = []
    old_validation = pd.read_csv(processed / "validation_2023_predictions.csv.gz", dtype={"municipality_code": str})
    old_test = pd.read_csv(processed / "test_2024_2025_predictions.csv.gz", dtype={"municipality_code": str})
    for data in (old_validation, old_test):
        output_rows = validation_rows if data["target_year"].eq(2023).all() else test_rows
        for model_name, column in (
            ("Persistência", "pred_last_week"),
            ("Sazonal (52 semanas)", "pred_seasonal"),
            ("LightGBM atual", "prediction"),
        ):
            output_rows.append(_prediction_rows(data, data[column].to_numpy(), model_name))

    for model_name, params, poisson in [
        ("LightGBM Poisson", {**best_poisson["params"]}, True),
        ("LightGBM ajustado", {**best["params"]}, False),
    ]:
        validation_pred = _fit_lgbm(train, validation, params, poisson=poisson)
        final_pred = _fit_lgbm(final_train, test, params, poisson=poisson)
        validation_rows.append(_prediction_rows(validation, validation_pred, model_name))
        test_rows.append(_prediction_rows(test, final_pred, model_name))

    categories = [str(value) for value in panel["municipality_code"].cat.categories]
    uf_categories = [str(value) for value in panel["uf"].cat.categories]
    print("Random Forest: ajustando amostra fixa de até 300.000 linhas...", flush=True)
    rf_validation_frame = _complete_feature_rows(validation)
    rf_test_frame = _complete_feature_rows(test)
    rf_validation = _fit_random_forest(train, rf_validation_frame, categories, uf_categories)
    rf_test = _fit_random_forest(final_train, rf_test_frame, categories, uf_categories)
    validation_rows.append(_prediction_rows(rf_validation_frame, rf_validation, "Random Forest (amostra)"))
    test_rows.append(_prediction_rows(rf_test_frame, rf_test, "Random Forest (amostra)"))

    predictions = pd.concat(validation_rows + test_rows, ignore_index=True)
    metrics = pd.DataFrame(
        [_metrics(predictions.loc[predictions["model"].eq(model)], year)
         for model in predictions["model"].unique() for year in (2023, 2024, 2025)]
    )
    tuning_output = pd.DataFrame([
        {"family": family, "candidate": item["candidate"], "validation_wape": item["validation_wape"], "params": json.dumps(item["params"], sort_keys=True)}
        for family, results in (("LightGBM L1", tuning), ("LightGBM Poisson", poisson_tuning))
        for item in results
    ])
    metrics.to_csv(processed / "model_comparison_metrics.csv", index=False)
    tuning_output.to_csv(processed / "model_tuning_validation.csv", index=False)
    (processed / "model_comparison_summary.json").write_text(json.dumps({
        "run_seconds": round(time.time() - started, 1),
        "random_forest_train_sample_rows": RANDOM_FOREST_SAMPLE_SIZE,
        "tuned_lightgbm_candidate": best["candidate"],
        "tuned_lightgbm_params": best["params"],
        "tuned_poisson_candidate": best_poisson["candidate"],
        "tuned_poisson_params": best_poisson["params"],
        "selection_metric": "2023 validation WAPE",
        "test_years": [2024, 2025],
    }, indent=2))
    return metrics, tuning_output


if __name__ == "__main__":
    model_metrics, tuning_metrics = run_comparison()
    print("Comparação de modelos:")
    print(model_metrics.to_string(index=False))
    print("Busca de parâmetros (validação 2023):")
    print(tuning_metrics.to_string(index=False))
