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

"""The data models to be persisted in the database.

The database is workflow state and cache; SVN stays the source of record, and the database must
be rebuildable from raw mail plus SVN (REQUIREMENTS.md, section 5). The admin data browser (`/admin/data`)
lists a table only where `app.admin.routes.BROWSABLE` names it, and only the columns it names: each new
table is put there or in `NOT_BROWSABLE`, and a test fails until it is.
"""

# NOTE: this module deliberately omits the __future__ annotations import: sqlmodel resolves
# the model fields at class creation and does not support postponed (string) annotations.

import datetime

import sqlalchemy
import sqlmodel
from sqlmodel import SQLModel

from app.db.engine import UTCDateTime, create_registry


class DBBase(SQLModel, registry=create_registry()):
    pass


class IclaSubmission(DBBase, table=True):
    """An ICLA as the submission form received it (REQUIREMENTS.md, F-101): the fields the submitter wrote,
    how they signed, and the files kept for it under the state directory. Personal data: the secretarial
    roles' alone, and not in the admin data browser (`app.admin.routes.NOT_BROWSABLE`)."""

    id: int | None = sqlmodel.Field(default=None, primary_key=True)
    received_at: datetime.datetime = sqlmodel.Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
    )
    full_name: str
    family_first: bool = False
    public_name: str = ""
    postal_address: str
    country: str
    email: str
    apache_ids: str = ""
    """Comma-separated, the first preferred."""
    notify_project: str = ""
    invitation: str = ""
    signing: str
    """`typed`, `signed-pdf` or `pgp` (`app.icla.fields.Signing`)."""
    typed_signature: str | None = None
    """For a typed signature: the name as typed."""
    remote_address: str | None = None
    """For a typed signature: the address it was given from, which is part of the record of it."""
    files: str = ""
    """The names of the files kept for it, comma-separated, under `<state>/icla/<folder>/`."""
    folder: str = ""
    """A random name, not the row's id: SQLite gives an id again after the newest row is deleted."""
    mailed: bool = False
    """Whether the mail to the Secretary was handed to the relay."""
