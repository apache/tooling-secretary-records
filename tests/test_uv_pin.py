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

"""The uv version is written in three places that each have to agree: the minimum uv accepts
(`required-version` in pyproject.toml), the version CI and the dev image install (`.tool-versions`),
and the version of the pre-commit hooks that run uv."""

import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _pinned() -> str:
    match = re.search(r"^uv +(\S+)", (ROOT / ".tool-versions").read_text(), re.MULTILINE)
    assert match, ".tool-versions names no uv"
    return match.group(1)


def test_the_pin_is_not_below_the_minimum() -> None:
    required = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["required-version"]
    match = re.fullmatch(r">=(\d+(?:\.\d+)*)", required)
    assert match, f"required-version is {required!r}, which this test reads as a minimum only"
    assert _version(_pinned()) >= _version(match.group(1))


def test_the_pre_commit_hooks_run_the_pinned_uv() -> None:
    config = (ROOT / ".pre-commit-config.yaml").read_text()
    match = re.search(r"repo: https://github.com/astral-sh/uv-pre-commit\n\s+rev: (\S+)", config)
    assert match, "no uv-pre-commit hook"
    assert match.group(1) == _pinned()
