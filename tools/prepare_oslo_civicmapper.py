#!/usr/bin/env python3
"""Prepare Oslo parcels for local CivicMapper-style geovizwiz testing.

The parcel geometry source is Kartverket's open "Matrikkelen - Eiendomskart
Teig" dataset from Geonorge. Parcel-level value/tax fields can be joined from
Oslo kommune's public property-tax XLSX list.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any


MATRIKKELEN_TEIG_UUID = "74340c24-1c8a-4454-b813-bfe498e80f16"
GEONORGE_ORDER_URL = "https://nedlasting.geonorge.no/api/order"
WFS_URL = "https://wfs.geonorge.no/skwms1/wfs.matrikkelen-eiendomskart-teig"
SOURCE_PAGE_URL = f"https://kartkatalog.geonorge.no/metadata/uuid/{MATRIKKELEN_TEIG_UUID}"
SOURCE_PUBLISHER = "Kartverket / Geonorge"
DEFAULT_OUT_DIR = Path("local-data")
COUNTRY_CODE = "no"
SQFT_PER_SQM = 10.76391041671
M2_PER_ACRE = 4046.8564224
OSLO_BBOX_4326_AXIS_ORDER = "59.83,10.62,60.00,10.90,urn:ogc:def:crs:EPSG::4326"
APP_NS = "http://skjema.geonorge.no/SOSI/produktspesifikasjon/Matrikkelen-Eiendomskart-Teig/20211101"
GML_NS = "http://www.opengis.net/gml/3.2"


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "oslo"


def output_filename(city: str, output_format: str) -> str:
    ext = "parquet" if output_format == "parquet" else "geojson"
    return f"{slugify(city)}-{COUNTRY_CODE}-parcels.{ext}"


def build_geonorge_order(
    file_format: str = "FGDB",
    projection: str = "25832",
    area_code: str = "03",
) -> dict[str, Any]:
    area_name = "Oslo" if area_code == "03" else area_code
    return {
        "downloadAsBundle": False,
        "orderLines": [
            {
                "areas": [{"code": area_code, "name": area_name, "type": "fylke"}],
                "formats": [{"name": file_format}],
                "metadataUuid": MATRIKKELEN_TEIG_UUID,
                "projections": [
                    {
                        "code": projection,
                        "name": f"EUREF89 UTM sone {projection[-2:]}, 2d",
                        "codespace": f"http://www.opengis.net/def/crs/EPSG/0/{projection}",
                    }
                ],
            }
        ],
        "softwareClient": "geovizwiz",
    }


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def clean_part(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return ""
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def first_present(row: dict[str, Any], names: list[str]) -> Any:
    lower_map = {key.lower(): key for key in row}
    for name in names:
        key = lower_map.get(name.lower())
        if key is not None and row.get(key) not in (None, ""):
            return row[key]
    return None


def parcel_id_from_row(row: dict[str, Any]) -> str:
    direct = first_present(
        row,
        [
            "parcel_id",
            "lokalId",
            "lokalid",
            "localId",
            "uuid",
        ],
    )
    if direct:
        return str(direct).strip()

    municipality = clean_part(first_present(row, ["kommunenummer", "kommuneNr", "kommunenr"]))
    matrikkelnummer = clean_part(first_present(row, ["matrikkelnummer", "matrikkelnummertekst"]))
    if matrikkelnummer:
        return f"{municipality}-{matrikkelnummer}" if municipality else matrikkelnummer

    gnr = clean_part(first_present(row, ["gaardsnummer", "gårdsnummer", "gardsnummer", "gnr"]))
    bnr = clean_part(first_present(row, ["bruksnummer", "bnr"]))
    fnr = clean_part(first_present(row, ["festenummer", "fnr"]))
    snr = clean_part(first_present(row, ["seksjonsnummer", "snr"]))
    if not municipality and any([gnr, bnr]):
        municipality = "0301"
    if not any([gnr, bnr, fnr, snr]):
        return municipality or ""
    suffix = f"{gnr or '0'}/{bnr or '0'}"
    if fnr and fnr != "0":
        suffix += f"/{fnr}"
    if snr and snr != "0":
        suffix += f"/{snr}"
    return f"{municipality}-{suffix}" if municipality else suffix


def load_value_csv(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    values: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8-sig") as infile:
        reader = csv.DictReader(infile)
        for row in reader:
            parcel_id = str(first_present(row, ["parcel_id", "matrikkelnummer", "matrikkelnummertekst"]) or "")
            parcel_id = parcel_id.strip()
            if parcel_id:
                values[parcel_id] = row
    return values


def parcel_id_from_tax_key(value: Any, municipality: str = "0301") -> str:
    parts = [clean_part(part) for part in str(value or "").strip().split(".")]
    parts = [part for part in parts if part != ""]
    if len(parts) < 2:
        return ""
    gnr, bnr = parts[0], parts[1]
    fnr = parts[2] if len(parts) > 2 else "0"
    snr = parts[3] if len(parts) > 3 else "0"
    suffix = f"{gnr}/{bnr}"
    if fnr and fnr != "0":
        suffix += f"/{fnr}"
    if snr and snr != "0":
        suffix += f"/{snr}"
    return f"{municipality}-{suffix}"


def base_parcel_id_from_tax_key(value: Any, municipality: str = "0301") -> str:
    parts = [clean_part(part) for part in str(value or "").strip().split(".")]
    parts = [part for part in parts if part != ""]
    if len(parts) < 2:
        return ""
    gnr, bnr = parts[0], parts[1]
    fnr = parts[2] if len(parts) > 2 else "0"
    suffix = f"{gnr}/{bnr}"
    if fnr and fnr != "0":
        suffix += f"/{fnr}"
    return f"{municipality}-{suffix}"


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9æøå]+", "", value.lower())


def aggregate_tax_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}

    def add(target_id: str, row: dict[str, Any]) -> None:
        if not target_id:
            return
        entry = values.setdefault(
            target_id,
            {
                "parcel_id": target_id,
                "address": row.get("Adresse") or row.get("address") or target_id,
                "REALLANDVA": 0.0,
                "tax_basis_nok": 0.0,
                "property_tax_nok": 0.0,
                "tax_unit_count": 0,
            },
        )
        entry["REALLANDVA"] += to_float(row.get("Skattegrunnlag")) or 0.0
        entry["tax_basis_nok"] += to_float(row.get("Beregningsgrunnlag")) or 0.0
        entry["property_tax_nok"] += to_float(row.get("Eiendomsskatt")) or 0.0
        entry["tax_unit_count"] += 1

    for row in rows:
        key = row.get("GnrBnr")
        add(parcel_id_from_tax_key(key), row)
        base_id = base_parcel_id_from_tax_key(key)
        if base_id != parcel_id_from_tax_key(key):
            add(base_id, row)
    return values


def read_xlsx_rows(path: Path) -> list[dict[str, Any]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

    def col_idx(ref: str) -> int:
        letters = "".join(ch for ch in ref if ch.isalpha())
        n = 0
        for ch in letters:
            n = n * 26 + ord(ch.upper()) - 64
        return n - 1

    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                shared.append("".join(t.text or "" for t in si.findall(".//a:t", ns)))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        sheet = workbook.find("a:sheets/a:sheet", ns)
        if sheet is None:
            return []
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = relmap[rid]
        sheet_path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
        root = ET.fromstring(archive.read(sheet_path))

        header: list[str] | None = None
        output: list[dict[str, Any]] = []
        for row in root.findall(".//a:sheetData/a:row", ns):
            vals: dict[int, str] = {}
            for cell in row.findall("a:c", ns):
                value = cell.find("a:v", ns)
                text = "" if value is None else value.text or ""
                if cell.attrib.get("t") == "s" and text:
                    text = shared[int(text)]
                vals[col_idx(cell.attrib.get("r", "A"))] = text
            if not vals:
                continue
            row_values = [vals.get(i, "") for i in range(max(vals) + 1)]
            if header is None:
                normalized = [normalize_header(value) for value in row_values]
                if "gnrbnr" in normalized and "eiendomsskatt" in normalized:
                    header = [canonical_tax_header(value) for value in row_values]
                continue
            record = {
                header[i]: row_values[i]
                for i in range(min(len(header), len(row_values)))
                if header[i]
            }
            if record.get("GnrBnr"):
                output.append(record)
    return output


def canonical_tax_header(value: str) -> str:
    normalized = normalize_header(value)
    mapping = {
        "gnrbnr": "GnrBnr",
        "adresse": "Adresse",
        "beregningsmetode": "Beregningsmetode",
        "fritakstyper": "Fritakstyper",
        "skattegrunnlag": "Skattegrunnlag",
        "fritak": "Fritak",
        "bunnfradrag": "Bunnfradrag",
        "beregningsgrunnlag": "Beregningsgrunnlag",
        "promille": "Promille",
        "eiendomsskatt": "Eiendomsskatt",
    }
    return mapping.get(normalized, value.strip())


def load_tax_xlsx(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    return aggregate_tax_rows(read_xlsx_rows(path))


def derive_properties(
    row: dict[str, Any],
    value_rows: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value_rows = value_rows or {}
    parcel_id = parcel_id_from_row(row)
    joined = value_rows.get(parcel_id, {})
    area_sqm = to_float(
        first_present(
            row,
            ["land_area_sqm", "lagretBeregnetAreal", "beregnetAreal", "teigareal", "areal", "oppgittAreal"],
        )
    )
    assessed_value = to_float(
        first_present(joined, ["REALLANDVA", "land_value", "land_value_nok", "grunnverdi", "grunnværdi"])
    )
    property_tax = to_float(first_present(joined, ["property_tax_nok", "eiendomsskatt", "tax_nok"]))
    tax_basis = to_float(first_present(joined, ["tax_basis_nok", "Beregningsgrunnlag"]))
    tax_unit_count = to_float(first_present(joined, ["tax_unit_count"]))
    address = first_present(joined, ["address", "adresse"]) or first_present(row, ["address", "adresse"])
    if not address:
        address = parcel_id

    area_sqft = area_sqm * SQFT_PER_SQM if area_sqm and area_sqm > 0 else None
    value_per_sqm = assessed_value / area_sqm if assessed_value is not None and area_sqm and area_sqm > 0 else None
    value_per_sqft = assessed_value / area_sqft if assessed_value is not None and area_sqft else None

    props = {
        **row,
        "parcel_id": parcel_id,
        "address": address,
        "land_area_sqm": area_sqm,
        "land_area_acres": area_sqm / M2_PER_ACRE if area_sqm is not None else None,
        "REALLANDVA": assessed_value,
        "tax_basis_nok": tax_basis,
        "property_tax_nok": property_tax,
        "tax_unit_count": int(tax_unit_count) if tax_unit_count is not None else None,
        "land_value_per_sqm": value_per_sqm,
        "REALLANDVA_per_sqft": value_per_sqft,
        "land_value_per_sqft": value_per_sqft,
    }
    return props


def request_geonorge_order(order: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(order).encode("utf-8")
    request = urllib.request.Request(
        GEONORGE_ORDER_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, name: str) -> str | None:
    for child in element.iter():
        if local_name(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def parse_pos_list(text: str) -> list[list[float]]:
    values = [float(part) for part in text.split()]
    coords = []
    for idx in range(0, len(values), 2):
        lat = values[idx]
        lon = values[idx + 1]
        coords.append([lon, lat])
    return coords


def parse_polygon(teig: ET.Element) -> dict[str, Any] | None:
    polygon = None
    for element in teig.iter():
        if local_name(element.tag) == "Polygon":
            polygon = element
            break
    if polygon is None:
        return None

    rings = []
    for ring in polygon.iter():
        if local_name(ring.tag) != "LinearRing":
            continue
        pos_list = child_text(ring, "posList")
        if pos_list:
            rings.append(parse_pos_list(pos_list))
    if not rings:
        return None
    return {"type": "Polygon", "coordinates": rings}


def parse_teig_properties(teig: ET.Element) -> dict[str, Any]:
    names = [
        "teigId",
        "kommunenummer",
        "kommunenavn",
        "gardsnummer",
        "bruksnummer",
        "festenummer",
        "seksjonsnummer",
        "bruksnavn",
        "matrikkelenhetstype",
        "matrikkelenhetId",
        "uuidMatrikkelenhet",
        "matrikkelnummerTekst",
        "teigMedFlereMatrikkelenheter",
        "tvist",
        "uregistrertJordsameie",
        "avklartEiere",
        "lagretBeregnetAreal",
        "noyaktighetsklasseTeig",
        "uuidTeig",
    ]
    return {name: child_text(teig, name) for name in names if child_text(teig, name) is not None}


def parse_teig_gml(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    features = []
    for teig in root.iter(f"{{{APP_NS}}}Teig"):
        geometry = parse_polygon(teig)
        if geometry is None:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": parse_teig_properties(teig),
                "geometry": geometry,
            }
        )
    return features


def build_wfs_url(start_index: int, count: int, bbox: str) -> str:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": "app:Teig",
        "count": str(count),
        "startIndex": str(start_index),
        "srsName": "urn:ogc:def:crs:EPSG::4326",
        "bbox": bbox,
    }
    return WFS_URL + "?" + urllib.parse.urlencode(params)


def fetch_wfs_features(
    bbox: str = OSLO_BBOX_4326_AXIS_ORDER,
    page_size: int = 1000,
    max_features: int | None = None,
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    start = 0
    while True:
        url = build_wfs_url(start, page_size, bbox)
        with urllib.request.urlopen(url) as response:
            page_features = parse_teig_gml(response.read().decode("utf-8"))
        if not page_features:
            break
        features.extend(page_features)
        print(f"Fetched {len(features)} WFS parcel features")
        if max_features is not None and len(features) >= max_features:
            return features[:max_features]
        if len(page_features) < page_size:
            break
        start += page_size
    return features


def filter_features_by_municipality(
    features: list[dict[str, Any]],
    municipality_code: str,
) -> list[dict[str, Any]]:
    return [
        feature
        for feature in features
        if (feature.get("properties") or {}).get("kommunenummer") == municipality_code
    ]


def ensure_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required command '{name}' was not found. Install GDAL and retry.")
    return path


def convert_source_to_geojson(source: Path, target: Path) -> None:
    if source.suffix.lower() in {".geojson", ".json"}:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return
    ogr2ogr = ensure_command("ogr2ogr")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ogr2ogr, "-overwrite", "-f", "GeoJSON", str(target), str(source), "-t_srs", "EPSG:4326"],
        check=True,
    )


def package_geojson(source_geojson: Path, output_path: Path, value_rows: dict[str, dict[str, Any]]) -> int:
    data = json.loads(source_geojson.read_text(encoding="utf-8"))
    return package_feature_collection(data, output_path, value_rows)


def package_feature_collection(
    data: dict[str, Any],
    output_path: Path,
    value_rows: dict[str, dict[str, Any]],
) -> int:
    features = data.get("features") or []
    for index, feature in enumerate(features):
        row = feature.get("properties") or {}
        props = derive_properties(row, value_rows)
        feature["id"] = props.get("parcel_id") or index
        feature["properties"] = clean_for_json(props)
    data["name"] = "Oslo, Norway cadastral parcels for local CivicMapper-style visualization"
    data["source"] = {
        "cadastre": {
            "publisher": SOURCE_PUBLISHER,
            "dataset": "Matrikkelen - Eiendomskart Teig",
            "page": SOURCE_PAGE_URL,
            "uuid": MATRIKKELEN_TEIG_UUID,
            "caveat": "Open cadastral parcel extract. Joined REALLANDVA is Oslo kommune Skattegrunnlag from the public property-tax list, not a separate land-only valuation.",
        }
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(features)


def clean_for_json(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = {}
    for key, value in row.items():
        if isinstance(value, float) and not math.isfinite(value):
            cleaned[key] = None
        else:
            cleaned[key] = value
    return cleaned


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Oslo, Norway parcels for local CivicMapper-style geovizwiz use."
    )
    parser.add_argument("--city", default="Oslo")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--source", type=Path, help="Local source GIS file/zip accepted by GDAL.")
    parser.add_argument("--values-csv", type=Path, help="Optional CSV keyed by parcel_id with value/tax fields.")
    parser.add_argument("--tax-xlsx", type=Path, help="Optional Oslo kommune property-tax XLSX.")
    parser.add_argument("--download", action="store_true", help="Request and download the Oslo parcel extract from Geonorge.")
    parser.add_argument("--engine", choices=("auto", "gdal", "wfs"), default="auto")
    parser.add_argument("--wfs-page-size", type=int, default=1000)
    parser.add_argument(
        "--bbox",
        default=OSLO_BBOX_4326_AXIS_ORDER,
        help="WFS bbox in EPSG:4326 axis order: minLat,minLon,maxLat,maxLon,crs.",
    )
    parser.add_argument("--max-features", type=int)
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/oslo-cadastre"))
    parser.add_argument("--format", choices=("geojson", "parquet"), default="geojson")
    parser.add_argument("--geonorge-format", default="FGDB")
    parser.add_argument("--projection", default="25832")
    parser.add_argument("--area-code", default="03")
    parser.add_argument("--municipality-code", default="0301")
    parser.add_argument("--print-order", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    order = build_geonorge_order(args.geonorge_format, args.projection, args.area_code)
    if args.print_order:
        print(json.dumps(order, indent=2, ensure_ascii=False))
        return 0

    source = args.source
    try:
        use_wfs = args.engine == "wfs" or (args.engine == "auto" and source is None and not shutil.which("ogr2ogr"))
        values = load_value_csv(args.values_csv)
        values.update(load_tax_xlsx(args.tax_xlsx))
        output_path = args.out_dir / output_filename(args.city, args.format)

        if use_wfs:
            features = fetch_wfs_features(
                bbox=args.bbox,
                page_size=args.wfs_page_size,
                max_features=args.max_features,
            )
            features = filter_features_by_municipality(features, args.municipality_code)
            count = package_feature_collection(
                {"type": "FeatureCollection", "features": features},
                output_path,
                values,
            )
        else:
            if args.download:
                receipt = request_geonorge_order(order)
                files = receipt.get("files") or []
                if not files:
                    raise RuntimeError(f"Geonorge order did not return a ready file: {receipt}")
                source = args.work_dir / files[0]["name"]
                download_file(files[0]["downloadUrl"], source)
            if source is None:
                raise RuntimeError("Provide --source, use --download, or set --engine wfs.")

            with tempfile.TemporaryDirectory() as tmp:
                temp_geojson = Path(tmp) / "oslo-source.geojson"
                convert_source_to_geojson(source, temp_geojson)
                count = package_geojson(temp_geojson, output_path, values)
    except (RuntimeError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    print(f"Wrote {count} features")
    print(f"Wrote {output_path}")
    if not values:
        print("Warning: no value CSV supplied; REALLANDVA and value-per-area fields are empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
