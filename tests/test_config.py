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

import pathlib

import pytest

from app import config


def test_an_empty_config_file_is_every_default(tmp_path: pathlib.Path):
    path = tmp_path / "config.yaml"
    path.write_text("")

    loaded = config.load_app_config(path)

    assert loaded == config.AppConfig()
    assert loaded.base.state_dir == "./state"


def test_no_smtp_block_means_no_mail(tmp_path: pathlib.Path):
    path = tmp_path / "config.yaml"
    path.write_text("base:\n  dev_mode: true\n")

    loaded = config.load_app_config(path)

    assert loaded.smtp is None
    assert not loaded.is_smtp_configured()


def test_a_missing_config_file_is_an_error(tmp_path: pathlib.Path):
    with pytest.raises(RuntimeError, match="does not exist"):
        config.load_app_config(tmp_path / "absent.yaml")


def test_the_example_config_loads():
    loaded = config.load_app_config(pathlib.Path(__file__).parent.parent / "config.yaml.example")

    assert loaded.base.dev_mode is True
    assert loaded.smtp is None
