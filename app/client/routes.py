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

"""The pages, and the dev-only route the end-to-end suite signs in with."""

from __future__ import annotations

import dataclasses
import functools
from typing import TYPE_CHECKING, Any, Final

import markdown
import quart
from asfquart import session as asf_session

from app import access
from app.access import require_role
from app.models.role import SECRETARIAL_ROLES, Role

from . import CLIENT

if TYPE_CHECKING:
    import werkzeug.wrappers

_MARKDOWN_DIR: Final = "app/md"


def _e2e_only(func):
    """A route for the Playwright suite alone: 404 unless SR_E2E says otherwise."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        if not access.e2e_enabled():
            quart.abort(404)
        return await func(*args, **kwargs)

    return wrapper


@CLIENT.route("/e2e/login/<uid>")
@_e2e_only
async def e2e_login(uid: str) -> werkzeug.wrappers.Response:
    """Mint an asfquart session for `uid` without going through ASF OAuth.

    `?roles=secretary,admin` grants those roles to this session alone. They are kept in the
    session's own metadata rather than on anything shared, so one login cannot leak a role into
    the next one - which BAT's test login, granting onto a shared roster, did. A name that is not
    a role is ignored, as `access.roles_of` ignores it.
    """
    roles = [name.strip() for name in quart.request.args.get("roles", "").split(",") if name.strip()]
    asf_session.write(
        {
            "uid": uid,
            "fullname": uid,
            "email": f"{uid}@example.org",
            "isMember": True,
            "isChair": False,
            "isRoot": False,
            "pmcs": [],
            "projects": [],
            "metadata": {access.E2E_ROLES_KEY: roles},
        }
    )
    return quart.redirect(quart.url_for("client.home"))


@CLIENT.route("/")
async def home() -> str:
    return await quart.render_template("home.html", svelte_page="home", secretarial_roles=SECRETARIAL_ROLES)


@dataclasses.dataclass(frozen=True)
class DocSection:
    """One fragment of the documentation page.

    `roles` names the roles whose work the fragment describes, or is empty for the parts that
    are everybody's. The page renders every section - a reader has to be able to look up a role
    they do not hold yet - and `mine` only marks the ones they do."""

    key: str
    title: str
    roles: tuple[Role, ...] = ()
    mine: bool = False

    @property
    def anchor(self) -> str:
        return f"doc-{self.key}"


_DOC_SECTIONS: Final[tuple[DocSection, ...]] = (
    DocSection("overview", "What this tool is for"),
    DocSection("secretary", "Secretary and Assistant Secretary", (Role.SECRETARY, Role.ASSISTANT_SECRETARY)),
    DocSection("secretarial-team", "Secretarial team", (Role.SECRETARIAL_TEAM,)),
    DocSection("admin", "Admin", (Role.ADMIN,)),
)


def _render_markdown(key: str) -> str:
    """One fragment under app/md/documentation, its `##` shifted to an h3 under the page's h2."""
    with open(f"{_MARKDOWN_DIR}/documentation/{key}.md", encoding="utf-8") as fragment:
        return markdown.markdown(
            fragment.read(),
            extensions=["toc", "tables", "fenced_code"],
            extension_configs={"toc": {"baselevel": 2}},
        )


@CLIENT.route("/documentation")
@require_role(*SECRETARIAL_ROLES, Role.ADMIN, to="read the documentation")
async def documentation() -> str:
    user = await access.user_or_abort()
    sections = [
        dataclasses.replace(section, mine=user.has_any_role(section.roles)) if section.roles else section
        for section in _DOC_SECTIONS
    ]
    rendered = {section.key: _render_markdown(section.key) for section in sections}
    return await quart.render_template("documentation.html", sections=sections, rendered=rendered)


@CLIENT.route("/<path:_path>")
async def catch_all(_path: str) -> Any:
    """A catch-all so this blueprint answers an unknown path with its own 404 page."""
    quart.abort(404)
