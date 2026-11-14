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

"""Where a record is written: `SR_BEHAVIOUR_OUT`, which `scripts/ci/behaviour.py` names.

Nothing here compares. The record of this checkout and the record of the commit it would merge into
are compared by that script, each having been written by its own commit's harness.
"""

from __future__ import annotations

import os
import pathlib

from tests.behaviour import normalise

OUT_ENV = "SR_BEHAVIOUR_OUT"


def out() -> pathlib.Path:
    return pathlib.Path(os.environ[OUT_ENV])


def write(part: str, produced: dict[str, bytes]) -> None:
    """Write one part of the record, and how often each normalisation rule rewrote it.

    The counts are part of the record, so that a rule that starts matching more - a widened mask - is a
    difference like any other. They are written per part, since parts run in processes of their own."""
    root = out()
    for name, content in produced.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    counts = normalise.take_counts()
    lines = [f"{name}: {counts.get(name, 0)}" for name in normalise.NAMES]
    path = root / "normalisation" / f"{part}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
