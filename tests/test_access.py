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

import asfquart.session
import pytest

from app import access
from app.models.role import Role


def _session(uid: str, *, committees: list[str] | None = None, is_root: bool = False, e2e_roles=None):
    raw = {"uid": uid, "pmcs": committees or [], "isRoot": is_root}
    if e2e_roles is not None:
        raw["metadata"] = {access.E2E_ROLES_KEY: e2e_roles}
    return asfquart.session.ClientSession(raw)


@pytest.mark.parametrize(
    ("uid", "role"),
    [("ada", Role.SECRETARY), ("bea", Role.ASSISTANT_SECRETARY), ("cy", Role.SECRETARIAL_TEAM)],
)
def test_a_configured_uid_holds_its_role_and_no_other(app, uid, role):
    assert access.roles_of(_session(uid)) == {role}


def test_a_uid_on_no_list_holds_nothing(app):
    assert access.roles_of(_session("zed")) == frozenset()


def test_tooling_and_root_are_admins_and_nothing_more(app):
    assert access.roles_of(_session("zed", committees=["tooling"])) == {Role.ADMIN}
    assert access.roles_of(_session("zed", is_root=True)) == {Role.ADMIN}
    assert access.roles_of(_session("zed", committees=["httpd"])) == frozenset()


def test_the_test_login_grants_nothing_unless_e2e_is_on(app, monkeypatch):
    monkeypatch.delenv("SR_E2E", raising=False)
    assert access.roles_of(_session("zed", e2e_roles=["secretary"])) == frozenset()

    monkeypatch.setenv("SR_E2E", "1")
    assert access.roles_of(_session("zed", e2e_roles=["secretary"])) == {Role.SECRETARY}


def test_a_name_that_is_not_a_role_is_ignored(app, monkeypatch):
    monkeypatch.setenv("SR_E2E", "1")
    assert access.roles_of(_session("zed", e2e_roles=["director", "admin"])) == {Role.ADMIN}


def test_the_refusal_names_every_role_that_would_have_done():
    assert (
        access.role_refusal([Role.SECRETARY], "file a document")
        == "Only users with role 'secretary' can file a document"
    )
    assert (
        access.role_refusal([Role.SECRETARY, Role.ASSISTANT_SECRETARY, Role.ADMIN], "look")
        == "Only users with role 'secretary', 'assistant_secretary' or 'admin' can look"
    )


def test_require_role_wants_at_least_one_role():
    with pytest.raises(ValueError, match="at least one role"):
        access.require_role(to="do anything")
