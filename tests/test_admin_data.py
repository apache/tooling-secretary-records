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

"""The admin data browser shows only what it is told to: the paperwork is the secretarial team's."""

from __future__ import annotations

from app.admin.routes import BROWSABLE, NOT_BROWSABLE
from app.db.models import DBBase


def test_every_table_is_either_browsable_or_kept_out_and_never_both() -> None:
    """A new model fails this until somebody decides whether an admin may see its rows, and which
    columns: read from the metadata, the browser would have shown a submission's address."""
    tables = {table.name: table for table in DBBase.metadata.sorted_tables}

    assert set(tables) - set(BROWSABLE) - NOT_BROWSABLE == set(), "decide these in app/admin/routes.py"
    assert set(BROWSABLE) & NOT_BROWSABLE == set()
    for name, columns in BROWSABLE.items():
        assert name in tables, f"{name} is no table"
        assert set(columns) <= {column.name for column in tables[name].columns}, f"{name} lists no such column"


async def test_a_table_not_listed_is_not_there(authenticated_client) -> None:
    admin = await authenticated_client("zed", roles="admin")

    assert (await admin.get("/admin/data/anything")).status_code == 404
