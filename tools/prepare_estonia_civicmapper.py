#!/usr/bin/env python3
"""Prepare an Estonia parcel dataset for local CivicMapper-style testing.

The script uses the official Estonia cadastral GPKG zip and derives the field
names expected by the CivicMapper visualizer family. Conversion is delegated to
GDAL's ogr2ogr when available; GeoJSON output also has a dependency-free
GeoPackage reader for local proof runs.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path


SOURCE_PAGE_URL = "https://geoportaal.maaamet.ee/eng/spatial-data/cadastral-data-p310.html"
SOURCE_PUBLISHER = "Republic of Estonia Land and Spatial Development Board / Maa- ja Ruumiamet"
DEFAULT_SOURCE_URL = "https://s3.pilw.io/rp-kemit-kataster/ANDMED/Eesti_KATASTER_GPKG.zip"
DEFAULT_OUT_DIR = Path("local-data")
COUNTRY_CODE = "ee"
M2_PER_ACRE = 4046.8564224
SQFT_PER_SQM = 10.76391041671


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "estonia"


def output_filename(municipality: str, output_format: str) -> str:
    ext = "parquet" if output_format == "parquet" else "geojson"
    return f"{slugify(municipality)}-{COUNTRY_CODE}-parcels.{ext}"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_select_sql(layer_name: str, municipality: str | None) -> str:
    layer = quote_identifier(layer_name)
    municipality_clause = ""
    if municipality:
        pattern = f"%{municipality}%"
        municipality_clause = (
            "\nWHERE lower(coalesce(\"ov_nimi\", \"ay_nimi\", \"l_aadress\", '')) "
            f"LIKE lower({quote_literal(pattern)})"
        )

    return f"""SELECT
  *,
  "tunnus" AS parcel_id,
  "l_aadress" AS address,
  "pindala" AS land_area_sqm,
  "pindala" / {M2_PER_ACRE} AS land_area_acres,
  "maks_hind" AS REALLANDVA,
  "maks_hind" / NULLIF("pindala", 0) AS land_value_per_sqm,
  "maks_hind" / NULLIF("pindala" * {SQFT_PER_SQM}, 0) AS REALLANDVA_per_sqft,
  "maks_hind" / NULLIF("pindala" * {SQFT_PER_SQM}, 0) AS land_value_per_sqft
FROM {layer}{municipality_clause}"""


def derive_properties(row: dict[str, object]) -> dict[str, object]:
    area_sqm = to_float(row.get("pindala"))
    assessed_value = to_float(row.get("maks_hind"))
    area_sqft = area_sqm * SQFT_PER_SQM if area_sqm and area_sqm > 0 else None
    value_per_sqm = assessed_value / area_sqm if assessed_value is not None and area_sqm and area_sqm > 0 else None
    value_per_sqft = assessed_value / area_sqft if assessed_value is not None and area_sqft else None

    return {
        **row,
        "parcel_id": row.get("tunnus"),
        "address": row.get("l_aadress"),
        "land_area_sqm": area_sqm,
        "land_area_acres": area_sqm / M2_PER_ACRE if area_sqm is not None else None,
        "REALLANDVA": assessed_value,
        "land_value_per_sqm": value_per_sqm,
        "REALLANDVA_per_sqft": value_per_sqft,
        "land_value_per_sqft": value_per_sqft,
    }


def to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def find_gpkg_member(names: list[str]) -> str:
    gpkg_members = sorted(name for name in names if name.lower().endswith(".gpkg"))
    if not gpkg_members:
        raise ValueError("No .gpkg file found in the zip archive.")
    return gpkg_members[0]


def ensure_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"Required command '{name}' was not found. Install GDAL, then rerun this script."
        )
    return path


def download_file(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    with urllib.request.urlopen(url) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)


def extract_gpkg(zip_path: Path, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        member = find_gpkg_member(archive.namelist())
        archive.extract(member, work_dir)
    return work_dir / member


def detect_first_layer(gpkg_path: Path) -> str:
    try:
        return detect_first_layer_sqlite(gpkg_path)
    except sqlite3.Error:
        pass
    ogrinfo = ensure_command("ogrinfo")
    result = subprocess.run(
        [ogrinfo, "-ro", "-q", str(gpkg_path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for line in result.stdout.splitlines():
        match = re.match(r"\s*\d+:\s+([^\s(]+)", line)
        if match:
            return match.group(1)
    raise RuntimeError(f"Could not detect a layer in {gpkg_path}")


def detect_first_layer_sqlite(gpkg_path: Path) -> str:
    with sqlite3.connect(gpkg_path) as conn:
        row = conn.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type = 'features' LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError(f"Could not detect a feature layer in {gpkg_path}")
    return str(row[0])


def detect_geometry_column(conn: sqlite3.Connection, layer_name: str) -> tuple[str, int]:
    row = conn.execute(
        "SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name = ? LIMIT 1",
        (layer_name,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Could not detect geometry column for layer {layer_name}")
    return str(row[0]), int(row[1])


def convert_dataset(
    gpkg_path: Path,
    output_path: Path,
    layer_name: str,
    municipality: str | None,
    output_format: str,
) -> None:
    ogr2ogr = ensure_command("ogr2ogr")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sql = build_select_sql(layer_name, municipality)
    driver = "Parquet" if output_format == "parquet" else "GeoJSON"
    args = [
        ogr2ogr,
        "-overwrite",
        "-f",
        driver,
        str(output_path),
        str(gpkg_path),
        "-dialect",
        "SQLite",
        "-sql",
        sql,
        "-t_srs",
        "EPSG:4326",
    ]
    if output_format == "parquet":
        args.extend(["-lco", "GEOMETRY_ENCODING=WKB"])
    print("Running:", " ".join(args))
    subprocess.run(args, check=True)


def convert_dataset_python_geojson(
    gpkg_path: Path,
    output_path: Path,
    layer_name: str,
    municipality: str | None,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(gpkg_path) as conn:
        conn.row_factory = sqlite3.Row
        geom_column, srs_id = detect_geometry_column(conn, layer_name)
        table_columns = [
            row[1] for row in conn.execute(f"PRAGMA table_info({quote_identifier(layer_name)})")
        ]
        property_columns = [col for col in table_columns if col != geom_column]
        sql = build_python_select_sql(layer_name, geom_column, property_columns, municipality)
        cursor = conn.execute(sql)
        count = write_geojson(cursor, output_path, geom_column, srs_id)
    return count


def build_python_select_sql(
    layer_name: str,
    geom_column: str,
    property_columns: list[str],
    municipality: str | None,
) -> str:
    columns = [quote_identifier(geom_column)] + [quote_identifier(col) for col in property_columns]
    where = ""
    if municipality:
        where = (
            "\nWHERE lower(coalesce(\"ov_nimi\", \"ay_nimi\", \"l_aadress\", '')) "
            f"LIKE lower({quote_literal(f'%{municipality}%')})"
        )
    return f"SELECT {', '.join(columns)}\nFROM {quote_identifier(layer_name)}{where}"


def write_geojson(
    rows: sqlite3.Cursor,
    output_path: Path,
    geom_column: str,
    srs_id: int,
) -> int:
    transform = transform_for_srs(srs_id)
    count = 0
    with output_path.open("w", encoding="utf-8") as out:
        header = {
            "type": "FeatureCollection",
            "name": "Tallinn, Estonia cadastral parcels for local CivicMapper-style visualization",
            "source": {
                "publisher": SOURCE_PUBLISHER,
                "page": SOURCE_PAGE_URL,
                "download": DEFAULT_SOURCE_URL,
                "fields": {
                    "tunnus": "cadastral unit ID",
                    "l_aadress": "address",
                    "pindala": "parcel area",
                    "maks_hind": "assessed value in euros",
                },
                "caveat": "maks_hind is an assessed cadastral value, not a sales-derived market value.",
            },
        }
        prefix = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
        out.write(prefix[:-1])
        out.write(',"features":[\n')
        for row in rows:
            row_dict = dict(row)
            geom_blob = row_dict.pop(geom_column)
            if not geom_blob:
                continue
            geometry = parse_gpkg_geometry(geom_blob)
            geometry = transform_geometry(geometry, transform)
            properties = derive_properties(row_dict)
            feature = {
                "type": "Feature",
                "id": properties.get("parcel_id") or properties.get("id") or count,
                "properties": properties,
                "geometry": geometry,
            }
            if count:
                out.write(",\n")
            json.dump(feature, out, ensure_ascii=False, separators=(",", ":"))
            count += 1
        out.write("\n]}\n")
    return count


def parse_gpkg_geometry(blob: bytes) -> dict[str, object]:
    if len(blob) < 8 or blob[:2] != b"GP":
        raise ValueError("Not a GeoPackage binary geometry.")
    flags = blob[3]
    endian = "<" if flags & 1 else ">"
    envelope_type = (flags >> 1) & 7
    envelope_lengths = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    envelope_length = envelope_lengths.get(envelope_type)
    if envelope_length is None:
        raise ValueError(f"Unsupported GeoPackage envelope type {envelope_type}.")
    # Validate/read SRS id for malformed header detection; actual transform is
    # selected from gpkg_geometry_columns for the layer.
    struct.unpack(f"{endian}i", blob[4:8])
    return parse_wkb_geometry(blob[8 + envelope_length :])


def parse_wkb_geometry(data: bytes, offset: int = 0) -> dict[str, object]:
    endian_marker = data[offset]
    endian = "<" if endian_marker == 1 else ">"
    geom_type = struct.unpack_from(f"{endian}I", data, offset + 1)[0]
    base_type = geom_type % 1000
    cursor = offset + 5
    if base_type == 3:
        rings, cursor = parse_wkb_polygon_rings(data, cursor, endian)
        return {"type": "Polygon", "coordinates": rings}
    if base_type == 6:
        polygon_count = struct.unpack_from(f"{endian}I", data, cursor)[0]
        cursor += 4
        polygons = []
        for _ in range(polygon_count):
            polygon = parse_wkb_geometry(data, cursor)
            polygons.append(polygon["coordinates"])
            cursor = next_wkb_offset(data, cursor)
        return {"type": "MultiPolygon", "coordinates": polygons}
    raise ValueError(f"Unsupported WKB geometry type {geom_type}.")


def parse_wkb_polygon_rings(data: bytes, cursor: int, endian: str) -> tuple[list[list[list[float]]], int]:
    ring_count = struct.unpack_from(f"{endian}I", data, cursor)[0]
    cursor += 4
    rings = []
    for _ in range(ring_count):
        point_count = struct.unpack_from(f"{endian}I", data, cursor)[0]
        cursor += 4
        ring = []
        for _ in range(point_count):
            x, y = struct.unpack_from(f"{endian}dd", data, cursor)
            cursor += 16
            ring.append([x, y])
        rings.append(ring)
    return rings, cursor


def next_wkb_offset(data: bytes, offset: int) -> int:
    endian = "<" if data[offset] == 1 else ">"
    geom_type = struct.unpack_from(f"{endian}I", data, offset + 1)[0]
    base_type = geom_type % 1000
    cursor = offset + 5
    if base_type == 3:
        _, cursor = parse_wkb_polygon_rings(data, cursor, endian)
        return cursor
    if base_type == 6:
        polygon_count = struct.unpack_from(f"{endian}I", data, cursor)[0]
        cursor += 4
        for _ in range(polygon_count):
            cursor = next_wkb_offset(data, cursor)
        return cursor
    raise ValueError(f"Unsupported WKB geometry type {geom_type}.")


def transform_for_srs(srs_id: int):
    if srs_id == 4326:
        return lambda x, y: [x, y]
    if srs_id == 3301:
        return epsg3301_to_wgs84
    raise RuntimeError(f"Unsupported source SRS EPSG:{srs_id}; install GDAL for reprojection.")


def transform_geometry(geometry: dict[str, object], transform) -> dict[str, object]:
    geom_type = geometry["type"]
    coords = geometry["coordinates"]
    if geom_type == "Polygon":
        return {
            "type": geom_type,
            "coordinates": [[round_xy(transform(x, y)) for x, y in ring] for ring in coords],
        }
    if geom_type == "MultiPolygon":
        return {
            "type": geom_type,
            "coordinates": [
                [[round_xy(transform(x, y)) for x, y in ring] for ring in polygon]
                for polygon in coords
            ],
        }
    raise ValueError(f"Unsupported geometry type {geom_type}.")


def round_xy(coord: list[float]) -> list[float]:
    return [round(coord[0], 8), round(coord[1], 8)]


def epsg3301_to_wgs84(x: float, y: float) -> list[float]:
    # EPSG:3301, Estonian Coordinate System of 1997, Lambert Conic Conformal 2SP.
    a = 6378137.0
    inv_f = 298.257222101
    f = 1 / inv_f
    e = math.sqrt(2 * f - f * f)
    lat_1 = math.radians(58.0)
    lat_2 = math.radians(59.333333333333336)
    lat_0 = math.radians(57.51755393055556)
    lon_0 = math.radians(24.0)
    false_easting = 500000.0
    false_northing = 6375000.0

    def m(phi: float) -> float:
        return math.cos(phi) / math.sqrt(1 - e * e * math.sin(phi) ** 2)

    def t(phi: float) -> float:
        sin_phi = math.sin(phi)
        return math.tan(math.pi / 4 - phi / 2) / (
            ((1 - e * sin_phi) / (1 + e * sin_phi)) ** (e / 2)
        )

    n = (math.log(m(lat_1)) - math.log(m(lat_2))) / (math.log(t(lat_1)) - math.log(t(lat_2)))
    big_f = m(lat_1) / (n * (t(lat_1) ** n))
    rho_0 = a * big_f * (t(lat_0) ** n)
    dx = x - false_easting
    dy = y - false_northing
    rho = math.copysign(math.sqrt(dx * dx + (rho_0 - dy) ** 2), n)
    theta = math.atan2(dx, rho_0 - dy)
    small_t = (rho / (a * big_f)) ** (1 / n)
    phi = math.pi / 2 - 2 * math.atan(small_t)
    for _ in range(8):
        sin_phi = math.sin(phi)
        phi = math.pi / 2 - 2 * math.atan(
            small_t * (((1 - e * sin_phi) / (1 + e * sin_phi)) ** (e / 2))
        )
    lon = lon_0 + theta / n
    return [math.degrees(lon), math.degrees(phi)]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Estonia cadastral parcels for local CivicMapper-style visualization."
    )
    parser.add_argument("--municipality", default="Tallinn", help="Municipality/city filter text.")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL, help="Official GPKG zip URL.")
    parser.add_argument("--zip", dest="zip_path", type=Path, help="Use an existing downloaded zip.")
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/estonia-cadastre"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--layer", help="GPKG layer name. Defaults to the first layer from ogrinfo.")
    parser.add_argument(
        "--format",
        choices=("geojson", "parquet"),
        default="geojson",
        help="Output format. GeoJSON is easiest for local inspection; Parquet matches CivicMapper.",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "python", "gdal"),
        default="auto",
        help="Conversion engine. Python supports GeoJSON without external GIS tools.",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Require --zip or an already downloaded source zip in the work directory.",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print the derived SELECT SQL and exit without converting.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    layer_name = args.layer or "kataster"
    if args.print_sql:
        print(build_select_sql(layer_name, args.municipality))
        return 0

    zip_path = args.zip_path or args.work_dir / "Eesti_KATASTER_GPKG.zip"
    if not zip_path.exists():
        if args.no_download:
            print(f"Source zip not found: {zip_path}", file=sys.stderr)
            return 2
        download_file(args.source_url, zip_path)

    try:
        gpkg_path = extract_gpkg(zip_path, args.work_dir / "extracted")
        if not args.layer:
            layer_name = detect_first_layer(gpkg_path)
        output_path = args.out_dir / output_filename(args.municipality, args.format)
        if args.engine == "python" or (
            args.engine == "auto" and args.format == "geojson" and not shutil.which("ogr2ogr")
        ):
            if args.format != "geojson":
                raise RuntimeError("The Python engine only supports GeoJSON output.")
            count = convert_dataset_python_geojson(gpkg_path, output_path, layer_name, args.municipality)
            print(f"Wrote {count} features")
        else:
            convert_dataset(gpkg_path, output_path, layer_name, args.municipality, args.format)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
