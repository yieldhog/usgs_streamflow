"""Constants for the USGS Streamflow integration."""

import re
from dataclasses import dataclass

DOMAIN = "usgs_streamflow"

CONF_SITE_ID = "site_id"
CONF_SITE_NAME = "site_name"

# Options (set via the integration's Configure / options flow)
CONF_SCAN_INTERVAL = "scan_interval_minutes"
CONF_ENABLED_PARAMETERS = "enabled_parameters"
# api.data.gov key for the modernized USGS Water Data (OGC) API.  Stored per
# entry so each gauge can carry its own key; the legacy backend ignores it.
CONF_API_KEY = "api_key"

# Shared low-volume key issued by api.data.gov.  Used as a fallback when the
# user leaves the key blank, so the modern backend still works for light use
# (subject to a strict rate limit).  The legacy backend never uses this.
DEMO_KEY = "DEMO_KEY"

# Which API backend a config entry polls.  Advanced per-entry option; defaults
# to legacy so existing entries are unaffected until explicitly switched.
CONF_BACKEND = "backend"
BACKEND_LEGACY = "legacy"
BACKEND_MODERN = "modern"

# --- Percent-of-normal / condition statistics (opt-in) ------------------------
# Build day-of-year percentile "envelopes" from the long-term daily-mean record
# and expose Condition / Percentile / %-of-Normal sensors that place the live
# reading against its own history — the WaterWatch / Groundwater Watch view.
#
# Off by default: turning it on triggers a one-time ~30-year daily-values pull
# per gauge (heavy), after which the envelope is persisted on disk and refreshed
# only occasionally.  Enable it per entry in the options flow.
CONF_ENABLE_STATS = "enable_stats"

# USGS NWIS statistic code for the daily *mean* value — the series the envelope
# is built from (matches the statistic USGS WaterWatch percentiles use).
STAT_DAILY_MEAN = "00003"

# Years of daily-mean record requested when (re)building an envelope.
STATS_RECORD_YEARS = 30

# Centered day-of-year window, in days, for grouping historical values per
# calendar day.  0 = exact calendar day (matches USGS WaterWatch daily
# statistics, verified live); a small window trades a little fidelity for
# smoother percentiles on short records.
STATS_WINDOW_DAYS = 0

# Rebuild a cached envelope once it is older than this many days.  The long-term
# distribution barely shifts day to day, so an infrequent refresh keeps the heavy
# fetch rare while still folding in new water years.
STATS_REFRESH_DAYS = 30

# Minimum distinct daily values a calendar day needs before we report a
# percentile for it, so one or two years of record can't yield a meaningless
# classification.
STATS_MIN_SAMPLES = 10

# Polling cadence.  USGS instantaneous-values data typically refreshes about
# every 15 minutes, so polling faster just adds load without yielding new data.
DEFAULT_SCAN_INTERVAL_MINUTES = 15
MIN_SCAN_INTERVAL_MINUTES = 15
MAX_SCAN_INTERVAL_MINUTES = 1440

# Derived (rate-of-change / trend) sensor settings.
# Rate is computed over a trailing window of actual USGS observations and
# expressed per hour; the buffer is retained a bit longer than the window
# so a full window is available even when transmissions arrive in bursts.
DERIVED_RATE_WINDOW_MINUTES = 60
HISTORY_RETENTION_MINUTES = 180
# Require at least this much elapsed time between the oldest and newest
# samples before reporting a rate, so a pair of near-simultaneous readings
# can't produce an enormous extrapolated value.
MIN_RATE_SPAN_MINUTES = 5

# USGS NWIS parameter codes.
PARAM_DISCHARGE = "00060"             # Discharge, cubic feet per second
PARAM_GAUGE_HEIGHT = "00065"          # Gauge height, feet
PARAM_WATER_TEMP = "00010"            # Temperature, water, degrees Celsius
PARAM_SPECIFIC_CONDUCTANCE = "00095"  # Specific conductance, uS/cm @ 25C
PARAM_DISSOLVED_OXYGEN = "00300"      # Dissolved oxygen, mg/L
PARAM_DO_PCT_SAT = "00301"            # Dissolved oxygen, percent saturation
PARAM_PH = "00400"                    # pH, standard units
PARAM_TURBIDITY = "63680"             # Turbidity, FNU
PARAM_PRECIPITATION = "00045"         # Precipitation, inches (incremental)
PARAM_GW_DEPTH = "72019"              # Depth to water level, ft below land surface
# Weather / atmospheric
PARAM_AIR_TEMP = "00020"              # Air temperature, degrees Celsius
PARAM_REL_HUMIDITY = "00052"          # Relative humidity, percent
PARAM_WIND_SPEED = "00035"            # Wind speed, miles per hour
PARAM_WIND_DIR = "00036"              # Wind direction, degrees clockwise from N
# Extended water quality
PARAM_SALINITY = "00480"              # Salinity, parts per thousand
PARAM_NITRATE = "99133"              # Nitrate + nitrite, mg/L as nitrogen
PARAM_CHLOROPHYLL = "32316"           # Chlorophyll relative fluorescence, RFU
# Lake / reservoir & velocity
PARAM_RESERVOIR_ELEV = "62614"        # Lake/reservoir water-surface elevation, ft
PARAM_RESERVOIR_STORAGE = "00054"     # Reservoir storage, acre-feet
PARAM_DISCHARGE_TIDAL = "72137"       # Discharge, tidally filtered, ft³/s
PARAM_VELOCITY = "72255"              # Mean water velocity, ft/s

# Ordered map of every parameter the integration understands -> short label.
# Single source of truth for which codes are fetched (coordinator) and which
# can be toggled (options flow).  sensor.py attaches HA units/device classes.
SUPPORTED_PARAMETERS: dict[str, str] = {
    PARAM_DISCHARGE: "Discharge",
    PARAM_GAUGE_HEIGHT: "Gauge Height",
    PARAM_WATER_TEMP: "Water Temperature",
    PARAM_SPECIFIC_CONDUCTANCE: "Specific Conductance",
    PARAM_DISSOLVED_OXYGEN: "Dissolved Oxygen",
    PARAM_DO_PCT_SAT: "Dissolved Oxygen (% Saturation)",
    PARAM_PH: "pH",
    PARAM_TURBIDITY: "Turbidity",
    PARAM_PRECIPITATION: "Precipitation",
    PARAM_GW_DEPTH: "Depth to Water Level",
    PARAM_AIR_TEMP: "Air Temperature",
    PARAM_REL_HUMIDITY: "Relative Humidity",
    PARAM_WIND_SPEED: "Wind Speed",
    PARAM_WIND_DIR: "Wind Direction",
    PARAM_SALINITY: "Salinity",
    PARAM_NITRATE: "Nitrate",
    PARAM_CHLOROPHYLL: "Chlorophyll",
    PARAM_RESERVOIR_ELEV: "Reservoir Elevation",
    PARAM_RESERVOIR_STORAGE: "Reservoir Storage",
    PARAM_DISCHARGE_TIDAL: "Discharge (Tidally Filtered)",
    PARAM_VELOCITY: "Water Velocity",
}

@dataclass(frozen=True)
class StatsParamConfig:
    """How a parameter's statistics behave.

    ``invert`` flips the percentile / condition / %-of-normal so a *higher raw
    reading* reads as *below normal* — needed for depth-to-water, where deeper
    means less groundwater.

    ``percent_of_normal`` gates the "% of Normal" sensor.  It is meaningful only
    for quantities with a real zero (discharge: no flow; depth-to-water: land
    surface).  Gauge height is measured from an arbitrary local datum, so a ratio
    to the median has no physical meaning — its Condition and Percentile are
    still valid (comparing the gauge to its own history, the datum cancels), but
    it gets no % of Normal.
    """

    invert: bool = False
    percent_of_normal: bool = True


# Parameters that get statistics sensors and how each behaves.  Confirmed live
# against USGS WaterWatch (discharge) and Groundwater Watch (depth-to-water).
STATS_PARAMS: dict[str, StatsParamConfig] = {
    PARAM_DISCHARGE: StatsParamConfig(invert=False),
    PARAM_GW_DEPTH: StatsParamConfig(invert=True),
    PARAM_GAUGE_HEIGHT: StatsParamConfig(invert=False, percent_of_normal=False),
}

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
# Legacy daily-values service — long-term daily statistics for envelope building.
USGS_DV_URL = "https://waterservices.usgs.gov/nwis/dv/"

# Shared site-number parsing.  Used by the config flow to validate user input and
# by the client backends to decide between a direct site-number lookup and a
# name search.  Kept here (rather than in config_flow or client) so both can
# import it without a circular dependency.
#
# USGS site numbers are 6-15 digits: surface-water sites are typically 8, while
# groundwater, combined-sewer, and other lat/long-based IDs run to 15.
SITE_NUMBER_RE = re.compile(r"^\d{6,15}$")
_AGENCY_PREFIX_RE = re.compile(r"^[A-Za-z]+-")


def normalize_site_number(raw: str) -> str:
    """Strip whitespace and an optional agency prefix (e.g. 'USGS-') from input.

    WDFN monitoring-location pages display these IDs with a 'USGS-' prefix, so
    we strip it before deciding whether the input is a site number.
    """
    return _AGENCY_PREFIX_RE.sub("", raw.strip())
