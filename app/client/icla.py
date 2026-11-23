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

import asyncio
import collections
import datetime
import ipaddress
import json
import re
import time
import urllib.parse
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
_DOCUMENTS_PER_HOUR: Final[int] = 30
"""Downloads of the filled ICLA from one address: nothing is kept or mailed, but each is a PDF built."""

_RECENT: Final[dict[str, collections.deque[float]]] = {}
"""When each address last submitted, or downloaded the ICLA, within the hour, by `<what> <address>`."""


_PROXIES: frozenset[str] = frozenset({"127.0.0.1", "::1"})
"""The peers whose `X-Forwarded-For` is believed: the Infra proxy, on this host."""


def _address() -> str:
    """Who is asking. Behind the Infra proxy the connection is the proxy's, so the address it forwards is
    taken - but only from a proxy on this host: a header from anywhere else is the caller's to write. An
    IPv6 address counts as its /64, which one host holds whole."""
    peer = quart.request.remote_addr or "unknown"
    forwarded = quart.request.headers.get("X-Forwarded-For", "")
    if forwarded and peer in _PROXIES:
        peer = forwarded.split(",")[-1].strip()
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else str(ip)


def _cross_site() -> bool:
    """Whether another site's page sent this: a form post needs no CORS, so a page anywhere could make its
    visitors submit ICLAs, each a mail to the Secretary."""
    site = quart.request.headers.get("Sec-Fetch-Site")
    if site is not None:
        return site not in ("same-origin", "none")
    origin = quart.request.headers.get("Origin")
    return origin is not None and urllib.parse.urlsplit(origin).netloc != quart.request.host


def _take(key: str, limit: int) -> bool:
    """Take one of the hour's `limit` for `key`, or answer False where none is left. Taken before the
    request is read, so that requests arriving together cannot all pass; `_give_back` returns it where
    the request is refused."""
    now = time.monotonic()
    recent = _RECENT.setdefault(key, collections.deque())
    while recent and now - recent[0] > _WINDOW:
        recent.popleft()
    if len(recent) >= limit:
        return False
    recent.append(now)
    return True


def _give_back(key: str) -> None:
    recent = _RECENT.get(key)
    if recent:
        recent.pop()
    if not recent:
        _RECENT.pop(key, None)


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
    if _cross_site():
        return {"errors": {"form": "The ICLA is filled in from this site's own form"}}, 403
    if not _take(f"document {_address()}", _DOCUMENTS_PER_HOUR):
        return {"errors": {"form": "Too many downloads from here this hour: try again later"}}, 429
    fields = _fields(await quart.request.get_json(silent=True) or {})
    if isinstance(fields, dict):
        return {"errors": fields}, 400
    # a PDF built is the better part of a second: not on the loop every other request waits on
    pdf = await asyncio.to_thread(document.filled, fields, datetime.datetime.now(datetime.UTC).date())
    return quart.Response(
        pdf,
        content_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="icla.pdf"', "Cache-Control": "no-store"},
    )


async def _upload(name: str) -> bytes | None:
    files = await quart.request.files
    upload = files.get(name)
    return upload.read() if upload is not None else None


class _RefusedError(Exception):
    """What is wrong with a submission, by the form's field."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(errors)
        self.errors = errors


async def _read() -> tuple[IclaFields, Signed]:
    """The submission's fields and how they were signed. Raises `_RefusedError` with what is wrong."""
    form = await quart.request.form
    try:
        raw = json.loads(form.get("fields", "{}"))
        signing = Signing(form.get("signing", ""))
    except (ValueError, TypeError, RecursionError):
        raise _RefusedError({"signing": "Choose how you sign"}) from None
    fields = _fields(raw)
    if isinstance(fields, dict):
        raise _RefusedError(fields)
    signed = Signed(
        signing=signing,
        typed_name=form.get("typedSignature", ""),
        agreed=form.get("agreed") == "true",
        pdf=await _upload("pdf"),
        signature=await _upload("signature"),
        remote_address=_address(),
    )
    if refused := submission.refusals(fields, signed):
        raise _RefusedError(refused)
    return fields, signed


@CLIENT.route("/icla/submit", methods=["POST"])
async def icla_submit() -> Any:
    """The ICLA, signed one of three ways (`Signing`): kept, and mailed to the Secretary. 400 says what is
    wrong by field; 403 a post from another site; 413 a body too large; 429 too many from one address
    within the hour, where a refused submission does not count."""
    if _cross_site():
        return {"errors": {"form": "The ICLA is sent from this site's own form"}}, 403
    if (quart.request.content_length or 0) > _MAX_BODY:
        return {"errors": {"form": "The files are larger than the form takes: 10 MB for the ICLA"}}, 413
    key = f"submission {_address()}"
    if not _take(key, config.get().icla.submissions_per_hour):
        return {"errors": {"form": "Too many submissions from here this hour: try again later"}}, 429
    try:
        fields, signed = await _read()
    except _RefusedError as refused:
        _give_back(key)
        return {"errors": refused.errors}, 400
    row = await submission.receive(fields, signed)
    return {"received": True, "mailed": row.mailed}, 201


def forget() -> None:
    """For tests: no request seen from anywhere."""
    _RECENT.clear()
