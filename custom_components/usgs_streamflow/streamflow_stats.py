"""Percent-of-normal / condition statistics — pure, backend-independent logic.

This module turns a long-term record of daily-mean values into a day-of-year
*envelope*: for each calendar day it stores the percentile distribution of the
values historically observed on (or near) that day.  A live reading is then
placed against the envelope for its day to yield three things, exactly as USGS
WaterWatch and Groundwater Watch present them:

* **Condition** — a WaterWatch class (Much below / Below / Normal / Above /
  Much above normal) from the reading's percentile.
* **Percentile** — where the reading falls in its day's historical range.
* **% of Normal** — the reading as a percentage of the day's historical median.

It is deliberately free of any Home Assistant or network import so it can be
unit-tested in isolation and reused by either API backend.  The envelope is
stored as a small set of percentile *anchors* per day (rather than every raw
value): that is the same model USGS publishes as daily statistics, keeps the
persisted cache compact, and is enough to interpolate a percentile and classify.

Inversion (``invert=True``) handles depth-to-water, where a *deeper* reading
means *less* groundwater: the raw value percentile is high, but the reported
water-level percentile — and therefore the condition — is its complement, so a
deep reading reads as "below normal".  Verified live against Groundwater Watch.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

# WaterWatch condition classes, ordered low -> high.  Used verbatim as the
# Condition sensor's enum options and as its state strings.
CONDITION_MUCH_BELOW = "Much below normal"
CONDITION_BELOW = "Below normal"
CONDITION_NORMAL = "Normal"
CONDITION_ABOVE = "Above normal"
CONDITION_MUCH_ABOVE = "Much above normal"
CONDITION_ORDER: tuple[str, ...] = (
    CONDITION_MUCH_BELOW,
    CONDITION_BELOW,
    CONDITION_NORMAL,
    CONDITION_ABOVE,
    CONDITION_MUCH_ABOVE,
)

# Percentile breakpoints between the classes (WaterWatch 5-class scheme):
# <10 much below, 10-25 below, 25-75 normal, 75-90 above, >90 much above.
_CLASS_BREAKS = (10.0, 25.0, 75.0, 90.0)

# Percentile anchors stored per calendar day.  The 0/100 anchors are the
# period-of-record min/max for the day, so a reading outside the historical
# range still classifies sensibly instead of clamping at an inner break.
ANCHOR_PERCENTILES: tuple[int, ...] = (0, 5, 10, 25, 50, 75, 90, 95, 100)


def day_key(d: date) -> str:
    """Return the calendar-day key ``"MM-DD"`` for grouping, folding Feb 29.

    Leap day is folded onto Feb 28 so its handful of values join a stable bucket
    rather than forming a separate, sparsely-populated day-of-year.
    """
    month, day = d.month, d.day
    if month == 2 and day == 29:
        day = 28
    return f"{month:02d}-{day:02d}"


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation quantile (``q`` in 0..100) of a sorted, non-empty list."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = (q / 100.0) * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def percentile_of(anchors: dict[int, float], value: float) -> float:
    """Percentile (0..100) of ``value`` interpolated across the day's anchors.

    Anchor values are monotonically non-decreasing in their percentile, so this
    walks the (percentile, value) points and linearly interpolates the percentile
    within the bracketing pair.  Values at or beyond the record min/max clamp to
    0 / 100.  Flat regions (repeated values, e.g. a stream that often reads 0)
    resolve to the upper percentile of the run.
    """
    points = sorted(anchors.items())  # by percentile; values non-decreasing
    if value <= points[0][1]:
        return float(points[0][0])
    if value >= points[-1][1]:
        return float(points[-1][0])
    for (p0, v0), (p1, v1) in zip(points, points[1:]):
        if v0 <= value <= v1:
            if v1 == v0:
                return float(p1)
            frac = (value - v0) / (v1 - v0)
            return p0 + frac * (p1 - p0)
    return 50.0  # unreachable for monotonic anchors; defensive only


def classify(percentile: float, invert: bool = False) -> tuple[str, float]:
    """Map a raw value-percentile to a (condition, reported-percentile) pair.

    With ``invert`` the reported percentile is the complement (100 - p), so a
    deep depth-to-water reading (high value percentile) becomes a low water-level
    percentile and therefore a below-normal condition.
    """
    p = (100.0 - percentile) if invert else percentile
    much_below, below, above, much_above = _CLASS_BREAKS
    if p < much_below:
        cond = CONDITION_MUCH_BELOW
    elif p < below:
        cond = CONDITION_BELOW
    elif p <= above:
        cond = CONDITION_NORMAL
    elif p <= much_above:
        cond = CONDITION_ABOVE
    else:
        cond = CONDITION_MUCH_ABOVE
    return cond, p


@dataclass(frozen=True)
class StatsResult:
    """The placement of one live reading against its day's envelope."""

    condition: str
    percentile: float          # reported percentile (already inverted if needed)
    percent_of_normal: float   # value / day-median * 100
    median: float              # day's historical median (P50)
    sample_count: int          # distinct daily values behind the day's anchors
    value: float               # the live reading evaluated
    observation_date: str      # ISO date of the reading
    inverted: bool             # whether inversion was applied


@dataclass(frozen=True)
class DayStat:
    """Per-calendar-day percentile anchors and the sample size behind them."""

    n: int
    anchors: dict[int, float]


@dataclass
class Envelope:
    """A site/parameter's day-of-year percentile distribution.

    ``days`` maps ``"MM-DD"`` keys to :class:`DayStat`.  Metadata records what the
    envelope was built from and when, so the cache layer can detect staleness.
    """

    param_cd: str
    statistic_id: str
    window_days: int
    years: int
    record_start: str | None
    record_end: str | None
    built: str | None
    days: dict[str, DayStat]

    def evaluate(
        self, observation_date: date, value: float | None, invert: bool = False
    ) -> StatsResult | None:
        """Place ``value`` (observed on ``observation_date``) against its day.

        Returns ``None`` when there is no reading or no envelope coverage for the
        day (the day was dropped at build time for too few samples).
        """
        if value is None:
            return None
        stat = self.days.get(day_key(observation_date))
        if stat is None:
            return None
        raw_pct = percentile_of(stat.anchors, value)
        condition, reported = classify(raw_pct, invert)
        median = stat.anchors.get(50)
        percent_of_normal = (
            round(value / median * 100, 1) if median else 0.0
        )
        return StatsResult(
            condition=condition,
            percentile=round(reported, 1),
            percent_of_normal=percent_of_normal,
            median=median if median is not None else 0.0,
            sample_count=stat.n,
            value=value,
            observation_date=observation_date.isoformat(),
            inverted=invert,
        )

    # -- serialization for the persisted cache ----------------------------- #
    def to_dict(self) -> dict:
        return {
            "param_cd": self.param_cd,
            "statistic_id": self.statistic_id,
            "window_days": self.window_days,
            "years": self.years,
            "record_start": self.record_start,
            "record_end": self.record_end,
            "built": self.built,
            # Anchor keys are ints; JSON requires string keys, so stringify here.
            "days": {
                key: {"n": ds.n, "a": {str(p): v for p, v in ds.anchors.items()}}
                for key, ds in self.days.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Envelope":
        days = {
            key: DayStat(
                n=int(entry["n"]),
                anchors={int(p): float(v) for p, v in entry["a"].items()},
            )
            for key, entry in (raw.get("days") or {}).items()
        }
        return cls(
            param_cd=raw["param_cd"],
            statistic_id=raw["statistic_id"],
            window_days=int(raw.get("window_days", 0)),
            years=int(raw.get("years", 0)),
            record_start=raw.get("record_start"),
            record_end=raw.get("record_end"),
            built=raw.get("built"),
            days=days,
        )


def _build_day_stats(values: list[float]) -> DayStat:
    """Compute the anchor map for one calendar day's values."""
    sv = sorted(values)
    anchors = {p: _quantile(sv, p) for p in ANCHOR_PERCENTILES}
    return DayStat(n=len(sv), anchors=anchors)


def build_envelope(
    records: list[tuple[date, float]],
    *,
    param_cd: str,
    statistic_id: str,
    window_days: int = 0,
    min_samples: int = 10,
    built: datetime | None = None,
) -> Envelope | None:
    """Build an :class:`Envelope` from ``(date, value)`` daily-mean records.

    ``window_days`` centers a +/- window on each record's day so neighboring
    calendar days contribute to the bucket (0 = exact day, matching WaterWatch).
    Calendar days with fewer than ``min_samples`` distinct values are dropped so
    a thin bucket can't produce a meaningless classification.  Returns ``None``
    when there are no records at all.
    """
    if not records:
        return None

    buckets: dict[str, list[float]] = defaultdict(list)
    if window_days <= 0:
        for d, v in records:
            buckets[day_key(d)].append(v)
    else:
        from datetime import timedelta

        for d, v in records:
            for offset in range(-window_days, window_days + 1):
                buckets[day_key(d + timedelta(days=offset))].append(v)

    days = {
        key: _build_day_stats(vals)
        for key, vals in buckets.items()
        if len(vals) >= min_samples
    }
    if not days:
        return None

    dates = [d for d, _ in records]
    years = len({d.year for d in dates})
    return Envelope(
        param_cd=param_cd,
        statistic_id=statistic_id,
        window_days=window_days,
        years=years,
        record_start=min(dates).isoformat(),
        record_end=max(dates).isoformat(),
        built=(built or datetime.now()).isoformat(),
        days=days,
    )
