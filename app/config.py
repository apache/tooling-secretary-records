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

"""The configuration: defaults here, overrides in config.yaml (written by Puppet in production)."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, cast

import pydantic

if TYPE_CHECKING:
    from asfquart.base import QuartApp


class BaseConfig(pydantic.BaseModel):
    state_dir: str = "./state"
    """The state directory to store persistent data, including the sqlite database."""

    public_url: str = ""
    """The externally reachable base URL of this instance, used to build absolute links in
    outgoing email. Empty yields site-relative links."""

    dev_mode: bool = False
    """Whether the instance runs in dev mode: a banner on every page says it is not connected to
    production data."""

    csp_report_only: bool = False
    """Send the Content Security Policy as `Content-Security-Policy-Report-Only`, so the browser
    blocks nothing and logs what it would have blocked to its own console (see
    app/security_headers.py). A way back for an operator whose instance the policy breaks, not a
    setting to leave on."""

    @property
    def state_dir_path(self) -> pathlib.Path:
        return pathlib.Path(self.state_dir)


class DBConfig(pydantic.BaseModel):
    sqlite_db_file: str = "secretary-records.db"
    """The filename of the sqlite database file, in the state directory."""


class ServerConfig(pydantic.BaseModel):
    # The default a containerised app needs: bound to the loopback of its own namespace it
    # would be reachable from nothing, including the port publishing it. What it is exposed
    # to is decided by what publishes the port, not here. Operators override it in config.yaml.
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8080
    certfile: str | None = None
    keyfile: str | None = None
    access_log: str = "-"
    error_log: str = "-"

    @property
    def bind(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def bind_url(self) -> str:
        protocol = "http" if self.keyfile is None else "https"
        return f"{protocol}://{self.host}:{self.port}"


class LoggingConfig(pydantic.BaseModel):
    debug: bool | list[str] = False
    file: str | None = "secretary-records.log"

    @property
    def file_path(self) -> pathlib.Path | None:
        return pathlib.Path(self.file) if self.file is not None else None


class SMTPConfig(pydantic.BaseModel):
    """Where outgoing mail goes.

    No default host: an instance that says nothing about mail sends none, rather than reaching
    for the Foundation's relay because nobody said not to. See `AppConfig.smtp`.
    """

    host: str
    port: int = 587


class RolesConfig(pydantic.BaseModel):
    """Who holds which role (REQUIREMENTS.md, section 2).

    A stand-in. R-3 asks for the Secretary and Assistant Secretary to come from the offices in
    committee-info.txt, as BAT derives `Role.SECRETARY`; until that reader exists, all three
    lists are configured here. The secretarial team stays a configured list either way.
    """

    secretary: list[str] = []
    """uids holding the office of Secretary."""

    assistant_secretary: list[str] = []
    """uids holding the office of Assistant Secretary."""

    secretarial_team: list[str] = []
    """uids of the members appointed to help the Secretary."""


class IclaConfig(pydantic.BaseModel):
    """The ICLA submission form (REQUIREMENTS.md, F-101)."""

    deliver_to: str = "secretary@apache.org"
    """Where a submission is mailed, as an emailed ICLA arrives today, beside the record kept of it."""

    projects_url: str | None = None
    """The Roster's `/api/roster/projects`, the projects a submitter may name (F-102); None offers a text
    field alone."""

    submissions_per_hour: int = 5
    """From one address: the form is open to anybody, and each submission is mail to the Secretary."""


class AppConfig(pydantic.BaseModel):
    base: BaseConfig = BaseConfig()
    db: DBConfig = DBConfig()
    server: ServerConfig = ServerConfig()
    smtp: SMTPConfig | None = None
    """where outgoing mail goes, or None to send none

    Absent rather than defaulted: in BAT the default was once the Foundation's own relay, so
    every instance that had not been told otherwise - a fresh checkout, the end-to-end harness,
    a preview - addressed real mail to real people. Here the mail goes to people who sent the
    Foundation their personal data, which is a worse place to make that mistake."""
    icla: IclaConfig = IclaConfig()
    roles: RolesConfig = RolesConfig()
    logging: LoggingConfig = LoggingConfig()

    def is_smtp_configured(self) -> bool:
        return self.smtp is not None


def load_app_config(cfg_path: pathlib.Path) -> AppConfig:
    import yaml

    if not cfg_path.exists():
        raise RuntimeError(f"Config file {cfg_path} does not exist")

    return AppConfig.model_validate(yaml.safe_load(cfg_path.read_text()) or {})


def setup_app_config(quart_app: QuartApp, app_config: AppConfig) -> None:
    quart_app.extensions["app_config"] = app_config


def get() -> AppConfig:
    import asfquart

    return cast("AppConfig", asfquart.APP.extensions["app_config"])  # pyright: ignore [reportAttributeAccessIssue]


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", action="store_true")
    parser.add_argument("--access-log", action="store_true")
    parser.add_argument("--error-log", action="store_true")
    parser.add_argument("--pid-file", action="store_true")
    args = parser.parse_args(sys.argv[1:])

    config = load_app_config(pathlib.Path("config.yaml"))
    if args.bind:
        print(config.server.bind)
    if args.access_log:
        print(config.server.access_log)
    if args.error_log:
        print(config.server.error_log)
    if args.pid_file:
        print(config.base.state_dir_path / "secretary-records.pid")
