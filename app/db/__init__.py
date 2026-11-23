# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""The SQLite database: created at startup, migrated to head, and checked against the models."""

from __future__ import annotations

import logging
import pathlib
from typing import TYPE_CHECKING, Final

import alembic.command
import alembic.config
import sqlalchemy.ext.asyncio

from app import config
from app.db.engine import Session, create_async_engine
from app.db.models import DBBase, IclaSubmission

if TYPE_CHECKING:
    import asfquart.base as base

_LOGGER: Final = logging.getLogger(__name__)

_global_engine: sqlalchemy.ext.asyncio.AsyncEngine | None = None
_global_session_maker: sqlalchemy.ext.asyncio.async_sessionmaker | None = None


def alembic_config(db_path: pathlib.Path) -> alembic.config.Config:
    """Alembic, pointed at this database and this repository's migrations."""
    alembic_cfg = alembic.config.Config("app/db/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    alembic_cfg.set_main_option("script_location", "app/db/migrations")
    return alembic_cfg


def db_path() -> pathlib.Path:
    app_config = config.get()
    return pathlib.Path(app_config.base.state_dir) / app_config.db.sqlite_db_file


def init_database(app: base.QuartApp) -> None:
    """Create the database before serving, apply every pending migration, and refuse to start
    when the models and the migrated schema disagree - a model changed without a migration is
    caught here rather than by the first query that needs the column."""

    @app.before_serving
    async def create() -> None:
        global _global_engine, _global_session_maker

        path = db_path()
        _global_engine = await create_async_engine(path)
        _global_session_maker = sqlalchemy.ext.asyncio.async_sessionmaker(
            bind=_global_engine, class_=Session, expire_on_commit=False
        )

        alembic_cfg = alembic_config(path)
        logging.getLogger("alembic").setLevel(logging.INFO)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

        _LOGGER.info("applying database migrations to %s", path)
        alembic.command.upgrade(alembic_cfg, "head")
        alembic.command.check(alembic_cfg)
        _LOGGER.info("database is at head and matches the models")

    @app.after_serving
    async def close() -> None:
        await _shutdown_database()


def is_ready() -> bool:
    """Whether there is a database to ask: not where an app was created and never started."""
    return _global_session_maker is not None


def session() -> Session:
    """Create a new asynchronous database session."""
    if _global_session_maker is None:
        raise RuntimeError("database not initialized")
    return _global_session_maker()  # type: ignore


async def _shutdown_database() -> None:
    """Dispose of the engine and forget it.

    Clearing the globals is the point of the second half. Left set, they still hand out
    sessions on a disposed engine, and each one checks out a *new* aiosqlite connection -
    whose worker thread is not a daemon, and which nothing is left to close. Anything
    outliving shutdown could quietly leave the process unable to exit. Failing loudly at the
    point of use beats hanging at interpreter exit. In a ``finally`` for the same reason: a
    ``dispose()`` that raises is when we least want the globals left pointing at it.
    """
    global _global_engine, _global_session_maker

    try:
        if _global_engine:
            _LOGGER.info("closing database")
            await _global_engine.dispose()
    finally:
        _global_engine = None
        _global_session_maker = None


__all__ = ["DBBase", "IclaSubmission", "Session", "alembic_config", "db_path", "init_database", "is_ready", "session"]
