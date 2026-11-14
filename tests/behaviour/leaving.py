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

"""What would leave the machine while the app is recorded: refused, and noted.

Caught as low as it can be, so that how the app reaches it does not matter. Every HTTP request the app
makes of a service off the machine is answered as unreachable, the same on every run. Any other
connection off the machine - an SMTP relay, an LDAP server, a mail store - is refused. Each refusal is
noted and fails the test that made it (`conftest.py`), since the app may well swallow the error it
raises.

The app sends no mail and writes to no repository yet. The feature that first does adds a catch here
that records what it would send or commit, as part of the record, rather than letting the guard refuse it.
"""

from __future__ import annotations

import dataclasses
import socket
from typing import Any


def _from_app() -> str:
    """Where in the app an attempt came from: the innermost frame of app/ on the stack, if any."""
    import traceback

    frames = [frame for frame in traceback.extract_stack() if "/app/" in frame.filename]
    return f", from {frames[-1].filename.split('/app/', 1)[1]}:{frames[-1].lineno}" if frames else ""


@dataclasses.dataclass
class Refused:
    """What tried to leave the machine; the test that made it fails."""

    attempts: list[str] = dataclasses.field(default_factory=list)


REFUSED = Refused()


OUTSIDE: list[str] = []
"""Every request the app made of a service off the machine, by method and url, in order."""


def _no_outside_services(monkeypatch: Any) -> None:
    """Every HTTP request the app makes answered as a service that cannot be reached: what it would get
    from one that is down, the same on every run. Caught on `aiohttp.ClientSession` itself, so however a
    module came by its session; anything that went round it reaches `connect`, and the guard."""
    import aiohttp

    async def unreachable(session: Any, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        OUTSIDE.append(f"{method} {url}")
        raise aiohttp.ClientConnectionError(f"the behaviour record has no {url}")

    monkeypatch.setattr(aiohttp.ClientSession, "_request", unreachable)


def guard(monkeypatch: Any) -> None:
    """Refuse every connection off the machine."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def refuse(sock: socket.socket, address: Any) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            REFUSED.attempts.append(f"a connection to {address}{_from_app()}")
            raise ConnectionRefusedError("the behaviour record connects to nothing")

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        refuse(sock, address)
        return connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        refuse(sock, address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    _no_outside_services(monkeypatch)
