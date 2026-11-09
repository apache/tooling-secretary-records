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

"""Sending mail: to the Secretary, of what arrived for them.

What is logged names a message by its id and never by its subject, which names the person it is about:
the log is the host's, and Admin reads it (REQUIREMENTS R-1).

Nothing is sent where the configuration names no relay (`AppConfig.smtp`): an instance that says nothing
about mail sends none, and says so in its log, rather than claiming it did.
"""

from __future__ import annotations

import logging
from email.utils import formataddr, formatdate, make_msgid
from typing import TYPE_CHECKING, Final

import aiosmtplib

from app import config

if TYPE_CHECKING:
    from email.message import EmailMessage

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

_SMTP_TIMEOUT: Final[int] = 30

TOOL_SENDER: Final[str] = formataddr(("Secretary Records", "donotreply@apache.org"))
"""Who mail nobody wrote comes from: the tool, named, from an address nothing reads."""


async def send(email: EmailMessage) -> bool:
    """Whether the message was handed to the relay, every recipient taking it."""
    email["Date"] = formatdate()
    if "Message-Id" not in email:
        email["Message-Id"] = make_msgid(domain="apache.org")
    smtp = config.get().smtp
    if smtp is None:
        _LOGGER.warning("no SMTP is configured: not sending %s to %s", email["Message-Id"], email["To"])
        return False
    errors, response = await aiosmtplib.send(
        email, hostname=smtp.host, port=smtp.port, timeout=_SMTP_TIMEOUT, start_tls=True
    )
    if errors:
        _LOGGER.warning("%s was refused by %s", email["Message-Id"], ", ".join(errors))
        return False
    _LOGGER.info("sent %s to %s: %s", email["Message-Id"], email["To"], response)
    return True
