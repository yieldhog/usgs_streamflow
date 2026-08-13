# Home Assistant Core Readiness — Quality Scale Checklist

Tracking this integration against Home Assistant's [Integration Quality
Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
(IQS), as if submitting to **Home Assistant Core**. Today it ships as a HACS
custom component; this file records what is already satisfied and what remains
for each tier.

**Legend:** ✅ done · ⚠️ partial · ❌ to do · ➖ not applicable (exempt)

## Current standing

- **Bronze:** code-complete. `config-flow-test-coverage` tests are written on the
  HA harness (`tests_ha/`) and await a first `pytest` run; the only non-code item
  left is `brands` (a logo PR to home-assistant/brands).
- **Silver:** `parallel-updates` and `reauthentication-flow` done. Remaining:
  run the harness suite under `pytest-homeassistant-custom-component` and confirm
  measured ≥95% `test-coverage`.
- **Gold:** `diagnostics`, `entity-category`, `devices`, and all doc rules done;
  `reconfiguration-flow`/`dynamic-devices`/`discovery`/`stale-devices` are N/A.
  Open: the **translations trio** (`entity-translations`, `icon-translations`,
  `exception-translations`) and `entity-disabled-by-default` / `repair-issues`.
- **Platinum:** async + injected websession done; `strict-typing` open.

The translations trio changes every entity's rendered name/icon, so it is best
done as one pass **after** the harness CI is green (it can't be verified without
rendering in a running HA).

Recommended near-term target: **finish the Bronze/Silver test-harness migration,
then Gold.** `manifest.json` does not yet declare `quality_scale`; add it once a
tier is fully green (Bronze once brands + harness config-flow tests land).

---

## Pre-core structural work (not IQS rules, but required to merge into core)

- [ ] ❌ Move `custom_components/usgs_streamflow/` into the core repo; drop
      `hacs.json` and the `version` key from `manifest.json` (core integrations
      are versioned by HA itself).
- [ ] ❌ Add `"quality_scale": "bronze"` (or higher) to `manifest.json` once the
      tier is met.
- [ ] ⚠️ Port the test suite to `pytest-homeassistant-custom-component` (the core
      test framework). **In progress:** an HA-harness suite now lives in
      `tests_ha/` (config-flow, reauth, setup/unload/reauth lifecycle) alongside
      the dependency-free `tests/` logic suite, and a **CI job** (`harness` in
      `.github/workflows/tests.yml`) runs it with coverage. Run locally with
      `pip install -r requirements_test.txt && pytest`. These harness tests were
      authored without a local HA install, so the first CI run may need minor
      fixture tweaks; measure coverage there and close the gap to ≥95%.
- [ ] ✅ No third-party runtime requirements (`requirements: []`) — nothing to
      vendor or vet.

---

## Bronze

- [x] ➖ **action-setup** — no service actions are registered (exempt).
- [x] ✅ **appropriate-polling** — 15-min default matches USGS' ~15-min cadence
      (min 15, configurable); the stats envelope refreshes only ~monthly.
- [ ] ❌ **brands** — needs a logo/icon PR to
      [home-assistant/brands](https://github.com/home-assistant/brands) for the
      `usgs_streamflow` domain.
- [x] ✅ **common-modules** — logic lives in `coordinator.py`, `client.py`,
      `stats_coordinator.py`, `streamflow_stats.py`. (A shared `entity.py` base
      would tidy the repeated `DeviceInfo`/`unique_id` wiring — nice-to-have.)
- [ ] ⚠️ **config-flow-test-coverage** — HA-harness config-flow tests added in
      `tests_ha/test_config_flow.py` (user happy path, no-results, cannot_connect,
      name-search-needs-state, duplicate abort, reauth invalid+valid, options
      flow). Pending first `pytest` run to confirm/measure.
- [x] ✅ **config-flow** — UI setup; `data` holds the site, `options` hold
      settings; `strings.json` provides `data_description` for each field.
- [x] ✅ **dependency-transparency** — only HA-provided libs (aiohttp) are used.
- [x] ➖ **docs-actions** — no actions (exempt).
- [x] ✅ **docs-high-level-description** — README opening + Features.
- [x] ✅ **docs-installation-instructions** — README HACS + manual install.
- [x] ✅ **docs-removal-instructions** — README "Removing the integration";
      `async_remove_entry` also deletes the persisted stats cache on removal.
- [x] ✅ **entity-event-setup** — all entities are `CoordinatorEntity`
      subclasses; subscriptions happen through the coordinator lifecycle.
- [x] ✅ **entity-unique-id** — every entity sets a stable `unique_id`.
- [x] ✅ **has-entity-name** — `_attr_has_entity_name = True` throughout.
- [x] ✅ **runtime-data** — coordinators are stored on `entry.runtime_data`
      (`UsgsConfigEntry = ConfigEntry[UsgsRuntimeData]`); no hass.data table.
- [x] ✅ **test-before-configure** — the config flow performs a live site search
      before creating the entry, surfacing `cannot_connect` on failure.
- [x] ✅ **test-before-setup** — `async_config_entry_first_refresh()` raises
      `ConfigEntryNotReady` when the first poll fails. *(See reauth for
      distinguishing auth failures.)*
- [x] ✅ **unique-config-entry** — `async_set_unique_id("usgs_<site>")` +
      `_abort_if_unique_id_configured()` prevents duplicate gauges.

---

## Silver

- [x] ➖ **action-exceptions** — no actions (exempt).
- [x] ✅ **config-entry-unloading** — `async_unload_entry` unloads the sensor
      platform and pops state; the stats→source listener is torn down via
      `entry.async_on_unload(...)`.
- [x] ✅ **docs-configuration-parameters** — README options table +
      `data_description` strings.
- [x] ✅ **docs-installation-parameters** — search term, site, and API key are
      documented in setup docs.
- [x] ✅ **entity-unavailable** — sensors implement `available`; offline/seasonal
      detection and "no envelope / no reading yet" all resolve to unavailable.
- [x] ✅ **integration-owner** — `codeowners: ["@yieldhog"]`.
- [x] ⚠️ **log-when-unavailable** — provided by `DataUpdateCoordinator` (logs the
      first failure, suppresses repeats, logs recovery). Confirm this is
      sufficient for the stats coordinator too.
- [x] ✅ **parallel-updates** — `PARALLEL_UPDATES = 0` declared in `sensor.py`.
- [x] ✅ **reauthentication-flow** — clients declare `uses_auth`; the coordinator
      maps HTTP 401/403 on the authenticated (Modern) backend to
      `ConfigEntryAuthFailed`, and the config flow's `async_step_reauth` /
      `async_step_reauth_confirm` validate and store a new key, then reload.
      *(Legacy is unauthenticated, so its 401/403 stays a retryable failure.)*
- [ ] ⚠️ **test-coverage** — strong logic tests (`tests/`) plus a new HA-harness
      suite (`tests_ha/`). Still need to run under `pytest-homeassistant-custom-
      component`, measure coverage, and close to ≥95% across all modules.

---

## Gold

- [x] ✅ **devices** — each gauge is a device via `DeviceInfo`.
- [x] ✅ **diagnostics** — `diagnostics.py` dumps entry data/options (API key
      redacted), coordinator state, and stats envelope metadata + results.
- [x] ➖ **discovery** — USGS gauges aren't network-discoverable; the user
      searches and selects a site (exempt).
- [x] ➖ **discovery-update-info** — no discovery (exempt).
- [x] ✅ **docs-data-update** — README "How it works" + "Data sources" explain
      polling and the cached envelope.
- [x] ✅ **docs-examples** — README "Automation examples".
- [x] ✅ **docs-known-limitations** — consolidated "Known limitations" section
      (history/datum requirements, tidal noise, provisional data, cadence, beta).
- [x] ✅ **docs-supported-devices** — supported site types / parameters are
      described.
- [x] ✅ **docs-supported-functions** — every sensor is documented.
- [x] ✅ **docs-troubleshooting** — README "Troubleshooting".
- [x] ✅ **docs-use-cases** — explicit "Use cases" section (flood watch,
      drought/percent-of-normal, recreation, groundwater, dashboards).
- [x] ➖ **dynamic-devices** — one device per config entry; nothing to add
      dynamically (exempt).
- [x] ✅ **entity-category** — Station Status is `EntityCategory.DIAGNOSTIC`.
- [x] ✅ **entity-device-class** — device classes used where they exist
      (temperature, humidity, distance, speed, pH, wind speed, enum for
      trend/condition); custom units (cfs, FNU…) correctly carry none.
- [ ] ⚠️ **entity-disabled-by-default** — all reported parameters are enabled;
      consider disabling the less-common water-quality entities by default.
- [ ] ❌ **entity-translations** — entity names are English `_attr_name` /
      description names. Move to `translation_key` + entity translations in
      `strings.json`.
- [ ] ❌ **exception-translations** — `UpdateFailed` / flow errors are English
      f-strings; move user-facing messages to translatable keys.
- [ ] ❌ **icon-translations** — icons are set via `_attr_icon` / descriptions;
      migrate to `icons.json` icon translations (incl. state-based trend/
      condition icons).
- [x] ➖ **reconfiguration-flow** — every editable setting (interval, parameters,
      API key, backend, stats) is already in the options flow, and the site is
      the entry's immutable identity, so a separate reconfigure flow would be
      redundant. Revisit if API key/backend move out of options.
- [ ] ⚠️ **repair-issues** — optional but valuable: raise a repair issue for
      "using rate-limited DEMO_KEY" or "gauge decommissioned / no longer
      reporting".
- [x] ➖ **stale-devices** — single device per entry, removed on unload (exempt).

---

## Platinum

- [x] ✅ **async-dependency** — no sync dependency; all I/O is async (aiohttp).
- [x] ✅ **inject-websession** — uses `async_get_clientsession(hass)` rather than
      creating its own session.
- [ ] ⚠️ **strict-typing** — code is type-annotated but not yet verified under
      HA's strict-typing mypy config; add the domain to `.strict-typing` and make
      it pass.

---

## Error handling & robustness (already addressed)

Not IQS rules by name, but core review scrutinizes these — current state:

- ✅ All USGS calls raise typed `UsgsClientError` subclasses; the coordinator
      translates them to `UpdateFailed` (→ `ConfigEntryNotReady` on first poll).
- ✅ The percent-of-normal envelope fetch is best-effort: a failure is logged and
      never blocks setup; the cached envelope is kept and retried later.
- ✅ Rate/trend warm-start (`_seed_history`) is fully non-fatal — it runs outside
      the poll's error handling, so it catches everything and can never fail a
      poll.
- ✅ Cache read/write (`Store`) failures are caught: a bad read rebuilds, a failed
      write keeps the in-memory envelope.
- ✅ Missing-data sentinels (`-999999`), non-numeric values, and unparseable
      timestamps are dropped rather than surfaced.
- ✅ Auth failures (401/403 on the authenticated backend) raise
      `ConfigEntryAuthFailed` → reauth flow, instead of retrying a bad key
      forever.
- ✅ Entry removal deletes the persisted stats cache (`async_remove_entry`), so
      no orphaned files remain.
