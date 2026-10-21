"""NASA POWER municipal weather and IBGE spatial weights for the dengue panel."""

from __future__ import annotations

import json
import io
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
WEATHER_URL = "https://power.larc.nasa.gov/api/temporal/daily/regional"
WEATHER_START = "2014-12-28"
WEATHER_END = "2025-12-31"
WEATHER_PARAMETERS = {
    "PRECTOTCORR": "precipitation_mm",
    "T2M": "temperature_c",
    "RH2M": "relative_humidity_pct",
}


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
    batch_cache.mkdir(parents=True, exist_ok=True)
    locations = reference[["municipality_code", "latitude", "longitude"]].dropna()
    locations = locations.sort_values("municipality_code").reset_index(drop=True)
    locations["grid_latitude"] = (locations["latitude"] * 2).round() / 2
    locations["grid_longitude"] = (locations["longitude"] / 0.625).round() * 0.625

    lat_min = int(-35 + ((locations["latitude"].min() + 35) // 10) * 10)
    lat_max = int(-35 + ((locations["latitude"].max() + 35) // 10) * 10)
    lon_min = int(-75 + ((locations["longitude"].min() + 75) // 10) * 10)
    lon_max = int(-75 + ((locations["longitude"].max() + 75) // 10) * 10)
    years = range(pd.Timestamp(WEATHER_START).year, pd.Timestamp(WEATHER_END).year + 1)
    tiles = []
    for tile_lat in range(lat_min, lat_max + 1, 10):
        for tile_lon in range(lon_min, lon_max + 1, 10):
            cells = locations.loc[
                locations["grid_latitude"].ge(tile_lat) & locations["grid_latitude"].lt(tile_lat + 10)
                & locations["grid_longitude"].ge(tile_lon) & locations["grid_longitude"].lt(tile_lon + 10),
                ["grid_latitude", "grid_longitude"],
            ].drop_duplicates().rename(columns={"grid_latitude": "latitude", "grid_longitude": "longitude"})
            if not cells.empty:
                tiles.append((tile_lat, tile_lon, cells))
    cached: list[pd.DataFrame] = []
    total = len(tiles) * len(years) * len(WEATHER_PARAMETERS)
    completed = 0

    for tile_lat, tile_lon, cells in tiles:
        for year in years:
            start_date = max(pd.Timestamp(WEATHER_START), pd.Timestamp(year=year, month=1, day=1))
            end_date = min(pd.Timestamp(WEATHER_END), pd.Timestamp(year=year, month=12, day=31))
            for parameter in WEATHER_PARAMETERS:
                cache_path = batch_cache / f"{year}_lat{tile_lat}_lon{tile_lon}_{parameter}.csv.gz"
                if cache_path.exists():
                    weekly = pd.read_csv(cache_path, parse_dates=["week_start"])
                else:
                    params = {
                        "parameters": parameter,
                        "community": "AG",
                        "longitude-min": tile_lon,
                        "longitude-max": tile_lon + 10,
                        "latitude-min": tile_lat,
                        "latitude-max": tile_lat + 10,
                        "start": start_date.strftime("%Y%m%d"),
                        "end": end_date.strftime("%Y%m%d"),
                        "format": "CSV",
                        "time-standard": "UTC",
                    }
                    for attempt in range(5):
                        try:
                            response = requests.get(WEATHER_URL, params=params, timeout=(30, 180))
                            response.raise_for_status()
                            weekly = _parse_power_weather(response.text, parameter, tile_lat, tile_lon, cells)
                            _atomic_csv(weekly, cache_path)
                            time.sleep(1)
                            break
                        except requests.HTTPError as error:
                            response = getattr(error, "response", None)
                            status = getattr(response, "status_code", None)
                            if attempt == 4 or (status is not None and status not in (429, 500, 502, 503, 504)):
                                raise RuntimeError(f"NASA POWER rejeitou {parameter}, {year}, tile ({tile_lat}, {tile_lon}): {error}") from error
                            time.sleep(min(30 * (2 ** attempt), 300))
                        except requests.RequestException:
                            if attempt == 4:
                                raise
                            time.sleep(2 ** attempt)
                cached.append(weekly)
                completed += 1
                print(f"NASA POWER: {completed}/{total} blocos", flush=True)

    data = pd.concat(cached, ignore_index=True)
    data = data.groupby(["latitude", "longitude", "week_start", "parameter"], as_index=False).agg(
        value_sum=("value_sum", "sum"), day_count=("day_count", "sum"),
    )
    weather_frames = []
    for parameter, column in WEATHER_PARAMETERS.items():
        part = data.loc[data["parameter"].eq(parameter)].copy()
        part[column] = part["value_sum"] if parameter == "PRECTOTCORR" else part["value_sum"] / part["day_count"]
        weather_frames.append(part[["latitude", "longitude", "week_start", column]])
    weather = weather_frames[0]
    for part in weather_frames[1:]:
        weather = weather.merge(part, on=["latitude", "longitude", "week_start"], how="outer")

    locations = locations.merge(
        weather,
        left_on=["grid_latitude", "grid_longitude"],
        right_on=["latitude", "longitude"],
        how="left",
    )
    return locations[["municipality_code", "week_start", *WEATHER_PARAMETERS.values()]]


def _parse_power_weather(
    text: str, parameter: str, tile_lat: int, tile_lon: int, cells: pd.DataFrame,
) -> pd.DataFrame:
    csv_start = next(
        (index for index, line in enumerate(text.splitlines()) if line.startswith("LAT,LON,YEAR,DOY,")),
        None,
    )
    if csv_start is None:
        raise ValueError(f"NASA POWER não retornou CSV para {parameter}: {text[:300]}")
    daily = pd.read_csv(io.StringIO("\n".join(text.splitlines()[csv_start:])))
    daily = daily.rename(columns={"LAT": "latitude", "LON": "longitude"})
    daily["latitude"] = daily["latitude"].round(6)
    daily["longitude"] = daily["longitude"].round(6)
    daily = daily.loc[
        daily["latitude"].ge(tile_lat) & daily["latitude"].lt(tile_lat + 10)
        & daily["longitude"].ge(tile_lon) & daily["longitude"].lt(tile_lon + 10)
    ].copy()
    daily = daily.merge(cells, on=["latitude", "longitude"], how="inner")
    daily["value"] = pd.to_numeric(daily[parameter], errors="coerce").replace(-999, np.nan)
    daily = daily.dropna(subset=["value"])
    daily["date"] = pd.to_datetime(
        daily["YEAR"].astype(str) + daily["DOY"].astype(str).str.zfill(3), format="%Y%j",
    )
    daily["week_start"] = daily["date"] - pd.to_timedelta((daily["date"].dt.dayofweek + 1) % 7, unit="D")
    return daily.groupby(["latitude", "longitude", "week_start"], as_index=False).agg(
        value_sum=("value", "sum"), day_count=("value", "count"),
    ).assign(parameter=parameter)


def load_environmental_data(
    reference: pd.DataFrame,
    processed: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return municipal coordinates, weekly weather, and row-normalized Queen edges."""
    coords_path = processed / "ibge_municipal_geometry.csv"
    neighbors_path = processed / "municipal_neighbors.csv"
    weather_path = processed / "municipality_week_weather_nasa_power.csv.gz"
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
        weather = _download_weather(enriched_reference, processed / "weather_nasa_power_tiles")
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
