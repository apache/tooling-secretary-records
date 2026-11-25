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

# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
"""Everything that gates a push, at once, and only what changed since it last passed.

`make verify` runs each stage as the make target of the same name, all of them at the same time:
pytest keeps up to six workers busy, and the rest - the hooks, the linters, vitest, the behaviour record and
the quality ratchet - fit beside it on the cores it leaves, so the gate takes about as long as its
longest stage rather than the sum of them. Each stage writes its own log under state/verify/, and
what is printed is one line per stage, and the end of the log of one that failed.

A stage that passed is not run again until something it reads changes. What it reads is its key: the
content of its files - tracked, staged or untracked, as they are in the working tree, so an edit the
gate has not seen is a change - the commands its make target runs (`make -n`, so that a comment in the
Makefile or in devkit.mk, which it includes, is not, for a stage that does not read the Makefile itself),
and what else the stage depends on (`extras`). The key is taken before the stage runs and again after it,
and a pass is recorded only when the two agree: a hook that rewrote a file while pytest was reading it
leaves nothing recorded, and the next run runs both again.

A stage whose files are only some of the tree says which (`Stage.paths`), and each of those lists is
a claim that the stage reads nothing else. Where a stage reads the whole tree it says so, and a
change anywhere runs it.

    scripts/container.sh uv run scripts/ci/verify.py                 # what `make container-verify` runs
    scripts/container.sh uv run scripts/ci/verify.py --fresh         # run every stage, passed or not
    scripts/container.sh uv run scripts/ci/verify.py test-python     # one stage

Each of these is sized to the machine and holds one of its gate slots, as `make container-verify`
is (scripts/container-size.sh).

What a repository's stages are is `scripts/local/verify.toml`, which it owns:

    environment = ["MINUTES_CORPUS"]    # variables, besides PYTEST_ADDOPTS and DEVKIT_PYTHON, that
                                        # change what a stage checks

    [extras.quality-base]               # what a stage depends on that is not a file of the tree:
    kind = "file"                       #   "file" - `path`, or the path the variable `env` holds -
    path = "state/quality-base.json"    #   "trailers" - the messages of this branch's own commits -
    env = "QUALITY_BASE"                #   or "command" - what `run` prints

    [[stages]]
    name = "test-python"                # the make target, and the name of the log and the record
    paths = ["assets/", "tests/data/"]  # prefixes of the files it reads; none means the whole tree
    extras = ["quality-base"]
    reads_makefile = false
    longest = true                      # the stage the gate waits for

The file is data, read with tomllib: nothing of the repository is imported to run the gate.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
STATE = ROOT / "state" / "verify"

# Lines of a failed stage's log printed under its verdict; the whole log is a path away.
_TAIL = 60


@dataclasses.dataclass(frozen=True)
class Stage:
    name: str
    """The make target, and the name of the log and the record."""
    paths: tuple[str, ...] | None = None
    """The prefixes of the files the stage reads, or None for the whole tree."""
    extras: tuple[str, ...] = ()
    """Names in `extras`: what the stage depends on that is not a file of the tree."""
    reads_makefile: bool = False
    """Whether the Makefile is a file the stage reads, rather than only where its command comes from -
    which `recipe` covers, so that a comment there runs nothing again."""
    longest: bool = False
    """The stage the gate waits for. The others run niced, so that the cores go to it first and the
    rest fill what it leaves."""


_BUILTIN_ENVIRONMENT = ("DEVKIT_PYTHON", "PYTEST_ADDOPTS")
"""Settings a run reads from the environment that change what it checks in every repository -
PYTEST_ADDOPTS among them, since `-k` or `--lf` there runs some of the suite and would otherwise record a
pass of all of it. A repository adds its own (`environment`). Make's own variables reach the key through
`make -n` instead, all but PYTEST_WORKERS (`recipe`)."""


@dataclasses.dataclass(frozen=True)
class Extra:
    kind: str
    """"file", "trailers" or "command"."""
    path: str = ""
    env: str = ""
    """For a file: the variable that names the path where the Makefile passes one."""
    run: str = ""


@dataclasses.dataclass(frozen=True)
class Config:
    stages: tuple[Stage, ...]
    extras: dict[str, Extra]
    environment: tuple[str, ...]


class ConfigError(Exception):
    """The repository's verify.toml says something that cannot be run."""


CONFIG = pathlib.Path("scripts/local/verify.toml")
"""Where a repository states its stages, from the root of the repository."""

_EXTRA_KEYS = {"file": {"path", "env"}, "trailers": set(), "command": {"run"}}
_STAGE_KEYS = {"name", "paths", "extras", "reads_makefile", "longest"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _strings(value: object, where: str, problems: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(f"{where} is a list of strings")
        return ()
    return tuple(value)


def _extras(table: object, problems: list[str]) -> dict[str, Extra]:
    extras: dict[str, Extra] = {}
    if not isinstance(table, dict):
        problems.append("extras is a table of tables")
        return extras
    for name, body in table.items():
        where = f"extras.{name}"
        if not isinstance(body, dict) or body.get("kind") not in _EXTRA_KEYS:
            problems.append(f"{where} has a kind of {', '.join(sorted(_EXTRA_KEYS))}")
            continue
        kind = body["kind"]
        problems += [f"{where}: no {key} for a {kind}" for key in sorted(set(body) - {"kind"} - _EXTRA_KEYS[kind])]
        required = {"file": "path", "command": "run"}.get(kind)
        values = {key: body.get(key, "") for key in ("path", "env", "run")}
        if not all(isinstance(value, str) for value in values.values()) or (required and not values[required]):
            problems.append(f"{where} gives {required or 'nothing'} as a string")
            continue
        extras[name] = Extra(kind=kind, **values)
    return extras


def _stages(entries: object, extras: dict[str, Extra], problems: list[str]) -> tuple[Stage, ...]:
    stages: list[Stage] = []
    if not isinstance(entries, list) or not entries:
        problems.append("there is no [[stages]]")
        return ()
    for number, entry in enumerate(entries, 1):
        name = entry.get("name") if isinstance(entry, dict) else None
        where = f"stage {number}" + (f" ({name})" if isinstance(name, str) else "")
        if not isinstance(entry, dict) or not isinstance(name, str) or not _NAME.fullmatch(name):
            problems.append(f"{where} has a name that is a make target: letters, digits, `.`, `_` and `-`")
            continue
        problems += [f"{where} has no {key}" for key in sorted(set(entry) - _STAGE_KEYS)]
        if any(stage.name == name for stage in stages):
            problems.append(f"{where} is named twice")
        paths = _strings(entry["paths"], f"{where}: paths", problems) if "paths" in entry else None
        used = _strings(entry.get("extras", []), f"{where}: extras", problems)
        problems += [f"{where}: no extra named {extra}" for extra in used if extra not in extras]
        flags = {key: entry.get(key, False) for key in ("reads_makefile", "longest")}
        problems += [f"{where}: {key} is true or false" for key, flag in flags.items() if not isinstance(flag, bool)]
        stages.append(Stage(name, paths=paths, extras=used, **flags))
    return tuple(stages)


def load_config(path: pathlib.Path) -> Config:
    """What the repository's verify.toml says its stages are, or every reason it cannot be run."""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(
            f"{path} cannot be read ({error.strerror}). A repository names its stages in {CONFIG}: "
            "the format is in the docstring of this script."
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not TOML: {error}") from error
    problems = [f"{key} is not a setting" for key in sorted(set(document) - {"environment", "extras", "stages"})]
    environment = _strings(document.get("environment", []), "environment", problems)
    problems += [f"environment: {name} is not a variable name" for name in environment if not _VARIABLE.fullmatch(name)]
    extras = _extras(document.get("extras", {}), problems)
    stages = _stages(document.get("stages"), extras, problems)
    if problems:
        raise ConfigError(f"{path}:\n" + "\n".join(f"  {problem}" for problem in problems))
    return Config(stages=stages, extras=extras, environment=environment)


def _trailers() -> str:
    """The messages of this branch's own commits, which is where an allowance is read from
    (`Behaviour-Allow:`, `Quality-Allow:`): a trailer added by an amend changes what the stage says."""
    base = _git("merge-base", os.environ.get("VERIFY_REF", "origin/main"), "HEAD", check=False)
    return _git("log", "--format=%B%x00", f"{base}..HEAD", check=False) if base else "no merge base"


def _file(relative: str, named_by: str) -> str:
    """What a file holds, read when the key is taken - from the path the variable `named_by` gives,
    where the Makefile passes one, since that is the file the stage reads."""
    named = os.environ.get(named_by, "") if named_by else ""
    path = ROOT / (named or relative)
    return path.read_text(encoding="utf-8") if path.is_file() else f"no {path}"


def extra_text(extra: Extra) -> str:
    """What an extra holds now: the text that goes into the key of a stage that depends on it."""
    if extra.kind == "trailers":
        return _trailers()
    if extra.kind == "file":
        return _file(extra.path, extra.env)
    finished = subprocess.run(
        ["sh", "-c", extra.run], cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
    )
    return f"exit {finished.returncode}\n{finished.stdout}"


def _git(*arguments: str, check: bool = True, stdin: str | None = None) -> str:
    finished = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True, input=stdin)
    if check and finished.returncode != 0:
        raise SystemExit(f"git {' '.join(arguments)}: {finished.stderr.strip()}")
    return finished.stdout.strip() if finished.returncode == 0 else ""


def tree() -> dict[str, str]:
    """Every file of the working tree - tracked, staged, or untracked and not ignored by a .gitignore -
    and the hash of what it holds now. A tracked file that is gone is listed as gone.

    Only the .gitignore files decide what is ignored, not .git/info/exclude or a global excludes file:
    a test file hidden there is still one pytest collects."""
    listed = _git("ls-files", "-z", "--cached", "--others", "--exclude-per-directory=.gitignore").split("\0")
    paths = sorted({p for p in listed if p})
    present = [p for p in paths if (ROOT / p).is_file()]
    hashes = _git("hash-object", "--stdin-paths", stdin="\n".join(present)).splitlines() if present else []
    found = dict(zip(present, hashes, strict=True))
    return {p: found.get(p, "gone") for p in paths}


def recipe(stage: Stage) -> str:
    """What the stage's make target runs, as make prints it without running it - with the worker count
    pinned, since scripts/container-size.sh chooses it from the memory free when the gate starts, and a
    count is how long the suite takes rather than what it checks."""
    finished = subprocess.run(
        [os.environ.get("MAKE", "make"), "--no-print-directory", "-n", stage.name, "PYTEST_WORKERS=any"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        raise SystemExit(f"make -n {stage.name}: {finished.stderr.strip()}")
    return finished.stdout


MAKEFILES = ("Makefile", "devkit.mk")
"""What a stage's recipe comes from: read through `make -n` rather than as files, unless the stage reads them."""


def key(stage: Stage, files: dict[str, str], config: Config) -> str:
    digest = hashlib.sha256()
    for part in (stage.name, recipe(stage), sys.version):
        digest.update(part.encode() + b"\0")
    for name in (*config.environment, *(n for n in _BUILTIN_ENVIRONMENT if n not in config.environment)):
        digest.update(f"{name}={os.environ.get(name, '')}\0".encode())
    for path, blob in files.items():
        if path in MAKEFILES and not stage.reads_makefile:
            continue
        if stage.paths is None or path.startswith(stage.paths):
            digest.update(f"{path} {blob}\0".encode())
    for extra in stage.extras:
        digest.update(f"{extra}\0{extra_text(config.extras[extra])}\0".encode())
    return digest.hexdigest()


def _record(stage: Stage) -> pathlib.Path:
    return STATE / f"{stage.name}.passed"


def passed(stage: Stage) -> dict[str, str] | None:
    try:
        return json.loads(_record(stage).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


@dataclasses.dataclass(frozen=True)
class Outcome:
    stage: Stage
    verdict: str
    """"passed", "failed" or "unchanged"."""
    seconds: float
    log: pathlib.Path


def run(stage: Stage, config: Config, *, fresh: bool) -> Outcome:
    log = STATE / f"{stage.name}.log"
    before = key(stage, tree(), config)
    earlier = None if fresh else passed(stage)
    if earlier is not None and earlier.get("key") == before:
        return Outcome(stage, "unchanged", 0.0, log)
    _record(stage).unlink(missing_ok=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as out:
        finished = subprocess.run(
            [
                *(() if stage.longest else ("nice", "-n", "10")),
                os.environ.get("MAKE", "make"),
                "--no-print-directory",
                stage.name,
            ],
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    seconds = time.monotonic() - started
    if finished.returncode != 0:
        return Outcome(stage, "failed", seconds, log)
    if key(stage, tree(), config) == before:
        when = datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds")
        _record(stage).write_text(json.dumps({"key": before, "at": when}) + "\n", encoding="utf-8")
    return Outcome(stage, "passed", seconds, log)


def report(outcome: Outcome) -> str:
    name, log = outcome.stage.name, outcome.log.relative_to(ROOT)
    if outcome.verdict == "unchanged":
        at = (passed(outcome.stage) or {}).get("at", "earlier")
        return f"{name}: unchanged since it passed at {at}, not run"
    if outcome.verdict == "passed":
        return f"{name}: passed in {outcome.seconds:.0f}s ({log})"
    lines = outcome.log.read_text(encoding="utf-8", errors="replace").splitlines()[-_TAIL:]
    return "\n".join(
        [f"{name}: FAILED in {outcome.seconds:.0f}s - the end of {log}:", *(f"    {line}" for line in lines)]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stages", nargs="*", metavar="STAGE", help="the stages of scripts/local/verify.toml")
    parser.add_argument("--fresh", action="store_true", help="run every stage, whether it passed before or not")
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / CONFIG, help=f"default {CONFIG}")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"verify: {error}", file=sys.stderr)
        return 2
    names = [s.name for s in config.stages]
    unknown = sorted(set(args.stages) - set(names))
    if unknown:
        parser.error(f"no stage {', '.join(unknown)}: the stages are {', '.join(names)}")
    chosen = [s for s in config.stages if not args.stages or s.name in args.stages]
    STATE.mkdir(parents=True, exist_ok=True)

    print(f"verify: {len(chosen)} stages at once - {', '.join(s.name for s in chosen)}", flush=True)
    outcomes: list[Outcome] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chosen)) as pool:
        futures = [pool.submit(run, stage, config, fresh=args.fresh) for stage in chosen]
        for future in concurrent.futures.as_completed(futures):
            outcomes.append(future.result())
            print(report(outcomes[-1]), flush=True)

    failed = [o.stage.name for o in outcomes if o.verdict == "failed"]
    if failed:
        print(f"verify: FAILED - {', '.join(failed)}")
        return 1
    print("verify: every stage passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
