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

"""What a submitter writes on the form, held to the rules the ICLA's own PDF states or implies."""

from __future__ import annotations

import enum
import re
import unicodedata
from typing import Final

import pydantic

APACHE_ID: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9]{2,19}$")
"""A preferred Apache id: lowercase letters and digits, beginning with a letter (F-100). Whether it is
free is the Secretary's to check when the ICLA is filed (F-113 is not built yet)."""

EMAIL: Final[re.Pattern[str]] = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
"""One address: the ICLA names one, and an account is created for one."""

MAX_IDS: Final[int] = 3


class Signing(enum.StrEnum):
    TYPED = "typed"
    """The submitter types their full name and agrees: an electronic signature, kept with when and from
    where it was given."""
    SIGNED_PDF = "signed-pdf"
    """The filled ICLA, signed by hand or by a PDF tool, uploaded."""
    PGP = "pgp"
    """The filled ICLA and a detached PGP signature of it, uploaded."""


def _text(value: str, field: str, limit: int, *, lines: int = 1) -> str:
    value = "\n".join(line.strip() for line in value.strip().splitlines())
    if len(value) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    if value.count("\n") >= lines:
        raise ValueError(f"{field} is at most {lines} line{'s' if lines > 1 else ''}")
    if any(unicodedata.category(char).startswith("C") and char != "\n" for char in value):
        raise ValueError(f"{field} holds a character that cannot be printed")
    return value


class IclaFields(pydantic.BaseModel):
    """The fields of the ICLA, as the form sends them. Each rule's message names the field as the form
    labels it, for the form to show beside it."""

    model_config = pydantic.ConfigDict(extra="forbid")

    full_name: str
    family_first: bool = False
    """Whether the family name comes first in `full_name` - the PDF's "Family First" box."""
    public_name: str = ""
    """How the name is shown in public; the full name where it is left empty."""
    postal_address: str
    country: str
    email: str
    apache_ids: list[str] = []
    """Up to three, the first preferred."""
    notify_project: str = ""
    """The project the submitter names, by its id where the form offered it, or as written."""
    invitation: str = ""
    """The invitation the submitter came from, where they followed one (section 3.13)."""

    @pydantic.field_validator("full_name")
    @classmethod
    def _full_name(cls, value: str) -> str:
        value = _text(value, "Full name", 200)
        if not value:
            raise ValueError("Full name is required")
        return value

    @pydantic.field_validator("public_name")
    @classmethod
    def _public_name(cls, value: str) -> str:
        return _text(value, "Public name", 200)

    @pydantic.field_validator("postal_address")
    @classmethod
    def _postal_address(cls, value: str) -> str:
        value = _text(value, "Postal address", 400, lines=6)
        if not value:
            raise ValueError("Postal address is required")
        return value

    @pydantic.field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        value = _text(value, "Country", 100)
        if not value:
            raise ValueError("Country is required")
        return value

    @pydantic.field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if not EMAIL.match(value) or len(value) > 254:
            raise ValueError("Email is one address, written like name@example.org")
        return value

    @pydantic.field_validator("apache_ids")
    @classmethod
    def _apache_ids(cls, value: list[str]) -> list[str]:
        ids = [each.strip() for each in value if each.strip()]
        if len(ids) > MAX_IDS:
            raise ValueError(f"Preferred Apache ids are at most {MAX_IDS}")
        if wrong := [each for each in ids if not APACHE_ID.match(each)]:
            raise ValueError(
                f"{', '.join(wrong)}: an Apache id is 3 to 20 lowercase letters and digits, beginning with a letter"
            )
        return list(dict.fromkeys(ids))

    @pydantic.field_validator("notify_project", "invitation")
    @classmethod
    def _short(cls, value: str) -> str:
        return _text(value, "Project", 100)

    @property
    def shown_name(self) -> str:
        return self.public_name or self.full_name


def errors_of(error: pydantic.ValidationError) -> dict[str, str]:
    """Each field's message, by the field's name, for the form to show beside it."""
    found: dict[str, str] = {}
    for each in error.errors():
        field = str(each["loc"][0]) if each["loc"] else "form"
        message = str(each["msg"]).removeprefix("Value error, ")
        found.setdefault(field, message if each["type"] == "value_error" else f"{field.replace('_', ' ')}: {message}")
    return found
