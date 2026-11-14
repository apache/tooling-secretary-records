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
# Install packages from whichever Ubuntu mirror is answering.
#
#   scripts/ci/apt-install.sh subversion libldap2-dev libsasl2-dev
#
# One mirror is a single point of failure and both of the ones this repository has used have
# been it. `azure.archive.ubuntu.com` began failing every request on 2026-08-18, which is why
# #702 gave apt a timeout - its default is none, so a stalled fetch ran to the job's own
# ceiling. On 2026-09-11 `archive.ubuntu.com` answered at 2 kB/s instead, which a timeout
# does not help with: three retries all crawl, and eight jobs across two pull requests were
# re-run, one of them cancelled at thirty minutes with apt still going.
#
# So the mirror is a list rather than a choice. On an EC2 instance - the ASF's self-hosted
# runners are - the archive mirror inside the region is both the closest one and the one
# Canonical's load does not reach, so it is tried first, and what the runner image shipped with
# is the fallback. A GitHub-hosted runner, which is what .github/workflows/ asks for, is not one,
# and where no region is found the image's own sources are used as they are.
set -eu

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <package>..." >&2
    exit 2
fi

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

# Every file apt reads its sources from, in either format: noble writes deb822 under
# sources.list.d, and a runner image built from an older one may still have the one-line
# sources.list.
sources="/etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources"

# The options #702 added, which still matter: a mirror that stalls is dropped and retried
# rather than waited on, because apt's own default is to wait forever. The packages are kept
# after they are installed rather than swept, since keeping them is the point of the cache.
apt_opts="-o Acquire::Retries=3 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15"
apt_opts="$apt_opts -o Binary::apt::APT::Keep-Downloaded-Packages=true"

# shellcheck disable=SC2086  # apt_opts is a list of arguments, deliberately unquoted
install_them() {
    sudo apt-get $apt_opts update && sudo apt-get $apt_opts install -y "$@"
}

# Where a job's cache of these packages lives, when the job passed one (see
# .github/actions/apt-install). Empty means fetch them, which is what a run outside CI does.
cache=${SR_APT_CACHE:-}

# Install what is in the cache, if what is in it is the whole of what was asked for.
#
# `dpkg` rather than apt, because the point is to need no index: the packages in there are
# the closure apt worked out on a machine of this same image, so they install as they are.
# The 40 MB of package index that `apt-get update` fetches is the expensive half of this
# step - on 2026-09-11 it took six minutes where the packages themselves were 562 kB - so a
# cache that still needed the index would save almost nothing.
from_cache() {
    [ -n "$cache" ] && [ -d "$cache" ] || return 1
    set -- "$cache"/*.deb
    [ -f "$1" ] || return 1
    echo "installing $# cached package(s) from $cache" >&2
    if sudo dpkg -i "$@"; then
        return 0
    fi

    # A cache that will not install is worse than none, so it is said out loud and thrown
    # away. What dpkg unpacked before it stopped is left half-configured, and the fetch below
    # names the same packages again - `apt-get install` finishes what dpkg started rather than
    # leaving it. Emptying the directory is for this job: the entry it was restored from is
    # keyed by the week and outlives the run, so a cache that is bad is bad until the key
    # rolls over, and every job takes the fetch in the meantime.
    echo "the cached packages did not install; fetching them instead" >&2
    rm -f "${cache:?}"/*.deb
    return 1
}

# And put them there for the next run, which is every run in the week after the first.
fill_cache() {
    [ -n "$cache" ] || return 0
    mkdir -p "$cache"
    sudo cp -- /var/cache/apt/archives/*.deb "$cache"/ 2>/dev/null || true
    sudo chown -R "$(id -u):$(id -g)" "$cache"
}

# The region this instance is in, if it is an instance at all. IMDSv2 wants a token first,
# and both calls are given two seconds: this is an optimisation, and a metadata service that
# is not there or not answering means there is no mirror to prefer, not that the run fails.
# `-f`, and a region spelled as AWS spells one: another cloud's metadata service can listen on
# the same address, and an error page it answers with must not be read as a region, which
# would point apt at a host that does not exist.
ec2_region() {
    token=$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token \
        -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null) || return 1
    [ -n "$token" ] || return 1
    found=$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $token" \
        http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null) || return 1
    printf '%s\n' "$found" | grep -Ex '[a-z]{2}(-[a-z]+)+-[0-9]+' || return 1
}

# The cache first: it is the only path here that touches no network at all.
if from_cache; then
    exit 0
fi

for file in $sources; do
    [ -f "$file" ] && sudo cp -- "$file" "$file.sr-original"
done

restore_sources() {
    for file in $sources; do
        [ -f "$file.sr-original" ] && sudo cp -- "$file.sr-original" "$file"
    done
}

region=$(ec2_region || true)
if [ -n "${region:-}" ]; then
    mirror="${region}.ec2.archive.ubuntu.com"
    echo "trying the archive mirror in $region" >&2
    # shellcheck disable=SC2086  # the source paths are a list, deliberately unquoted
    sudo "$here/apt-mirror.sh" "$mirror" $sources
    if install_them "$@"; then
        fill_cache
        exit 0
    fi
    echo "$mirror did not answer; falling back to the runner's own sources" >&2
    restore_sources
fi

install_them "$@"
fill_cache
