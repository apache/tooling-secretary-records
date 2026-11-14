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

"""A submission received: its signature checked as far as the form can check it, kept with its files, and
mailed to the Secretary - kept first, so that a mail that cannot be sent loses nothing."""

from __future__ import annotations

import dataclasses
import datetime
import logging
import os
import re
import secrets
import shutil
from email.message import EmailMessage
from typing import TYPE_CHECKING, Final

from app import config, db, mail
from app.icla import document
from app.icla.fields import Signing, text

if TYPE_CHECKING:
    import pathlib

    from app.icla.fields import IclaFields

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

MAX_PDF: Final[int] = 10 * 1024 * 1024
MAX_SIGNATURE: Final[int] = 64 * 1024
_PGP_SIGNATURE: Final[bytes] = b"-----BEGIN PGP SIGNATURE-----"
_SPACES: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True)
class Signed:
    """How the submitter signed, and what they sent to show it."""

    signing: Signing
    typed_name: str = ""
    agreed: bool = False
    pdf: bytes | None = None
    """The ICLA as they signed it: for `signed-pdf`, signed in it; for `pgp`, the file the signature is of."""
    signature: bytes | None = None
    """For `pgp`: the detached signature, ascii-armoured."""
    remote_address: str | None = None


def _same_name(typed: str, full_name: str) -> bool:
    return _SPACES.sub(" ", typed.strip()).casefold() == _SPACES.sub(" ", full_name.strip()).casefold()


def refusals(fields: IclaFields, signed: Signed) -> dict[str, str]:
    """What is wrong with the signature, by the form's field, or nothing."""
    found: dict[str, str] = {}
    if signed.signing == Signing.TYPED:
        try:
            typed = text(signed.typed_name, "Your full name, to sign", 200)
        except ValueError as ex:
            return {"typedSignature": str(ex)}
        if not _same_name(typed, fields.full_name):
            found["typedSignature"] = "Type your full name exactly as you wrote it above"
        if not signed.agreed:
            found["agreed"] = "Agree to the terms of the ICLA to sign it"
        return found
    if signed.pdf is None or not signed.pdf.startswith(b"%PDF-"):
        found["pdf"] = "Choose the signed ICLA, a PDF file"
    elif len(signed.pdf) > MAX_PDF:
        found["pdf"] = "The ICLA is larger than 10 MB"
    if signed.signing == Signing.PGP:
        if signed.signature is None or _PGP_SIGNATURE not in signed.signature[:256]:
            found["signature"] = "Choose the detached signature, an armoured .asc file"
        elif len(signed.signature) > MAX_SIGNATURE:
            found["signature"] = "The signature is larger than 64 KB"
    return found


def _files(fields: IclaFields, signed: Signed, today: datetime.date) -> dict[str, bytes]:
    if signed.signing == Signing.TYPED:
        return {"icla.pdf": document.filled(fields, today)}
    files = {"icla-signed.pdf": signed.pdf or b""}
    if signed.signing == Signing.PGP:
        files["icla-signed.pdf.asc"] = signed.signature or b""
    return files


def _body(row: db.IclaSubmission, signed: Signed) -> str:
    lines = [
        "An ICLA arrived through the submission form.",
        "",
        f"Full name:      {row.full_name}{' (family name first)' if row.family_first else ''}",
        f"Public name:    {row.public_name or row.full_name}",
        f"Email:          {row.email}",
        f"Country:        {row.country}",
        f"Apache ids:     {row.apache_ids or 'none given'}",
        f"Notify project: {row.notify_project or 'none given'}",
    ]
    if row.invitation:
        lines.append(f"Invitation:     {row.invitation}")
    lines += ["", "Postal address:", *(f"  {line}" for line in row.postal_address.splitlines()), ""]
    if signed.signing == Signing.TYPED:
        lines.append(
            f"Signed electronically: '{row.typed_signature}' typed, and the terms agreed to, at "
            f"{row.received_at:%Y-%m-%d %H:%M:%S} UTC from {row.remote_address or 'an unknown address'}. "
            "The attached ICLA is the form filled in with the fields above."
        )
    elif signed.signing == Signing.PGP:
        lines.append("Signed with PGP: the attached ICLA, and its detached signature. Neither is verified yet.")
    else:
        lines.append("Signed in the attached PDF, as the submitter uploaded it.")
    lines += ["", f"Kept as submission {row.id} in Secretary Records."]
    return "\n".join(lines)


def _message(row: db.IclaSubmission, signed: Signed, files: dict[str, bytes]) -> EmailMessage:
    email = EmailMessage()
    email["From"] = mail.TOOL_SENDER
    email["To"] = config.get().icla.deliver_to
    # a reply goes to the submitter, as a reply to an emailed ICLA does
    email["Reply-To"] = row.email
    email["Subject"] = f"ICLA: {row.full_name}"
    email.set_content(_body(row, signed))
    for name, content in files.items():
        subtype = "pgp-signature" if name.endswith(".asc") else "pdf"
        email.add_attachment(content, maintype="application", subtype=subtype, filename=name)
    return email


def _root() -> pathlib.Path:
    return config.get().base.state_dir_path / "icla"


def _write(files: dict[str, bytes]) -> str:
    """The files, written into a folder of their own, and its name: a random one rather than the row's
    id, which SQLite gives again to a row added after the newest was deleted, or to a rebuilt database
    beside a state directory that kept its folders."""
    folder = secrets.token_hex(16)
    path = _root() / folder
    _root().mkdir(parents=True, exist_ok=True, mode=0o700)
    path.mkdir(mode=0o700)
    try:
        for name, content in files.items():
            descriptor = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as out:
                out.write(content)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return folder


async def receive(fields: IclaFields, signed: Signed, today: datetime.date | None = None) -> db.IclaSubmission:
    """Keep the submission and its files, then mail it to the Secretary. Call it only where `refusals` found
    nothing. The row says whether the mail was sent."""
    files = _files(fields, signed, today or datetime.datetime.now(datetime.UTC).date())
    row = db.IclaSubmission(
        full_name=fields.full_name,
        family_first=fields.family_first,
        public_name=fields.public_name,
        postal_address=fields.postal_address,
        country=fields.country,
        email=fields.email,
        apache_ids=", ".join(fields.apache_ids),
        notify_project=fields.notify_project,
        invitation=fields.invitation,
        signing=str(signed.signing),
        typed_signature=signed.typed_name.strip() if signed.signing == Signing.TYPED else None,
        remote_address=signed.remote_address if signed.signing == Signing.TYPED else None,
        files=",".join(files),
        folder=_write(files),
    )
    try:
        async with db.session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
    except Exception:
        # a folder no row names is nobody's to find: gone with the row that would have named it
        shutil.rmtree(_root() / row.folder, ignore_errors=True)
        raise
    try:
        row.mailed = await mail.send(_message(row, signed, files))
    except Exception:
        _LOGGER.exception("could not mail ICLA submission %s; it is kept", row.id)
        row.mailed = False
    async with db.session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row
