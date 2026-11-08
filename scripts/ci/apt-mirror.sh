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
# Point apt's archive at a named mirror, in the files it is given.
#
#   scripts/ci/apt-mirror.sh <host> <file>...
#
# Only the distribution archive moves. `security.ubuntu.com` stays where it is: it is served
# by a different set of machines, it is the one that carries updates nobody should be waiting
# on a mirror for, and a run that has to choose between a slow mirror and no security updates
# is choosing wrongly either way.
#
# Every archive host is matched rather than one named one, so that this says where apt points
# now and not where it pointed when the runner image was built - `archive.ubuntu.com`,
# `azure.archive.ubuntu.com` and `<region>.ec2.archive.ubuntu.com` are all the same archive
# and a runner image can ship any of them.
set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <mirror-host> <file>..." >&2
    exit 2
fi

mirror=$1
shift

# A host and nothing else: a url or a path here would be pasted into the middle of every
# source line, and the failure would be a runner that cannot see any archive at all.
case $mirror in
*[!A-Za-z0-9.-]* | "" | .* | *.)
    echo "$0: '$mirror' is not a hostname" >&2
    exit 2
    ;;
*) ;;
esac

for file in "$@"; do
    [ -f "$file" ] || continue
    # The host ends where it is matched, and not in the middle of a longer one:
    # `archive.ubuntu.com.example.com` is a different host altogether, and rewriting its
    # first three labels would point apt at `<mirror>.example.com` - somewhere nobody named
    # and nobody is watching. What can follow the host is the path, a space, or the end of
    # the line, and whichever it is comes through the substitution unchanged.
    #
    # http, not https: an EC2 archive mirror serves plain http, and apt verifies the
    # signature on what it fetches rather than the transport it came over.
    sed -i -E "s#https?://[A-Za-z0-9.-]*archive\.ubuntu\.com(/|[[:space:]]|\$)#http://${mirror}\1#g" "$file"
done
