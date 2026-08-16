"""Shared constants and payload builders for the HA harness suite."""
from __future__ import annotations

from datetime import datetime, timezone

SITE_ID = "01646500"
SITE_NAME = "POTOMAC RIVER NEAR WASH, DC"


def recent_iso() -> str:
    """An ISO timestamp within the freshness window so the station reads online."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iv_json(*params: str) -> dict:
    """A minimal NWIS instantaneous-values JSON payload for the given params."""
    when = recent_iso()
    return {
        "value": {
            "timeSeries": [
                {
                    "variable": {"variableCode": [{"value": code}]},
                    "values": [{"value": [{"value": "1940", "dateTime": when}]}],
                }
                for code in params
            ]
        }
    }


def dv_json_today(n_years: int = 6) -> dict:
    """A legacy `dv` response with N yearly daily-mean values on today's calendar day.

    Enough distinct years (>= STATS_MIN_SAMPLES) for today's day-of-year bucket
    so an envelope builds and the stats sensors report.
    """
    today = datetime.now(timezone.utc).date()
    values = []
    for k in range(1, n_years + 1):
        year = today.year - k
        try:
            day = today.replace(year=year)
        except ValueError:  # today is Feb 29 and `year` isn't a leap year
            day = today.replace(year=year, day=28)
        values.append({"value": str(1000 + k * 100), "dateTime": day.isoformat()})
    return {"value": {"timeSeries": [{"values": [{"value": values}]}]}}


def modern_latest_json(*params: str) -> dict:
    """A latest-continuous FeatureCollection for the modern backend."""
    when = recent_iso()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "parameter_code": code,
                    "time": when,
                    "value": "1940",
                    "statistic_id": "00011",
                },
            }
            for code in params
        ],
        "links": [],
    }


def modern_tsm_json(*params: str) -> dict:
    """A time-series-metadata FeatureCollection (capability gating / key check)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "parameter_code": code,
                    "computation_period_identifier": "Points",
                },
            }
            for code in params
        ],
        "links": [],
    }


def modern_sites_json(*sites: tuple[str, str]) -> dict:
    """A monitoring-locations FeatureCollection for the modern site search.

    Each ``site`` is ``(number, name)``; defaults to the canonical test site.
    """
    if not sites:
        sites = ((SITE_ID, SITE_NAME),)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "monitoring_location_id": f"USGS-{num}",
                    "monitoring_location_number": num,
                    "monitoring_location_name": name,
                    "site_type": "Stream",
                },
            }
            for num, name in sites
        ],
        "links": [],
    }


# An empty FeatureCollection (no matches / station reporting nothing).
EMPTY_FC = {"type": "FeatureCollection", "features": [], "links": []}


# A minimal RDB (tab-delimited) site-search response the legacy parser accepts.
# (Retained for any legacy-path tests; the config flow now searches via modern.)
SITE_RDB = (
    "# USGS site service\n"
    "agency_cd\tsite_no\tstation_nm\tsite_tp_cd\tdec_lat_va\tdec_long_va\tstate_cd\n"
    "5s\t15s\t30s\t5s\t16s\t16s\t5s\n"
    f"USGS\t{SITE_ID}\t{SITE_NAME}\tST\t38.9\t-77.1\t24\n"
)
