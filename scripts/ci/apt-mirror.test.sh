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
#
# What apt-mirror.sh moves, and - the half that matters - what it leaves where it is. A
# rewrite that catches too much leaves a runner unable to see any archive at all, which shows
# up as every job failing to install anything, several minutes into each one.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
mirror_sh="$here/apt-mirror.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0

check() {
    description=$1
    expected=$2
    actual=$3
    if [ "$expected" = "$actual" ]; then
        echo "ok - $description"
    else
        echo "FAIL - $description"
        echo "  expected: $expected"
        echo "  actual:   $actual"
        failures=$((failures + 1))
    fi
}

refuses() {
    description=$1
    shift
    if "$mirror_sh" "$@" 2>/dev/null; then
        echo "FAIL - $description (it was accepted)"
        failures=$((failures + 1))
    else
        echo "ok - $description"
    fi
}

# Noble's own, as the runner image ships it.
deb822="$work/ubuntu.sources"
write_deb822() {
    cat > "$deb822" <<'SOURCES'
Types: deb
URIs: http://archive.ubuntu.com/ubuntu/
Suites: noble noble-updates noble-backports
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu/
Suites: noble-security
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
SOURCES
}

write_deb822
"$mirror_sh" us-east-1.ec2.archive.ubuntu.com "$deb822"

check "the archive moves to the named mirror" \
    "URIs: http://us-east-1.ec2.archive.ubuntu.com/ubuntu/" \
    "$(grep -m 1 'ec2' "$deb822")"

check "security stays where it is" \
    "URIs: http://security.ubuntu.com/ubuntu/" \
    "$(grep 'security.ubuntu.com' "$deb822")"

check "every other line is left alone" \
    "Suites: noble noble-updates noble-backports" \
    "$(grep -m 1 'Suites:' "$deb822")"

# A second pass over a file that has already moved: the runs this is for retry, and a rewrite
# that only matched the original host would leave the second mirror unreachable.
"$mirror_sh" eu-west-1.ec2.archive.ubuntu.com "$deb822"
check "a mirror can be moved to another mirror" \
    "URIs: http://eu-west-1.ec2.archive.ubuntu.com/ubuntu/" \
    "$(grep -m 1 'ec2' "$deb822")"

# The one-line format, and https, which an older runner image can carry.
oneline="$work/sources.list"
cat > "$oneline" <<'SOURCES'
deb https://azure.archive.ubuntu.com/ubuntu noble main
deb http://security.ubuntu.com/ubuntu noble-security main
SOURCES
"$mirror_sh" us-east-1.ec2.archive.ubuntu.com "$oneline"

check "the one-line format moves too, https included" \
    "deb http://us-east-1.ec2.archive.ubuntu.com/ubuntu noble main" \
    "$(head -n 1 "$oneline")"

check "and leaves security alone there as well" \
    "deb http://security.ubuntu.com/ubuntu noble-security main" \
    "$(tail -n 1 "$oneline")"

# A file that is not there is not an error: the two formats are both looked for and a runner
# has one of them.
"$mirror_sh" us-east-1.ec2.archive.ubuntu.com "$work/not-here.sources" "$oneline"
check "a missing file is passed over" \
    "deb http://us-east-1.ec2.archive.ubuntu.com/ubuntu noble main" \
    "$(head -n 1 "$oneline")"

# A host that merely begins with the archive's name is a different host, and rewriting its
# first three labels would point apt at `<mirror>.example.com` - somewhere nobody named.
lookalike="$work/lookalike.list"
cat > "$lookalike" <<'SOURCES'
deb http://archive.ubuntu.com.example.com/ubuntu noble main
deb http://archive.ubuntu.com noble main
SOURCES
"$mirror_sh" us-east-1.ec2.archive.ubuntu.com "$lookalike"

check "a host that only starts with the archive's name is left alone" \
    "deb http://archive.ubuntu.com.example.com/ubuntu noble main" \
    "$(head -n 1 "$lookalike")"

check "and a source with no path at all still moves" \
    "deb http://us-east-1.ec2.archive.ubuntu.com noble main" \
    "$(tail -n 1 "$lookalike")"

# What must not be accepted, since it is pasted into the middle of every source line.
write_deb822
refuses "a url is refused" "http://us-east-1.ec2.archive.ubuntu.com/ubuntu" "$deb822"
refuses "a host with a path is refused" "mirror.example/ubuntu" "$deb822"
refuses "an empty host is refused" "" "$deb822"
refuses "a shell metacharacter is refused" 'mirror.example; rm -rf /' "$deb822"
check "and none of them wrote anything" \
    "URIs: http://archive.ubuntu.com/ubuntu/" \
    "$(grep -m 1 'URIs:' "$deb822")"

if [ "$failures" -ne 0 ]; then
    echo "$failures case(s) failed"
    exit 1
fi

echo "apt-mirror.sh: PASSED"
