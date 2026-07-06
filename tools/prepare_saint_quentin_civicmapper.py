#!/usr/bin/env python3
"""Prepare Saint-Quentin for local CivicMapper-style geovizwiz testing.

This script reuses the existing OpenAVMKit Saint-Quentin pipeline outputs and
packages a browser-loadable GeoJSON with parcel geometry, metric parcel/building
fields, DVF sale fields, and model-derived value-per-area fields.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


DEFAULT_OPENAVMKIT_DATA_DIR = Path(
    "/home/roland/openavmkit/notebooks/pipeline/data/fr-02-saint_quentin"
)
DEFAULT_OUT_DIR = Path("local-data")
CADASTRE_URL = (
    "https://services1.arcgis.com/5nIW6mZeb2YNJ7np/ArcGIS/rest/services/"
    "SIG_CADASTRE/FeatureServer"
)
DVF_PAGE_URL = "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres"
DVF_API_URL = "https://www.data.gouv.fr/api/1/datasets/demandes-de-valeurs-foncieres/"
SQM_TO_SQFT = 10.763910416709722
M2_PER_ACRE = 4046.8564224


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "saint-quentin"


def output_filename(city: str, output_format: str) -> str:
    ext = "geojson" if output_format == "geojson" else output_format
    return f"{slugify(city)}-fr-parcels.{ext}"


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if hasattr(value, "item"):
            value = value.item()
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def safe_divide(numerator: Any, denominator: Any) -> float | None:
    num = to_float(numerator)
    den = to_float(denominator)
    if num is None or den is None or den == 0:
        return None
    return num / den


def derive_value_fields(row: dict[str, Any]) -> dict[str, Any]:
    land_area_sqm = row.get("land_area_sqm")
    land_area_sqft = row.get("land_area_sqft")
    return {
        "sale_price_per_sqm": safe_divide(row.get("sale_price"), land_area_sqm),
        "sale_price_per_sqft": safe_divide(row.get("sale_price"), land_area_sqft),
        "sale_price_time_adj_per_sqm": safe_divide(row.get("sale_price_time_adj"), land_area_sqm),
        "sale_price_time_adj_per_sqft": safe_divide(row.get("sale_price_time_adj"), land_area_sqft),
        "main_prediction_per_sqm": safe_divide(row.get("main_prediction"), land_area_sqm),
        "main_prediction_per_sqft": safe_divide(row.get("main_prediction"), land_area_sqft),
        "vacant_prediction_per_sqm": safe_divide(row.get("vacant_prediction"), land_area_sqm),
        "vacant_prediction_per_sqft": safe_divide(row.get("vacant_prediction"), land_area_sqft),
    }


def require_geo_stack():
    try:
        import geopandas as gpd
        import pandas as pd
    except ModuleNotFoundError as err:
        raise RuntimeError(
            "This script needs pandas/geopandas. Run it with the OpenAVMKit venv, e.g. "
            "/home/roland/openavmkit/venv/bin/python tools/prepare_saint_quentin_civicmapper.py"
        ) from err
    return gpd, pd


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Saint-Quentin, France for local CivicMapper-style geovizwiz use."
    )
    parser.add_argument("--city", default="Saint-Quentin")
    parser.add_argument("--openavmkit-data-dir", type=Path, default=DEFAULT_OPENAVMKIT_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--model", default="local_area", choices=("local_area", "naive_area", "lightgbm"))
    parser.add_argument(
        "--format",
        default="geojson",
        choices=("geojson",),
        help="Only GeoJSON is emitted for local browser loading.",
    )
    return parser.parse_args(argv)


def read_inputs(data_dir: Path, model: str):
    gpd, pd = require_geo_stack()
    parcels_path = data_dir / "in" / "parcels.parquet"
    sales_path = data_dir / "in" / "sales.parquet"
    main_universe_path = data_dir / "out" / "models" / "all" / "main" / model / "pred_universe.parquet"
    vacant_universe_path = data_dir / "out" / "models" / "all" / "vacant" / model / "pred_universe.parquet"
    main_sales_path = data_dir / "out" / "models" / "all" / "main" / model / "pred_sales.parquet"

    required = [parcels_path, sales_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(
            "Missing OpenAVMKit Saint-Quentin inputs. Run "
            "`/home/roland/openavmkit/venv/bin/python scripts/prepare_saint_quentin.py "
            "--years 2021 2022 2023 2024 2025` in /home/roland/openavmkit first. "
            f"Missing: {', '.join(missing)}"
        )

    parcels = gpd.read_parquet(parcels_path)
    sales = pd.read_parquet(sales_path)
    main_universe = pd.read_parquet(main_universe_path) if main_universe_path.exists() else None
    vacant_universe = pd.read_parquet(vacant_universe_path) if vacant_universe_path.exists() else None
    main_sales = pd.read_parquet(main_sales_path) if main_sales_path.exists() else None
    return parcels, sales, main_universe, vacant_universe, main_sales


def latest_sales_frame(sales, main_sales):
    _, pd = require_geo_stack()
    sales = sales.copy()
    sales["sale_date_dt"] = pd.to_datetime(sales["sale_date"], errors="coerce")
    sales["sale_count"] = sales.groupby("key")["key_sale"].transform("count")
    latest = (
        sales.sort_values(["key", "sale_date_dt", "key_sale"])
        .groupby("key", as_index=False)
        .tail(1)
        .drop(columns=["sale_date_dt"])
    )

    if main_sales is not None and "sale_price_time_adj" in main_sales.columns:
        adj = main_sales[["key_sale", "sale_price_time_adj"]].copy()
        latest = latest.merge(adj, on="key_sale", how="left")
    else:
        latest["sale_price_time_adj"] = None

    latest = latest.rename(columns={"key_sale": "sale_id"})
    keep = [
        "key",
        "sale_id",
        "sale_date",
        "sale_price",
        "sale_price_time_adj",
        "sale_nature",
        "property_type",
        "sale_land_area_sqm",
        "valid_sale",
        "vacant_sale",
        "valid_for_ratio_study",
        "valid_for_land_ratio_study",
        "sale_count",
    ]
    return latest[[col for col in keep if col in latest.columns]]


def prediction_frame(frame, prefix: str):
    if frame is None:
        return None
    cols = ["key", "prediction", "prediction_impr_sqm", "prediction_land_sqm"]
    cols = [col for col in cols if col in frame.columns]
    out = frame[cols].copy()
    rename = {
        "prediction": f"{prefix}_prediction",
        "prediction_impr_sqm": f"{prefix}_prediction_impr_sqm",
        "prediction_land_sqm": f"{prefix}_prediction_land_sqm",
    }
    return out.rename(columns=rename)


def clean_for_json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def build_dataset(data_dir: Path, model: str):
    parcels, sales, main_universe, vacant_universe, main_sales = read_inputs(data_dir, model)
    latest = latest_sales_frame(sales, main_sales)
    out = parcels.merge(latest, on="key", how="left")

    for frame in [
        prediction_frame(main_universe, "main"),
        prediction_frame(vacant_universe, "vacant"),
    ]:
        if frame is not None:
            out = out.merge(frame, on="key", how="left")

    out["parcel_id"] = out["key"]
    out["address"] = out["codeident"]
    out["land_area_acres"] = out["land_area_sqm"] / M2_PER_ACRE

    for col in ["sale_price", "sale_price_time_adj", "main_prediction", "vacant_prediction"]:
        if col in out.columns:
            out[col] = out[col].astype("float64")

    derived_rows = [derive_value_fields(row) for row in out.drop(columns="geometry").to_dict("records")]
    for key in derived_rows[0].keys() if derived_rows else []:
        out[key] = [row[key] for row in derived_rows]

    return out


def write_geojson(gdf, out_path: Path, city: str, model: str) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = json.loads(gdf.to_json(drop_id=True))
    fc["name"] = f"{city}, France parcels for local CivicMapper-style visualization"
    fc["source"] = {
        "cadastre": {
            "publisher": "Saint-Quentin / ArcGIS FeatureServer",
            "url": CADASTRE_URL,
            "layers": "Cadastre parcels and buildings",
        },
        "dvf": {
            "publisher": "Direction générale des Finances publiques via data.gouv.fr",
            "page": DVF_PAGE_URL,
            "api": DVF_API_URL,
        },
        "openavmkit_reference": "/home/roland/openavmkit/scripts/prepare_saint_quentin.py",
        "model": model,
        "caveats": [
            "DVF records are sales, not official assessed land values.",
            "Sale values may represent multi-parcel or partial-property transactions.",
            "Per-area fields divide sale or model values by parcel area and should be treated as exploratory.",
            "Model prediction fields come from the local OpenAVMKit Saint-Quentin pipeline outputs when present.",
        ],
    }
    for feature in fc["features"]:
        props = feature.get("properties") or {}
        feature["id"] = props.get("parcel_id")
        feature["properties"] = {key: clean_for_json(value) for key, value in props.items()}

    out_path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(fc["features"])


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        gdf = build_dataset(args.openavmkit_data_dir, args.model)
        out_path = args.out_dir / output_filename(args.city, args.format)
        count = write_geojson(gdf, out_path, args.city, args.model)
    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(f"Wrote {count} features")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
