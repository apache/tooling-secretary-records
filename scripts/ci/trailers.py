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

"""What the commits a branch adds say about themselves in their trailers.

Two checks read one: the quality ratchet (`Quality-Allow:`) and the behaviour record
(`Behaviour-Allow:`, `Behaviour-Harness:`). Each lets a change argue for something the check would
otherwise refuse, and puts the argument in the commit that makes the change - so it needs no file,
and it is gone the moment those commits are behind the base.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib


def values(
    key: str, since: str | None, cwd: pathlib.Path, *, head: str = "HEAD", own: bool = False
) -> list[tuple[str, str]]:
    """`(subject, value)` for every `<key>:` trailer on a commit between `since` and `head`.

    git decides what a trailer is, rather than a regex over the message. The difference is not
    academic: a commit that *mentions* the trailer - documenting it, quoting a message, pasting a
    review comment about it - would otherwise grant what it is only talking about. `key=` matches
    case-insensitively and `valueonly` drops the token back off.

    `own` reads only the branch's own commits: its first-parent line, without merges. A merge
    commit's message, and the commits of a branch merged in, are not this branch arguing for its
    change.

    Empty rather than an error where git cannot walk the range: a shallow clone cannot, and a base
    that is not an ancestor is a rebase away from being one. Neither says anything about the change
    itself - but a branch that argued for something then reads as not having, which is why the jobs
    that read these take the whole history.
    """
    if since is None:
        return []

    walk = ["--no-merges", "--first-parent"] if own else []
    log = subprocess.run(
        ["git", "log", *walk, f"--format=%s%x1f%(trailers:key={key},valueonly,separator=%x0a)%x00", f"{since}..{head}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if log.returncode != 0:
        return []

    found = []
    for entry in log.stdout.strip().split("\0"):
        subject, _, trailers = entry.partition("\x1f")
        found += [(subject.strip(), value.strip()) for value in trailers.splitlines() if value.strip()]
    return found
