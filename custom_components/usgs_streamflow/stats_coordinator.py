"""Coordinator for the opt-in percent-of-normal / condition statistics.

This sits beside the main polling coordinator and owns the *slow-moving* half of
the statistics feature: the day-of-year percentile envelopes built from each
gauge's long-term daily record.  Those envelopes are expensive to fetch (~30
years of daily values) but barely change, so they are:

* persisted to disk via a Home Assistant :class:`Store` (so a restart reuses the
  cache instead of re-fetching), and
* rebuilt only when missing or older than ``STATS_REFRESH_DAYS``.

The *fast-moving* half — placing the current reading against its day's envelope —
is cheap and pure, so it is recomputed in-process on every source poll via a
listener, keeping the Condition / Percentile / %-of-Normal sensors in step with
the live value without any extra network traffic.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import streamflow_stats as stats
from .client import UsgsClient, UsgsClientError
from .const import (
    DOMAIN,
    STAT_DAILY_MEAN,
    STATS_MIN_SAMPLES,
    STATS_RECORD_YEARS,
    STATS_REFRESH_DAYS,
    STATS_WINDOW_DAYS,
    StatsParamConfig,
)
from .coordinator import USGSStreamflowCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# How often to wake and check whether any envelope needs rebuilding.  The actual
# rebuild is still gated on STATS_REFRESH_DAYS; this just bounds how stale a
# cache can get before the check runs.
_REFRESH_CHECK_INTERVAL = timedelta(hours=12)


def stats_store(hass: HomeAssistant, site_id: str) -> Store:
    """The persisted percent-of-normal cache Store for one gauge.

    Single source of truth for the store key so the coordinator and entry-removal
    cleanup (``async_remove_entry``) always target the same file.
    """
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}_stats_{site_id}")


def _is_stale(envelope: stats.Envelope, now) -> bool:
    """True when an envelope is missing its build time or older than the refresh window."""
    if not envelope.built:
        return True
    built = dt_util.parse_datetime(envelope.built)
    if built is None:
        return True
    return (now - built) > timedelta(days=STATS_REFRESH_DAYS)


class USGSStatsCoordinator(DataUpdateCoordinator[dict[str, stats.StatsResult]]):
    """Owns the cached envelopes and computes per-parameter statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        source: USGSStreamflowCoordinator,
        client: UsgsClient,
        params: dict[str, StatsParamConfig],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"USGS Stats {source.site_name}",
            update_interval=_REFRESH_CHECK_INTERVAL,
        )
        self._source = source
        self._client = client
        # Mirror the source's site id so entities bound to this coordinator can
        # read it the same way they do on the main coordinator.
        self.site_id = source.site_id
        # param_cd -> StatsParamConfig, limited to what this station serves.
        self.params = params
        self._store: Store = stats_store(hass, source.site_id)
        self.envelopes: dict[str, stats.Envelope] = {}
        self._cache_loaded = False

    # -- coordinator update ------------------------------------------------ #
    async def _async_update_data(self) -> dict[str, stats.StatsResult]:
        """Ensure envelopes are present/fresh, then compute current statistics."""
        await self._ensure_envelopes()
        return self._compute()

    async def _ensure_envelopes(self) -> None:
        """Load the cache once, then (re)build any missing or stale envelope."""
        if not self._cache_loaded:
            await self._load_cache()
            self._cache_loaded = True

        now = dt_util.utcnow()
        rebuilt = False
        for param in self.params:
            existing = self.envelopes.get(param)
            if existing is not None and not _is_stale(existing, now):
                continue
            if await self._rebuild(param):
                rebuilt = True
        if rebuilt:
            await self._save_cache()

    async def _rebuild(self, param: str) -> bool:
        """Fetch the long-term record for ``param`` and rebuild its envelope.

        Returns ``True`` when a usable envelope was produced.  A fetch failure is
        logged and swallowed so it never breaks setup or the polling cycle; the
        previous cached envelope (if any) is kept and the rebuild retried later.
        """
        end = dt_util.utcnow().date()
        start = date(end.year - STATS_RECORD_YEARS, 1, 1)
        try:
            records = await self._client.get_daily_means(
                self._source.site_id, param, STAT_DAILY_MEAN,
                start.isoformat(), end.isoformat(),
            )
        except UsgsClientError as err:
            _LOGGER.warning(
                "Could not fetch daily history for %s param %s: %s",
                self._source.site_id, param, err,
            )
            return False

        envelope = stats.build_envelope(
            records,
            param_cd=param,
            statistic_id=STAT_DAILY_MEAN,
            window_days=STATS_WINDOW_DAYS,
            min_samples=STATS_MIN_SAMPLES,
            built=dt_util.utcnow(),
        )
        if envelope is None or not envelope.days:
            _LOGGER.warning(
                "No usable daily history to build a stats envelope for %s param %s",
                self._source.site_id, param,
            )
            return False

        self.envelopes[param] = envelope
        _LOGGER.debug(
            "Built stats envelope for %s param %s: %d days, %d years (%s..%s)",
            self._source.site_id, param, len(envelope.days), envelope.years,
            envelope.record_start, envelope.record_end,
        )
        return True

    # -- pure computation -------------------------------------------------- #
    def _compute(self) -> dict[str, stats.StatsResult]:
        """Place the latest source reading against each envelope (no network)."""
        results: dict[str, stats.StatsResult] = {}
        source_data = self._source.data
        if source_data is None:
            return results
        today = dt_util.utcnow().date()
        for param, cfg in self.params.items():
            envelope = self.envelopes.get(param)
            if envelope is None:
                continue
            value = source_data.values.get(param)
            if value is None:
                continue
            reading_dt = source_data.reading_times.get(param)
            observation_date = reading_dt.date() if reading_dt else today
            result = envelope.evaluate(observation_date, value, cfg.invert)
            if result is not None:
                results[param] = result
        return results

    # -- live recompute on each source poll -------------------------------- #
    @callback
    def _handle_source_update(self) -> None:
        """Recompute (cheaply) whenever the source coordinator gets fresh values."""
        self.async_set_updated_data(self._compute())

    def attach_source(self):
        """Subscribe to the source coordinator; returns the unsubscribe callback."""
        return self._source.async_add_listener(self._handle_source_update)

    # -- persistence ------------------------------------------------------- #
    async def _load_cache(self) -> None:
        try:
            data = await self._store.async_load()
        except Exception as err:  # noqa: BLE001 - cache is best-effort; rebuild on any read error
            _LOGGER.debug(
                "Could not load stats cache for %s: %s", self._source.site_id, err
            )
            return
        if not data:
            return
        for param, raw in (data.get("envelopes") or {}).items():
            if param not in self.params:
                continue
            try:
                self.envelopes[param] = stats.Envelope.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                # A corrupt or out-of-schema cache entry is simply rebuilt.
                _LOGGER.debug("Discarding unreadable stats cache for %s", param)

    async def _save_cache(self) -> None:
        try:
            await self._store.async_save(
                {"envelopes": {p: e.to_dict() for p, e in self.envelopes.items()}}
            )
        except Exception as err:  # noqa: BLE001 - a failed write must not disrupt stats
            # The envelope is already held in memory; a persistence failure just
            # means it will be rebuilt next time rather than loaded from disk.
            _LOGGER.warning(
                "Could not persist stats cache for %s: %s",
                self._source.site_id, err,
            )
