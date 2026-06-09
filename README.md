# USGS Streamflow for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/yieldhog/usgs_streamflow.svg)](https://github.com/yieldhog/usgs_streamflow/releases)
![HA Version](https://img.shields.io/badge/Home%20Assistant-%3E%3D%202026.3-brightgreen)

A Home Assistant integration that pulls real-time data from the [USGS National Water Information System (NWIS)](https://waterservices.usgs.gov/) for any active monitoring location in the United States — streams, lakes and reservoirs, canals, groundwater wells, and continuous water-quality sites.

## Features

- **Search by name or site number** — find any active USGS monitoring location via the setup wizard, across all site types (not just streams)
- **Up to 10 measurement sensors per site** — discharge, gauge height, water temperature, specific conductance, dissolved oxygen, % saturation, pH, turbidity, precipitation, and depth to water level — each created only where the site actually reports it
- **Station Status sensor** — shows `Active` or `Offline` so seasonal/winter shutdowns are handled cleanly
- **Proper unavailability handling** — sensors mark as `Unavailable` (not `Unknown`) when a gauge is seasonally decommissioned
- **Configurable poll interval** — defaults to 15 minutes (matching USGS update frequency); adjustable per site
- **Parameter selection** — choose which measurements become sensors to keep multi-parameter sites tidy
- **Multiple sites** — add as many locations as you want, each becomes its own device

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

### Beta / pre-release versions

To try a pre-release before it ships to everyone: open **USGS Streamflow** in HACS → three-dot menu → **Redownload** → **"Need a different version?"** → select the beta version, then restart Home Assistant. Pre-releases are hidden from the normal update flow, so you opt in explicitly.

### Manual

1. Download the latest release zip from the [releases page](https://github.com/yieldhog/usgs_streamflow/releases)
2. Extract and copy the `custom_components/usgs_streamflow` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **USGS Streamflow**
3. Enter a site or station name (e.g. `Bear Creek`) and a two-letter state code (e.g. `CO`)
   - You can also paste a USGS site number directly (e.g. `06711565`) — no state code needed
   - Not sure of your site number? Browse the [USGS National Water Dashboard](https://dashboard.waterdata.usgs.gov/) and filter by parameter
4. Pick your site from the results list
5. Repeat to add additional sites

## Configuration (Options)

After a site is added, click **Configure** on the integration entry to adjust:

- **Update interval** — how often to poll USGS, in minutes (minimum 15; lower values add load without new data, since instantaneous values refresh about every 15 minutes)
- **Parameters to show** — uncheck any measurements you don't want as sensors. Unchecked parameters won't create sensors even if the site reports them.

Changing options reloads the integration entry.

## Sensors

Each configured site creates a device. A measurement sensor is created for each parameter the site reports (and that you've enabled in options), plus a Station Status sensor:

| Entity | Unit | Notes |
|--------|------|-------|
| Discharge | ft³/s | Volumetric flow rate (CFS) |
| Gauge Height | ft | Water level above the gauge datum |
| Water Temperature | °C | |
| Specific Conductance | µS/cm | At 25 °C |
| Dissolved Oxygen | mg/L | |
| Dissolved Oxygen (% Saturation) | % | |
| pH | — | Standard units |
| Turbidity | FNU | |
| Precipitation | in | Incremental per reporting interval, not an accumulating total |
| Depth to Water Level | ft | Below land surface (groundwater wells) |
| Station Status | — | `Active` or `Offline` |

Sensors are created only for parameters a site actually reports — you won't get empty entities for measurements a site doesn't have. The Station Status entity stays active even when the site is offline for the season and includes an `offline_reason` attribute explaining why.

## Examples

The [`examples/usgs_streamflow_examples.yaml`](examples/usgs_streamflow_examples.yaml) package shows how to turn these sensors into trends, alerts, and condition flags using only Home Assistant's built-in integrations (statistics, derivative, trend, threshold, template, automation):

- **Rate of rise (ft/hr)** and a **Rising / Falling / Steady** trend sensor
- **Rolling statistics** — 24-hour average discharge, 7-day max gauge height, net change
- **Flow Condition** and a **Good To Fish** flag based on your own thresholds
- **Alerts** — rapid rise, action-stage crossing, gauge offline, and high water temperature

Replace the placeholder entity ids with your own and tune the thresholds — flow ranges and flood/action stages are specific to your water body and (for stage) the gauge's datum.

## Data Source

All data comes from the [USGS NWIS Instantaneous Values API](https://waterservices.usgs.gov/rest/IV-Service.html), which is free and requires no API key.

## Contributing

Issues and pull requests welcome at [github.com/yieldhog/usgs_streamflow](https://github.com/yieldhog/usgs_streamflow).
