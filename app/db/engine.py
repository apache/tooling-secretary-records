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
import pathlib
from typing import Final

import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlalchemy.sql as sql
from sqlalchemy.orm import registry

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

    # Set SQLite pragmas for better performance
    # Use 64 MB for the cache_size, and 5000ms for busy_timeout
    # also applied suggestions for WAL from
    # https://wiki.mozilla.org/Performance/Avoid_SQLite_In_Your_Next_Firefox_Feature
    async with async_engine.begin() as conn:
        await conn.execute(sql.text("PRAGMA journal_mode=WAL"))
        await conn.execute(sql.text("PRAGMA wal_autocheckpoint=16"))
        await conn.execute(sql.text("PRAGMA journal_size_limit=1536"))
        await conn.execute(sql.text("PRAGMA synchronous=NORMAL"))
        await conn.execute(sql.text("PRAGMA cache_size=-64000"))
        await conn.execute(sql.text("PRAGMA mmap_size=268435456"))
        await conn.execute(sql.text("PRAGMA foreign_keys=ON"))
        await conn.execute(sql.text("PRAGMA busy_timeout=5000"))

    return async_engine
