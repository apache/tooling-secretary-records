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


"""Which regressions the ratchet permits, and where a change says so.

Split from quality.py for the same reason quality_meters.py was: what a meter *is* and what
a verdict *is* are two things, and this is a third - what a change is allowed to make worse,
and the argument it gives. quality.py compares; this decides what a comparison forgives.

One place says it: a `Quality-Allow: <meter> <key>` trailer on a commit the branch adds. The
reason is the commit's subject, because the argument for a regression is the argument for the
change. It needs no ceiling and no cleanup - it is read from the commits between the base and
here, so it is gone the moment they are behind the base, and there is no file for two branches
to conflict on.

There was a committed file too, for "a regression that has to outlive one branch and be bounded
while it does". Neither half of that survived contact. A regression cannot outlive its branch:
the base is the commit the branch would merge into, so once the change merges the base carries
the reading it made, and every later branch is measured against it. And the bound was the
harmful part - an absolute ceiling goes stale the moment the base moves, which is how one entry
came to permit seventy-two lines nobody had argued for. What was left was a file for two
branches to conflict on, so it went.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess

import quality_meters
import trailers


@dataclasses.dataclass(frozen=True)
class Allowance:
    """One regression the tree is allowed to carry, and why.

    There is no ceiling, and none is needed. A ceiling bounds what some *later* change may do
    to the key, which was a problem only while an allowance outlived the change that wrote it.
    A trailer does not: it is read from the commits this branch adds, so it is gone the moment
    they are behind the base. What it permits is whatever this change reads, which is the thing
    being reviewed anyway.
    """

    meter: str
    key: str
    reason: str

    def permits(self, now: float | None) -> bool:
        """An unmeasured reading is forgiven by nothing - there is no reading to argue about,
        and saying otherwise would let a meter that failed to run pass as allowed."""
        return now is not None


# What remains of a `Quality-Allow: <meter> <key>` trailer once git has read it: the meter it
# applies to, and the key inside it. `trailers.values` says why git reads it rather than a regex.
_TRAILER_VALUE = re.compile(r"^(\S+)[^\S\n]+(\S+)[^\S\n]*$")


def allowances_in_commits(since: str | None) -> list[Allowance]:
    """Every `Quality-Allow:` trailer on a commit this branch adds, as an allowance.

    `since` is the commit the branch is measured against, so the range is exactly what this
    change is. Nothing outside it is read: an allowance argued for on `main` last month is
    spent by definition, because the base already carries the reading it permitted.

    The reason is the commit's subject. That is the point of putting it here rather than in
    a file - the argument for a regression is the argument for the change, and it is already
    written in the message beside it.
    """

    found = []
    for subject, value in trailers.values("Quality-Allow", since, quality_meters.ROOT):
        matched = _TRAILER_VALUE.match(value)
        if matched is None:
            continue
        meter, key = matched.groups()
        found.append(Allowance(meter=meter, key=key, reason=f"{subject} (in the commit message)"))
    return found


def git_maybe(*arguments: str) -> str | None:
    """The same question, where not being answered is one of the answers.

    A shallow clone cannot walk a range, and a sha the fetch never brought down is not
    resolvable. Both are facts about the checkout rather than about the code being measured,
    so they read as "no trailers found" rather than stopping the check.
    """

    finished = subprocess.run(["git", *arguments], cwd=quality_meters.ROOT, capture_output=True, text=True)
    return finished.stdout.strip() if finished.returncode == 0 else None
