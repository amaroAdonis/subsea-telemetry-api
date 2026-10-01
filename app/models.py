"""Relational model for subsea equipment and its sensor time series."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EquipmentType(StrEnum):
    """Subsea assets the SUB team tracks."""

    WET_CHRISTMAS_TREE = 'wet_christmas_tree'
    MANIFOLD = 'manifold'
    RISER = 'riser'
    BOP = 'bop'
    UMBILICAL = 'umbilical'
    PLET = 'plet'


class EquipmentStatus(StrEnum):
    OPERATING = 'operating'
    STANDBY = 'standby'
    MAINTENANCE = 'maintenance'
    DECOMMISSIONED = 'decommissioned'


class SensorType(StrEnum):
    PRESSURE = 'pressure'
    TEMPERATURE = 'temperature'
    VIBRATION = 'vibration'
    FLOW_RATE = 'flow_rate'


def enum_column(enum_class: type[StrEnum]) -> Enum:
    """Store the member *value*, not its name.

    SQLAlchemy defaults to persisting `MEMBER_NAME`, which would put
    `WET_CHRISTMAS_TREE` in a column the REST contract describes as
    `wet_christmas_tree`. Anyone reading the table in SQL would see a different
    vocabulary from the API, so pin it to the value on both sides.
    """
    return Enum(
        enum_class,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class Equipment(Base):
    __tablename__ = 'equipment'
    __table_args__ = (
        CheckConstraint('water_depth_m > 0', name='ck_equipment_positive_depth'),
        Index('ix_equipment_field_status', 'field_name', 'status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tag: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    equipment_type: Mapped[EquipmentType] = mapped_column(enum_column(EquipmentType))
    field_name: Mapped[str] = mapped_column(String(64))
    water_depth_m: Mapped[float] = mapped_column(Float)
    status: Mapped[EquipmentStatus] = mapped_column(enum_column(EquipmentStatus), default=EquipmentStatus.OPERATING)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    readings: Mapped[list[SensorReading]] = relationship(
        back_populates='equipment', cascade='all, delete-orphan', passive_deletes=True
    )
    limits: Mapped[list[OperatingLimit]] = relationship(
        back_populates='equipment', cascade='all, delete-orphan', passive_deletes=True
    )


class SensorReading(Base):
    """A single sensor sample. The composite index is what keeps the window
    queries cheap once a field has a few million rows."""

    __tablename__ = 'sensor_reading'
    __table_args__ = (Index('ix_reading_equipment_sensor_time', 'equipment_id', 'sensor_type', 'recorded_at'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey('equipment.id', ondelete='CASCADE'), index=True)
    sensor_type: Mapped[SensorType] = mapped_column(enum_column(SensorType))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    equipment: Mapped[Equipment] = relationship(back_populates='readings')


class OperatingLimit(Base):
    """Envelope a sensor is expected to stay within. Drives the alert endpoint."""

    __tablename__ = 'operating_limit'
    __table_args__ = (
        UniqueConstraint('equipment_id', 'sensor_type', name='uq_limit_equipment_sensor'),
        CheckConstraint('min_value < max_value', name='ck_limit_ordered_bounds'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey('equipment.id', ondelete='CASCADE'), index=True)
    sensor_type: Mapped[SensorType] = mapped_column(enum_column(SensorType))
    min_value: Mapped[float] = mapped_column(Float)
    max_value: Mapped[float] = mapped_column(Float)

    equipment: Mapped[Equipment] = relationship(back_populates='limits')
