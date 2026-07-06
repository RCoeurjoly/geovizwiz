import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("prepare_saint_quentin_civicmapper.py")


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_saint_quentin_civicmapper", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SaintQuentinCivicMapperPrepTests(unittest.TestCase):
    def test_output_filename_uses_slug_and_country_code(self):
        prep = load_module()

        self.assertEqual(
            prep.output_filename("Saint-Quentin", "geojson"),
            "saint-quentin-fr-parcels.geojson",
        )

    def test_safe_divide_handles_missing_and_zero_denominator(self):
        prep = load_module()

        self.assertEqual(prep.safe_divide(10, 2), 5)
        self.assertIsNone(prep.safe_divide(10, 0))
        self.assertIsNone(prep.safe_divide(None, 2))

    def test_derive_value_fields_adds_per_area_metrics(self):
        prep = load_module()
        row = {
            "sale_price": 100_000,
            "sale_price_time_adj": 120_000,
            "land_area_sqm": 1000,
            "land_area_sqft": 10763.910416709722,
            "main_prediction": 90_000,
        }

        props = prep.derive_value_fields(row)

        self.assertEqual(props["sale_price_per_sqm"], 100)
        self.assertAlmostEqual(props["sale_price_per_sqft"], 9.290303999999761)
        self.assertEqual(props["sale_price_time_adj_per_sqm"], 120)
        self.assertEqual(props["main_prediction_per_sqm"], 90)


if __name__ == "__main__":
    unittest.main()
