"""LegacyClient: WaterServices IV parsing and RDB site parsing."""
import unittest

from custom_components.usgs_streamflow.client import (
    LegacyClient,
    UsgsResponseFormatError,
)

SAMPLE_IV = {
    "value": {
        "timeSeries": [
            {
                "variable": {"variableCode": [{"value": "00065"}]},
                "values": [{"value": [
                    {"value": "3.50", "dateTime": "2026-06-11T03:45:00.000-05:00"},
                ]}],
            },
            {  # missing-data sentinel
                "variable": {"variableCode": [{"value": "00060"}]},
                "values": [{"value": [
                    {"value": "-999999", "dateTime": "2026-06-11T03:45:00.000-05:00"},
                ]}],
            },
            {  # non-numeric
                "variable": {"variableCode": [{"value": "00010"}]},
                "values": [{"value": [
                    {"value": "Ice", "dateTime": "2026-06-11T03:45:00.000-05:00"},
                ]}],
            },
            {  # empty value list -> station does not have this sensor
                "variable": {"variableCode": [{"value": "00400"}]},
                "values": [{"value": []}],
            },
        ]
    }
}

RDB = "\n".join([
    "# a comment line, ignored",
    "agency_cd\tsite_no\tstation_nm\tstate_cd",
    "5s\t15s\t50s\t2s",
    "USGS\t01460595\tDELAWARE CANAL\t42",
    "USGS\t06710247\tCHERRY CREEK\t08",
])


class TestLegacyParseLatest(unittest.TestCase):
    def test_values_and_metadata(self):
        result = LegacyClient._parse_latest(SAMPLE_IV)
        self.assertTrue(result.station_reporting)
        self.assertEqual(result.readings["00065"].value, 3.50)
        self.assertIsNotNone(result.readings["00065"].reading_time)
        # legacy does not provide these
        self.assertIsNone(result.readings["00065"].approval_status)
        self.assertIsNone(result.readings["00065"].qualifier)
        self.assertIsNone(result.readings["00065"].statistic_id)
        self.assertIsNone(result.readings["00065"].time_series_id)

    def test_sentinel_and_non_numeric_become_none(self):
        result = LegacyClient._parse_latest(SAMPLE_IV)
        self.assertIsNone(result.readings["00060"].value)  # -999999 sentinel
        self.assertIsNone(result.readings["00010"].value)  # "Ice"

    def test_empty_value_list_is_not_reported(self):
        result = LegacyClient._parse_latest(SAMPLE_IV)
        self.assertNotIn("00400", result.readings)
        # reported params == reading keys
        self.assertEqual(set(result.readings), {"00065", "00060", "00010"})

    def test_empty_timeseries_means_not_reporting(self):
        result = LegacyClient._parse_latest({"value": {"timeSeries": []}})
        self.assertFalse(result.station_reporting)
        self.assertEqual(result.readings, {})

    def test_bad_structure_raises(self):
        with self.assertRaises(UsgsResponseFormatError):
            LegacyClient._parse_latest({})
        with self.assertRaises(UsgsResponseFormatError):
            LegacyClient._parse_latest({"value": None})

    def test_reservoir_alias_00062_keyed_as_canonical(self):
        """A 00062 reservoir-elevation series parses under canonical 62614."""
        data = {"value": {"timeSeries": [{
            "variable": {"variableCode": [{"value": "00062"}]},
            "values": [{"value": [
                {"value": "660.5", "dateTime": "2026-06-11T03:45:00.000-05:00"},
            ]}],
        }]}}
        result = LegacyClient._parse_latest(data)
        self.assertIn("62614", result.readings)
        self.assertNotIn("00062", result.readings)
        self.assertEqual(result.readings["62614"].value, 660.5)


class TestLegacyParseRdb(unittest.TestCase):
    def test_rows_parsed_with_fips_state(self):
        hits = LegacyClient._parse_rdb_sites(RDB)
        self.assertEqual(len(hits), 2)
        by_id = {h.site_id: h for h in hits}
        self.assertEqual(by_id["01460595"].site_name, "DELAWARE CANAL")
        self.assertEqual(by_id["01460595"].state, "PA")  # FIPS 42
        self.assertEqual(by_id["06710247"].state, "CO")  # FIPS 08

    def test_too_few_lines_returns_empty(self):
        self.assertEqual(LegacyClient._parse_rdb_sites("h\tonly\nrow"), [])
        self.assertEqual(LegacyClient._parse_rdb_sites(""), [])


if __name__ == "__main__":
    unittest.main()
