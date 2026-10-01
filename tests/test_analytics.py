"""Unit tests for the pandas layer. No database, no HTTP."""

from datetime import UTC, datetime, timedelta

import pytest

from app import analytics


def series(values: list[float], step_minutes: int = 10) -> list[tuple[datetime, float]]:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    return [(base + timedelta(minutes=step_minutes * index), value) for index, value in enumerate(values)]


def test_resample_averages_each_bucket() -> None:
    points = analytics.resample(series([100.0, 200.0, 300.0, 400.0], step_minutes=10), freq='1h')

    assert len(points) == 1
    assert points[0]['value'] == pytest.approx(250.0)
    assert points[0]['sample_count'] == 4


def test_resample_keeps_empty_buckets_as_null() -> None:
    rows = [
        (datetime(2026, 5, 1, 12, 0, tzinfo=UTC), 100.0),
        (datetime(2026, 5, 1, 15, 0, tzinfo=UTC), 400.0),
    ]

    points = analytics.resample(rows, freq='1h')

    assert len(points) == 4
    assert points[1]['value'] is None
    assert points[1]['sample_count'] == 0


def test_resample_honours_the_aggregation() -> None:
    rows = series([10.0, 90.0])

    assert analytics.resample(rows, freq='1h', agg='max')[0]['value'] == pytest.approx(90.0)
    assert analytics.resample(rows, freq='1h', agg='min')[0]['value'] == pytest.approx(10.0)
    assert analytics.resample(rows, freq='1h', agg='sum')[0]['value'] == pytest.approx(100.0)


def test_resample_on_empty_input_returns_no_points() -> None:
    assert analytics.resample([]) == []


def test_resample_rejects_unknown_frequency() -> None:
    with pytest.raises(analytics.AnalyticsError):
        analytics.resample(series([1.0]), freq='3fortnights')


def test_resample_rejects_unknown_aggregation() -> None:
    with pytest.raises(analytics.AnalyticsError):
        analytics.resample(series([1.0]), agg='eval')


def test_describe_reports_percentiles() -> None:
    stats = analytics.describe(series([float(value) for value in range(1, 101)]))

    assert stats['count'] == 100
    assert stats['minimum'] == pytest.approx(1.0)
    assert stats['maximum'] == pytest.approx(100.0)
    assert stats['p50'] == pytest.approx(50.5)
    assert stats['p95'] == pytest.approx(95.05)


def test_describe_of_single_sample_has_no_std() -> None:
    stats = analytics.describe(series([42.0]))

    assert stats['count'] == 1
    assert stats['std'] is None
    assert stats['mean'] == pytest.approx(42.0)


def test_describe_of_empty_series_is_all_null() -> None:
    stats = analytics.describe([])

    assert stats['count'] == 0
    assert stats['mean'] is None
    assert stats['first_reading_at'] is None


def test_describe_sorts_out_of_order_input() -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    rows = [(base + timedelta(hours=2), 3.0), (base, 1.0), (base + timedelta(hours=1), 2.0)]

    stats = analytics.describe(rows)

    assert stats['first_reading_at'] == base
    assert stats['last_reading_at'] == base + timedelta(hours=2)


def test_rolling_deviation_flags_a_spike() -> None:
    values = [100.0] * 20 + [100.0, 101.0, 99.0, 100.5, 500.0]
    rows = series(values)

    flagged = analytics.rolling_deviation(rows, window=12, z_threshold=3.0)

    assert [point['value'] for point in flagged] == [500.0]


def test_rolling_deviation_ignores_a_steady_series() -> None:
    rows = series([100.0 + (index % 3) for index in range(60)])

    assert analytics.rolling_deviation(rows, window=12, z_threshold=3.0) == []


def test_rolling_deviation_needs_more_samples_than_the_window() -> None:
    assert analytics.rolling_deviation(series([1.0, 2.0, 3.0]), window=12) == []


def test_rolling_deviation_rejects_a_degenerate_window() -> None:
    with pytest.raises(analytics.AnalyticsError):
        analytics.rolling_deviation(series([1.0, 2.0]), window=1)
