"""Register the GitHub App from its manifest, and put its private key where the sync can use it.

GitHub requires a person to press "Create GitHub App" in a browser, so no workflow can do this alone. Everything
around that one click is automated by `projecthub app create`, run once by the owner:

1. A listener starts on the loopback interface and the browser opens a page that POSTs the manifest to GitHub.
2. The person presses Create. GitHub redirects to the listener with a one-time code, valid for an hour.
3. The code is exchanged for the App's id and private key.
4. The key is stored in two places and nowhere else: Windows Credential Manager, for the local sync, and a
   GitHub Actions secret in the `github-app` environment, for setup-project.yml. A GitHub secret cannot be read
   back outside a workflow, which is why the local copy exists.
5. The browser opens the App's install page, which also needs a person.

The private key is never printed, logged or written to a file. If a storing step fails, the command says which;
the recovery is to generate a new key on the App's settings page, because the conversion cannot be repeated.

    projecthub app create
"""

from __future__ import annotations

import argparse
import ctypes
import html
import http.server
import json
import pathlib
import secrets
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from projecthub import config

MANIFEST = pathlib.Path(__file__).parent / "data" / "app_manifest.json"
ENVIRONMENT = "github-app"
SECRET_NAME = "PROJECTHUB_APP_PRIVATE_KEY"
APP_ID_VARIABLE = "PROJECTHUB_APP_ID"
CREDENTIAL_TARGET = "projecthub/github-app/private-key"
CODE_LIFETIME = 3600.0


@dataclass(frozen=True)
class Done:
    code: int
    out: str
    err: str


Runner = Callable[[list[str], str | None], Done]


def gh(argv: list[str], stdin: str | None = None) -> Done:
    done = subprocess.run(  # noqa: S603, S607
        ["gh", *argv],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return Done(done.returncode, done.stdout, done.stderr)


def build_manifest(redirect_url: str) -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["redirect_url"] = redirect_url
    return manifest


def registration_url(org: str, state: str) -> str:
    return f"https://github.com/organizations/{urllib.parse.quote(org)}/settings/apps/new?state={state}"


def form_page(action: str, manifest: dict[str, Any]) -> str:
    """A page that posts the manifest to GitHub. GitHub accepts it only as a form POST from the browser."""
    return (
        "<!doctype html><title>Create the GitHub App</title>"
        f'<form method="post" action="{html.escape(action, quote=True)}">'
        f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest), quote=True)}">'
        '<button type="submit">Continue to GitHub</button></form>'
        "<script>document.forms[0].submit()</script>"
    )


def wait_for_code(
    make_page: Callable[[int], str],
    state: str,
    on_ready: Callable[[int], None],
    timeout: float = CODE_LIFETIME,
) -> str:
    """Serve the page (which depends on the port) until GitHub redirects back with this state; return the code."""
    received: list[str] = []
    pages: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the name http.server dispatches on
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/":
                self._send(200, pages[0])
                return
            query = urllib.parse.parse_qs(url.query)
            if (
                url.path == "/callback"
                and query.get("state") == [state]
                and query.get("code")
            ):
                received.append(query["code"][0])
                self._send(
                    200,
                    "<!doctype html><title>Done</title>The App was created. You can close this tab.",
                )
                return
            self._send(
                400, "<!doctype html><title>Refused</title>Not a callback for this run."
            )

        def _send(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the signature http.server calls
            """Silent: the request line holds the one-time code."""

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1.0
    try:
        port = server.server_address[1]
        pages.append(make_page(port))
        on_ready(port)
        deadline = time.monotonic() + timeout
        while not received and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not received:
        raise SystemExit(
            "error: no callback from GitHub within the hour; run the command again"
        )
    return received[0]


def exchange(code: str, run: Runner = gh) -> dict[str, Any]:
    """Trade the one-time code for the App. The reply holds the key, so stdout is never echoed."""
    done = run(["api", "-X", "POST", f"/app-manifests/{code}/conversions"], None)
    if done.code != 0:
        raise SystemExit(f"error: GitHub refused the code: {done.err.strip()[:300]}")
    try:
        app = json.loads(done.out)
        return {"id": int(app["id"]), "slug": str(app["slug"]), "pem": str(app["pem"])}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise SystemExit("error: GitHub's reply was not an App") from None


def ensure_environment(repo: str, run: Runner = gh) -> str:
    """The `github-app` environment, deployable only from main. Returns "" or why it could not be made."""
    base = f"repos/{repo}/environments/{ENVIRONMENT}"
    policy = json.dumps(
        {
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            }
        }
    )
    done = run(["api", "-X", "PUT", base, "--input", "-"], policy)
    if done.code != 0:
        return f"could not create the environment: {done.err.strip()[:300]}"
    listed = run(
        [
            "api",
            f"{base}/deployment-branch-policies",
            "--jq",
            ".branch_policies[].name",
        ],
        None,
    )
    if "main" not in listed.out.split():
        added = run(
            [
                "api",
                "-X",
                "POST",
                f"{base}/deployment-branch-policies",
                "-f",
                "name=main",
            ],
            None,
        )
        if added.code != 0:
            return f"could not limit the environment to main: {added.err.strip()[:300]}"
    return ""


def store_on_github(repo: str, app: dict[str, Any], run: Runner = gh) -> str:
    """The key as an environment secret and the id as a variable. Returns "" or why not; never the key."""
    problem = ensure_environment(repo, run)
    if problem:
        return problem
    done = run(
        ["secret", "set", SECRET_NAME, "--repo", repo, "--env", ENVIRONMENT], app["pem"]
    )
    if done.code != 0:
        return f"could not set the secret: {done.err.strip()[:300]}"
    done = run(
        [
            "variable",
            "set",
            APP_ID_VARIABLE,
            "--repo",
            repo,
            "--env",
            ENVIRONMENT,
            "--body",
            str(app["id"]),
        ],
        None,
    )
    if done.code != 0:
        return f"could not set the App id variable: {done.err.strip()[:300]}"
    return ""


class _Credential(ctypes.Structure):
    _fields_ = [  # noqa: RUF012 - ctypes reads this as a class attribute
        ("Flags", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", ctypes.c_uint64),
        ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", ctypes.c_uint32),
        ("AttributeCount", ctypes.c_uint32),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


def store_locally(pem: str) -> str:
    """The key in Windows Credential Manager, through the API so it is never on a command line.

    UTF-8, because the blob limit is 2,560 bytes and cmdkey's UTF-16 would not fit a 2048-bit key.
    Returns "" or why not.
    """
    if sys.platform != "win32":
        return "Windows Credential Manager is not available on this platform"
    blob = pem.encode("utf-8")
    buffer = ctypes.create_string_buffer(blob, len(blob))
    credential = _Credential(
        Type=CRED_TYPE_GENERIC,
        TargetName=CREDENTIAL_TARGET,
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
        Persist=CRED_PERSIST_LOCAL_MACHINE,
        UserName="github-app",
    )
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0):
        return f"CredWrite failed with Windows error {ctypes.GetLastError()}"
    return ""


def create(args: argparse.Namespace) -> int:
    state = secrets.token_urlsafe(32)

    def base(port: int) -> str:
        # The socket is bound to 127.0.0.1 only. The URL says localhost, as GitHub's own Probot framework does.
        return f"http://localhost:{port}"

    def make_page(port: int) -> str:
        return form_page(
            registration_url(args.org, state),
            build_manifest(f"{base(port)}/callback"),
        )

    def on_ready(port: int) -> None:
        print(f"Open {base(port)}/ and press Create GitHub App.", flush=True)
        if not args.no_browser:
            webbrowser.open(f"{base(port)}/")

    code = wait_for_code(make_page, state, on_ready)
    app = exchange(code)
    print(f"Created the App {app['slug']} (id {app['id']}).")
    failures = []
    for label, problem in (
        ("Windows Credential Manager", store_locally(app["pem"])),
        (f"GitHub secret on {args.repo}", store_on_github(args.repo, app)),
    ):
        print(f"  {label}: {problem or 'stored'}")
        if problem:
            failures.append(label)
    install = f"https://github.com/apps/{app['slug']}/installations/new"
    print(f"Install it (this needs a person): {install}")
    if not args.no_browser:
        webbrowser.open(install)
    if failures:
        settings = (
            f"https://github.com/organizations/{args.org}/settings/apps/{app['slug']}"
        )
        print(
            f"Not stored: {', '.join(failures)}. The key cannot be fetched again; generate a new one at {settings}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create", help="register the App from its manifest, once")
    make.add_argument("--org", default=config.PROJECT_OWNER)
    make.add_argument(
        "--repo",
        default=config.PORTFOLIO_REPO,
        help="where setup-project.yml's secret goes",
    )
    make.add_argument(
        "--no-browser",
        action="store_true",
        help="print the URLs instead of opening them",
    )
    args = parser.parse_args(argv)
    return create(args)


if __name__ == "__main__":
    raise SystemExit(main())
