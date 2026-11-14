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

"""What an admin can look at: how the server is doing, and what the database holds."""

from __future__ import annotations

import datetime
import platform
import time
from typing import Final

import pydantic
import quart
import quart_schema
import sqlalchemy

from app import config, db
from app.db.models import DBBase

from . import ADMIN

_STARTED: Final = time.monotonic()
_STARTED_AT: Final = datetime.datetime.now(datetime.UTC)


class DataFilter(pydantic.BaseModel):
    page_size: int = pydantic.Field(default=100, ge=1, le=1000)
    offset: int = pydantic.Field(default=0, ge=0)


async def _database_revision() -> str | None:
    """The migration the database is at, as alembic recorded it."""
    async with db.session() as session:
        result = await session.execute(sqlalchemy.text("SELECT version_num FROM alembic_version"))
        return result.scalar_one_or_none()


@ADMIN.route("/server-status")
async def server_status() -> str:
    app_config = config.get()
    uptime = datetime.timedelta(seconds=int(time.monotonic() - _STARTED))
    return await quart.render_template(
        "server_status.html",
        rows=[
            ("Started", f"{_STARTED_AT:%Y-%m-%d %H:%M:%S %Z} ({uptime} ago)"),
            ("Python", platform.python_version()),
            ("Dev mode", "on" if app_config.base.dev_mode else "off"),
            ("Database", str(db.db_path())),
            ("Database revision", await _database_revision() or "none"),
            ("Outgoing mail", app_config.smtp.host if app_config.smtp is not None else "none - no smtp block"),
            ("Secretary", ", ".join(app_config.roles.secretary) or "nobody configured"),
            ("Assistant Secretary", ", ".join(app_config.roles.assistant_secretary) or "nobody configured"),
            ("Secretarial team", ", ".join(app_config.roles.secretarial_team) or "nobody configured"),
        ],
    )


BROWSABLE: Final[dict[str, tuple[str, ...]]] = {}
"""Each table an admin may browse, with the columns shown. Name no column holding personal data: a
name, an address, anything from a document."""

NOT_BROWSABLE: Final[frozenset[str]] = frozenset()
"""The tables deliberately kept out of the browser, because what they hold is the secretarial team's."""


@ADMIN.route("/data")
@ADMIN.route("/data/<table_name>")
@quart_schema.validate_querystring(DataFilter)
async def data(query_args: DataFilter, table_name: str | None = None) -> str:
    """Browse the rows of the tables `BROWSABLE` lists, and only the columns it names for each.

    Opt-in rather than read from the metadata: Admin is the Tooling PMC and infra root, who do not
    read the paperwork (REQUIREMENTS R-1), and a table read from the metadata would show a
    submission's name and address the moment its model existed. tests/test_admin_data.py fails on a
    table that is in neither `BROWSABLE` nor `NOT_BROWSABLE`, so each new model is a decision."""
    tables = {table.name: table for table in DBBase.metadata.sorted_tables if table.name in BROWSABLE}
    if table_name is None:
        table_name = next(iter(tables), None)
    elif table_name not in tables:
        quart.abort(404, description=f"There is no table '{table_name}'")

    columns: list[str] = []
    records: list[dict[str, object]] = []
    if table_name is not None:
        table = tables[table_name]
        columns = list(BROWSABLE[table_name])
        statement = (
            sqlalchemy.select(*(table.c[name] for name in columns))
            .limit(query_args.page_size)
            .offset(query_args.offset)
        )
        async with db.session() as session:
            records = [dict(row._mapping) for row in await session.execute(statement)]

    return await quart.render_template(
        "data-browser.html",
        tables=list(tables),
        table_name=table_name,
        columns=columns,
        records=records,
    )
