#!/bin/sh
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

# The app for looking at by hand, inside the container `make container-keep` starts: the e2e
# fixtures, with /e2e/login/<uid>?roles=... in place of ASF OAuth, on 0.0.0.0:8080.
#
# Not scripts/run-dev.sh, which is for running on the host: it serves config.yaml's localhost:4500
# over TLS behind caddy, and from inside a container a server bound to localhost is reachable by
# nothing a published port forwards to. A state directory of its own, so that it never resets
# under an e2e run in the same checkout, nor that run under it. The config sits beside that
# directory rather than in it, because e2e/serve.py empties the state directory as it starts, and
# the state directory keeps the state-e2e name that serve.py will agree to empty.
set -eu

cd "$(dirname "$0")/.."
(cd assets && yarn build)

mkdir -p state-preview
sed -e 's#state-e2e#state-preview/state-e2e#g' \
    -e 's#^\(  host:\).*#\1 0.0.0.0#' \
    -e 's#^\(  port:\).*#\1 8080#' \
    e2e/e2e-config.yaml > state-preview/config.yaml

exec uv run python e2e/serve.py state-preview/config.yaml
