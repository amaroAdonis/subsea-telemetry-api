"""Ingestion and analysis of sensor time series."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analytics
from app.config import get_settings
from app.models import OperatingLimit, SensorReading, SensorType
from app.routers.equipment import Session, load_equipment
from app.schemas import (
    Alert,
    IngestionResult,
    ReadingBatch,
    ReadingRead,
    ResampleResponse,
    SeriesStats,
)

router = APIRouter(prefix='/equipment/{equipment_id}/readings', tags=['readings'])


async def _series(
    session: AsyncSession,
    equipment_id: int,
    sensor_type: SensorType,
    start: datetime | None,
    end: datetime | None,
) -> list[tuple[datetime, float]]:
    query = select(SensorReading.recorded_at, SensorReading.value).where(
        SensorReading.equipment_id == equipment_id,
        SensorReading.sensor_type == sensor_type,
    )
    if start is not None:
        query = query.where(SensorReading.recorded_at >= start)
    if end is not None:
        query = query.where(SensorReading.recorded_at <= end)

    result = await session.execute(query.order_by(SensorReading.recorded_at))
    return [(row.recorded_at, row.value) for row in result]


@router.post('', response_model=IngestionResult, status_code=status.HTTP_201_CREATED)
async def ingest_readings(equipment_id: int, payload: ReadingBatch, session: Session) -> IngestionResult:
    """Bulk ingestion. The whole batch is rejected if any sample is unusable,
    so the caller never has to guess which half landed."""
    settings = get_settings()
    await load_equipment(equipment_id, session)

    if len(payload.readings) > settings.max_batch_size:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f'batch of {len(payload.readings)} exceeds the limit of {settings.max_batch_size}',
        )

    now = datetime.now(UTC)
    oldest_allowed = now - timedelta(days=settings.max_reading_age_days)
    for reading in payload.readings:
        if reading.recorded_at > now:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, 'recorded_at cannot be in the future')
        if reading.recorded_at < oldest_allowed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f'recorded_at is older than {settings.max_reading_age_days} days',
            )

    session.add_all([SensorReading(equipment_id=equipment_id, **reading.model_dump()) for reading in payload.readings])
    await session.flush()
    return IngestionResult(accepted=len(payload.readings), equipment_id=equipment_id)


@router.get('', response_model=list[ReadingRead])
async def list_readings(
    equipment_id: int,
    session: Session,
    sensor_type: SensorType | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[SensorReading]:
    await load_equipment(equipment_id, session)

    query = select(SensorReading).where(SensorReading.equipment_id == equipment_id)
    if sensor_type is not None:
        query = query.where(SensorReading.sensor_type == sensor_type)
    if start is not None:
        query = query.where(SensorReading.recorded_at >= start)
    if end is not None:
        query = query.where(SensorReading.recorded_at <= end)

    result = await session.execute(query.order_by(SensorReading.recorded_at.desc()).limit(limit))
    return list(result.scalars().all())


@router.get('/resample', response_model=ResampleResponse)
async def resample_readings(
    equipment_id: int,
    sensor_type: SensorType,
    session: Session,
    freq: Annotated[str, Query(description='pandas offset alias, e.g. 15min, 1h, 1D')] = '1h',
    agg: str = 'mean',
    start: datetime | None = None,
    end: datetime | None = None,
) -> ResampleResponse:
    """Irregular raw samples bucketed into a fixed grid, ready to chart."""
    await load_equipment(equipment_id, session)
    rows = await _series(session, equipment_id, sensor_type, start, end)

    try:
        points = analytics.resample(rows, freq=freq, agg=agg)
    except analytics.AnalyticsError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    return ResampleResponse(
        equipment_id=equipment_id,
        sensor_type=sensor_type,
        freq=freq,
        agg=agg,
        points=points,
    )


@router.get('/stats', response_model=SeriesStats)
async def series_stats(
    equipment_id: int,
    sensor_type: SensorType,
    session: Session,
    start: datetime | None = None,
    end: datetime | None = None,
) -> SeriesStats:
    await load_equipment(equipment_id, session)
    rows = await _series(session, equipment_id, sensor_type, start, end)
    return SeriesStats(equipment_id=equipment_id, sensor_type=sensor_type, **analytics.describe(rows))


@router.get('/alerts', response_model=list[Alert])
async def list_alerts(
    equipment_id: int,
    session: Session,
    sensor_type: SensorType | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Alert]:
    """Readings that left the configured operating envelope.

    A sensor with no envelope configured produces no alerts. Silence here means
    "not monitored", not "healthy", which is why the limits endpoint is part of
    the same resource.
    """
    await load_equipment(equipment_id, session)

    limits_query = select(OperatingLimit).where(OperatingLimit.equipment_id == equipment_id)
    if sensor_type is not None:
        limits_query = limits_query.where(OperatingLimit.sensor_type == sensor_type)
    limits_result = await session.execute(limits_query)
    limits = {limit.sensor_type: limit for limit in limits_result.scalars().all()}

    if not limits:
        return []

    readings_query = select(SensorReading).where(
        SensorReading.equipment_id == equipment_id,
        SensorReading.sensor_type.in_(limits.keys()),
    )
    if start is not None:
        readings_query = readings_query.where(SensorReading.recorded_at >= start)
    if end is not None:
        readings_query = readings_query.where(SensorReading.recorded_at <= end)

    readings_result = await session.execute(readings_query.order_by(SensorReading.recorded_at))

    alerts: list[Alert] = []
    for reading in readings_result.scalars().all():
        limit = limits[reading.sensor_type]
        if reading.value < limit.min_value:
            breach = 'below'
        elif reading.value > limit.max_value:
            breach = 'above'
        else:
            continue

        alerts.append(
            Alert(
                reading_id=reading.id,
                sensor_type=reading.sensor_type,
                value=reading.value,
                unit=reading.unit,
                recorded_at=reading.recorded_at,
                min_value=limit.min_value,
                max_value=limit.max_value,
                breach=breach,
            )
        )

    return alerts
