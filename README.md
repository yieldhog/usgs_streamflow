# USGS Streamflow for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/yieldhog/usgs_streamflow.svg)](https://github.com/yieldhog/usgs_streamflow/releases)
![HA Version](https://img.shields.io/badge/Home%20Assistant-%3E%3D%202026.3-brightgreen)

A Home Assistant integration that pulls real-time streamflow data from the [USGS National Water Information System (NWIS)](https://waterservices.usgs.gov/) for any active stream gauge in the United States.

## Features

- **Search by name or site number** — find any active USGS stream gauge via the setup wizard
- **Up to 3 sensors per gauge** — Gauge Height (ft), Discharge (ft³/s), and Water Temperature (°C) where available
- **Station Status sensor** — shows `Active` or `Offline` so seasonal/winter shutdowns are handled cleanly
- **Proper unavailability handling** — sensors mark as `Unavailable` (not `Unknown`) when a gauge is seasonally decommissioned
- **Polled every 15 minutes** — matches USGS data update frequency
- **Multiple gauges** — add as many stations as you want, each becomes its own device

## Requirements

- Home Assistant 2026.3 or newer
- HACS installed

## Installation

### Via HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/yieldhog/usgs_streamflow` as an **Integration**
4. Find **USGS Streamflow** in HACS and click **Download**
5. Restart Home Assistant

### Manual

1. Download the latest release zip from the [releases page](https://github.com/yieldhog/usgs_streamflow/releases)
2. Extract and copy the `custom_components/usgs_streamflow` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **USGS Streamflow**
3. Enter a stream or station name (e.g. `Bear Creek`) and a two-letter state code (e.g. `CO`)
   - You can also paste a USGS site number directly (e.g. `06711565`) — no state code needed
   - Not sure of your site number? [Search on the USGS Water Resources site](https://waterdata.usgs.gov/nwis/rt)
4. Pick your gauge from the results list
5. Repeat to add additional gauges

## Sensors

Each configured gauge creates a device with up to 4 entities:

| Entity | Unit | Notes |
|--------|------|-------|
| Gauge Height | ft | Water level above the gauge datum |
| Discharge | ft³/s | Volumetric flow rate (CFS) |
| Water Temperature | °C | Not available at all gauges |
| Station Status | — | `Active` or `Offline` |

The Station Status entity stays active even when the gauge is offline for the season, and includes an `offline_reason` attribute explaining why.

Measurement sensors for parameters a gauge never reports (e.g., Water Temperature at a gauge with no thermistor) will show as `Unavailable` after the first successful data fetch.

## Data Source

All data comes from the [USGS NWIS Instantaneous Values API](https://waterservices.usgs.gov/rest/IV-Service.html), which is free and requires no API key.

## Contributing

Issues and pull requests welcome at [github.com/yieldhog/usgs_streamflow](https://github.com/yieldhog/usgs_streamflow).
