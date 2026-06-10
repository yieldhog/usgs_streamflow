"""Constants for the USGS Streamflow integration."""

DOMAIN = "usgs_streamflow"

CONF_SITE_ID = "site_id"
CONF_SITE_NAME = "site_name"

# Options (set via the integration's Configure / options flow)
CONF_SCAN_INTERVAL = "scan_interval_minutes"
CONF_ENABLED_PARAMETERS = "enabled_parameters"

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
}

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
