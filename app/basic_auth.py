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

"""An HTTP Basic header is not a login here: asfquart is kept from checking it against LDAP.

For a request carrying `Authorization: Basic`, `asfquart.session.read` binds to ASF's LDAP as the
user and reads their project groups - with whatever any client sends, on every request. That is a
password oracle in front of LDAP, and nothing in this tool asks for it. BAT switches it off for the
same reason (its app/basic_auth.py).
"""

import asfquart.ldap


def install() -> None:
    """Turn asfquart's Basic login off, so that a Basic header opens no session and no connection.

    asfquart decides it from `asfquart.ldap.LDAP_SUPPORTED`, which it reads on each request and sets
    at import from whether `asfpy.aioldap` imports. Nothing here installs that today; the LDAP reader
    REQUIREMENTS.md asks for will, so it is switched off here rather than by leaving a dependency out.
    """
    asfquart.ldap.LDAP_SUPPORTED = False
