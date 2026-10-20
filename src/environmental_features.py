"""Municipal ERA5 weather and IBGE spatial weights for the dengue panel."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.sparse import csr_matrix
from shapely.geometry import shape
from shapely.strtree import STRtree


IBGE_MESH_URL = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
    "?intrarregiao=municipio&formato=application%2Fvnd.geo%2Bjson&qualidade=minima"
)
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_START = "2014-12-28"
WEATHER_END = "2025-12-31"
WEATHER_VARIABLES = [
    "precipitation_sum", "temperature_2m_mean", "relative_humidity_2m_mean",
]
WEATHER_BATCH_SIZE = 30


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        frame.to_csv(temporary, index=False, compression="gzip" if path.suffix == ".gz" else None)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _mesh(reference: pd.DataFrame, cache_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read cached IBGE GeoJSON or download it; return centroids and Queen edges."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        geojson = json.loads(cache_path.read_text())
    else:
        response = requests.get(IBGE_MESH_URL, timeout=(30, 180))
        response.raise_for_status()
        geojson = response.json()
        temporary = cache_path.with_suffix(cache_path.suffix + ".part")
        temporary.write_text(json.dumps(geojson))
        temporary.replace(cache_path)

    features = [
        (str(item["properties"]["codarea"])[:6], shape(item["geometry"]))
        for item in geojson["features"]
    ]
    geometry_by_code = dict(features)
    codes = sorted(set(reference["municipality_code"].astype(str)) & set(geometry_by_code))
    geometries = [geometry_by_code[code] for code in codes]
    points = [geometry.representative_point() for geometry in geometries]
    coordinates = pd.DataFrame({
        "municipality_code": codes,
        "longitude": [point.x for point in points],
        "latitude": [point.y for point in points],
    })

    tree = STRtree(geometries)
    pairs = tree.query(geometries, predicate="touches")
    rows = [(codes[int(source)], codes[int(target)]) for source, target in pairs.T if source != target]
    edge_frame = pd.DataFrame(rows, columns=["municipality_code", "neighbor_code"])
    if not edge_frame.empty:
        edge_frame = edge_frame.drop_duplicates()
        degree = edge_frame.groupby("municipality_code")["neighbor_code"].transform("count")
        edge_frame["weight"] = 1.0 / degree
    else:
        edge_frame["weight"] = pd.Series(dtype=float)
    return coordinates, edge_frame


def aggregate_daily_weather(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.rename(columns={
        "time": "date",
        "precipitation_sum": "precipitation_mm",
        "temperature_2m_mean": "temperature_c",
        "relative_humidity_2m_mean": "relative_humidity_pct",
    }).copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["week_start"] = daily["date"] - pd.to_timedelta((daily["date"].dt.dayofweek + 1) % 7, unit="D")
    return daily.groupby(["municipality_code", "week_start"], as_index=False).agg(
        precipitation_mm=("precipitation_mm", "sum"),
        temperature_c=("temperature_c", "mean"),
        relative_humidity_pct=("relative_humidity_pct", "mean"),
    )


def _download_weather(reference: pd.DataFrame, batch_cache: Path) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    batch_cache.mkdir(parents=True, exist_ok=True)
    locations = reference[["municipality_code", "latitude", "longitude"]].dropna()
    locations = locations.sort_values("municipality_code").reset_index(drop=True)
    for start in range(0, len(locations), WEATHER_BATCH_SIZE):
        batch = locations.iloc[start : start + WEATHER_BATCH_SIZE]
        cached_batch = batch_cache / f"batch_{start:05d}.csv.gz"
        if cached_batch.exists():
            rows.append(pd.read_csv(cached_batch, dtype={"municipality_code": str}, parse_dates=["week_start"]))
            continue
        params = {
            "latitude": ",".join(f"{value:.5f}" for value in batch["latitude"]),
            "longitude": ",".join(f"{value:.5f}" for value in batch["longitude"]),
            "start_date": WEATHER_START,
            "end_date": WEATHER_END,
            "daily": ",".join(WEATHER_VARIABLES),
            "timezone": "GMT",
            "models": "era5",
        }
        for attempt in range(5):
            try:
                response = requests.get(WEATHER_URL, params=params, timeout=(30, 180))
                response.raise_for_status()
                payload = response.json()
                break
            except requests.HTTPError as error:
                response = getattr(error, "response", None)
                status = getattr(response, "status_code", None)
                if status == 429:
                    reason = response.json().get("reason", "limite de requisições")
                    if attempt == 4:
                        raise RuntimeError(f"Open-Meteo manteve o limite de requisições: {reason}") from error
                    print(f"Open-Meteo: {reason} Aguardando 60 segundos.", flush=True)
                    time.sleep(60)
                elif status == 400:
                    raise RuntimeError(f"Open-Meteo rejeitou lote de clima: {response.text}") from error
                elif attempt == 4:
                    raise
                else:
                    time.sleep(2 ** attempt)
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        if isinstance(payload, dict):
            payload = [payload]
        batch_rows = []
        for location, result in zip(batch.itertuples(index=False), payload):
            daily = result.get("daily", {})
            if not daily.get("time"):
                continue
            frame = pd.DataFrame(daily)
            frame["municipality_code"] = location.municipality_code
            batch_rows.append(frame)
        batch_daily = pd.concat(batch_rows, ignore_index=True)
        weekly = aggregate_daily_weather(batch_daily)
        _atomic_csv(weekly, cached_batch)
        rows.append(weekly)
        print(f"Clima ERA5: {min(start + len(batch), len(locations)):,}/{len(locations):,} municípios", flush=True)
        time.sleep(10)

    return pd.concat(rows, ignore_index=True)


def load_environmental_data(
    reference: pd.DataFrame,
    processed: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return municipal coordinates, weekly weather, and row-normalized Queen edges."""
    coords_path = processed / "ibge_municipal_geometry.csv"
    neighbors_path = processed / "municipal_neighbors.csv"
    weather_path = processed / "municipality_week_weather.csv.gz"
    mesh_path = processed / "ibge_municipalities_current.geojson"

    if coords_path.exists() and neighbors_path.exists():
        coordinates = pd.read_csv(coords_path, dtype={"municipality_code": str})
        edges = pd.read_csv(neighbors_path, dtype={"municipality_code": str, "neighbor_code": str})
    else:
        coordinates, edges = _mesh(reference, mesh_path)
        _atomic_csv(coordinates, coords_path)
        _atomic_csv(edges, neighbors_path)

    enriched_reference = reference.merge(coordinates, on="municipality_code", how="left")
    if weather_path.exists():
        weather = pd.read_csv(weather_path, dtype={"municipality_code": str}, parse_dates=["week_start"])
    else:
        weather = _download_weather(enriched_reference, processed / "weather_batches")
        _atomic_csv(weather, weather_path)
    return enriched_reference, weather, edges


def spatial_lag(
    cases: np.ndarray,
    municipality_codes: list[str],
    week_count: int,
    edges: pd.DataFrame,
) -> np.ndarray:
    """Apply row-normalized Queen weights to municipality-by-week case counts."""
    code_index = {code: index for index, code in enumerate(municipality_codes)}
    edge_rows = edges[edges["municipality_code"].isin(code_index) & edges["neighbor_code"].isin(code_index)]
    matrix = csr_matrix(
        (
            edge_rows["weight"].to_numpy(dtype=np.float32),
            (
                edge_rows["municipality_code"].map(code_index).to_numpy(),
                edge_rows["neighbor_code"].map(code_index).to_numpy(),
            ),
        ),
        shape=(len(municipality_codes), len(municipality_codes)),
    )
    values = np.asarray(cases, dtype=np.float32).reshape(len(municipality_codes), week_count)
    result = matrix @ values
    isolated = np.asarray(matrix.sum(axis=1)).ravel() == 0
    result[isolated] = np.nan
    return np.asarray(result, dtype=np.float32).ravel()
