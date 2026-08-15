"""Sensor coverage: descriptions and supported parameters stay in sync."""
import unittest
from types import SimpleNamespace

from custom_components.usgs_streamflow import sensor as sensor_mod
from custom_components.usgs_streamflow.const import (
    DERIVED_PARAM_CODES,
    SUPPORTED_PARAMETERS,
)

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

    def test_derived_sensors_match_shared_param_codes(self):
        # The coordinator seeds the rate buffer for DERIVED_PARAM_CODES; the
        # sensor descriptions must cover exactly the same set (no drift).
        derived = {cfg.param_cd for cfg in sensor_mod.DERIVED_SENSORS}
        self.assertEqual(derived, set(DERIVED_PARAM_CODES))

    def test_descriptions_well_formed(self):
        for d in self.descs:
            with self.subTest(key=d.key):
                # Names come from translations, keyed by `key` (used as the
                # entity translation_key), so descriptions carry no `name`/`icon`.
                self.assertTrue(d.key)
                self.assertIsNone(d.name)
                self.assertIsNone(d.icon)
                self.assertIsNotNone(d.state_class)
                self.assertTrue(
                    d.native_unit_of_measurement is not None or d.device_class is not None,
                    "needs a unit or a device class",
                )


class TestParamsToCreate(unittest.TestCase):
    """Which measurement sensors are registered given the first-refresh state."""

    def _coord(self, known, station_offline=None):
        # station_offline=None models "no first-refresh data at all".
        data = (
            None if station_offline is None
            else SimpleNamespace(station_offline=station_offline)
        )
        return SimpleNamespace(known_params=set(known), data=data)

    @property
    def _all(self):
        return {d.param_cd for d in sensor_mod.SENSOR_DESCRIPTIONS}

    def test_known_params_create_exactly_those(self):
        got = sensor_mod._params_to_create(
            self._coord({"00060", "00065"}, station_offline=False)
        )
        self.assertEqual(got, {"00060", "00065"})

    def test_reporting_but_unsupported_creates_nothing(self):
        # Station responded (not offline) but reported no supported params —
        # no phantom sensors.
        got = sensor_mod._params_to_create(self._coord(set(), station_offline=False))
        self.assertEqual(got, set())

    def test_offline_at_startup_creates_full_set(self):
        got = sensor_mod._params_to_create(self._coord(set(), station_offline=True))
        self.assertEqual(got, self._all)

    def test_no_first_refresh_data_creates_full_set(self):
        got = sensor_mod._params_to_create(self._coord(set(), station_offline=None))
        self.assertEqual(got, self._all)


if __name__ == "__main__":
    unittest.main()
