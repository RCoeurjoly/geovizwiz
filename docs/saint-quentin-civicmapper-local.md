# Local Saint-Quentin CivicMapper Proof

This workflow packages the existing OpenAVMKit Saint-Quentin pipeline outputs
as a local geovizwiz/CivicMapper-style GeoJSON. It is a French metric proof that
uses public cadastre/building data and DVF sales data.

## Data Sources

Cadastre parcels and buildings:

```text
https://services1.arcgis.com/5nIW6mZeb2YNJ7np/ArcGIS/rest/services/SIG_CADASTRE/FeatureServer
```

DVF sales data:

```text
https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres
https://www.data.gouv.fr/api/1/datasets/demandes-de-valeurs-foncieres/
```

Reference pipeline:

```text
/home/roland/openavmkit/scripts/prepare_saint_quentin.py
```

## Prepare

If the OpenAVMKit source files are missing, regenerate them first:

```bash
cd /home/roland/openavmkit
venv/bin/python scripts/prepare_saint_quentin.py --years 2021 2022 2023 2024 2025
```

Then package the geovizwiz-local file from this repo:

```bash
cd /home/roland/geovizwiz
/home/roland/openavmkit/venv/bin/python tools/prepare_saint_quentin_civicmapper.py
```

Output:

```text
local-data/saint-quentin-fr-parcels.geojson
```

The generated file contains 43,189 parcel features in the current local output.

## Load Locally

Start geovizwiz:

```bash
cd /home/roland/geovizwiz/viz
npm run dev -- --mode browser
```

Open the local Vite URL and load:

```text
local-data/saint-quentin-fr-parcels.geojson
```

## Key Field Choices

Use these mappings in the "Identify key fields" dialog:

```text
Parcel ID:              parcel_id
Address:                address

Building/Improvement
Size:                   bldg_area_finished_sqm
Size unit:              sqm
Quality:                leave blank
Condition:              leave blank
Age:                    leave blank
Eff. Age:               leave blank
Beds:                   leave blank
Baths:                  leave blank
Type:                   bldg_type

Land
Size:                   land_area_sqm
Size unit:              sqm
Type:                   neighborhood
Zoning:                 cadastral_section

Assessed Values
Full market value:      main_prediction
Assessed/taxable value: leave blank
Land value:             vacant_prediction
Improvement value:      leave blank

Sale
Sale ID:                sale_id
Price:                  sale_price
Date:                   sale_date
Valid:                  valid_for_ratio_study
Vacant:                 valid_for_land_ratio_study
```

Useful visualization fields:

```text
land_area_sqm
bldg_area_finished_sqm
sale_price
sale_price_time_adj
sale_price_per_sqm
sale_price_time_adj_per_sqm
main_prediction_per_sqm
vacant_prediction_per_sqm
is_vacant
```

## Caveats

- DVF records are sales, not official assessed land values.
- Sale values may represent multi-parcel or partial-property transactions.
- Per-area fields divide sale or model values by parcel area and are exploratory.
- `main_prediction` and `vacant_prediction` come from local OpenAVMKit model
  outputs when present; they are not official values.
- `address` is currently set to the cadastral identifier because the local
  source output does not expose a clean street-address field.
- This is a local proof, not live CivicMapper.org support.
