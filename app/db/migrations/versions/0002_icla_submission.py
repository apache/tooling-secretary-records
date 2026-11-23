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

"""The ICLA submissions the form receives.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

import app.db.engine

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "iclasubmission",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("received_at", app.db.engine.UTCDateTime(timezone=True), nullable=False),
        sa.Column("full_name", text(), nullable=False),
        sa.Column("family_first", sa.Boolean(), nullable=False),
        sa.Column("public_name", text(), nullable=False),
        sa.Column("postal_address", text(), nullable=False),
        sa.Column("country", text(), nullable=False),
        sa.Column("email", text(), nullable=False),
        sa.Column("apache_ids", text(), nullable=False),
        sa.Column("notify_project", text(), nullable=False),
        sa.Column("invitation", text(), nullable=False),
        sa.Column("signing", text(), nullable=False),
        sa.Column("typed_signature", text(), nullable=True),
        sa.Column("remote_address", text(), nullable=True),
        sa.Column("files", text(), nullable=False),
        sa.Column("folder", text(), nullable=False),
        sa.Column("mailed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("iclasubmission")
