# Tests

Dependency-free unit tests for the integration's logic, run with the standard
library `unittest` runner:

```bash
python -m unittest discover -s tests -t . -v
```

No `homeassistant` install is required. `tests/_ha.py` registers small
stand-ins for the slice of Home Assistant (plus `aiohttp` and `voluptuous`)
that the integration imports, so the tests exercise pure logic quickly and
without pinning a Home Assistant version. They run on every push/PR via
`.github/workflows/tests.yml`.

## What's covered

| File | Focus |
|------|-------|
| `test_legacy_client.py` | WaterServices IV parsing (values, sentinel, empty series) and RDB site parsing |
| `test_modern_client.py` | OGC API request building, response parsing, multi-series dedupe, CQL2 search bodies, 429 backoff, pagination, auth/DEMO_KEY |
| `test_coordinator.py` | Backend-independent offline detection and per-reading attribute mapping |
| `test_backend_and_options.py` | Backend factory (with safe default) and the options flow (advanced gating, merge-on-save) |
| `test_api_key_plumbing.py` | API key reaches the client (ignored by legacy); config-flow prefill |
| `test_sensor_coverage.py` | Every supported parameter has exactly one well-formed sensor description |

## Scope and future work

These are **logic** tests by design. Full Home Assistant integration tests
(config-entry setup, entity lifecycle, the reload-on-options path) would use
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
against a real Home Assistant install, and are a good future addition once the
project is ready to take on that test dependency.
