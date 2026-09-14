"""Shared SQLAlchemy engine.

One engine per process, reused across requests / worker loop iterations. No
ORM Session here — callers use `engine.begin()` for a transactional
connection and issue Core statements or raw SQL.
"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.settings import settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)
