"""Pydantic v2 schemas: the API contract, kept separate from the ORM model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import EquipmentStatus, EquipmentType, SensorType

TAG_PATTERN = r'^[A-Z]{2,6}-[A-Z0-9]{1,4}-\d{2,4}$'


class EquipmentCreate(BaseModel):
    tag: str = Field(
        pattern=TAG_PATTERN,
        description='Asset tag, e.g. ANM-P-012.',
        examples=['ANM-P-012'],
    )
    equipment_type: EquipmentType
    field_name: str = Field(min_length=2, max_length=64)
    water_depth_m: float = Field(gt=0, le=4000, description='Water depth in metres.')
    status: EquipmentStatus = EquipmentStatus.OPERATING
    installed_at: datetime | None = None


class EquipmentUpdate(BaseModel):
    """Every field optional: PATCH only touches what the caller sent."""

    field_name: str | None = Field(default=None, min_length=2, max_length=64)
    water_depth_m: float | None = Field(default=None, gt=0, le=4000)
    status: EquipmentStatus | None = None
    installed_at: datetime | None = None


class EquipmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str
    equipment_type: EquipmentType
    field_name: str
    water_depth_m: float
    status: EquipmentStatus
    installed_at: datetime | None
    created_at: datetime


class ReadingCreate(BaseModel):
    sensor_type: SensorType
    value: float
    unit: str = Field(min_length=1, max_length=16)
    recorded_at: datetime

    @field_validator('recorded_at')
    @classmethod
    def must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError('recorded_at must be timezone-aware (ISO 8601 with offset)')
        return value


class ReadingBatch(BaseModel):
    readings: list[ReadingCreate] = Field(min_length=1)


class ReadingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    equipment_id: int
    sensor_type: SensorType
    value: float
    unit: str
    recorded_at: datetime


class OperatingLimitCreate(BaseModel):
    sensor_type: SensorType
    min_value: float
    max_value: float

    @model_validator(mode='after')
    def bounds_must_be_ordered(self) -> OperatingLimitCreate:
        if self.min_value >= self.max_value:
            raise ValueError('min_value must be lower than max_value')
        return self


class OperatingLimitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    equipment_id: int
    sensor_type: SensorType
    min_value: float
    max_value: float


class ResampledPoint(BaseModel):
    bucket_start: datetime
    value: float | None
    sample_count: int


class ResampleResponse(BaseModel):
    equipment_id: int
    sensor_type: SensorType
    freq: str
    agg: str
    points: list[ResampledPoint]


class SeriesStats(BaseModel):
    equipment_id: int
    sensor_type: SensorType
    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    p50: float | None
    p95: float | None
    maximum: float | None
    first_reading_at: datetime | None
    last_reading_at: datetime | None


class Alert(BaseModel):
    reading_id: int
    sensor_type: SensorType
    value: float
    unit: str
    recorded_at: datetime
    min_value: float
    max_value: float
    breach: str = Field(description="'below' or 'above'")


class IngestionResult(BaseModel):
    accepted: int
    equipment_id: int
