"""Load a CSV export of field telemetry into the database.

Stands in for the real ingestion path: an engineering team hands over a CSV
pulled from the acquisition system, and this normalises it and writes it in
chunks. Run it with no arguments to generate a synthetic dataset instead.

    python -m scripts.seed                       # synthetic, 3 assets, 7 days
    python -m scripts.seed --csv readings.csv    # real export
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.database import SessionFactory, engine
from app.models import (
    Equipment,
    EquipmentStatus,
    EquipmentType,
    OperatingLimit,
    SensorReading,
    SensorType,
)

REQUIRED_COLUMNS = {'tag', 'sensor_type', 'value', 'unit', 'recorded_at'}
CHUNK_SIZE = 2000

FLEET = [
    ('ANM-P-012', EquipmentType.WET_CHRISTMAS_TREE, 'Tupi', 2140.0),
    ('MAN-A-004', EquipmentType.MANIFOLD, 'Buzios', 1980.0),
    ('RIS-F-101', EquipmentType.RISER, 'Marlim', 1250.0),
]

SENSOR_PROFILE = {
    # sensor: (unit, baseline, noise sigma, operating envelope)
    SensorType.PRESSURE: ('bar', 210.0, 4.0, (180.0, 240.0)),
    SensorType.TEMPERATURE: ('degC', 48.0, 1.5, (35.0, 60.0)),
    SensorType.VIBRATION: ('mm/s', 2.4, 0.4, (0.0, 4.5)),
}


def synthesise(days: int = 7, step_minutes: int = 15) -> pd.DataFrame:
    """Build a plausible fleet dataset, including a few genuine excursions."""
    rng = np.random.default_rng(seed=42)
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    index = pd.date_range(end=end, periods=int(days * 24 * 60 / step_minutes), freq=f'{step_minutes}min', tz='UTC')

    frames = []
    for tag, _, _, _ in FLEET:
        for sensor, (unit, baseline, sigma, _) in SENSOR_PROFILE.items():
            # Daily thermal cycle plus white noise: enough structure for the
            # resample endpoint to show something other than a flat line.
            cycle = np.sin(np.arange(len(index)) * 2 * np.pi / (24 * 60 / step_minutes))
            values = baseline + cycle * sigma * 2 + rng.normal(0, sigma, len(index))

            # Two excursions per series so the alerts endpoint has material.
            for position in rng.choice(len(index), size=2, replace=False):
                values[position] *= rng.choice([0.55, 1.6])

            frames.append(
                pd.DataFrame(
                    {
                        'tag': tag,
                        'sensor_type': sensor.value,
                        'value': values.round(3),
                        'unit': unit,
                        'recorded_at': index,
                    }
                )
            )

    return pd.concat(frames, ignore_index=True)


def load_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)

    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise SystemExit(f'CSV is missing required columns: {", ".join(sorted(missing))}')

    return frame


def clean(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw export: drop unusable rows, coerce types, de-duplicate.

    Field exports routinely carry blank rows, repeated samples from a retrying
    gateway and timestamps without an offset. Dropping them here keeps the
    database honest instead of pushing the problem to every query.
    """
    before = len(frame)

    frame = frame.dropna(subset=['tag', 'sensor_type', 'value', 'recorded_at'])
    frame['value'] = pd.to_numeric(frame['value'], errors='coerce')
    frame = frame.dropna(subset=['value'])
    frame['recorded_at'] = pd.to_datetime(frame['recorded_at'], utc=True, errors='coerce')
    frame = frame.dropna(subset=['recorded_at'])

    frame['sensor_type'] = frame['sensor_type'].str.strip().str.lower()
    frame = frame[frame['sensor_type'].isin({sensor.value for sensor in SensorType})]

    frame = frame.drop_duplicates(subset=['tag', 'sensor_type', 'recorded_at'], keep='last')

    print(f'cleaned: {before} rows in, {len(frame)} rows out ({before - len(frame)} discarded)')
    return frame.sort_values('recorded_at')


async def ensure_fleet(session, tags: set[str]) -> dict[str, int]:
    """Create any asset referenced by the dataset that is not registered yet."""
    known = {tag: equipment_type for tag, equipment_type, _, _ in FLEET}
    depths = {tag: (field, depth) for tag, _, field, depth in FLEET}

    result = await session.execute(select(Equipment))
    registry = {equipment.tag: equipment.id for equipment in result.scalars().all()}

    for tag in sorted(tags - registry.keys()):
        field, depth = depths.get(tag, ('Unknown', 1000.0))
        equipment = Equipment(
            tag=tag,
            equipment_type=known.get(tag, EquipmentType.MANIFOLD),
            field_name=field,
            water_depth_m=depth,
            status=EquipmentStatus.OPERATING,
            installed_at=datetime.now(UTC) - timedelta(days=900),
        )
        session.add(equipment)
        await session.flush()
        registry[tag] = equipment.id
        print(f'registered {tag} (id={equipment.id})')

    return registry


async def ensure_limits(session, registry: dict[str, int]) -> None:
    result = await session.execute(select(OperatingLimit))
    existing = {(limit.equipment_id, limit.sensor_type) for limit in result.scalars().all()}

    for equipment_id in registry.values():
        for sensor, (_, _, _, (minimum, maximum)) in SENSOR_PROFILE.items():
            if (equipment_id, sensor) in existing:
                continue
            session.add(
                OperatingLimit(
                    equipment_id=equipment_id,
                    sensor_type=sensor,
                    min_value=minimum,
                    max_value=maximum,
                )
            )
    await session.flush()


async def ingest(frame: pd.DataFrame) -> None:
    async with SessionFactory() as session:
        registry = await ensure_fleet(session, set(frame['tag'].unique()))
        await ensure_limits(session, registry)

        frame = frame[frame['tag'].isin(registry)]
        total = 0
        for start in range(0, len(frame), CHUNK_SIZE):
            chunk = frame.iloc[start : start + CHUNK_SIZE]
            session.add_all(
                [
                    SensorReading(
                        equipment_id=registry[row.tag],
                        sensor_type=SensorType(row.sensor_type),
                        value=float(row.value),
                        unit=row.unit,
                        recorded_at=row.recorded_at.to_pydatetime(),
                    )
                    for row in chunk.itertuples()
                ]
            )
            await session.flush()
            total += len(chunk)
            print(f'ingested {total}/{len(frame)}')

        await session.commit()

    await engine.dispose()
    print(f'done: {total} readings across {len(registry)} assets')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, help='CSV export to load; omit to synthesise a dataset')
    parser.add_argument('--days', type=int, default=7, help='days of synthetic history (default: 7)')
    args = parser.parse_args()

    frame = load_csv(args.csv) if args.csv else synthesise(days=args.days)
    asyncio.run(ingest(clean(frame)))


if __name__ == '__main__':
    main()
