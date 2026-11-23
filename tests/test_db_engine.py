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
        assert engine.pool.checkedin() >= 2
    finally:
        await engine.dispose()
