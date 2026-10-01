"""Time-series processing with pandas.

Kept free of FastAPI and SQLAlchemy on purpose: these functions take rows and
return plain values, so they can be unit tested without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pandas as pd

SUPPORTED_AGGREGATIONS = ('mean', 'min', 'max', 'median', 'sum', 'std')

# pandas offset aliases we expose. Anything else is rejected rather than passed
# through, so a malformed freq cannot reach pandas and raise a 500.
SUPPORTED_FREQUENCIES = ('1min', '5min', '15min', '30min', '1h', '6h', '12h', '1D', '7D')


class AnalyticsError(ValueError):
    """Raised for caller mistakes that should surface as a 4xx, not a 500."""


def to_frame(rows: Sequence[tuple[datetime, float]]) -> pd.DataFrame:
    """Build a time-indexed frame from (recorded_at, value) pairs."""
    frame = pd.DataFrame(list(rows), columns=['recorded_at', 'value'])
    if frame.empty:
        return frame.set_index(pd.DatetimeIndex([], tz='UTC', name='recorded_at'))

    frame['recorded_at'] = pd.to_datetime(frame['recorded_at'], utc=True)
    return frame.set_index('recorded_at').sort_index()


def resample(
    rows: Sequence[tuple[datetime, float]],
    freq: str = '1h',
    agg: str = 'mean',
) -> list[dict[str, Any]]:
    """Downsample an irregular sensor series into fixed buckets.

    Raw subsea telemetry arrives at uneven intervals, so a chart or a report
    needs it bucketed first. Empty buckets are kept with a null value: a gap in
    the series is itself information for whoever is reading the chart.
    """
    if agg not in SUPPORTED_AGGREGATIONS:
        raise AnalyticsError(f'unsupported aggregation {agg!r}; use one of {", ".join(SUPPORTED_AGGREGATIONS)}')
    if freq not in SUPPORTED_FREQUENCIES:
        raise AnalyticsError(f'unsupported freq {freq!r}; use one of {", ".join(SUPPORTED_FREQUENCIES)}')

    frame = to_frame(rows)
    if frame.empty:
        return []

    grouped = frame['value'].resample(freq)
    values = getattr(grouped, agg)()
    counts = grouped.count()

    return [
        {
            'bucket_start': bucket.to_pydatetime(),
            'value': None if pd.isna(value) else float(value),
            'sample_count': int(counts.loc[bucket]),
        }
        for bucket, value in values.items()
    ]


def describe(rows: Sequence[tuple[datetime, float]]) -> dict[str, Any]:
    """Summary statistics for a sensor series, including p50/p95."""
    frame = to_frame(rows)
    if frame.empty:
        return {
            'count': 0,
            'mean': None,
            'std': None,
            'minimum': None,
            'p50': None,
            'p95': None,
            'maximum': None,
            'first_reading_at': None,
            'last_reading_at': None,
        }

    series = frame['value']
    # std of a single sample is NaN, which is not JSON-serialisable.
    std = series.std()

    return {
        'count': int(series.count()),
        'mean': float(series.mean()),
        'std': None if pd.isna(std) else float(std),
        'minimum': float(series.min()),
        'p50': float(series.quantile(0.5)),
        'p95': float(series.quantile(0.95)),
        'maximum': float(series.max()),
        'first_reading_at': frame.index[0].to_pydatetime(),
        'last_reading_at': frame.index[-1].to_pydatetime(),
    }


def rolling_deviation(
    rows: Sequence[tuple[datetime, float]],
    window: int = 12,
    z_threshold: float = 3.0,
    min_relative_change: float = 0.05,
) -> list[dict[str, Any]]:
    """Flag samples that sit far from their own recent neighbourhood.

    Complements the fixed operating envelope: a reading can stay inside the
    envelope and still be anomalous if it jumps away from the local trend. The
    first `window` samples have no history behind them and are never flagged.

    A z-score alone is unusable on subsea telemetry, because a sensor parked on
    a flat value has a near-zero rolling std, which turns a 1% wobble into a
    z of 3+. So a sample has to clear two independent bars: statistically far
    from the window (`z_threshold`) *and* materially far from it
    (`min_relative_change`, as a fraction of the window mean).
    """
    if window < 2:
        raise AnalyticsError('window must be at least 2 samples')
    if min_relative_change < 0:
        raise AnalyticsError('min_relative_change cannot be negative')

    frame = to_frame(rows)
    if len(frame) <= window:
        return []

    series = frame['value']
    mean = series.rolling(window).mean()
    std = series.rolling(window).std()

    deviation = series - mean
    # A flat window has std 0; dividing by it yields inf, so blank it out first.
    z_score = deviation / std.replace(0.0, pd.NA)
    relative_change = (deviation / mean.replace(0.0, pd.NA)).abs()

    flagged = ((z_score.abs() >= z_threshold) & (relative_change >= min_relative_change)).fillna(False)

    return [
        {
            'recorded_at': timestamp.to_pydatetime(),
            'value': float(series.loc[timestamp]),
            'z_score': float(z_score.loc[timestamp]),
        }
        for timestamp in frame.index[flagged]
    ]
