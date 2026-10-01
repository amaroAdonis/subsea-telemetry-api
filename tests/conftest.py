"""Test fixtures.

Each test gets a clean schema. The default URL is SQLite so the suite runs with
no services up; point TEST_DATABASE_URL at the compose Postgres to run the same
tests against the real engine (`make test-pg`).
"""

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_session
from app.main import app

TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL', 'sqlite+aiosqlite:///:memory:')


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as test_session:
        yield test_session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def equipment_payload() -> dict:
    return {
        'tag': 'ANM-P-012',
        'equipment_type': 'wet_christmas_tree',
        'field_name': 'Tupi',
        'water_depth_m': 2140.0,
    }


@pytest_asyncio.fixture
async def equipment_id(client: AsyncClient, equipment_payload: dict) -> int:
    response = await client.post('/equipment', json=equipment_payload)
    assert response.status_code == 201
    return response.json()['id']
