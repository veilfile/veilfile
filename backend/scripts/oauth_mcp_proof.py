#!/usr/bin/env python3
"""MCP-spec OAuth proof: the real HTTP dance against a LOCAL runserver.

Run:  DJANGO_DEBUG=True ../venv/bin/python manage.py runserver 127.0.0.1:8000
Then: PROOF_USERNAME=<user> PROOF_PASSWORD=<pw> \
      ../../venv/bin/python scripts/oauth_mcp_proof.py \
      [--base http://127.0.0.1:8000]

The user must have a VERIFIED email address (see docs/API.md: unverified
users get 403 at the authorize step, which this script asserts as the
negative case only when PROOF_EXPECT_UNVERIFIED=1).

Flow:
  DCR register -> authorize URL -> simulated browser approval (login + POST)
  -> PKCE code exchange -> POST /mcp tools/list with the token.
Negative cases:
  no token -> 401 WITH WWW-Authenticate header
  revoked token -> 401
  wrong PKCE verifier -> token exchange fails
  both .well-known metadata endpoints return valid JSON
  unverified user -> 403 at authorize (only with PROOF_EXPECT_UNVERIFIED=1)

Exit 0 on all-pass, 1 on any failure. Never prints secrets (only redacted
prefixes). Contains no credentials: the login comes from env vars.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import urllib.parse

import requests

BASE = os.environ.get("PROOF_BASE", "http://127.0.0.1:8000").rstrip("/")
USERNAME = os.environ.get("PROOF_USERNAME")
PASSWORD = os.environ.get("PROOF_PASSWORD")
EXPECT_UNVERIFIED = os.environ.get("PROOF_EXPECT_UNVERIFIED") == "1"
REDIRECT_URI = "http://127.0.0.1:9876/callback"

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("PASS " if condition else "FAIL ") + name + (f" -- {detail}" if detail and not condition else ""))


def redact(value):
    return (value[:6] + "...") if value else "<empty>"


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def main():
    if not USERNAME or not PASSWORD:
        print("Set PROOF_USERNAME and PROOF_PASSWORD env vars (verified user).")
        return 1

    s = requests.Session()

    # --- 1. DCR register -------------------------------------------------
    r = s.post(
        f"{BASE}/o/register/",
        json={
            "client_name": "proof-client",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
        },
    )
    check("dcr register -> 201", r.status_code == 201, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 201:
        return report()
    client_id = r.json()["client_id"]
    print(f"     client_id={redact(client_id)}")

    # --- metadata documents ----------------------------------------------
    r = s.get(f"{BASE}/.well-known/oauth-authorization-server")
    try:
        auth_doc = r.json()
        auth_ok = (
            r.status_code == 200
            and "mcp:use" in auth_doc.get("scopes_supported", [])
            and auth_doc.get("authorization_endpoint", "").endswith("/o/authorize/")
        )
    except ValueError:
        auth_ok = False
    check("oauth-authorization-server metadata valid", auth_ok)

    r = s.get(f"{BASE}/.well-known/oauth-protected-resource")
    try:
        res_doc = r.json()
        res_ok = (
            r.status_code == 200
            and res_doc.get("resource") == f"{BASE}/mcp"
            and res_doc.get("scopes_supported") == ["mcp:use"]
            and "header" in res_doc.get("bearer_methods_supported", [])
        )
    except ValueError:
        res_ok = False
    check("oauth-protected-resource metadata valid", res_ok)

    # --- 2. browser login --------------------------------------------------
    login_page = s.get(f"{BASE}/accounts/login/")
    csrf = s.cookies.get("csrftoken")
    check("login page reachable", login_page.status_code == 200 and csrf)
    r = s.post(
        f"{BASE}/accounts/login/",
        data={"username": USERNAME, "password": PASSWORD, "csrfmiddlewaretoken": csrf},
        headers={"Referer": f"{BASE}/accounts/login/"},
        allow_redirects=False,
    )
    check("browser login", r.status_code in (302, 303), f"got {r.status_code}")

    # --- 3. authorize ------------------------------------------------------
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "mcp:use",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    consent = s.get(f"{BASE}/o/authorize/", params=params)
    if EXPECT_UNVERIFIED:
        check("unverified user -> 403 at authorize", consent.status_code == 403)
        return report()
    check("authorize GET -> consent page", consent.status_code == 200, f"got {consent.status_code}")
    if consent.status_code != 200:
        return report()
    csrf2 = s.cookies.get("csrftoken")
    # Phishing hardening is visible in the served page.
    check(
        "consent page shows unverified-app framing",
        "nverified third-party app" in consent.text,
    )
    check("consent page shows redirect host", "127.0.0.1:9876" in consent.text)

    approval = s.post(
        f"{BASE}/o/authorize/",
        data={
            "csrfmiddlewaretoken": csrf2,
            "redirect_uri": REDIRECT_URI,
            "scope": "mcp:use",
            "client_id": client_id,
            "state": state,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "allow": "Authorize",
        },
        headers={"Referer": f"{BASE}/o/authorize/"},
        allow_redirects=False,
    )
    check("approval -> 302 to redirect_uri", approval.status_code == 302 and approval.headers.get("Location", "").startswith(REDIRECT_URI))
    query = urllib.parse.parse_qs(urllib.parse.urlparse(approval.headers["Location"]).query)
    check("code issued, state echoed", "code" in query and query.get("state") == [state])
    code = query["code"][0]
    print(f"     code={redact(code)}")

    # --- 4. code exchange ---------------------------------------------------
    r = s.post(
        f"{BASE}/o/token/",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    check("token exchange -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        return report()
    token_body = r.json()
    access_token = token_body["access_token"]
    check("access_token scope is mcp:use", token_body.get("scope") == "mcp:use")
    check("refresh_token issued", bool(token_body.get("refresh_token")))
    print(f"     access_token={redact(access_token)}")

    # --- 5. /mcp with the token --------------------------------------------
    def mcp_call(cred):
        headers = {"Content-Type": "application/json"}
        if cred:
            headers["Authorization"] = f"Bearer {cred}"
        return s.post(
            f"{BASE}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=headers,
        )

    r = mcp_call(access_token)
    ok = r.status_code == 200 and "upload_artifact" in [
        t["name"] for t in r.json()["result"]["tools"]
    ]
    check("mcp tools/list with OAuth token -> 200", ok, f"got {r.status_code}")

    # --- negatives ----------------------------------------------------------
    r = mcp_call(None)
    www = r.headers.get("WWW-Authenticate", "")
    check(
        "no token -> 401 WITH WWW-Authenticate",
        r.status_code == 401 and "resource_metadata" in www,
        f"got {r.status_code}, header={www[:80]}",
    )

    # wrong PKCE verifier: fresh code, bad verifier
    verifier2, challenge2 = pkce_pair()
    params2 = dict(params, code_challenge=challenge2, state=secrets.token_urlsafe(16))
    consent2 = s.get(f"{BASE}/o/authorize/", params=params2)
    approval2 = s.post(
        f"{BASE}/o/authorize/",
        data={
            "csrfmiddlewaretoken": s.cookies.get("csrftoken"),
            "redirect_uri": REDIRECT_URI,
            "scope": "mcp:use",
            "client_id": client_id,
            "state": params2["state"],
            "response_type": "code",
            "code_challenge": challenge2,
            "code_challenge_method": "S256",
            "allow": "Authorize",
        },
        headers={"Referer": f"{BASE}/o/authorize/"},
        allow_redirects=False,
    )
    code2 = urllib.parse.parse_qs(urllib.parse.urlparse(approval2.headers["Location"]).query)["code"][0]
    r = s.post(
        f"{BASE}/o/token/",
        data={
            "grant_type": "authorization_code",
            "code": code2,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": "definitely-the-wrong-verifier",
        },
    )
    check(
        "wrong PKCE verifier -> exchange fails",
        r.status_code == 400 and r.json().get("error") == "invalid_grant",
        f"got {r.status_code}",
    )

    # code single-use: the first code is already consumed
    r = s.post(
        f"{BASE}/o/token/",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    check("authorization code single-use", r.status_code == 400)

    # revocation: kill the grant, token must die
    r = s.post(
        f"{BASE}/api/v1/oauth/revoke-grants/",
        headers={"X-CSRFToken": s.cookies.get("csrftoken", "")},
    )
    check("revoke-grants endpoint -> 200", r.status_code == 200, f"got {r.status_code}")
    r = mcp_call(access_token)
    check("revoked token -> 401", r.status_code == 401, f"got {r.status_code}")

    # --- plain PKCE rejected -------------------------------------------------
    verifier3 = secrets.token_urlsafe(48)
    params3 = dict(params, code_challenge=verifier3, code_challenge_method="plain", state=secrets.token_urlsafe(16))
    consent3 = s.get(f"{BASE}/o/authorize/", params=params3)
    approval3 = s.post(
        f"{BASE}/o/authorize/",
        data={
            "csrfmiddlewaretoken": s.cookies.get("csrftoken"),
            "redirect_uri": REDIRECT_URI,
            "scope": "mcp:use",
            "client_id": client_id,
            "state": params3["state"],
            "response_type": "code",
            "code_challenge": verifier3,
            "code_challenge_method": "plain",
            "allow": "Authorize",
        },
        headers={"Referer": f"{BASE}/o/authorize/"},
        allow_redirects=False,
    )
    q3 = urllib.parse.parse_qs(urllib.parse.urlparse(approval3.headers.get("Location", "")).query)
    check("plain PKCE method refused", q3.get("error") == ["invalid_request"] and "code" not in q3)

    return report()


def report():
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for name in failed:
        print("  FAILED:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
