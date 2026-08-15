# Tests

The project has **two** suites, both run in CI via
`.github/workflows/tests.yml`:

- **`tests/`** — dependency-free logic tests (this directory), run with the
  standard-library `unittest` runner:

  ```bash
  python -m unittest discover -s tests -t . -v
  ```

  No `homeassistant` install is required. `tests/_ha.py` registers small
  stand-ins for the slice of Home Assistant (plus `aiohttp` and `voluptuous`)
  that the integration imports, so the tests exercise pure logic quickly and
  without pinning a Home Assistant version.

- **`tests_ha/`** — Home Assistant harness tests (config/reauth/options flows,
  setup/unload lifecycle, diagnostics), run against a real HA via
  [`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

  ```bash
  pip install -r requirements_test.txt && pytest
  ```

Combined coverage across both suites is enforced at **≥95%** in CI.

## What's covered

| File | Focus |
|------|-------|
| `test_legacy_client.py` | WaterServices IV parsing (values, sentinel, empty series) and RDB site parsing |
| `test_modern_client.py` | OGC API request building, response parsing, multi-series dedupe, CQL2 search bodies, 429 backoff, pagination, auth/DEMO_KEY |
| `test_coordinator.py` | Backend-independent offline detection and per-reading attribute mapping |
| `test_backend_and_options.py` | Backend factory (with safe default) and the options flow (advanced gating, merge-on-save) |
| `test_api_key_plumbing.py` | API key reaches the client (ignored by legacy); config-flow prefill |
| `test_sensor_coverage.py` | Every supported parameter has exactly one well-formed sensor description; the setup fallback (`_params_to_create`) and the `provisional` attribute |
| `test_history_seed.py` | Rate/trend warm-start seeding, incl. skipping disabled parameters |
| `test_diagnostics.py` | Redaction + the fields the diagnostics dump surfaces |

(`tests/` is the logic half; the Home Assistant flow/lifecycle tests live in
`tests_ha/`.)
