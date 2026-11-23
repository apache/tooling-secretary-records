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

"""Every connection of the pool is configured, not only the first (`app/db/engine.py`)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import sqlalchemy

from app.db.engine import create_async_engine

if TYPE_CHECKING:
    import pathlib


async def test_the_file_is_in_wal_mode_once_the_engine_is_made(tmp_path: pathlib.Path) -> None:
    """Before the migrations run on it: switching a file to WAL needs a moment nobody else holds it."""
    import sqlite3

    engine = await create_async_engine(tmp_path / "test.db")
    try:
        with sqlite3.connect(tmp_path / "test.db") as raw:
            assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        await engine.dispose()


async def test_every_connection_waits_30_seconds_for_a_lock(tmp_path: pathlib.Path) -> None:
    engine = await create_async_engine(tmp_path / "test.db")
    try:
        async with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                timeout = (await connection.execute(sqlalchemy.text("PRAGMA busy_timeout"))).scalar_one()
                assert timeout == 30000
    finally:
        await engine.dispose()


async def test_each_connection_the_pool_opens_enforces_foreign_keys(tmp_path: pathlib.Path) -> None:
    engine = await create_async_engine(tmp_path / "test.db")
    try:
        held = asyncio.Event()
        release = asyncio.Event()

        async def first() -> int:
            async with engine.connect() as connection:
                value = (await connection.execute(sqlalchemy.text("PRAGMA foreign_keys"))).scalar_one()
                held.set()
                await release.wait()
                return value

        async def second() -> int:
            await held.wait()
            try:
                async with engine.connect() as connection:
                    return (await connection.execute(sqlalchemy.text("PRAGMA foreign_keys"))).scalar_one()
            finally:
                release.set()

        values = await asyncio.gather(first(), second())
        assert values == [1, 1]
    finally:
        await engine.dispose()
