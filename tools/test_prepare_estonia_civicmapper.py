import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("prepare_estonia_civicmapper.py")


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_estonia_civicmapper", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EstoniaCivicMapperPrepTests(unittest.TestCase):
    def test_output_filename_uses_slug_and_country_code(self):
        prep = load_module()

        self.assertEqual(
            prep.output_filename("Tallinn", "parquet"),
            "tallinn-ee-parcels.parquet",
        )
        self.assertEqual(
            prep.output_filename("Tartu linn", "geojson"),
            "tartu-linn-ee-parcels.geojson",
        )

    def test_build_select_sql_derives_civicmapper_fields(self):
        prep = load_module()

        sql = prep.build_select_sql("kataster", "Tallinn")

        self.assertIn('"tunnus" AS parcel_id', sql)
        self.assertIn('"l_aadress" AS address', sql)
        self.assertIn('"pindala" AS land_area_sqm', sql)
        self.assertIn('"maks_hind" AS REALLANDVA', sql)
        self.assertIn('"maks_hind" / NULLIF("pindala", 0)', sql)
        self.assertIn("WHERE", sql)
        self.assertIn('"ov_nimi"', sql)
        self.assertIn("Tallinn", sql)

    def test_find_gpkg_member_prefers_first_geopackage_in_zip(self):
        prep = load_module()

        member = prep.find_gpkg_member([
            "README.txt",
            "nested/Eesti_KATASTER.gpkg",
            "nested/other.gpkg",
        ])

        self.assertEqual(member, "nested/Eesti_KATASTER.gpkg")

    def test_parse_little_endian_wkb_polygon(self):
        prep = load_module()
        # POLYGON ((0 0, 1 0, 1 1, 0 0))
        wkb = (
            b"\x01"
            + (3).to_bytes(4, "little")
            + (1).to_bytes(4, "little")
            + (4).to_bytes(4, "little")
            + prep.struct.pack("<8d", 0, 0, 1, 0, 1, 1, 0, 0)
        )

        geometry = prep.parse_wkb_geometry(wkb)

        self.assertEqual(geometry["type"], "Polygon")
        self.assertEqual(geometry["coordinates"][0], [[0, 0], [1, 0], [1, 1], [0, 0]])

    def test_derive_properties_handles_zero_area(self):
        prep = load_module()
        row = {
            "tunnus": "78401:001:0018",
            "l_aadress": "Ruunaoja tänav",
            "pindala": 0,
            "maks_hind": 1000.0,
        }

        props = prep.derive_properties(row)

        self.assertEqual(props["parcel_id"], "78401:001:0018")
        self.assertIsNone(props["land_value_per_sqm"])
        self.assertIsNone(props["land_value_per_sqft"])


if __name__ == "__main__":
    unittest.main()
