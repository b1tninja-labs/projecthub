"""Registering the App: only this run's callback is accepted, and the private key is never shown."""

from __future__ import annotations

import ctypes
import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

from projecthub import app as A

PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\nsecret-material\n-----END RSA PRIVATE KEY-----\n"
)
APP = {"id": 42, "slug": "projecthub-b1tninja-labs", "pem": PEM}


class Fake:
    def __init__(self, **replies):
        self.calls: list[tuple[list[str], str | None]] = []
        self.replies = replies

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        joined = " ".join(argv)
        for key, reply in self.replies.items():
            if key.replace("__", " ") in joined:
                return reply
        return A.Done(0, "", "")


def test_the_shipped_manifest_grants_what_the_sync_needs_and_no_webhook():
    manifest = A.build_manifest("http://localhost:1/callback")
    assert manifest["public"] is False and manifest["redirect_url"].endswith(
        "/callback"
    )
    assert manifest["default_permissions"] == {
        "issues": "write",
        "metadata": "read",
        "organization_projects": "write",
    }, "evidence runs locally, so the App needs nothing from the code repositories"
    assert manifest["default_events"] == [] and "hook_attributes" not in manifest


def test_the_form_escapes_the_manifest_and_carries_the_state():
    page = A.form_page(A.registration_url("b1tninja-labs", "S"), {"name": 'a"b<c'})
    assert (
        'action="https://github.com/organizations/b1tninja-labs/settings/apps/new?state=S"'
        in page
    )
    assert 'a"b<c' not in page


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as reply:  # noqa: S310 - a loopback test server
            return reply.status, reply.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""


def test_only_this_runs_callback_delivers_a_code():
    seen = {}

    def on_ready(port):
        def visit():
            base = f"http://127.0.0.1:{port}"
            seen["page"] = _get(f"{base}/")
            seen["forged"] = _get(f"{base}/callback?code=evil&state=wrong")
            seen["good"] = _get(f"{base}/callback?code=the-code&state=right")

        threading.Thread(target=visit, daemon=True).start()

    code = A.wait_for_code(
        lambda port: f"page on {port}", "right", on_ready, timeout=20
    )
    assert code == "the-code"
    assert seen["forged"][0] == 400, "a callback with another state is refused"
    assert seen["page"][0] == 200 and "page on" in seen["page"][1]


def test_no_callback_within_the_time_limit_is_an_error():
    with pytest.raises(SystemExit, match="no callback"):
        A.wait_for_code(lambda port: "", "s", lambda port: None, timeout=0.1)


def test_exchange_returns_the_app_and_never_echoes_a_bad_reply():
    good = Fake(conversions=A.Done(0, json.dumps({**APP, "client_secret": "c"}), ""))
    assert A.exchange("abc", good) == APP
    assert good.calls[0][0] == ["api", "-X", "POST", "/app-manifests/abc/conversions"]
    bad = Fake(conversions=A.Done(0, PEM, ""))
    with pytest.raises(SystemExit) as caught:
        A.exchange("abc", bad)
    assert "secret-material" not in str(caught.value)


def test_the_key_reaches_gh_only_on_stdin_and_never_on_a_command_line():
    run = Fake()
    assert A.store_on_github("b1tninja-labs/projecthub", APP, run) == ""
    secret = next(c for c in run.calls if c[0][:2] == ["secret", "set"])
    assert secret[1] == PEM and secret[0][2] == A.SECRET_NAME and "--env" in secret[0]
    assert all("secret-material" not in " ".join(argv) for argv, _ in run.calls)
    variable = next(c for c in run.calls if c[0][:2] == ["variable", "set"])
    assert variable[0][-1] == "42"
    put = next(c for c in run.calls if "PUT" in c[0])
    assert (
        json.loads(put[1])["deployment_branch_policy"]["custom_branch_policies"] is True
    )


def test_the_environment_is_limited_to_main_once():
    run = Fake(deployment=A.Done(0, "main\n", ""))
    assert A.ensure_environment("o/r", run) == ""
    assert not any("name=main" in argv for argv, _ in run.calls), (
        "main is already allowed"
    )


def test_a_failed_secret_says_so_without_the_key():
    run = Fake(secret__set=A.Done(1, "", "HTTP 403"))
    problem = A.store_on_github("o/r", APP, run)
    assert "403" in problem and "secret-material" not in problem


@pytest.mark.skipif(
    sys.platform == "win32", reason="elsewhere there is no credential store to write"
)
def test_elsewhere_the_local_store_reports_why_not():
    assert "not available" in A.store_locally(PEM)


@pytest.mark.skipif(
    sys.platform != "win32", reason="the CREDENTIALW layout is a Windows structure"
)
def test_the_credential_structure_matches_windows():
    assert ctypes.sizeof(A._Credential) == 80, "CREDENTIALW on x64"
