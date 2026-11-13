"""gh's coloured output must reach the client as plain text.

Carried from sdk-forge's test_ghprojects.py, where these were written against the defect: they run the real
`gh_runner` with this interpreter standing in for a gh that decorates its output, because every other client
test injects a fake and the defect lives in exactly what the fake replaces.
"""

from __future__ import annotations

import json
import sys

import pytest

from projecthub.github import client as g


@pytest.fixture(autouse=True)
def _python_stands_in_for_gh(monkeypatch):
    """`gh_runner` resolves gh on PATH; here this interpreter is gh, and each test starts undetected."""
    monkeypatch.setattr(g.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(g, "_PLAIN_ARGS", None)


def test_a_decorated_gh_stdout_still_reads_as_json():
    """The real `gh_runner` must hand its caller parseable text, not a terminal's decoration.

    Every read on this client goes through one of two conversions of `Completed.out`: `json.loads` in
    `graphql_partial`, and the line-by-line `X-OAuth-Scopes` scan in `scopes`. A decorated stdout breaks
    both, and what the breakage looks like is what makes it expensive: `json.loads` raises
    `JSONDecodeError`, which becomes "no usable response", so a board full of rows reads as an
    unreachable API.

    This runs the real runner rather than a scripted one — every other test here injects a fake, and the
    defect lives precisely in what the fake stands in for: the bytes `subprocess.run` collects from gh's
    stdout. So the child is this interpreter replaying the decoration a decorated gh emits. The escape
    bytes are built from their code point so no editor or formatter can fold them into something inert.
    """
    esc = chr(27)
    body = json.dumps({"data": {"viewer": {"login": "me"}}})
    script = (
        "import sys\n"
        f"sys.stdout.write({esc!r} + '[32m' + {body!r} + {esc!r} + '[0m')\n"
        f"sys.stderr.write({esc!r} + '[31m' + 'gh: note' + {esc!r} + '[0m')\n"
    )
    done = g.gh_runner(["-B", "-c", script], None)

    assert done.code == 0, f"the stand-in gh did not run: {done.err!r}"
    assert g._PLAIN_ARGS == g.PLAIN_ENV, (
        "the runner saw no decoration to strip, so this proves nothing"
    )

    payload = json.loads(done.out)
    assert payload["data"]["viewer"]["login"] == "me"
    assert esc not in done.out, "decoration reached the caller's json.loads"
    assert esc not in done.err


def test_the_real_scopes_scan_survives_a_decorated_header():
    """`scopes()` reads `out` line by line, so decoration there loses the grant rather than raising.

    A decorated header line no longer starts with `x-oauth-scopes`, the scan finds nothing and returns
    None — "unknown", which `require` treats as permitted. The API then answers INSUFFICIENT_SCOPES and
    `_error` calls `scopes()` again to name what the token has, so the error reads "(has: none)" beside
    `gh auth refresh -s project` for a token that does hold the scope. That is the loop which kept a run
    refreshing credentials it never needed to touch.
    """
    esc = chr(27)
    script = (
        "import sys\n"
        f"sys.stdout.write('HTTP/2.0 200 OK\\n' + {esc!r} + '[1m' + 'X-OAuth-Scopes' + {esc!r} + '[0m' "
        f"+ ': repo, project\\n\\n{{}}')\n"
    )
    argv = ["-B", "-c", script]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(g.shutil, "which", lambda name: sys.executable)
        granted = g.Projects(lambda a, s=None: g.gh_runner(argv, s)).scopes()

    assert granted == ("repo", "project")
