import importlib.util
import json
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("prepare_oslo_civicmapper.py")


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_oslo_civicmapper", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OsloCivicMapperPrepTests(unittest.TestCase):
    def test_output_filename_uses_city_and_country_code(self):
        prep = load_module()

        self.assertEqual(prep.output_filename("Oslo", "geojson"), "oslo-no-parcels.geojson")
        self.assertEqual(prep.output_filename("Oslo kommune", "parquet"), "oslo-kommune-no-parcels.parquet")

    def test_build_geonorge_order_requests_oslo_open_cadastre(self):
        prep = load_module()

        order = prep.build_geonorge_order("FGDB", "25832", "03")
        line = order["orderLines"][0]

        self.assertFalse(order["downloadAsBundle"])
        self.assertEqual(line["metadataUuid"], prep.MATRIKKELEN_TEIG_UUID)
        self.assertEqual(line["areas"], [{"code": "03", "name": "Oslo", "type": "fylke"}])
        self.assertEqual(line["formats"], [{"name": "FGDB"}])
        self.assertEqual(line["projections"][0]["code"], "25832")

    def test_derive_properties_maps_matrikkelen_fields(self):
        prep = load_module()
        row = {
            "kommunenummer": "0301",
            "gaardsnummer": 1,
            "bruksnummer": 23,
            "festenummer": 0,
            "seksjonsnummer": 0,
            "beregnetAreal": "4046.8564224",
        }

        props = prep.derive_properties(row)

        self.assertEqual(props["parcel_id"], "0301-1/23")
        self.assertEqual(props["address"], "0301-1/23")
        self.assertEqual(props["land_area_sqm"], 4046.8564224)
        self.assertAlmostEqual(props["land_area_acres"], 1.0)
        self.assertIsNone(props["REALLANDVA"])

    def test_derive_properties_merges_tax_values_by_parcel_id(self):
        prep = load_module()
        row = {
            "kommunenummer": "0301",
            "gaardsnummer": "1",
            "bruksnummer": "23",
            "festenummer": "",
            "seksjonsnummer": "",
            "beregnetAreal": "100",
        }
        values = {
            "0301-1/23": {
                "address": "Karl Johans gate 1",
                "REALLANDVA": 2500000,
                "tax_basis_nok": 2100000,
                "property_tax_nok": 4250,
                "tax_unit_count": 3,
            }
        }

        props = prep.derive_properties(row, values)

        self.assertEqual(props["address"], "Karl Johans gate 1")
        self.assertEqual(props["REALLANDVA"], 2500000.0)
        self.assertEqual(props["tax_basis_nok"], 2100000.0)
        self.assertEqual(props["property_tax_nok"], 4250.0)
        self.assertEqual(props["tax_unit_count"], 3)
        self.assertEqual(props["land_value_per_sqm"], 25000.0)
        self.assertAlmostEqual(props["land_value_per_sqft"], 2322.576, places=3)

    def test_tax_key_parses_oslo_gnr_bnr_dot_notation(self):
        prep = load_module()

        self.assertEqual(prep.parcel_id_from_tax_key("2.4.0.1"), "0301-2/4/1")
        self.assertEqual(prep.parcel_id_from_tax_key("235.122.0.0"), "0301-235/122")
        self.assertEqual(prep.base_parcel_id_from_tax_key("2.4.0.1"), "0301-2/4")
        self.assertEqual(prep.base_parcel_id_from_tax_key("2.4.7.0"), "0301-2/4/7")

    def test_aggregate_tax_rows_sums_sections_to_base_parcel(self):
        prep = load_module()
        rows = [
            {
                "GnrBnr": "2.4.0.1",
                "Adresse": "Lovisenlund 7 B",
                "Skattegrunnlag": "1000",
                "Beregningsgrunnlag": "600",
                "Eiendomsskatt": "10",
            },
            {
                "GnrBnr": "2.4.0.2",
                "Adresse": "Lovisenlund 7 B",
                "Skattegrunnlag": "2000",
                "Beregningsgrunnlag": "1600",
                "Eiendomsskatt": "20",
            },
        ]

        values = prep.aggregate_tax_rows(rows)

        self.assertEqual(values["0301-2/4"]["REALLANDVA"], 3000.0)
        self.assertEqual(values["0301-2/4"]["tax_basis_nok"], 2200.0)
        self.assertEqual(values["0301-2/4"]["property_tax_nok"], 30.0)
        self.assertEqual(values["0301-2/4"]["tax_unit_count"], 2)
        self.assertEqual(values["0301-2/4"]["address"], "Lovisenlund 7 B")
        self.assertEqual(values["0301-2/4/1"]["REALLANDVA"], 1000.0)

    def test_parse_teig_gml_extracts_polygon_and_properties(self):
        prep = load_module()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
  xmlns:app="http://skjema.geonorge.no/SOSI/produktspesifikasjon/Matrikkelen-Eiendomskart-Teig/20211101"
  xmlns:gml="http://www.opengis.net/gml/3.2">
  <wfs:member>
    <app:Teig gml:id="teig.1">
      <app:område>
        <gml:Polygon srsName="urn:ogc:def:crs:EPSG::4326">
          <gml:exterior><gml:LinearRing>
            <gml:posList>59.0 10.0 59.0 10.1 59.1 10.1 59.0 10.0</gml:posList>
          </gml:LinearRing></gml:exterior>
        </gml:Polygon>
      </app:område>
      <app:kommunenummer>0301</app:kommunenummer>
      <app:matrikkelenhet>
        <app:Matrikkelenhet>
          <app:gardsnummer>235</app:gardsnummer>
          <app:bruksnummer>122</app:bruksnummer>
        </app:Matrikkelenhet>
      </app:matrikkelenhet>
      <app:matrikkelnummerTekst>235/122</app:matrikkelnummerTekst>
      <app:teigareal><app:Areal><app:lagretBeregnetAreal>10480.6</app:lagretBeregnetAreal></app:Areal></app:teigareal>
    </app:Teig>
  </wfs:member>
</wfs:FeatureCollection>"""

        features = prep.parse_teig_gml(xml)

        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["geometry"]["type"], "Polygon")
        self.assertEqual(features[0]["geometry"]["coordinates"][0][0], [10.0, 59.0])
        self.assertEqual(features[0]["properties"]["kommunenummer"], "0301")
        self.assertEqual(features[0]["properties"]["gardsnummer"], "235")
        self.assertEqual(features[0]["properties"]["lagretBeregnetAreal"], "10480.6")

    def test_filter_features_by_municipality_keeps_only_oslo(self):
        prep = load_module()
        features = [
            {"type": "Feature", "properties": {"kommunenummer": "0301"}, "geometry": None},
            {"type": "Feature", "properties": {"kommunenummer": "3201"}, "geometry": None},
            {"type": "Feature", "properties": {"kommunenummer": "0301"}, "geometry": None},
        ]

        filtered = prep.filter_features_by_municipality(features, "0301")

        self.assertEqual(len(filtered), 2)
        self.assertTrue(all(f["properties"]["kommunenummer"] == "0301" for f in filtered))

    def test_convert_source_to_geojson_reuses_existing_geojson(self):
        prep = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source.geojson"
            target = pathlib.Path(tmp) / "target.geojson"
            source.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")

            prep.convert_source_to_geojson(source, target)

            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["type"], "FeatureCollection")


if __name__ == "__main__":
    unittest.main()
