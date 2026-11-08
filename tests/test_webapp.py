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

import sqlalchemy

from app import db


async def test_the_database_is_migrated_to_head_at_startup(running_app):
    async with db.session() as session:
        revision = (await session.execute(sqlalchemy.text("SELECT version_num FROM alembic_version"))).scalar_one()

    assert revision == "0001"


async def test_home_offers_a_sign_in_to_somebody_signed_out(running_app):
    response = await running_app.test_client().get("/")

    assert response.status_code == 200
    page = await response.get_data(as_text=True)
    assert "/auth?login=/" in page
    assert 'id="role-summary"' not in page


async def test_the_test_login_does_not_exist_outside_the_e2e_harness(running_app, monkeypatch):
    monkeypatch.delenv("SR_E2E")

    response = await running_app.test_client().get("/e2e/login/ada")

    assert response.status_code == 404


async def test_home_shows_the_queue_to_a_secretary(authenticated_client):
    client = await authenticated_client("ada")

    page = await (await client.get("/")).get_data(as_text=True)

    assert "data-roles='[\"secretary\"]'" in page
    assert 'data-testid="queue-placeholder"' in page


async def test_home_tells_somebody_without_a_role_why_there_is_no_queue(authenticated_client):
    client = await authenticated_client("zed")

    page = await (await client.get("/")).get_data(as_text=True)

    assert 'data-testid="no-access"' in page
    assert 'data-testid="queue-placeholder"' not in page


async def test_a_login_grants_its_roles_to_that_session_alone(authenticated_client):
    granted = await authenticated_client("zed", "secretarial_team")
    plain = await authenticated_client("zed")

    assert (await (await granted.get("/api/me")).get_json())["roles"] == ["secretarial_team"]
    assert (await (await plain.get("/api/me")).get_json())["roles"] == []


async def test_the_documentation_is_refused_to_somebody_without_a_role(authenticated_client):
    client = await authenticated_client("zed")

    response = await client.get("/documentation")

    assert response.status_code == 403
    assert "can read the documentation" in await response.get_data(as_text=True)


async def test_the_documentation_marks_the_sections_the_reader_holds(authenticated_client):
    client = await authenticated_client("cy")

    response = await client.get("/documentation")

    assert response.status_code == 200
    page = await response.get_data(as_text=True)
    assert 'data-doc-section="secretarial-team"' in page
    assert page.count('data-doc-mine="true"') == 1
    assert 'data-doc-section="secretary"' in page


async def test_the_admin_pages_are_refused_to_a_secretary(authenticated_client):
    client = await authenticated_client("ada")

    assert (await client.get("/admin/server-status")).status_code == 403
    assert (await client.get("/admin/data")).status_code == 403


async def test_an_admin_sees_the_server_status_and_the_data_browser(authenticated_client):
    client = await authenticated_client("zed", "admin")

    status = await client.get("/admin/server-status")
    assert status.status_code == 200
    assert "0001" in await status.get_data(as_text=True)

    browser = await client.get("/admin/data")
    assert browser.status_code == 200
    assert 'data-testid="no-tables"' in await browser.get_data(as_text=True)

    assert (await client.get("/admin/data/nothing_by_this_name")).status_code == 404


async def test_the_api_answers_401_rather_than_sending_a_fetch_to_oauth(running_app):
    response = await running_app.test_client().get("/api/me")

    assert response.status_code == 401


async def test_every_response_carries_the_security_headers(running_app):
    response = await running_app.test_client().get("/")

    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


async def test_an_unknown_page_is_a_404_page(running_app):
    response = await running_app.test_client().get("/no/such/page")

    assert response.status_code == 404
    assert 'data-testid="error-message"' in await response.get_data(as_text=True)
