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

"""The projects a submitter may name (F-102): every PMC and podling, as the Roster lists them at
`/api/roster/projects`. Read at most once an hour; where the Roster cannot be read, the last list read stands,
and with none the form offers a text field alone."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import urllib.request
from typing import Final

from app import config

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

_FRESH_FOR: Final[float] = 3600.0
_TIMEOUT: Final[float] = 10.0


@dataclasses.dataclass(frozen=True)
class Project:
    id: str
    name: str
    kind: str
    """`pmc` or `podling`."""


@dataclasses.dataclass
class _Cache:
    projects: list[Project] = dataclasses.field(default_factory=list)
    read_at: float | None = None


_CACHE: Final = _Cache()


def _read(url: str) -> list[Project]:
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"{url} is not a web address")
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310 - the scheme is checked above
        listed = json.loads(response.read().decode("utf-8"))
    return [
        Project(id=str(each["id"]), name=str(each["name"]), kind=str(each["kind"]))
        for each in listed
        if isinstance(each, dict) and {"id", "name", "kind"} <= each.keys()
    ]


async def offered() -> list[Project]:
    """The projects, as last read from the Roster - read again where that was over an hour ago."""
    url = config.get().icla.projects_url
    if url is None:
        return []
    if _CACHE.read_at is not None and time.monotonic() - _CACHE.read_at < _FRESH_FOR:
        return _CACHE.projects
    try:
        _CACHE.projects = await asyncio.to_thread(_read, url)
    except Exception:
        _LOGGER.exception("could not read the projects from %s; keeping the %d read before", url, len(_CACHE.projects))
        # tried again in five minutes, not an hour
        _CACHE.read_at = time.monotonic() - _FRESH_FOR + 300
        return _CACHE.projects
    _CACHE.read_at = time.monotonic()
    return _CACHE.projects


def forget() -> None:
    """For tests: nothing read yet."""
    _CACHE.projects, _CACHE.read_at = [], None
