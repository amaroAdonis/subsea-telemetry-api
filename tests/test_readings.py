from datetime import UTC, datetime, timedelta

from httpx import AsyncClient


def samples(count: int, *, start: datetime | None = None, step_minutes: int = 10, value: float = 200.0) -> list[dict]:
    base = start or datetime.now(UTC) - timedelta(hours=6)
    return [
        {
            'sensor_type': 'pressure',
            'value': value,
            'unit': 'bar',
            'recorded_at': (base + timedelta(minutes=step_minutes * index)).isoformat(),
        }
        for index in range(count)
    ]


async def test_ingest_accepts_batch(client: AsyncClient, equipment_id: int) -> None:
    response = await client.post(f'/equipment/{equipment_id}/readings', json={'readings': samples(5)})

    assert response.status_code == 201
    assert response.json() == {'accepted': 5, 'equipment_id': equipment_id}


async def test_ingest_on_unknown_equipment_returns_404(client: AsyncClient) -> None:
    response = await client.post('/equipment/9999/readings', json={'readings': samples(1)})

    assert response.status_code == 404


async def test_naive_timestamp_is_rejected(client: AsyncClient, equipment_id: int) -> None:
    payload = {'readings': [{**samples(1)[0], 'recorded_at': '2026-01-01T10:00:00'}]}

    response = await client.post(f'/equipment/{equipment_id}/readings', json=payload)

    assert response.status_code == 422


async def test_future_timestamp_is_rejected(client: AsyncClient, equipment_id: int) -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    payload = {'readings': [{**samples(1)[0], 'recorded_at': future}]}

    response = await client.post(f'/equipment/{equipment_id}/readings', json=payload)

    assert response.status_code == 422


async def test_stale_timestamp_is_rejected(client: AsyncClient, equipment_id: int) -> None:
    stale = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    payload = {'readings': [{**samples(1)[0], 'recorded_at': stale}]}

    response = await client.post(f'/equipment/{equipment_id}/readings', json=payload)

    assert response.status_code == 422


async def test_one_bad_sample_rejects_the_whole_batch(client: AsyncClient, equipment_id: int) -> None:
    batch = samples(3)
    batch[1]['recorded_at'] = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    response = await client.post(f'/equipment/{equipment_id}/readings', json={'readings': batch})
    assert response.status_code == 422

    stored = await client.get(f'/equipment/{equipment_id}/readings')
    assert stored.json() == []


async def test_empty_batch_is_rejected(client: AsyncClient, equipment_id: int) -> None:
    response = await client.post(f'/equipment/{equipment_id}/readings', json={'readings': []})

    assert response.status_code == 422


async def test_list_readings_filters_by_window(client: AsyncClient, equipment_id: int) -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    await client.post(
        f'/equipment/{equipment_id}/readings',
        json={'readings': samples(6, start=base, step_minutes=60)},
    )

    response = await client.get(
        f'/equipment/{equipment_id}/readings',
        params={
            'start': (base + timedelta(hours=2)).isoformat(),
            'end': (base + timedelta(hours=3)).isoformat(),
        },
    )

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_alerts_report_breach_direction(client: AsyncClient, equipment_id: int) -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    await client.put(
        f'/equipment/{equipment_id}/limits',
        json={'sensor_type': 'pressure', 'min_value': 150.0, 'max_value': 250.0},
    )
    readings = [
        {'sensor_type': 'pressure', 'value': 200.0, 'unit': 'bar', 'recorded_at': base.isoformat()},
        {
            'sensor_type': 'pressure',
            'value': 310.0,
            'unit': 'bar',
            'recorded_at': (base + timedelta(minutes=10)).isoformat(),
        },
        {
            'sensor_type': 'pressure',
            'value': 90.0,
            'unit': 'bar',
            'recorded_at': (base + timedelta(minutes=20)).isoformat(),
        },
    ]
    await client.post(f'/equipment/{equipment_id}/readings', json={'readings': readings})

    response = await client.get(f'/equipment/{equipment_id}/readings/alerts')

    assert response.status_code == 200
    assert [alert['breach'] for alert in response.json()] == ['above', 'below']


async def test_sensor_without_configured_limit_produces_no_alerts(client: AsyncClient, equipment_id: int) -> None:
    await client.post(f'/equipment/{equipment_id}/readings', json={'readings': samples(3, value=9999.0)})

    response = await client.get(f'/equipment/{equipment_id}/readings/alerts')

    assert response.json() == []


async def test_stats_on_empty_series_returns_zero_count(client: AsyncClient, equipment_id: int) -> None:
    response = await client.get(
        f'/equipment/{equipment_id}/readings/stats',
        params={'sensor_type': 'temperature'},
    )

    assert response.status_code == 200
    body = response.json()
    assert body['count'] == 0
    assert body['mean'] is None


async def test_resample_buckets_an_irregular_series(client: AsyncClient, equipment_id: int) -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    await client.post(
        f'/equipment/{equipment_id}/readings',
        json={'readings': samples(12, start=base, step_minutes=10)},
    )

    response = await client.get(
        f'/equipment/{equipment_id}/readings/resample',
        params={'sensor_type': 'pressure', 'freq': '1h', 'agg': 'mean'},
    )

    assert response.status_code == 200
    body = response.json()
    assert body['freq'] == '1h'
    assert len(body['points']) == 2
    assert body['points'][0]['sample_count'] == 6


async def test_unsupported_aggregation_returns_422(client: AsyncClient, equipment_id: int) -> None:
    await client.post(f'/equipment/{equipment_id}/readings', json={'readings': samples(3)})

    response = await client.get(
        f'/equipment/{equipment_id}/readings/resample',
        params={'sensor_type': 'pressure', 'agg': 'drop table'},
    )

    assert response.status_code == 422
