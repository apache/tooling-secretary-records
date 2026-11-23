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


import datetime
import logging
from typing import TYPE_CHECKING, Final

import sqlalchemy
import sqlalchemy.event
import sqlalchemy.ext.asyncio
from sqlalchemy.orm import registry

if TYPE_CHECKING:
    import pathlib

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


def create_metadata() -> sqlalchemy.MetaData:
    return sqlalchemy.MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def create_registry():
    return registry(metadata=create_metadata())


class UTCDateTime(sqlalchemy.types.TypeDecorator):
    """
    A custom column type to store datetime in sqlite.

    As sqlite does not have timezone support, we ensure that all datetimes stored
    within sqlite are converted to UTC. When retrieved, the datetimes are constructed
    as offset-aware datetime with UTC as their timezone.
    """

    impl = sqlalchemy.types.TIMESTAMP(timezone=True)

    cache_ok = True

    def process_bind_param(self, value, _dialect):  # type: ignore
        if value:
            if not isinstance(value, datetime.datetime):
                raise ValueError(f"Unexpected value type {type(value)}")

            if value.tzinfo is None:
                raise ValueError("Unexpected offset-naive datetime")

            # store the datetime in UTC in sqlite as it does not support timezones
            return value.astimezone(datetime.UTC)
        else:
            return value

    def process_result_value(self, value, _dialect):  # type: ignore
        if isinstance(value, datetime.datetime):
            return value.replace(tzinfo=datetime.UTC)
        else:
            return value


class Session(sqlalchemy.ext.asyncio.AsyncSession):
    pass


_PRAGMAS: Final[tuple[str, ...]] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA wal_autocheckpoint=16",
    "PRAGMA journal_size_limit=1536",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA cache_size=-64000",
    "PRAGMA mmap_size=268435456",
    "PRAGMA foreign_keys=ON",
)


def _configure_connection(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        for pragma in _PRAGMAS:
            cursor.execute(pragma)
    finally:
        cursor.close()


async def create_async_engine(db_path: pathlib.Path) -> sqlalchemy.ext.asyncio.AsyncEngine:
    sqlite_url = f"sqlite+aiosqlite:///{db_path!s}"

    _LOGGER.info("creating database with URL: %s", sqlite_url)

    # Use aiosqlite for async SQLite access
    async_engine = sqlalchemy.ext.asyncio.create_async_engine(
        sqlite_url,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
    )

    # SQLite keeps these per connection, and the pool opens more than one: each connection the pool
    # opens is configured as it opens, or a second one would accept a reference to a row that is not
    # there (foreign_keys is off by default). journal_mode=WAL is kept in the database file.
    # 64 MB for the cache_size; the WAL settings follow
    # https://wiki.mozilla.org/Performance/Avoid_SQLite_In_Your_Next_Firefox_Feature. How long a
    # connection waits for a lock is the `timeout` above, 30 s, on every connection: a busy_timeout
    # pragma here would replace it.
    sqlalchemy.event.listen(async_engine.sync_engine, "connect", _configure_connection)
    # One connection now, so that the file exists and is in WAL mode before the migrations run on it
    # and before requests share it: switching a file to WAL needs a moment nobody else holds it.
    async with async_engine.connect():
        pass

    return async_engine
