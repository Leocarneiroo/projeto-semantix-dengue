#!/usr/bin/env python3
"""Reproducible Brazil dengue early-warning analysis from official public data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from sklearn.metrics import mean_absolute_error, mean_squared_error

from src import environmental_features


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
FIGURES = ROOT / "figures"
YEARS = list(range(2015, 2026))
SINAN_URL = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SINAN/Dengue/csv/DENGBR{yy}.csv.zip"
IBGE_MUNICIPIOS_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?orderBy=nome"
IBGE_POPULATION_URL = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/4714/periodos/2022/"
    "variaveis/93?localidades=N6%5Ball%5D"
)
UF_BY_CODE = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
    "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        frame.to_csv(tmp, index=False, compression="gzip" if path.suffix == ".gz" else None)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json_atomic(data: dict[str, object], path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_source_metadata(
    sources: dict[int, Path], downloaded_at: dict[int, str], manifest_path: Path,
    run_at: str | None = None,
) -> dict[str, object]:
    try:
        previous = json.loads(manifest_path.read_text())
        previous_sources = {
            item["year"]: item for item in previous.get("sources", [])
        } if isinstance(previous, dict) else {}
    except (OSError, ValueError, TypeError, KeyError):
        previous_sources = {}

    entries = []
    for year, path in sorted(sources.items()):
        checksum = file_sha256(path)
        old = previous_sources.get(year, {})
        downloaded = downloaded_at.get(year)
        if old.get("sha256") == checksum and downloaded is None:
            downloaded = old.get("downloaded_at")
        entries.append({"year": year, "file": path.name, "sha256": checksum, "downloaded_at": downloaded})
    metadata = {"pipeline_run_at": run_at or utc_now(), "sources": entries}
    write_json_atomic(metadata, manifest_path)
    return metadata


def fetch(url: str, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        for attempt in range(3):
            try:
                with requests.get(url, stream=True, timeout=(30, 180)) as response:
                    response.raise_for_status()
                    with tmp.open("wb") as out:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                out.write(chunk)
                tmp.replace(path)
                return utc_now()
            except requests.RequestException as error:
                response = getattr(error, "response", None)
                status = getattr(response, "status_code", None)
                retryable = not isinstance(error, requests.HTTPError) or status is None or status == 429 or status >= 500
                if attempt == 2 or not retryable:
                    raise
                time.sleep(2 ** attempt)
    finally:
        tmp.unlink(missing_ok=True)
    raise RuntimeError("Download retry loop ended unexpectedly")


def municipality_reference() -> pd.DataFrame:
    cache = PROCESSED / "ibge_municipalities_2022.csv"
    if cache.exists():
        return pd.read_csv(cache, dtype={"municipality_code": str})

    municipalities = requests.get(IBGE_MUNICIPIOS_URL, timeout=90)
    municipalities.raise_for_status()
    population = requests.get(IBGE_POPULATION_URL, timeout=90)
    population.raise_for_status()
    pop_json = population.json()[0]["resultados"][0]["series"]
    pop_by_code = {
        str(item["localidade"]["id"])[:6]: int(item["serie"]["2022"])
        for item in pop_json
        if item.get("serie", {}).get("2022") not in (None, "-")
    }

    rows = []
    for item in municipalities.json():
        code = str(item["id"])[:6]
        uf = UF_BY_CODE[code[:2]]
        rows.append(
            {
                "municipality_code": code,
                "municipality": item["nome"],
                "uf": uf,
                "population_2022": pop_by_code.get(code),
            }
        )
    reference = pd.DataFrame(rows).drop_duplicates("municipality_code")
    cache.parent.mkdir(parents=True, exist_ok=True)
    write_csv_atomic(reference, cache)
    return reference


def load_year(year: int) -> tuple[pd.DataFrame, dict[str, object]]:
    yy = str(year)[-2:]
    path = RAW / f"DENGBR{yy}.csv.zip"
    source_downloaded_at = None
    if not path.exists():
        print(f"Baixando SINAN {year}...", flush=True)
        source_downloaded_at = fetch(SINAN_URL.format(yy=yy), path)

    with zipfile.ZipFile(path) as archive:
        csv_name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(csv_name) as csv_file:
            header = pd.read_csv(csv_file, nrows=0, encoding="latin1").columns.tolist()

    # Ler só os campos usados para definir e contar as notificações do estudo.
    wanted = ["DT_SIN_PRI", "ID_MN_RESI", "CLASSI_FIN", "NDUPLIC_N"]
    missing = [name for name in wanted[:3] if name not in header]
    if missing:
        raise ValueError(f"SINAN {year}: campos obrigatórios ausentes: {missing}")
    usecols = [name for name in wanted if name in header]
    aggregates = []
    qc = {
        "year": year,
        "rows_read": 0,
        "discarded": 0,
        "non_dengue_classification": 0,
        "duplicate_flagged": 0,
        "invalid_onset_or_residence": 0,
        "onset_year_mismatch": 0,
        "included_probable_notifications": 0,
        "downloaded_at": source_downloaded_at,
    }

    with zipfile.ZipFile(path) as archive:
        with archive.open(csv_name) as csv_file:
            chunks = pd.read_csv(
                csv_file,
                usecols=usecols,
                dtype=str,
                encoding="latin1",
                chunksize=250_000,
                low_memory=False,
            )
            for chunk in chunks:
                qc["rows_read"] += len(chunk)
                classification = chunk["CLASSI_FIN"].fillna("").str.strip()
                discarded = classification.eq("5")
                other_disease = classification.eq("13")
                if "NDUPLIC_N" in chunk:
                    duplicate_marker = pd.to_numeric(chunk["NDUPLIC_N"], errors="coerce").fillna(0).gt(0)
                else:
                    duplicate_marker = pd.Series(False, index=chunk.index)
                # Regra operacional: excluir descartados, chikungunya, duplicatas sinalizadas,
                # datas/códigos inválidos e datas cujo ano não corresponde ao arquivo.
                qc["discarded"] += int(discarded.sum())
                qc["non_dengue_classification"] += int(other_disease.sum())
                qc["duplicate_flagged"] += int(duplicate_marker.sum())

                dates = pd.to_datetime(chunk["DT_SIN_PRI"], errors="coerce")
                # Usar município de residência; os códigos do SINAN e do IBGE compartilham seis dígitos.
                codes = chunk["ID_MN_RESI"].fillna("").str.replace(r"\D", "", regex=True).str.zfill(6).str[-6:]
                wrong_year = dates.notna() & dates.dt.year.ne(year)
                invalid = dates.isna() | codes.eq("") | codes.eq("000000")
                qc["onset_year_mismatch"] += int(wrong_year.sum())
                qc["invalid_onset_or_residence"] += int(invalid.sum())

                keep = ~(discarded | other_disease | duplicate_marker | invalid | wrong_year)
                if not keep.any():
                    continue
                dates = dates.loc[keep]
                codes = codes.loc[keep]
                # A semana epidemiológica começa no domingo, como no painel de features abaixo.
                week_start = dates - pd.to_timedelta((dates.dt.dayofweek + 1) % 7, unit="D")
                grouped = pd.DataFrame(
                    {"municipality_code": codes.to_numpy(), "week_start": week_start.to_numpy()}
                ).groupby(["municipality_code", "week_start"], as_index=False).size()
                grouped.rename(columns={"size": "cases"}, inplace=True)
                grouped["onset_year"] = year
                aggregates.append(grouped)
                qc["included_probable_notifications"] += int(keep.sum())

    if not aggregates:
        return pd.DataFrame(columns=["municipality_code", "week_start", "cases"]), qc
    result = pd.concat(aggregates, ignore_index=True)
    result["week_start"] = pd.to_datetime(result["week_start"])
    result = result.groupby(["municipality_code", "week_start", "onset_year"], as_index=False)["cases"].sum()
    return result, qc


def load_and_prepare_counts(run_at: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    reference = municipality_reference()
    annual_parts = []
    quality: list[dict[str, object]] = []
    downloaded_at = {}
    sources = {}
    for year in YEARS:
        part, qc = load_year(year)
        annual_parts.append(part)
        quality.append(qc)
        sources[year] = RAW / f"DENGBR{str(year)[-2:]}.csv.zip"
        if qc["downloaded_at"]:
            downloaded_at[year] = qc["downloaded_at"]
        print(f"SINAN {year}: {qc['rows_read']:,} registros; {qc['included_probable_notifications']:,} incluídos", flush=True)

    counts = pd.concat(annual_parts, ignore_index=True)
    counts["week_start"] = pd.to_datetime(counts["week_start"])
    counts = counts.groupby(["municipality_code", "week_start", "onset_year"], as_index=False)["cases"].sum()
    counts = counts[counts["week_start"].between("2014-12-28", "2025-12-28")].copy()
    # Excluir códigos sem correspondência no IBGE, sem atribuí-los a um município desconhecido.
    valid_codes = set(reference["municipality_code"].astype(str))
    unmatched_cases = int(counts.loc[~counts["municipality_code"].isin(valid_codes), "cases"].sum())
    counts = counts[counts["municipality_code"].isin(valid_codes)].copy()
    counts["uf"] = counts["municipality_code"].str[:2].map(UF_BY_CODE)
    # ES mudou o sistema de notificação; excluir registros SINAN posteriores para evitar falsos zeros.
    es_cases = int(counts.loc[counts["uf"].eq("ES") & counts["onset_year"].ge(2020), "cases"].sum())
    counts = counts[~(counts["uf"].eq("ES") & counts["onset_year"].ge(2020))].copy()
    counts = counts.drop(columns="uf").merge(reference, on="municipality_code", how="left")
    counts["uf"] = counts["uf"].fillna(counts["municipality_code"].str[:2].map(UF_BY_CODE))
    write_csv_atomic(counts, PROCESSED / "municipality_week_cases.csv.gz")
    write_csv_atomic(pd.DataFrame(quality), PROCESSED / "data_quality_by_year.csv")
    metadata = write_source_metadata(sources, downloaded_at, PROCESSED / "source_metadata.json", run_at=run_at)
    exclusions = {"unmatched_municipality_cases": unmatched_cases, "es_incomplete_cases": es_cases}
    return counts, reference, {**metadata, **exclusions}


def build_feature_panel(
    counts: pd.DataFrame,
    reference: pd.DataFrame,
    weather: pd.DataFrame | None = None,
    neighbors: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    municipality_codes = sorted(set(reference["municipality_code"].astype(str)) | set(counts["municipality_code"].astype(str)))
    weeks = pd.date_range("2014-12-28", "2025-12-21", freq="W-SUN")
    panel = pd.MultiIndex.from_product(
        [municipality_codes, weeks], names=["municipality_code", "week_start"]
    ).to_frame(index=False)
    panel["week_start"] = pd.to_datetime(panel["week_start"])
    panel_counts = counts.groupby(["municipality_code", "week_start"], as_index=False)["cases"].sum()
    # Zero explícito significa nenhuma notificação registrada, não ausência de infecções.
    panel = panel.merge(panel_counts, on=["municipality_code", "week_start"], how="left")
    panel["cases"] = panel["cases"].fillna(0).astype(np.float32)
    panel["uf"] = panel["municipality_code"].str[:2].map(UF_BY_CODE).fillna("NA")
    group = panel.groupby("municipality_code", sort=False)["cases"]
    # Cada linha representa uma semana observada; o alvo são as notificações da semana seguinte.
    panel["target"] = group.shift(-1)
    # lag_51 corresponde à mesma semana-alvo do ano anterior (o alvo está uma semana à frente).
    for lag in (0, 1, 2, 3, 4, 13, 51, 52):
        panel[f"lag_{lag}"] = group.shift(lag).astype(np.float32)
    panel["mean_4w"] = panel[["lag_0", "lag_1", "lag_2", "lag_3"]].mean(axis=1)
    panel["target_week"] = panel["week_start"] + pd.Timedelta(days=7)
    panel["target_week_end"] = panel["target_week"] + pd.Timedelta(days=6)
    panel["target_year"] = (panel["target_week"] + pd.Timedelta(days=4)).dt.year.astype(np.int16)
    target_week_number = panel["target_week"].dt.strftime("%U").astype(int).clip(upper=52)
    panel["week_sin"] = np.sin(2 * np.pi * target_week_number / 52).astype(np.float32)
    panel["week_cos"] = np.cos(2 * np.pi * target_week_number / 52).astype(np.float32)
    if neighbors is not None:
        panel["spatial_lag_cases"] = environmental_features.spatial_lag(
            panel["cases"].to_numpy(), municipality_codes, len(weeks), neighbors
        )
    if weather is not None:
        panel = panel.merge(weather, on=["municipality_code", "week_start"], how="left")
    panel = panel[panel["target_week_end"].le(pd.Timestamp("2025-12-31"))]
    panel = panel[~((panel["uf"].eq("ES")) & (panel["target_year"].ge(2020)))].copy()
    # Exigir alvo conhecido e histórico suficiente para a feature sazonal usada pelo modelo.
    panel = panel.dropna(subset=["target", "lag_51"]).copy()
    panel["target"] = panel["target"].astype(np.float32)
    panel["municipality_code"] = panel["municipality_code"].astype("category")
    panel["uf"] = panel["uf"].astype("category")
    features = [
        "municipality_code", "uf", "lag_0", "lag_1", "lag_2", "lag_3", "lag_4",
        "lag_13", "lag_51", "lag_52", "mean_4w", "week_sin", "week_cos",
    ]
    if weather is not None:
        features.extend([
            "precipitation_mm", "temperature_c", "relative_humidity_pct",
        ])
    if neighbors is not None:
        features.append("spatial_lag_cases")
    return panel, features


def train_and_predict(panel: pd.DataFrame, features: list[str], params: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # Manter anos completos em ordem temporal: 2023 valida; 2024–25 ficam reservados para teste.
    train = panel[panel["target_year"].le(2022)]
    validation = panel[panel["target_year"].eq(2023)]
    test = panel[panel["target_year"].isin([2024, 2025])]
    print(f"Treinando validação temporal até 2022: {len(train):,} linhas", flush=True)
    model = lgb.LGBMRegressor(**params)
    # log1p reduz o peso de contagens municipais extremas; a previsão volta à escala original abaixo.
    model.fit(train[features], np.log1p(train["target"]), categorical_feature=["municipality_code", "uf"])

    def predict(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame[["municipality_code", "uf", "target_week", "target_year", "target", "lag_0", "lag_51"]].copy()
        out["prediction"] = np.maximum(0, np.expm1(model.predict(frame[features])))
        out["pred_last_week"] = frame["lag_0"].to_numpy()
        out["pred_seasonal"] = frame["lag_51"].to_numpy()
        return out

    validation_predictions = predict(validation)
    validation_predictions["municipality_code"] = validation_predictions["municipality_code"].astype(str)
    del model
    # Retreinar até 2023 depois de calcular as previsões de validação.
    final_train = panel[panel["target_year"].le(2023)]
    print(f"Treinando modelo final até 2023: {len(final_train):,} linhas; teste 2024–2025: {len(test):,}", flush=True)
    model = lgb.LGBMRegressor(**params)
    model.fit(final_train[features], np.log1p(final_train["target"]), categorical_feature=["municipality_code", "uf"])
    test_predictions = predict(test)
    test_predictions["municipality_code"] = test_predictions["municipality_code"].astype(str)
    metric_output = []
    for year in (2023, 2024, 2025):
        frame = validation_predictions if year == 2023 else test_predictions
        metric_output.extend(metric_rows(frame[frame["target_year"].eq(year)], str(year)))
    metrics = pd.DataFrame(metric_output)
    capture = top_decile_capture(test_predictions)
    return validation_predictions, test_predictions, metrics, capture


def metric_rows(frame: pd.DataFrame, period: str) -> list[dict[str, object]]:
    rows = []
    for prediction_column, model_name in [
        ("pred_last_week", "Última semana"),
        ("pred_seasonal", "Mesmo período há 52 semanas"),
        ("prediction", "LightGBM"),
    ]:
        actual = frame["target"].to_numpy(dtype=float)
        prediction = np.maximum(0, frame[prediction_column].to_numpy(dtype=float))
        rows.append(
            {
                "period": period,
                "model": model_name,
                "rows": int(len(frame)),
                "actual_cases": int(actual.sum()),
                "mae": float(mean_absolute_error(actual, prediction)),
                "rmse": float(mean_squared_error(actual, prediction) ** 0.5),
                "wape": float(np.abs(actual - prediction).sum() / max(1.0, actual.sum())),
            }
        )
    return rows


def top_decile_capture(frame: pd.DataFrame) -> pd.DataFrame:
    # Mede priorização retrospectiva; não prova previsão antecipada de surtos.
    rows = []
    for year, year_frame in frame.groupby("target_year"):
        for prediction_column, model_name in [
            ("pred_last_week", "Última semana"),
            ("pred_seasonal", "Mesmo período há 52 semanas"),
            ("prediction", "LightGBM"),
        ]:
            captured = total = 0.0
            for _, week in year_frame.groupby("target_week"):
                total += float(week["target"].sum())
                n_alerts = max(1, int(np.ceil(len(week) * 0.10)))
                selected = week.nlargest(n_alerts, prediction_column)
                captured += float(selected["target"].sum())
            rows.append(
                {
                    "year": int(year),
                    "model": model_name,
                    "alert_share": 0.10,
                    "case_capture": captured / max(total, 1.0),
                }
            )
    return pd.DataFrame(rows)


def save_figures(
    counts: pd.DataFrame,
    reference: pd.DataFrame,
    predictions: pd.DataFrame,
    metrics: pd.DataFrame,
    capture: pd.DataFrame,
) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    # As figuras também são exibidas em telas menores; aumentar a tipografia
    # mantém títulos, eixos, marcas e legendas legíveis depois da redução.
    plt.rcParams.update(
        {
            "font.size": 13,
            "axes.titlesize": 18,
            "axes.labelsize": 14,
            "xtick.labelsize": 12,
            "ytick.labelsize": 12,
            "legend.fontsize": 12,
            "legend.title_fontsize": 12,
        }
    )
    counts = counts.copy()
    national = counts.groupby("week_start", as_index=False)["cases"].sum().sort_values("week_start")
    national["rolling_4w"] = national["cases"].rolling(4, min_periods=1).mean()
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(national["week_start"], national["rolling_4w"], color="#087E8B", linewidth=2.4)
    ax.set(title="Dengue no Brasil: notificações prováveis por semana de início dos sintomas", xlabel="Semana", ylabel="Casos (média móvel de 4 semanas)")
    ax.ticklabel_format(axis="y", style="plain")
    fig.tight_layout()
    fig.savefig(FIGURES / "serie_nacional.png", dpi=180)
    plt.close(fig)

    annual = counts.groupby("onset_year", as_index=False)["cases"].sum()
    fig, ax = plt.subplots(figsize=(13, 7))
    colors = ["#F26419" if year in (2024, 2025) else "#2E86AB" for year in annual["onset_year"]]
    ax.bar(annual["onset_year"].astype(str), annual["cases"], color=colors)
    ax.set(title="Notificações prováveis por ano epidemiológico", xlabel="Ano", ylabel="Casos")
    ax.ticklabel_format(axis="y", style="plain")
    ax.tick_params(axis="x", rotation=0)
    for bar, cases in zip(ax.patches, annual["cases"]):
        ax.annotate(
            f"{cases / 1_000_000:.2f} mi",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 7), textcoords="offset points", ha="center", fontsize=11,
        )
    fig.tight_layout()
    fig.savefig(FIGURES / "casos_por_ano.png", dpi=180)
    plt.close(fig)

    recent = counts[counts["onset_year"].eq(2024)].copy()
    recent = recent.groupby(["municipality_code", "municipality", "uf", "population_2022"], as_index=False)["cases"].sum()
    recent = recent[recent["population_2022"].fillna(0).ge(20_000)].copy()
    recent["incidence_per_100k_2022"] = recent["cases"] / recent["population_2022"] * 100_000
    top = recent.nlargest(15, "incidence_per_100k_2022").sort_values("incidence_per_100k_2022")
    fig, ax = plt.subplots(figsize=(13, 9))
    ax.barh(top["municipality"] + " (" + top["uf"] + ")", top["incidence_per_100k_2022"], color="#F26419")
    ax.set(title="15 maiores incidências municipais em 2024\n(população do Censo 2022; municípios com ≥20 mil habitantes)", xlabel="Casos por 100 mil habitantes")
    fig.tight_layout()
    fig.savefig(FIGURES / "incidencia_municipios_2024.png", dpi=180)
    plt.close(fig)

    test = predictions[predictions["target_year"].isin([2024, 2025])].copy()
    test["target_week"] = pd.to_datetime(test["target_week"])
    weekly = test.groupby(["target_week", "target_year"], as_index=False)[["target", "prediction", "pred_last_week", "pred_seasonal"]].sum()
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(weekly["target_week"], weekly["target"], label="Observado", color="#222222", linewidth=2.4)
    ax.plot(weekly["target_week"], weekly["prediction"], label="LightGBM", color="#F26419", alpha=0.9)
    ax.plot(weekly["target_week"], weekly["pred_last_week"], label="Última semana", color="#2E86AB", alpha=0.85)
    ax.plot(weekly["target_week"], weekly["pred_seasonal"], label="Mesmo período há 52 semanas", color="#9C89B8", alpha=0.8)
    ax.set(title="Previsão semanal agregada: teste temporal 2024–2025", xlabel="Semana de destino", ylabel="Casos prováveis")
    ax.ticklabel_format(axis="y", style="plain")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.legend(frameon=False, ncol=3, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIGURES / "teste_previsao_semanal.png", dpi=180)
    plt.close(fig)

    metrics = metrics.copy()
    metrics["period"] = metrics["period"].astype(str)
    test_metrics = metrics[
        metrics["period"].isin(["2024", "2025"])
        & metrics["model"].isin(["Última semana", "LightGBM"])
    ].copy()
    fig, ax = plt.subplots(figsize=(12, 7))
    order = ["Última semana", "LightGBM"]
    piv = test_metrics.pivot(index="period", columns="model", values="wape")[order] * 100
    piv.plot(kind="bar", ax=ax, color=["#2E86AB", "#F26419"])
    for container in ax.containers:
        ax.bar_label(container, fmt="%.1f%%", padding=5, fontsize=12)
    ax.set(title="Previsão de casos: LightGBM versus persistência", xlabel="Ano de teste", ylabel="WAPE (%) — menor é melhor")
    ax.legend(title="Modelo", frameon=False)
    ax.tick_params(axis="x", rotation=0)
    fig.tight_layout()
    fig.savefig(FIGURES / "metricas_teste.png", dpi=180)
    plt.close(fig)

    capture_plot = capture.copy()
    capture_plot["case_capture"] *= 100
    fig, ax = plt.subplots(figsize=(12, 7))
    capture_pivot = capture_plot.pivot(index="year", columns="model", values="case_capture")
    capture_pivot[["Última semana", "LightGBM"]].plot(kind="bar", ax=ax, color=["#2E86AB", "#F26419"])
    for container in ax.containers:
        ax.bar_label(container, fmt="%.1f%%", padding=4, fontsize=12)
    ax.set(title="Casos capturados ao alertar 10% dos municípios por semana", xlabel="Ano de teste", ylabel="Casos observados capturados (%)")
    ax.set_ylim(0, 100)
    ax.legend(title="Ordenação por previsão", frameon=False)
    ax.tick_params(axis="x", rotation=0)
    fig.tight_layout()
    fig.savefig(FIGURES / "captura_top_10pct.png", dpi=180)
    plt.close(fig)


def main() -> None:
    started = time.time()
    started_at = utc_now()
    for directory in (RAW, PROCESSED, FIGURES):
        directory.mkdir(parents=True, exist_ok=True)

    params = {
        "objective": "regression_l1",
        "n_estimators": 200,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_bin": 63,
        "min_child_samples": 40,
        "reg_lambda": 2.0,
        "n_jobs": min(8, os.cpu_count() or 1),
        "verbosity": -1,
        "random_state": 42,
    }
    counts, reference, metadata = load_and_prepare_counts(run_at=started_at)
    reference, weather, neighbors = environmental_features.load_environmental_data(reference, PROCESSED)
    panel, features = build_feature_panel(counts, reference, weather, neighbors)
    validation_predictions, test_predictions, metrics, capture = train_and_predict(panel, features, params)
    write_csv_atomic(validation_predictions, PROCESSED / "validation_2023_predictions.csv.gz")
    write_csv_atomic(test_predictions, PROCESSED / "test_2024_2025_predictions.csv.gz")
    write_csv_atomic(metrics, PROCESSED / "model_metrics.csv")
    write_csv_atomic(capture, PROCESSED / "top_decile_case_capture.csv")
    save_figures(counts, reference, test_predictions, metrics, capture)

    annual_cases = counts.groupby("onset_year")["cases"].sum()
    summary = {
        "pipeline_run_at": metadata["pipeline_run_at"],
        "source_metadata_file": "source_metadata.json",
        "years": YEARS,
        "municipalities_in_ibge_reference": int(reference["municipality_code"].nunique()),
        "municipalities_with_cases": int(counts["municipality_code"].nunique()),
        "total_probable_notifications": int(counts["cases"].sum()),
        "unmatched_municipality_cases_excluded": metadata["unmatched_municipality_cases"],
        "es_cases_excluded_from_2020_onward_due_to_source_transition": metadata["es_incomplete_cases"],
        "annual_cases": {str(int(year)): int(value) for year, value in annual_cases.items()},
        "validation_year": 2023,
        "test_years": [2024, 2025],
        "features": features,
        "weather_source": "NASA POWER Daily API (MERRA-2/POWER)",
        "weather_municipalities": int(weather["municipality_code"].nunique()),
        "weather_missing_panel_rows": int(panel["relative_humidity_pct"].isna().sum()),
        "spatial_weights": "IBGE municipality Queen contiguity, row-normalized",
        "model": params,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    write_json_atomic(summary, PROCESSED / "analysis_summary.json")
    print("Métricas:")
    print(metrics.to_string(index=False))
    print(f"Concluído em {summary['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
