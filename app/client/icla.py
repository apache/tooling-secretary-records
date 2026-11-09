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

"""The ICLA submission form (REQUIREMENTS.md, F-101), open to anybody: whoever fills it in has no account
yet. The page, the projects it offers, the filled ICLA to download and sign, and the submission itself."""

from __future__ import annotations

import collections
import datetime
import json
import re
import time
from typing import Any, Final

import pydantic
import quart

from app import config
from app.icla import document, projects, submission
from app.icla.fields import IclaFields, Signing, errors_of
from app.icla.submission import Signed

from . import CLIENT

_WINDOW: Final[float] = 3600.0
_MAX_BODY: Final[int] = submission.MAX_PDF + submission.MAX_SIGNATURE + 64 * 1024

_RECENT: Final[dict[str, collections.deque[float]]] = {}
"""When each address last submitted, within the hour: the form is mail to the Secretary from anybody."""


def _address() -> str:
    return quart.request.remote_addr or "unknown"


def _too_many(address: str) -> bool:
    now = time.monotonic()
    recent = _RECENT.setdefault(address, collections.deque())
    while recent and now - recent[0] > _WINDOW:
        recent.popleft()
    return len(recent) >= config.get().icla.submissions_per_hour


_CAMEL: Final[re.Pattern[str]] = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(raw: Any) -> Any:
    """The fields with their names as the model has them. The app speaks camelCase on the wire
    (`QuartSchema(convert_casing=True)`), and answers the errors by field in it."""
    if not isinstance(raw, dict):
        return raw
    return {_CAMEL.sub("_", str(key)).lower(): value for key, value in raw.items()}


def _fields(raw: Any) -> IclaFields | dict[str, str]:
    """The fields, or what is wrong with them by field."""
    try:
        return IclaFields.model_validate(_snake(raw))
    except pydantic.ValidationError as ex:
        return errors_of(ex)


@CLIENT.route("/icla")
async def icla_form() -> str:
    invitation = quart.request.args.get("invitation", "")[:100]
    return await quart.render_template("icla.html", svelte_page="icla", invitation=invitation)


@CLIENT.route("/icla/projects")
async def icla_projects() -> quart.Response:
    listed = [{"id": p.id, "name": p.name, "kind": p.kind} for p in await projects.offered()]
    return quart.Response(json.dumps(listed), content_type="application/json")


@CLIENT.route("/icla/document", methods=["POST"])
async def icla_document() -> Any:
    """The ICLA filled in with the fields sent, to download and sign. Nothing is kept."""
    fields = _fields(await quart.request.get_json(silent=True) or {})
    if isinstance(fields, dict):
        return {"errors": fields}, 400
    pdf = document.filled(fields, datetime.datetime.now(datetime.UTC).date())
    return quart.Response(
        pdf,
        content_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="icla.pdf"', "Cache-Control": "no-store"},
    )


async def _upload(name: str) -> bytes | None:
    files = await quart.request.files
    upload = files.get(name)
    return upload.read() if upload is not None else None


@CLIENT.route("/icla/submit", methods=["POST"])
async def icla_submit() -> Any:
    """The ICLA, signed one of three ways (`Signing`): kept, and mailed to the Secretary. 400 says what is
    wrong by field; 413 a body too large; 429 too many from one address within the hour."""
    if (quart.request.content_length or 0) > _MAX_BODY:
        return {"errors": {"form": "The files are larger than the form takes: 10 MB for the ICLA"}}, 413
    address = _address()
    if _too_many(address):
        return {"errors": {"form": "Too many submissions from here this hour: try again later"}}, 429
    form = await quart.request.form
    try:
        raw = json.loads(form.get("fields", "{}"))
        signing = Signing(form.get("signing", ""))
    except (ValueError, TypeError):
        return {"errors": {"signing": "Choose how you sign"}}, 400
    fields = _fields(raw)
    if isinstance(fields, dict):
        return {"errors": fields}, 400
    signed = Signed(
        signing=signing,
        typed_name=form.get("typedSignature", ""),
        agreed=form.get("agreed") == "true",
        pdf=await _upload("pdf"),
        signature=await _upload("signature"),
        remote_address=address,
    )
    if refused := submission.refusals(fields, signed):
        return {"errors": refused}, 400
    _RECENT.setdefault(address, collections.deque()).append(time.monotonic())
    row = await submission.receive(fields, signed)
    return {"received": True, "mailed": row.mailed}, 201


def forget() -> None:
    """For tests: no submission seen from anywhere."""
    _RECENT.clear()
