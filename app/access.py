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

"""Who is signed in, which roles they hold, and the decorators that refuse everybody else.

Roles are worked out per request from the session and the configuration, and never stored on
the session: a uid taken off the configured list loses the role on the next request rather than
when the cookie expires. The one exception is the end-to-end harness, whose test login names the
roles it wants (see `e2e_login` in app/client/routes.py) - honoured only while SR_E2E is set,
which no deployed instance sets.
"""

from __future__ import annotations

import dataclasses
import functools
import os
from typing import TYPE_CHECKING, Any

import asfquart.session
import quart
from asfquart.auth import AuthenticationFailed, Requirements

from app import config
from app.models.role import Role

if TYPE_CHECKING:
    from collections.abc import Sequence

E2E_ROLES_KEY = "e2e_roles"
"""Where the test login keeps the roles it was asked for, in the session's metadata."""


def e2e_enabled() -> bool:
    """Whether the dev-only routes and the roles the test login grants are live."""
    return os.environ.get("SR_E2E") == "1"


@dataclasses.dataclass(frozen=True)
class User:
    uid: str
    fullname: str
    roles: frozenset[Role]

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    def has_any_role(self, roles: Sequence[Role]) -> bool:
        return any(role in self.roles for role in roles)

    @property
    def sorted_roles(self) -> list[str]:
        """The roles in the order the enum declares them, for a page to list."""
        return [str(role) for role in Role if role in self.roles]


def roles_of(client_session: asfquart.session.ClientSession) -> frozenset[Role]:
    uid = str(client_session.uid)
    configured = config.get().roles
    roles: set[Role] = set()

    if uid in configured.secretary:
        roles.add(Role.SECRETARY)
    if uid in configured.assistant_secretary:
        roles.add(Role.ASSISTANT_SECRETARY)
    if uid in configured.secretarial_team:
        roles.add(Role.SECRETARIAL_TEAM)
    # Tooling maintains the instance, as in BAT; infra-root may too.
    if client_session.isRoot or "tooling" in (client_session.committees or []):
        roles.add(Role.ADMIN)

    if e2e_enabled():
        for name in (client_session.metadata or {}).get(E2E_ROLES_KEY, []):
            try:
                roles.add(Role(name))
            except ValueError:
                continue

    return frozenset(roles)


async def current_user() -> User | None:
    client_session = await asfquart.session.read()
    if client_session is None:
        return None
    return User(
        uid=str(client_session.uid),
        fullname=str(client_session.fullname or client_session.uid),
        roles=roles_of(client_session),
    )


async def user_or_abort() -> User:
    """The signed-in user, or a 401 - which asfquart answers by sending the browser to sign in."""
    user = await current_user()
    if user is None:
        raise AuthenticationFailed(Requirements.E_NOT_LOGGED_IN, errorcode=401)
    return user


def role_refusal(roles: Sequence[Role], to: str) -> str:
    """ "Only users with role 'secretary' can file a document", for any number of roles."""
    named = [f"'{role}'" for role in roles]
    listed = named[0] if len(named) == 1 else f"{', '.join(named[:-1])} or {named[-1]}"
    return f"Only users with role {listed} can {to}"


def require_role(*roles: Role, to: str):
    """Refuse anyone holding none of `roles`, before the route body runs.

    `to` completes the refusal - `to="file a document"` - so the message reads as the thing
    being refused rather than as the check that refused it. Every mutating route carries one
    (REQUIREMENTS.md, section 5), and so does every route that reads the queue (R-1).

    It aborts rather than returning a body, so that each blueprint answers in its own idiom: the
    API as text, the pages through the client's 403 page. Innermost of a route's decorators,
    under any `quart_schema` ones, which inspect the function they wrap.
    """
    if not roles:
        raise ValueError("require_role needs at least one role")

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            user = await user_or_abort()
            if not user.has_any_role(roles):
                quart.abort(403, description=role_refusal(roles, to))
            return await func(*args, **kwargs)

        return wrapper

    return decorator
