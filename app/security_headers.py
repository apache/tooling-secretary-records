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


"""The security headers every response carries: CSP, HSTS, nosniff and frame denial.

Scripts are held to this origin plus a nonce minted per request, so an inline `<script>` runs
only when a template wrote it with `nonce="{{ csp_nonce() }}"`, and an inline event handler
attribute (`onclick="..."`) never runs at all. Templates attach their listeners from a nonced
block instead. A page that renders no nonce gets a policy naming none, so nothing inline runs.

Styles keep `'unsafe-inline'`: the admin pages carry `<style>` blocks and the Svelte components
static `style=` attributes, and a style cannot run code.

`base.csp_report_only` sends the same policy as `Content-Security-Policy-Report-Only`, for an
operator who finds the policy blocking something in production and needs the page back before
the fix ships. The policy names no `report-uri`, so a refusal, enforced or reported, shows only
in the console of the browser that met it. Browsers ignore `frame-ancestors` in a report-only
policy, which is one reason `X-Frame-Options` is sent as well.
"""

import secrets
import urllib.parse

import quart

from app import config

# The Foundation's own stylesheets, faces and images come from www.apache.org; see the font
# preload in layouts/base.html.
_ASF_WWW = "https://www.apache.org"

# A year, without `preload`: the preload list (hstspreload.org) takes only a registrable domain,
# so preloading this host means preloading apache.org and every host under it - a commitment
# that is not this application's to make.
_HSTS = "max-age=31536000; includeSubDomains"

# HSTS is keyed on the host alone, not the port, so a development instance served over https on
# localhost (config.yaml.example does) would pin every localhost port to https for a year.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def csp_nonce() -> str:
    """The nonce for this request's inline scripts, minted the first time a template asks."""
    nonce = quart.g.get("csp_nonce")
    if nonce is None:
        nonce = secrets.token_urlsafe(16)
        quart.g.csp_nonce = nonce
    return nonce


def content_security_policy(nonce: str | None) -> str:
    script_src = "'self'" if nonce is None else f"'self' 'nonce-{nonce}'"
    directives = {
        "default-src": "'self'",
        "script-src": script_src,
        "style-src": f"'self' 'unsafe-inline' {_ASF_WWW}",
        "font-src": f"'self' {_ASF_WWW}",
        # data: for the SVG backgrounds in bootstrap.min.css and app.css
        "img-src": f"'self' data: {_ASF_WWW}",
        # 'self' matches ws: and wss: on this host too, which a live-update socket will need.
        "connect-src": "'self'",
        "object-src": "'none'",
        "base-uri": "'self'",
        "form-action": "'self'",
        "frame-ancestors": "'none'",
    }
    return "; ".join(f"{name} {value}" for name, value in directives.items())


def _is_loopback(host: str) -> bool:
    try:
        return urllib.parse.urlsplit(f"//{host}").hostname in _LOOPBACK_HOSTS
    except ValueError:  # a malformed bracketed host, which is nobody's loopback
        return False


def setup_security_headers(quart_app: quart.Quart) -> None:
    quart_app.jinja_env.globals["csp_nonce"] = csp_nonce

    @quart_app.after_request
    async def _set_security_headers(response: quart.Response) -> quart.Response:
        report_only = config.get().base.csp_report_only
        csp_header = "Content-Security-Policy-Report-Only" if report_only else "Content-Security-Policy"
        response.headers[csp_header] = content_security_policy(quart.g.get("csp_nonce"))
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if not _is_loopback(quart.request.host):
            response.headers["Strict-Transport-Security"] = _HSTS
        return response
