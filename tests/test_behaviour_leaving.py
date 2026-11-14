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

"""What the behaviour record refuses on its way out (`tests/behaviour/leaving.py`).

The guard works by where a connection leaves rather than by which function makes it, so these make the
connection the ways the app might, including ones the record does not happen to exercise today.
"""

import asyncio
import socket

import pytest

from tests.behaviour import leaving

RELAY = "192.0.2.1"
"""An address (TEST-NET-1) rather than a name, so that a connection reaches `connect` rather than DNS."""


class TestGuard:
    def test_a_connection_off_the_machine_is_refused_and_noted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        leaving.REFUSED.attempts.clear()
        leaving.guard(monkeypatch)
        with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
            sock.connect((RELAY, 25))
        assert leaving.REFUSED.attempts == [f"a connection to ('{RELAY}', 25)"]

    def test_connect_ex_is_refused_as_well(self, monkeypatch: pytest.MonkeyPatch) -> None:
        leaving.REFUSED.attempts.clear()
        leaving.guard(monkeypatch)
        with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
            sock.connect_ex((RELAY, 389))
        assert leaving.REFUSED.attempts == [f"a connection to ('{RELAY}', 389)"]

    def test_a_mail_sent_reaches_the_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from email.message import EmailMessage

        import aiosmtplib

        message = EmailMessage()
        message["From"] = "secretary@example.org"
        message["To"] = "someone@example.org"
        message["Subject"] = "A subject"
        message.set_content("A body.")
        leaving.REFUSED.attempts.clear()
        leaving.guard(monkeypatch)
        with pytest.raises(Exception):  # noqa: B017 - whatever aiosmtplib makes of a refused connect
            asyncio.run(aiosmtplib.send(message, hostname=RELAY, port=25, timeout=5))
        assert leaving.REFUSED.attempts == [f"a connection to ('{RELAY}', 25)"]

    def test_a_local_socket_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        leaving.REFUSED.attempts.clear()
        leaving.guard(monkeypatch)
        left, right = socket.socketpair()
        left.close()
        right.close()
        assert leaving.REFUSED.attempts == []


class TestOutside:
    def test_a_request_of_an_outside_service_is_answered_as_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import aiohttp

        async def fetch(url: str) -> None:
            async with aiohttp.ClientSession() as session, session.get(url):
                pass

        leaving.REFUSED.attempts.clear()
        leaving.OUTSIDE.clear()
        leaving.guard(monkeypatch)
        with pytest.raises(aiohttp.ClientConnectionError):
            asyncio.run(fetch("https://issues.example.org/search"))
        assert leaving.OUTSIDE == ["GET https://issues.example.org/search"]
        assert leaving.REFUSED.attempts == []
