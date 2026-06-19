"""Sensor coverage: descriptions and supported parameters stay in sync."""
import unittest

from custom_components.usgs_streamflow import sensor as sensor_mod
from custom_components.usgs_streamflow.const import SUPPORTED_PARAMETERS

NEW_PARAMS = {
    "00020", "00052", "00035", "00036",   # weather
    "00480", "99133", "32316",            # extended water quality
    "62614", "00054", "72137", "72255",   # reservoir / velocity
}


class TestSensorCoverage(unittest.TestCase):
    def setUp(self):
        self.descs = sensor_mod.SENSOR_DESCRIPTIONS
        self.desc_params = [d.param_cd for d in self.descs]

    def test_expected_parameter_count(self):
        self.assertEqual(len(SUPPORTED_PARAMETERS), 21)

    def test_every_supported_param_has_a_description(self):
        missing = set(SUPPORTED_PARAMETERS) - set(self.desc_params)
        self.assertEqual(missing, set(), f"params without a sensor description: {missing}")

    def test_no_orphan_descriptions(self):
        orphans = set(self.desc_params) - set(SUPPORTED_PARAMETERS)
        self.assertEqual(orphans, set(), f"descriptions with no supported param: {orphans}")

    def test_no_duplicate_param_or_key(self):
        self.assertEqual(len(self.desc_params), len(set(self.desc_params)))
        keys = [d.key for d in self.descs]
        self.assertEqual(len(keys), len(set(keys)))

    def test_new_params_present(self):
        self.assertTrue(NEW_PARAMS <= set(SUPPORTED_PARAMETERS))

    def test_descriptions_well_formed(self):
        for d in self.descs:
            with self.subTest(key=d.key):
                self.assertTrue(d.name)
                self.assertIsNotNone(d.state_class)
                self.assertTrue(
                    d.native_unit_of_measurement is not None or d.device_class is not None,
                    "needs a unit or a device class",
                )


if __name__ == "__main__":
    unittest.main()
