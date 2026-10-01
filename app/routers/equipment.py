"""CRUD for subsea assets."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Equipment, EquipmentStatus, EquipmentType, OperatingLimit
from app.schemas import (
    EquipmentCreate,
    EquipmentRead,
    EquipmentUpdate,
    OperatingLimitCreate,
    OperatingLimitRead,
)

router = APIRouter(prefix='/equipment', tags=['equipment'])

Session = Annotated[AsyncSession, Depends(get_session)]


async def load_equipment(equipment_id: int, session: AsyncSession) -> Equipment:
    equipment = await session.get(Equipment, equipment_id)
    if equipment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f'equipment {equipment_id} not found')
    return equipment


@router.post('', response_model=EquipmentRead, status_code=status.HTTP_201_CREATED)
async def create_equipment(payload: EquipmentCreate, session: Session) -> Equipment:
    equipment = Equipment(**payload.model_dump())
    session.add(equipment)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f'tag {payload.tag} already exists') from error
    return equipment


@router.get('', response_model=list[EquipmentRead])
async def list_equipment(
    session: Session,
    field_name: str | None = None,
    equipment_type: EquipmentType | None = None,
    equipment_status: Annotated[EquipmentStatus | None, Query(alias='status')] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Equipment]:
    query = select(Equipment).order_by(Equipment.tag)
    if field_name is not None:
        query = query.where(Equipment.field_name == field_name)
    if equipment_type is not None:
        query = query.where(Equipment.equipment_type == equipment_type)
    if equipment_status is not None:
        query = query.where(Equipment.status == equipment_status)

    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


@router.get('/{equipment_id}', response_model=EquipmentRead)
async def get_equipment(equipment_id: int, session: Session) -> Equipment:
    return await load_equipment(equipment_id, session)


@router.patch('/{equipment_id}', response_model=EquipmentRead)
async def update_equipment(equipment_id: int, payload: EquipmentUpdate, session: Session) -> Equipment:
    equipment = await load_equipment(equipment_id, session)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(equipment, field, value)
    await session.flush()
    return equipment


@router.delete('/{equipment_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_equipment(equipment_id: int, session: Session) -> None:
    equipment = await load_equipment(equipment_id, session)
    await session.delete(equipment)


@router.put('/{equipment_id}/limits', response_model=OperatingLimitRead)
async def upsert_limit(equipment_id: int, payload: OperatingLimitCreate, session: Session) -> OperatingLimit:
    """One envelope per (equipment, sensor): re-sending replaces the bounds."""
    await load_equipment(equipment_id, session)

    result = await session.execute(
        select(OperatingLimit).where(
            OperatingLimit.equipment_id == equipment_id,
            OperatingLimit.sensor_type == payload.sensor_type,
        )
    )
    limit = result.scalar_one_or_none()

    if limit is None:
        limit = OperatingLimit(equipment_id=equipment_id, **payload.model_dump())
        session.add(limit)
    else:
        limit.min_value = payload.min_value
        limit.max_value = payload.max_value

    await session.flush()
    return limit


@router.get('/{equipment_id}/limits', response_model=list[OperatingLimitRead])
async def list_limits(equipment_id: int, session: Session) -> list[OperatingLimit]:
    await load_equipment(equipment_id, session)
    result = await session.execute(
        select(OperatingLimit).where(OperatingLimit.equipment_id == equipment_id).order_by(OperatingLimit.sensor_type)
    )
    return list(result.scalars().all())
