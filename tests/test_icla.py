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

"""The ICLA submission form (REQUIREMENTS.md, F-101): what it takes, the agreement it fills, and what
becomes of a submission - kept, its files written, and mailed to the Secretary."""

from __future__ import annotations

import datetime
import io
import json
from typing import TYPE_CHECKING, Any

import pydantic
import pypdf
import pytest
import sqlmodel

from app import config, db, mail
from app.client import icla as icla_routes
from app.icla import document, projects
from app.icla.fields import IclaFields, errors_of

if TYPE_CHECKING:
    from email.message import EmailMessage

FIELDS: dict[str, Any] = {
    "full_name": "Ada Lovelace",
    "family_first": False,
    "public_name": "Ada",
    "postal_address": "12 St James's Square\nLondon SW1Y 4JH",
    "country": "United Kingdom",
    "email": "ada@example.org",
    "apache_ids": ["ada", "lovelace"],
    "notify_project": "arbustum",
    "invitation": "",
}
PDF = b"%PDF-1.4\nsigned\n%%EOF\n"
SIGNATURE = b"-----BEGIN PGP SIGNATURE-----\n\niQ==\n-----END PGP SIGNATURE-----\n"


@pytest.fixture
def mailed(monkeypatch) -> list[EmailMessage]:
    """What would have been mailed; `send` answers as a relay that took it."""
    sent: list[EmailMessage] = []

    async def send(email: EmailMessage) -> bool:
        sent.append(email)
        return True

    monkeypatch.setattr(mail, "send", send)
    icla_routes.forget()
    return sent


def _form(signing: str, fields: dict[str, Any] | None = None, **extra: str) -> dict[str, str]:
    return {"fields": json.dumps(fields or FIELDS), "signing": signing, **extra}


async def _submit(client: Any, form: dict[str, str], files: dict[str, tuple[bytes, str]] | None = None) -> Any:
    from quart.datastructures import FileStorage

    uploads = {
        name: FileStorage(io.BytesIO(content), filename=filename) for name, (content, filename) in (files or {}).items()
    }
    return await client.post("/icla/submit", form=form, files=uploads)


async def _kept() -> list[db.IclaSubmission]:
    async with db.session() as session:
        return list((await session.execute(sqlmodel.select(db.IclaSubmission))).scalars().all())


# --- the fields ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "field", "says"),
    [
        ({"full_name": "  "}, "full_name", "Full name is required"),
        ({"email": "ada@example.org, bob@example.org"}, "email", "Email is one address"),
        ({"apache_ids": ["Ada"]}, "apache_ids", "an Apache id is 3 to 20 lowercase letters"),
        ({"apache_ids": ["a1"]}, "apache_ids", "an Apache id is 3 to 20 lowercase letters"),
        ({"apache_ids": ["one", "two", "three", "four"]}, "apache_ids", "at most 3"),
        ({"country": "UK\u0007"}, "country", "cannot be printed"),
        ({"postal_address": "\n".join(["line"] * 7)}, "postal_address", "at most 6 lines"),
    ],
)
def test_each_field_is_held_to_its_rule_and_says_which(change: dict[str, Any], field: str, says: str) -> None:
    with pytest.raises(pydantic.ValidationError) as raised:
        IclaFields.model_validate({**FIELDS, **change})

    errors = errors_of(raised.value)
    assert list(errors) == [field] and says in errors[field]


def test_a_field_the_form_does_not_have_is_refused() -> None:
    with pytest.raises(pydantic.ValidationError):
        IclaFields.model_validate({**FIELDS, "admin": True})


# --- the agreement --------------------------------------------------------------------------------------


def test_the_icla_is_the_foundations_own_form_filled_in() -> None:
    fields = IclaFields.model_validate({**FIELDS, "family_first": True})

    filled = pypdf.PdfReader(io.BytesIO(document.filled(fields, datetime.date(2026, 11, 9))))

    values = {name: field.get("/V") for name, field in (filled.get_fields() or {}).items()}
    assert values["Full Name"] == "Ada Lovelace"
    assert values["Postal Address"] == "12 St James's Square"
    assert values["Postal Address 2"] == "London SW1Y 4JH"
    assert values["Preferred Apache id(s)"] == "ada, lovelace"
    assert values["Date"] == "2026-11-09"
    assert values["Family First"] == "/Yes"
    assert "Individual Contributor License Agreement" in " ".join(filled.pages[0].extract_text().split())


async def test_the_filled_icla_is_downloaded_without_signing_in_and_kept_nowhere(running_app) -> None:
    client = running_app.test_client()

    response = await client.post("/icla/document", json=FIELDS)

    assert (response.status_code, response.content_type) == (200, "application/pdf")
    assert (await response.get_data()).startswith(b"%PDF-")
    assert await _kept() == []


async def test_a_download_with_fields_wrong_says_which(running_app) -> None:
    response = await running_app.test_client().post("/icla/document", json={**FIELDS, "email": "nobody"})

    assert response.status_code == 400
    assert list((await response.get_json())["errors"]) == ["email"]


# --- a submission ---------------------------------------------------------------------------------------


async def test_a_typed_signature_is_kept_with_when_and_where_and_mailed_with_the_filled_icla(
    running_app, mailed
) -> None:
    client = running_app.test_client()

    response = await _submit(client, _form("typed", typed_signature="ada  lovelace", agreed="true"))

    assert (response.status_code, await response.get_json()) == (201, {"received": True, "mailed": True})
    [row] = await _kept()
    assert (row.signing, row.typed_signature, row.mailed) == ("typed", "ada  lovelace", True)
    assert row.remote_address is not None
    folder = config.get().base.state_dir_path / "icla" / str(row.id)
    assert (folder / "icla.pdf").read_bytes().startswith(b"%PDF-")
    [email] = mailed
    assert (email["To"], email["Reply-To"], email["Subject"]) == (
        "secretary@apache.org",
        "ada@example.org",
        "ICLA: Ada Lovelace",
    )
    assert [part.get_filename() for part in email.iter_attachments()] == ["icla.pdf"]
    assert "Signed electronically: 'ada  lovelace' typed" in email.get_body().get_content()


@pytest.mark.parametrize(
    ("form", "field"),
    [
        (_form("typed", typed_signature="Ada Byron", agreed="true"), "typedSignature"),
        (_form("typed", typed_signature="Ada Lovelace"), "agreed"),
        (_form("written-in-blood"), "signing"),
    ],
)
async def test_a_typed_signature_is_the_full_name_and_an_agreement_or_nothing_is_kept(
    running_app, mailed, form: dict[str, str], field: str
) -> None:
    response = await _submit(running_app.test_client(), form)

    assert response.status_code == 400
    assert field in (await response.get_json())["errors"]
    assert (await _kept(), mailed) == ([], [])


async def test_a_signed_pdf_is_kept_and_mailed_as_uploaded(running_app, mailed) -> None:
    response = await _submit(running_app.test_client(), _form("signed-pdf"), {"pdf": (PDF, "mine.pdf")})

    assert response.status_code == 201
    [row] = await _kept()
    assert (config.get().base.state_dir_path / "icla" / str(row.id) / "icla-signed.pdf").read_bytes() == PDF
    assert [part.get_filename() for part in mailed[0].iter_attachments()] == ["icla-signed.pdf"]


async def test_a_pgp_signature_comes_with_the_pdf_it_signs(running_app, mailed) -> None:
    client = running_app.test_client()

    without = await _submit(client, _form("pgp"), {"pdf": (PDF, "icla.pdf")})
    not_a_pdf = await _submit(
        client, _form("pgp"), {"pdf": (b"hello", "icla.pdf"), "signature": (SIGNATURE, "icla.pdf.asc")}
    )
    both = await _submit(client, _form("pgp"), {"pdf": (PDF, "icla.pdf"), "signature": (SIGNATURE, "icla.pdf.asc")})

    assert (without.status_code, not_a_pdf.status_code, both.status_code) == (400, 400, 201)
    assert "signature" in (await without.get_json())["errors"]
    assert "pdf" in (await not_a_pdf.get_json())["errors"]
    assert [part.get_filename() for part in mailed[0].iter_attachments()] == ["icla-signed.pdf", "icla-signed.pdf.asc"]


async def test_a_submission_is_kept_where_no_mail_can_be_sent(running_app) -> None:
    """The form tells the submitter it arrived; the Secretary finds it kept, and the log says why no mail."""
    icla_routes.forget()

    response = await _submit(running_app.test_client(), _form("typed", typed_signature="Ada Lovelace", agreed="true"))

    assert (response.status_code, await response.get_json()) == (201, {"received": True, "mailed": False})
    [row] = await _kept()
    assert row.mailed is False


async def test_one_address_sends_so_many_an_hour_and_no_more(running_app, mailed, monkeypatch) -> None:
    monkeypatch.setattr(config.get().icla, "submissions_per_hour", 2)
    client = running_app.test_client()
    form = _form("typed", typed_signature="Ada Lovelace", agreed="true")

    codes = [(await _submit(client, form)).status_code for _ in range(3)]

    assert codes == [201, 201, 429]
    assert len(mailed) == 2


async def test_a_refused_submission_does_not_count_against_the_hour(running_app, mailed, monkeypatch) -> None:
    monkeypatch.setattr(config.get().icla, "submissions_per_hour", 1)
    client = running_app.test_client()

    refused = await _submit(client, _form("typed", typed_signature="Somebody Else", agreed="true"))
    taken = await _submit(client, _form("typed", typed_signature="Ada Lovelace", agreed="true"))

    assert (refused.status_code, taken.status_code) == (400, 201)


# --- the page and the projects ----------------------------------------------------------------------------


async def test_the_form_is_open_to_anybody_and_carries_the_invitation_followed(running_app) -> None:
    page = await (await running_app.test_client().get("/icla?invitation=inv-42")).get_data(as_text=True)

    assert 'id="icla-form"' in page
    assert 'data-invitation="inv-42"' in page


async def test_with_no_projects_url_the_form_offers_none(running_app) -> None:
    projects.forget()

    assert await (await running_app.test_client().get("/icla/projects")).get_json() == []


async def test_the_projects_are_bats_and_a_failed_read_keeps_the_last(running_app, monkeypatch) -> None:
    projects.forget()
    monkeypatch.setattr(config.get().icla, "projects_url", "https://agenda.apache.org/projects.json")
    answers: list[Any] = [[{"id": "arbustum", "name": "Arbustum", "kind": "pmc"}], OSError("BAT is down")]

    def read(_url: str) -> list[projects.Project]:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return [projects.Project(**each) for each in answer]

    monkeypatch.setattr(projects, "_read", read)

    first = await projects.offered()
    projects._CACHE.read_at = None
    second = await projects.offered()

    assert first == second == [projects.Project("arbustum", "Arbustum", "pmc")]


async def test_a_submission_is_not_in_the_admin_data_browser(authenticated_client) -> None:
    admin = await authenticated_client("tooler", roles="admin")

    assert (await admin.get("/admin/data/iclasubmission")).status_code == 404
