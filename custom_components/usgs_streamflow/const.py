"""Constants for the USGS Streamflow integration."""

DOMAIN = "usgs_streamflow"

CONF_SITE_ID = "site_id"
CONF_SITE_NAME = "site_name"

SCAN_INTERVAL_MINUTES = 15

# USGS NWIS parameter codes
PARAM_DISCHARGE = "00060"       # Discharge, cubic feet per second
PARAM_GAUGE_HEIGHT = "00065"    # Gauge height, feet
PARAM_WATER_TEMP = "00010"      # Temperature, water, degrees Celsius

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
