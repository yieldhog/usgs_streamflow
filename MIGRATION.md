# USGS Streamflow — Legacy → Modernized API Migration

**Status:** Planning complete (Gate 0 closed). No production code changed yet.
**Last verified:** 2026-06-10
**Target integration version for the migration beta:** `1.2.0bX`
**Current shipped version:** `1.1.0b2`

This document is the single source of truth for moving `usgs_streamflow` off the
deprecating USGS **WaterServices** API (`waterservices.usgs.gov`) and onto the
modernized **USGS Water Data OGC API** (`api.waterdata.usgs.gov/ogcapi`).

### Evidence convention

Every factual claim below is tagged:

- ✅ **Verified** — confirmed this planning cycle against the live API, the
  published OGC schema, or the official USGS docs. The verification method is
  stated inline or in [§9 Verification log](#9-verification-log).
- ⚙️ **Design decision** — our choice; not dictated by the API.
- ⚠️ **Unverified / assumption** — documented but not independently confirmed, or
  observed once and not generalized. Must be confirmed before code depends on it.

> Rule for this migration: **do not write code against any ⚠️ item.** Promote it
> to ✅ with a test first.

---

## 1. Why migrate

- ✅ The legacy WaterServices endpoints are slated for **decommission in Q1 2027**.
  USGS has stated no intentional degradation before **August 2026**, after which
  reliability is not guaranteed.
- ✅ The replacement is the OGC-API service at `https://api.waterdata.usgs.gov/ogcapi/v0`.
- ✅ The service is **alpha / v0** (server reports `wda_build_version 0.49.2`,
  `pygeoapi 0.23.0`, environment `production`). A **v1 will ship at a new URL**;
  v0 is explicitly "should not be relied upon for production workloads."
- ⚙️ Implication: isolate every USGS call behind one client module so the eventual
  `v0 → v1` base-URL change (and any alpha churn) is a one-file edit.

---

## 2. Current state of the integration (`1.1.0b2`)

Files under `custom_components/usgs_streamflow/`:

| File | Role | Touched by migration? |
|---|---|---|
| `__init__.py` | entry setup, coordinator wiring, options reload | Minimal (pass API key/backend through) |
| `const.py` | parameter table, intervals, derived-sensor config | Add modern URLs / backend constants |
| `coordinator.py` | **legacy fetch + parse**, history buffer | Fetch/parse moves behind client |
| `config_flow.py` | **legacy site search**, options flow | Search moves behind client; add key/backend |
| `sensor.py` | measurement + status + rate/trend entities | **No change** (backend-agnostic) |
| `manifest.json` | version, requirements | Version bump only |

Key invariants the migration must preserve:

- ✅ `SUPPORTED_PARAMETERS` (10 codes): `00060` discharge, `00065` gauge height,
  `00010` water temp, `00095` specific conductance, `00300` dissolved oxygen,
  `00301` DO % sat, `00400` pH, `63680` turbidity, `00045` precipitation,
  `72019` depth to water.
- ✅ `CoordinatorData` shape: `values{param→float|None}`,
  `reading_times{param→datetime|None}`, `station_offline`, `offline_reason`,
  `reported_params`.
- ✅ Phantom-avoidance: a sensor is created only for a parameter the station
  actually reports.
- ✅ Site-number normalization: `_normalize_site_number()` strips an optional
  `USGS-` agency prefix and whitespace; `_SITE_NUMBER_RE = ^\d{6,15}$`.
- ✅ Derived **rate/trend** sensors compute from `CoordinatorData` only, so they are
  **backend-independent** and need no migration changes.

---

## 3. Verified API reference

Base: `https://api.waterdata.usgs.gov/ogcapi/v0`

### 3.1 Authentication & rate limiting

- ✅ A key is required above a low free tier; without one, heavy use returns
  **HTTP 429**.
- ✅ Keys are issued by **api.data.gov**; `DEMO_KEY` works for low-volume testing.
- ✅ The key may be passed as the `api_key` **URL parameter** or the `X-Api-Key`
  **header** (both observed working).
- ✅ Responses report remaining budget (observed: a `Remaining requests this hour`
  indicator in the official R client output).
- ⚠️ Exact per-key hourly limits and the full set of `X-RateLimit-*` header names
  are **not confirmed**; treat the budget as "read the remaining-requests signal,
  back off on 429."

### 3.2 Endpoint mapping (legacy → modern)

| Legacy (`waterservices.usgs.gov`) | Modern (`api.waterdata.usgs.gov/ogcapi/v0`) | Used by us |
|---|---|---|
| `/nwis/iv/` (latest) | `/collections/latest-continuous` | ✅ **Yes — the poll** |
| `/nwis/iv/` (history) | `/collections/continuous` | No (we only need latest) |
| `/nwis/site/` (search/metadata) | `/collections/monitoring-locations` | ✅ **Yes — site search** |
| `/nwis/site/` (series availability) | `/collections/time-series-metadata` | ✅ **Yes — capability gating** |
| `/nwis/dv/` | `/collections/daily`, `/latest-daily` | No |
| `/measurements/` | `/collections/field-measurements`, `/channel-measurements` | No |

All three endpoints we depend on are ✅ confirmed present in the live
`/collections` inventory, alongside `daily`, `latest-daily`, `combined-metadata`,
`field-measurements`, `channel-measurements`, `peaks`, `parameter-codes`,
`statistic-codes`, and code-lookup collections.

### 3.3 `latest-continuous` — the poll endpoint (✅ schema verified)

Field list from the published collection schema, with parser guidance:

| Field | Type | Parser guidance |
|---|---|---|
| `monitoring_location_id` | string | Format `USGS-<number>` (e.g. `USGS-02238500`). Strip prefix to match our `site_id`. |
| `parameter_code` | string | 5-digit; our `SUPPORTED_PARAMETERS` key. |
| `value` | **string** | **Transmitted as a string to preserve precision** → `float(value)`. |
| `time` | string (RFC 3339) | The observation timestamp → maps to `reading_times[param]`. |
| `approval_status` | string | `"Approved"` or `"Provisional"` (scalar). Expose as attribute. |
| `qualifier` | string\|null | Ice-affected / estimated flags. Expose as attribute. |
| `statistic_id` | string | Instantaneous values observed as `00011`. (`00003`=mean, `00001`=max, `00002`=min.) |
| `time_series_id` | string | Stable series id; equals the `id` field in `time-series-metadata`. |
| `unit_of_measure` | string | Human-readable units. |
| `last_modified` | string (RFC 3339) | DB refresh time — **not** measurement time. |
| `id` | string (uuid) | ⚠️ **Volatile** — regenerated on every record refresh. **Never** use as a stable key. |

Response envelope: ✅ a GeoJSON `FeatureCollection` with `features[]`,
`numberReturned`, `links[]` (incl. `next` when paginated), and `timeStamp`.

**Settled inconsistency:** earlier USGS examples showed `approvals_status` /
`timeseries_id` — those were the `daily` collection. For `latest-continuous` the
canonical names are the **singular** `approval_status` and `time_series_id`
(✅ confirmed in the schema *and* in a live response, [§9](#9-verification-log)).

**Observation identity:** because `id` is volatile, a unique observation is
`(time_series_id, time)`. ⚙️ We don't persist observations, so this only matters
for the in-memory dedupe in the rate/trend buffer (which already keys on `time`).

### 3.4 `time-series-metadata` — capability & thresholds (✅ verified earlier)

Per-series records for a monitoring location. Key fields:

- `parameter_code`, `parameter_name`, `unit_of_measure`, `statistic_id`
- `computation_identifier` — e.g. `"Instantaneous"`, `"Mean"`
- `computation_period_identifier` — e.g. `"Points"`, `"Daily"`, `"Water Year"`
- `begin`, `end` — period of record (usable for offline/seasonal detection)
- `monitoring_location_id`, `sublocation_identifier`, `primary`, `web_description`
- `id` — the time series id (joins to `latest-continuous.time_series_id`)
- `thresholds[]` — ✅ **includes NWS flood stage** (e.g. a
  `"National Weather Service Floodstage"` reference value) plus operational limits.
  (Relevant to a future flood-status feature; not in scope for this migration.)

✅ **Continuous / "IV-equivalent" series** are identified by
`computation_period_identifier = "Points"` (with `computation_identifier =
"Instantaneous"`). This is the modern replacement for the legacy `hasDataTypeCd=iv`
filter — there is no single search flag; you query this endpoint per site.

### 3.5 `monitoring-locations` — site search (✅ queryables + live tests)

✅ Filterable fields (from the queryables document): `monitoring_location_number`,
`monitoring_location_name`, `state_code`, `state_name`, `county_code`,
`site_type_code`, `site_type`, `agency_code`, `id`, plus ~40 more (HUC, lat/long,
well construction, aquifer, etc.).

**Search mechanics — tested live ([§9](#9-verification-log)):**

| Mechanism | Result |
|---|---|
| `POST` CQL2 `{"op":"like",...}` on `monitoring_location_name` | ✅ **Works** — the supported substring search |
| `GET ?filter=... LIKE ...&filter-lang=cql2-text` | ✅ Tested → **returns nothing; unusable** |
| `GET ?q=<term>` | ✅ Tested → **ignores the term** (returns first page); a trap, do not use |
| `LIKE` case sensitivity | ✅ **Case-sensitive** — `%harvard%` matched nothing; `%HARVARD%` matched |
| `CASEI()` wrapper | ✅ Tested → server returns `Bad CQL JSON`; **not supported** in this build |
| `POST` CQL2 `and(like, =agency_code 'USGS')` | ✅ **Works** — agency scoping server-side; drops EPA/STORET partner rows |

⚠️ **URL-parameter equality on `monitoring-locations`** (e.g. `?state_code=44&
site_type_code=ST`) was tested and returned **mixed-state results** (a
Massachusetts site under `state_code=44`), so its semantics/units are **not
confirmed**. Prefer the CQL2 `=` operator (which *is* verified, via the
`agency_code='USGS'` test) for any equality filter here, and confirm `state_code`
units (FIPS numeric vs. postal) before using it.

> Note (verified): `latest-continuous` equality via **URL params**
> (`?monitoring_location_id=...&parameter_code=...`) **does** work cleanly — see
> the live Wickford response in [§10](#10-appendix-verified-requests). The
> URL-param ambiguity above is specific to `monitoring-locations`.

### 3.6 CQL2 (✅ operators verified)

- ✅ Supported: `in`, `between`, `isNull`, `and`, `or`, `not`, `=`, `like` (POST).
- ✅ `POST` body content type: `application/query-cql-json`.
- ✅ Combinable with URL params (`limit`, `f`, `api_key`).
- ✅ Multi-value example: `{"op":"in","args":[{"property":"parameter_code"},["00060","00065"]]}`.
- ❌ Not available in this build: `casei` / `CASEI()`.
- ⚠️ `like` via the **GET `filter=`** form did not work in testing; only the
  **POST** form is confirmed.

### 3.7 Query conventions (✅ verified)

- `datetime`: RFC 3339 single value, bounded interval (`start/end`), half-bounded
  (`../end`, `start/..`), or ISO-8601 duration (`P1M`, `PT36H`).
- `properties=a,b,c`: trims returned fields (smaller responses).
- Pagination: `limit`/`offset`, up to 50,000 features/page; follow the `next`
  link; stop when there is no `next` or `numberReturned` is 0.
- Output formats via `f=`: `json` (GeoJSON), `jsonld`, `html`, `csv`.

### 3.8 Transmission cadence (✅ verified — sets expectations, not behavior)

- ✅ USGS records instantaneous values ~every 15 min but **transmits ~hourly**
  (more frequently during floods); water-quality can record as fine as 5 min.
- ✅ Confirmed live: Wickford `00065` had `time = 03:18` vs `last_modified = 03:43`
  — a ~25-minute publish lag.
- ⚙️ Consequence: the published "latest" value changes roughly hourly for most
  sites, so a 15-minute poll legitimately sees unchanged values most cycles. This
  is expected; the integration's job is to faithfully hold USGS's latest
  *published* value, surfaced via the per-sensor `last_reading_time` attribute.
  Keep `MIN_SCAN_INTERVAL_MINUTES = 15`.

---

## 4. Migration design

### 4.1 The one principle

Route **every** USGS call through a single internal client module with a stable
interface. `sensor.py`, the options flow, `CoordinatorData`, and the entity/device
model never learn which backend answered. This enables side-by-side A/B and
contains the alpha-API churn.

### 4.2 Client interface (⚙️ design)

`client.py`:

```
class UsgsClient(Protocol):
    async def search_sites(term: str, *, state: str | None = None) -> list[SiteHit]:
        ...
    async def get_site_parameters(site_id: str) -> set[str]:
        # parameter codes the site reports as CONTINUOUS, ∩ SUPPORTED_PARAMETERS
        ...
    async def get_latest_values(site_id: str, params: list[str]) -> LatestResult:
        # {param -> (value: float|None, reading_time: datetime|None,
        #            approval_status: str|None, qualifier: str|None)}
        ...

# SiteHit: {site_id, site_name, site_type | None}
```

- `LegacyClient` — wraps today's `/nwis/site/` + `/nwis/iv/` logic unchanged.
- `ModernClient` — implements the interface against the OGC API (Phase C).
- The coordinator builds `CoordinatorData` from `get_latest_values()`; the
  config flow uses `search_sites()` + `get_site_parameters()`.

### 4.3 Data-model invariants (⚙️)

`CoordinatorData` is unchanged. `ModernClient` maps:
`value → float(value)`, `time → reading_time`, missing feature → `None`,
`approval_status`/`qualifier` → optional attributes carried through (additive;
sensors can ignore until we wire them up).

---

## 5. Gate 0 — verification (✅ COMPLETE)

All four blocking unknowns are resolved (details in [§3](#3-verified-api-reference)
and [§9](#9-verification-log)):

1. ✅ Site search by name/state/type/number — fields confirmed; **name substring =
   POST CQL2 `like`, uppercased, `and agency_code='USGS'`**.
2. ✅ Multi-parameter / multi-location single query — CQL2 `in` + `and`.
3. ✅ Continuous/IV identification — `time-series-metadata` `computation_period_identifier='Points'`.
4. ✅ `latest-continuous` field names — singular `approval_status` / `time_series_id`;
   `value` is a string; `id` is volatile.

⚠️ Still open but **out of scope** for this migration: the statistics/percentile API
(needed only for a future percent-of-normal feature) is unverified.

---

## 6. Phase plan

### Phase A — Client abstraction (legacy only, no-op for users)
- Create `client.py` + `LegacyClient` wrapping current calls.
- Refactor `coordinator.py` and `config_flow.py` to call the interface.
- **Acceptance:** behavior byte-for-byte identical to `1.1.0b2` on legacy
  (same sensors created, same values, same offline detection). No version bump
  needed to users beyond an internal checkpoint.

### Phase B — API key plumbing
- Add an **API key** field (config + options). Empty → fall back to `DEMO_KEY`
  with a clear warning about rate limits.
- Implement `X-Api-Key` header + 429 backoff driven by the remaining-requests
  signal.
- Ships while still defaulting to legacy (legacy ignores the key).
- ⚙️ **Decision:** key stored **per entry** for the beta (reload-safe, simple),
  pre-filled from an existing entry if present. Revisit centralizing later.
- **Acceptance:** key persists, reload-safe, no effect on legacy polling.

### Phase C — Modern backend (`ModernClient`)
- Implement the interface per [§7](#7-phase-c-client-specification).
- **Acceptance:** against a DEMO_KEY, `ModernClient` returns the same parameter
  set and values (within rounding) as `LegacyClient` for the canal `01460595`
  and the Wickford tide gage `413413071270400`.

### Phase D — Backend selector + A/B
- Add an advanced per-entry option `backend: legacy (default) | modern`.
- Keep production entries on legacy; add a **test entry on modern** for the same
  site. Compare over 1–2 days.
- ⚙️ Compare the **raw measurement sensors only** — not the derived rate/trend —
  so a derived-sensor quirk can't masquerade as a migration regression.
- No data migration: `site_id` already equals `monitoring_location_number`.
- **Acceptance:** values, detected parameters, and offline detection match
  legacy for both test sites across the window.

### Phase E — Cutover & cleanup (later; not this beta)
- Flip default to `modern` for new installs; migrate existing entries.
- Keep `LegacyClient` as a runtime fallback through the overlap.
- Remove legacy well before the **Q1 2027** decommission.

### Versioning
- Branch `release/1.2.0` off the `1.1.0b2` tag; manifest `1.2.0b1`; GitHub
  pre-release tagged to match (clean semver so HACS pre-release sorting holds).

---

## 7. Phase C client specification (✅ grounded in verified facts)

All requests include `f=json` and the API key (`X-Api-Key` header preferred;
`api_key=` URL param acceptable). Base = `https://api.waterdata.usgs.gov/ogcapi/v0`.

### 7.1 `search_sites(term, state=None)`

**If `term` matches a site number** (`_normalize_site_number` → `^\d{6,15}$`):
- ✅ Query `monitoring-locations` for exact `monitoring_location_number`.
  Use CQL2 `=` (verified) rather than the ⚠️ URL-param form.

**Otherwise (name search):** POST CQL2 to
`/collections/monitoring-locations/items`:

```json
{"op":"and","args":[
  {"op":"like","args":[{"property":"monitoring_location_name"}, "%TERM_UPPERCASED%"]},
  {"op":"=","args":[{"property":"agency_code"}, "USGS"]}
]}
```

- ⚙️ Uppercase the user's term before wrapping in `%…%` (USGS-operated names are
  uppercase; `LIKE` is case-sensitive; `CASEI` unsupported).
- ⚙️ Optionally add a third `and` arm on `state_code` **only after** confirming its
  units (⚠️ unconfirmed).
- Map each feature → `SiteHit{site_id = strip "USGS-" from monitoring_location_id
  (or monitoring_location_number), site_name = monitoring_location_name,
  site_type = site_type}`.

### 7.2 `get_site_parameters(site_id)`

- Query `/collections/time-series-metadata/items` for this
  `monitoring_location_id`, filtered to `computation_period_identifier='Points'`.
- Return the set of `parameter_code` values ∩ `SUPPORTED_PARAMETERS`.
- ⚙️ Cache per site at setup; refresh daily (keeps steady-state polling to one
  request/site/cycle).

### 7.3 `get_latest_values(site_id, params)`

- ✅ Query `/collections/latest-continuous/items` with
  `monitoring_location_id=USGS-<site_id>` and the parameter codes. For multiple
  codes, either repeat the poll per code or use CQL2 `in` on `parameter_code`.
  (URL-param equality is verified working here.)
- Parse each feature: `float(value)`, `time → reading_time`, carry
  `approval_status` / `qualifier`.
- ⚠️ **Missing-data handling:** legacy uses a `-999999` sentinel. Whether the modern
  API emits sentinels or simply omits the feature is **unconfirmed** — treat an
  absent feature or non-numeric `value` as `None`, and confirm during Phase C
  whether sentinels appear.
- Build `CoordinatorData` exactly as today (offline detection can additionally use
  `time-series-metadata` `end`, but the current `time`-age logic carries over).

---

## 8. Code change map

| Concern | Legacy today | Modern target |
|---|---|---|
| Base URL(s) | `USGS_IV_URL`, `USGS_SITE_URL` in `const.py` | Add modern base + collection paths |
| Site search | `_search_usgs_sites()` in `config_flow.py` | `client.search_sites()` |
| Param discovery | "request all, keep non-empty value_list" in `coordinator.py` | `client.get_site_parameters()` via time-series-metadata |
| Poll/parse | `_async_update_data` + `_parse_response` | `client.get_latest_values()` |
| Auth | none | API key field + `X-Api-Key` + 429 backoff |
| Sensors / device / data model | — | **unchanged** |
| Rate/trend derived sensors | — | **unchanged** (backend-agnostic) |

---

## 9. Verification log

What was tested, how, and the result. (Sites: canal `01460595`; Wickford tide gage
`413413071270400`.)

1. ✅ **Collections inventory** — fetched `/collections`; confirmed
   `latest-continuous`, `continuous`, `time-series-metadata`, `monitoring-locations`
   present with stated descriptions.
2. ✅ **`latest-continuous` schema** — fetched `/collections/latest-continuous/schema?f=html`;
   recorded the field table in [§3.3](#33-latest-continuous--the-poll-endpoint--schema-verified).
3. ✅ **Live `latest-continuous` response** — Wickford `00065`:
   `value="-1.02"` (string), `approval_status="Provisional"`, `qualifier=null`,
   `statistic_id="00011"`, `time=2026-06-11T03:18:00+00:00`,
   `last_modified=2026-06-11T03:43:30Z`, `id` a UUID. Confirms singular field
   names, string value, volatile id, ~25-min publish lag.
4. ✅ **`monitoring-locations` queryables** — fetched queryables doc; recorded
   filterable fields.
5. ✅ **Name search matrix** — live POST/GET tests:
   - POST CQL2 `like %HARVARD%` → returned HARVARD sites (works).
   - GET `filter=... LIKE ...` → empty (unusable).
   - `q=Harvard` → returned unrelated first-page rows (trap).
   - POST `like %harvard%` (lowercase) → empty (case-sensitive).
   - POST `casei(...)` → `Bad CQL JSON` (unsupported).
   - POST `and(like %HARVARD%, =agency_code 'USGS')` → only USGS rows; EPA
     partner sites dropped.
6. ⚠️ **URL-param equality on monitoring-locations** — `?state_code=44&site_type_code=ST`
   returned a MA site among RI results; semantics unconfirmed.
7. ✅ **Transmission cadence** — USGS docs + the Wickford `time`/`last_modified`
   gap.

---

## 10. Appendix: verified requests

> Replace `DEMO_KEY` with an api.data.gov key for real use. `X-Api-Key` header is
> preferred over the `api_key` URL param.

**Live poll (verified):**
```bash
curl -s "https://api.waterdata.usgs.gov/ogcapi/v0/collections/latest-continuous/items?monitoring_location_id=USGS-413413071270400&parameter_code=00065&f=json&api_key=DEMO_KEY"
```

**Name search (verified):**
```bash
curl -s -X POST "https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items?limit=10&f=json&api_key=DEMO_KEY" \
  -H "Content-Type: application/query-cql-json" \
  -d '{"op":"and","args":[
        {"op":"like","args":[{"property":"monitoring_location_name"},"%HARVARD%"]},
        {"op":"=","args":[{"property":"agency_code"},"USGS"]}]}'
```

**Continuous-series capability (pattern):**
```bash
curl -s "https://api.waterdata.usgs.gov/ogcapi/v0/collections/time-series-metadata/items?monitoring_location_id=USGS-01460595&f=json&api_key=DEMO_KEY"
# then keep series where computation_period_identifier == "Points"
```

**Representative live `latest-continuous` feature (Wickford `00065`):**
```json
{
  "type": "Feature",
  "id": "ac615a6a-3da0-4643-b1b0-5be189c5f999",
  "geometry": {"type": "Point", "coordinates": [-71.4511111, 41.5702778]},
  "properties": {
    "id": "ac615a6a-3da0-4643-b1b0-5be189c5f999",
    "time_series_id": "137f32ef352b452f82b8cbbc38cad762",
    "monitoring_location_id": "USGS-413413071270400",
    "parameter_code": "00065",
    "statistic_id": "00011",
    "time": "2026-06-11T03:18:00+00:00",
    "value": "-1.02",
    "unit_of_measure": "ft",
    "approval_status": "Provisional",
    "qualifier": null,
    "last_modified": "2026-06-11T03:43:30.583772+00:00"
  }
}
```

---

## 11. Open risks

| Risk | Status | Mitigation |
|---|---|---|
| `v0 → v1` URL change while alpha | ✅ known | Base URL isolated in `client.py`; one-file edit |
| `monitoring-locations` URL-param equality semantics (`state_code` units) | ⚠️ unconfirmed | Use CQL2 `=`; confirm units before using `state_code` |
| Modern missing-data representation (sentinel vs absent) | ⚠️ unconfirmed | Treat absent/non-numeric as `None`; confirm in Phase C |
| Exact rate-limit numbers / header names | ⚠️ unconfirmed | Read remaining-requests signal; back off on 429 |
| Statistics/percentile API (future feature) | ⚠️ unverified | Out of scope for this migration |
| Name search strictness (case-sensitive, USGS-only) | ✅ understood | Uppercase term; document the UX in the config flow |

---

## 12. Rollback & safety

- The beta ships with `backend` defaulting to **legacy**, so installing it changes
  nothing for existing entries until a user opts a specific entry into `modern`.
- `LegacyClient` remains until after the A/B proves parity and is retained as a
  runtime fallback through Phase E.
- No config-entry data migration is required (`site_id == monitoring_location_number`),
  so reverting an entry to legacy is a no-op switch.
