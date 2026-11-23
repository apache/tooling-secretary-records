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

"""The ICLA itself: the Foundation's own form, https://www.apache.org/licenses/icla.pdf, filled in with
what the submitter wrote. Not a document of this tool's: the agreement is the one every committer signs,
so the one apache.org publishes is filled rather than restated."""

from __future__ import annotations

import io
import pathlib
from typing import TYPE_CHECKING, Final

import pypdf

if TYPE_CHECKING:
    import datetime

    from app.icla.fields import IclaFields

TEMPLATE: Final[pathlib.Path] = pathlib.Path(__file__).parent / "icla.pdf"
"""As apache.org publishes it. Its fields are named as below; a new version with others fails `filled`."""

FIELDS: Final[tuple[str, ...]] = (
    "Full Name",
    "Public Name",
    "Postal Address",
    "Postal Address 2",
    "Country",
    "E-mail",
    "Preferred Apache id(s)",
    "Notify Project",
    "Date",
)


def values(fields: IclaFields, day: datetime.date) -> dict[str, str]:
    """What each of the form's text fields says. The address has two lines on the form: the first, and
    the rest joined."""
    first, *rest = fields.postal_address.splitlines()
    return {
        "Full Name": fields.full_name,
        "Public Name": fields.public_name,
        "Postal Address": first,
        "Postal Address 2": ", ".join(rest),
        "Country": fields.country,
        "E-mail": fields.email,
        "Preferred Apache id(s)": ", ".join(fields.apache_ids),
        "Notify Project": fields.notify_project,
        "Date": day.isoformat(),
    }


def filled(fields: IclaFields, day: datetime.date) -> bytes:
    """The ICLA with the submitter's fields written into it, and its "Family First" box ticked where they
    said so. Raises `KeyError` where the form lacks a field this writes."""
    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(TEMPLATE))
    present = set(writer.get_fields() or {})
    if missing := [name for name in (*FIELDS, "Family First") if name not in present]:
        raise KeyError(f"the ICLA form has no field {', '.join(missing)}")
    text = values(fields, day)
    for page in writer.pages:
        writer.update_page_form_field_values(page, text, auto_regenerate=False)
        if fields.family_first:
            writer.update_page_form_field_values(page, {"Family First": "/Yes"}, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
