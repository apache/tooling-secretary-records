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
be rebuildable from raw mail plus SVN (REQUIREMENTS.md, section 5). Every table here is listed by
the admin data browser (`/admin/data`) without being registered: it reads this metadata.
"""

# NOTE: this module deliberately omits the __future__ annotations import: sqlmodel resolves
# the model fields at class creation and does not support postponed (string) annotations.

from sqlmodel import SQLModel

from app.db.engine import create_registry


class DBBase(SQLModel, registry=create_registry()):
    pass
