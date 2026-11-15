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

"""A workflow file that GitHub cannot read does not run: it fails in no time with "workflow file issue",
on every push, and the schedule it holds never fires. check-yaml reads `env:` with nothing under it
as valid YAML, so say here what GitHub wants."""

import pathlib

import pytest
import yaml

WORKFLOWS = sorted((pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


def _envs(workflow: dict) -> list[tuple[str, object]]:
    found = [("the workflow", workflow["env"])] if "env" in workflow else []
    for name, job in workflow.get("jobs", {}).items():
        if "env" in job:
            found.append((f"job {name}", job["env"]))
        found += [(f"a step of job {name}", step["env"]) for step in job.get("steps", []) if "env" in step]
    return found


def test_there_are_workflows_to_read() -> None:
    assert WORKFLOWS


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_every_env_block_is_a_mapping_with_something_in_it(path: pathlib.Path) -> None:
    for where, env in _envs(yaml.safe_load(path.read_text())):
        assert isinstance(env, dict) and env, f"{path.name}: the env of {where} is {env!r}"
