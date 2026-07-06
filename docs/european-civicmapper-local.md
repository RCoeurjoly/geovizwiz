# Local European CivicMapper Proof

This repo does not contain the production CivicMapper.org city catalog. Use this
workflow to prepare a European parcel file locally, load it in `viz`, and produce
the same kind of artifact that can later be added to the private CivicMapper
deployment.

## Estonia / Tallinn

Estonia is the easiest first European proof because the official cadastral
download includes parcel geometry, parcel area, and assessed value.

Official source page:

```text
https://geoportaal.maaamet.ee/eng/spatial-data/cadastral-data-p310.html
```

Primary download used locally:

```text
https://s3.pilw.io/rp-kemit-kataster/ANDMED/Eesti_KATASTER_GPKG.zip
```

The prep script derives CivicMapper-style fields from the Estonian schema:

```text
parcel_id             = tunnus
address               = l_aadress
land_area_sqm         = pindala
land_area_acres       = pindala / 4046.8564224
REALLANDVA            = maks_hind
land_value_per_sqm    = maks_hind / pindala
REALLANDVA_per_sqft   = maks_hind / (pindala * 10.76391041671)
land_value_per_sqft   = same as REALLANDVA_per_sqft
```

`maks_hind` is treated as an assessed cadastral value in euros. This proof
visualizes assessed value per area; it is not a sales-derived market-value model
and does not replace the sales/AVM work needed for LVT shift modeling.

The municipality filter matches the requested text against `ov_nimi`, falling
back to `ay_nimi` and `l_aadress`. In the current official GPKG, `ov_nimi =
"Tallinn"` yields 38,653 parcels.

GeoJSON output works with standard Python only. Install GDAL only if you want
direct Parquet output through `ogr2ogr`.

Run a local GeoJSON proof:

```bash
python3 tools/prepare_estonia_civicmapper.py --municipality Tallinn --format geojson
```

Run a CivicMapper-style Parquet output, if GDAL is installed and your GDAL build
supports Parquet:

```bash
python3 tools/prepare_estonia_civicmapper.py --municipality Tallinn --format parquet
```

Use an already downloaded zip:

```bash
python3 tools/prepare_estonia_civicmapper.py \
  --zip /path/to/Eesti_KATASTER_GPKG.zip \
  --municipality Tallinn \
  --format geojson
```

Inspect the SQL without converting:

```bash
python3 tools/prepare_estonia_civicmapper.py --municipality Tallinn --print-sql
```

Output defaults to:

```text
local-data/tallinn-ee-parcels.geojson
local-data/tallinn-ee-parcels.parquet
```

## Load Locally

Start the visualizer:

```bash
cd viz
npm run dev -- --mode browser
```

Open the local Vite URL and load `local-data/tallinn-ee-parcels.geojson` with
the existing file picker. Visualize `land_value_per_sqm` for metric-friendly
review, or `land_value_per_sqft` / `REALLANDVA_per_sqft` to mimic CivicMapper's
current US field conventions.

## Production CivicMapper Handoff

Once Lars provides the production CivicMapper repo or deployment workflow, the
prepared Tallinn entry should be conceptually similar to:

```js
tallinn: {
  displayName: "Tallinn",
  state: "ee",
  filename: "tallinn-ee-parcels.parquet"
}
```

The production catalog also needs a display mapping for `ee`, for example:

```js
ee: "Estonia"
```

Longer term, the catalog should probably rename `state` to `region` or add a
separate `country` field, but `state: "ee"` is the smallest compatibility step.
