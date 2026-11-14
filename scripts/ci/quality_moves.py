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

"""Telling code that moved from code that arrived.

Meters key on where a finding is - a path, or a path and what was found in it - which is
what lets a new key mean new code that has to meet the bar outright. It is also the one
thing that could make that lie: both halves of a move leave the same shape behind as an
arrival, a key with no base reading. Renaming a 600-line file used to go red on every
meter that keys on its path, and the only way through was a `Quality-Allow:` trailer
whose subject argued for a regression that had not happened.

Two answers, deliberately unequal in what they are willing to assume.

`renames_since` and `rekey` prove it: git says a file was renamed, and the base reading
is read at the path the file has now. Nothing is inferred, and a checkout that cannot
answer finds no renames rather than guessing.

`within_the_room_freed` argues it, for the one meter that can hold the argument. A split
leaves the original file in place, so git reports the new file as an addition and no
rename detection can see it - but lines that left one file and arrived in another added
nothing to what anybody has to read, and `file-length` counts lines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from quality_allowances import git_maybe

if TYPE_CHECKING:
    from quality_meters import Meter


def renames_since(commit: str | None) -> dict[str, str]:
    """Where the files git can see were renamed since `commit` now live, old path to new.

    Not being answered is one of the answers, as it is for the trailers: a shallow clone
    cannot walk to the base, and a base that is not there finds no renames rather than
    stopping the check. The comparison is then the one it always was.

    A move git was never told about - a copy and a delete, with nothing staged - leaves
    the new path untracked and is not here. `git mv` stages what it moves.

    `-z` because the readable listing is not the path: git renders anything outside ASCII
    escaped and quoted, so `café.py` arrives as `"caf\303\251.py"` and matches no key any
    meter produces - those come from pathlib. NUL-delimited, git hands over the bytes it
    has, and the separator cannot occur in a path.
    """

    if not commit:
        return {}
    listed = git_maybe("diff", "--find-renames", "--diff-filter=R", "--name-status", "-z", commit, "--")
    fields = [field for field in (listed or "").split("\0") if field]
    renames: dict[str, str] = {}
    for status, was, now in zip(fields[0::3], fields[1::3], fields[2::3], strict=False):
        if status.startswith("R"):
            renames[was] = now
    return renames


def rekey(readings: dict[str, float], renames: dict[str, str]) -> dict[str, float]:
    """The same base readings, addressed by where each file is now.

    A key is a path, or a path and what was found in it - `app/a.py`, `app/a.py::C901` -
    so the path is everything before the first `::` and the rest rides along. A key that
    is not a path at all is left alone: `duplication` keys on a language and
    `frontend-dead-code` on a category, and neither is anywhere a file can be moved to.

    The readings handed back are this function's own either way, renames or none, because
    they belong to the caller and whether they are shared must not turn on whether anything
    happened to be renamed.

    A reading that moved is written after the ones that stayed, so that it wins when a
    rename lands on a path that had a reading of its own - `git mv -f a.py b.py` leaves one
    file at b.py, and it is the one that was a.py. The reading b.py had describes a file
    that is gone, and comparing the survivor against it would measure the wrong thing.
    """

    if not renames:
        return dict(readings)
    moved = {key: reading for key, reading in readings.items() if key.partition("::")[0] not in renames}
    for key, reading in readings.items():
        path, separator, rest = key.partition("::")
        if path in renames:
            moved[renames[path] + separator + rest] = reading
    return moved


def within_the_room_freed(meter: Meter, now: dict[str, float], was: dict[str, float]) -> set[str]:
    """The keys with no base reading that the same meter freed enough elsewhere to hold.

    Only a meter with an allowance answers anything, and it is `file-length` alone: a line
    is the same line wherever it sits. A count of findings is not like that - a new branchy
    function is a different function from the one somebody simplified - so the rest have no
    allowance and a key with no base reading there stays the regression it is.

    A key that is gone frees the whole reading it had, because a reading that is gone means
    the file is at or under the bar and there is no telling from here which. Crediting what
    it read is the only answer that is also right for a file deleted outright, and the
    meter's own total falls either way.

    All of them or none. Nothing here says which new key the freed room belongs to, so
    splitting it between them would be arithmetic dressed up as evidence.
    """

    if meter.move_allowance is None:
        return set()
    arrived = {key: reading for key, reading in now.items() if key not in was}
    if not arrived:
        return set()
    freed = sum(max(0.0, reading - now.get(key, 0.0)) for key, reading in was.items())
    room = freed + meter.move_allowance * len(arrived)
    return set(arrived) if sum(arrived.values()) <= room else set()
