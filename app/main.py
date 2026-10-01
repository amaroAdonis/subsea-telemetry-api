"""Application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.routers import equipment, readings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version='0.1.0',
    description=(
        'REST API for subsea equipment telemetry: asset registry, time-series '
        'ingestion, resampling and operating-envelope alerts.'
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(equipment.router)
app.include_router(readings.router)


@app.get('/health', tags=['ops'])
async def health() -> dict[str, str]:
    """Liveness plus a round trip to the database, so a dead pool is visible."""
    async with engine.connect() as connection:
        await connection.execute(text('SELECT 1'))
    return {'status': 'ok'}
