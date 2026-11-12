"""The evidence worker: a queued submission is checked once and ends passed or failed, never in between."""

from __future__ import annotations

import pathlib
import sqlite3
import subprocess
import textwrap
import time

import pytest

from projecthub.evidence import worker as W

MATRIX = """# demo

| Obligation | Spec | Status | Where |
|---|---|---|---|
| `core.double` | RFC 0000 | {status} | `tests/test_demo.py::test_double` |
"""
STUB = "def double(n):\n    raise NotImplementedError\n"
VERIFIER = "def double(n) -> bool:\n    if n < 0:\n        return False\n    return n % 2 == 0\n"
CONFTEST = (
    "import pathlib, sys\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
)
GOOD = "from demo import double\n\ndef test_double():\n    assert double(4)\n    assert double(-2) is False\n"
HOLLOW = (
    "from demo import double\n\ndef test_double():\n    double(4)\n    assert True\n"
)
KEY = "demo:core.double"


def _git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _commit(repo: pathlib.Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _flip(repo: pathlib.Path, test_body: str) -> str:
    return _commit(
        repo,
        {
            "demo/core.py": VERIFIER,
            "ROADMAP.md": MATRIX.format(status="[ok]"),
            "tests/test_demo.py": textwrap.dedent(test_body),
        },
        "flip",
    )


@pytest.fixture
def ws(tmp_path):
    """A workspace root holding one project, `demo`, whose main has the stub and the row open."""
    root = tmp_path / "ws"
    repo = root / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(
        repo,
        {
            "pyproject.toml": "[project]\nname = 'demo'\n",
            "conftest.py": CONFTEST,
            "demo/__init__.py": "from demo.core import double\n",
            "demo/core.py": STUB,
            "ROADMAP.md": MATRIX.format(status="[ ]"),
            "tests/__init__.py": "",
        },
        "base",
    )
    db_path = tmp_path / "state" / "leases.sqlite"
    return root, repo, base, db_path


def _submit(db_path: pathlib.Path, sha: str, lease_id: str = "", key: str = KEY) -> int:
    db = W._connect(db_path)
    try:
        cursor = db.execute(
            "INSERT INTO submissions (row_key, sha, lease_id, at, state) VALUES (?, ?, ?, ?, 'queued')",
            (key, sha, lease_id or None, time.time()),
        )
        return int(cursor.lastrowid or 0)
    finally:
        db.close()


def _state(db_path: pathlib.Path, sid: int) -> str:
    db = sqlite3.connect(db_path)
    try:
        return str(
            db.execute("SELECT state FROM submissions WHERE id=?", (sid,)).fetchone()[0]
        )
    finally:
        db.close()


def _lease(db_path: pathlib.Path, lease_id: str, branch: str) -> None:
    db = W._connect(db_path)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS leases (lease_id TEXT PRIMARY KEY, row_key TEXT, branch TEXT)"
        )
        db.execute("INSERT INTO leases VALUES (?, ?, ?)", (lease_id, KEY, branch))
        db.commit()
    finally:
        db.close()


def test_a_good_test_on_the_lease_branch_goes_queued_running_passed(ws):
    root, repo, base, db_path = ws
    _git(repo, "switch", "-q", "-c", "agents/double")
    sha = _flip(repo, GOOD)
    _git(repo, "switch", "-q", "main")
    _commit(repo, {"README.md": "main moved on\n"}, "unrelated")
    _lease(db_path, "L1", "agents/double")
    sid = _submit(db_path, sha, "L1")
    assert _state(db_path, sid) == "queued"
    seen: list[str] = []

    def watching(*args):
        seen.append(_state(db_path, sid))
        return W.check_in_child(*args)

    (verdict,) = W.run_once(db_path, root, checker=watching)
    assert seen == ["running"]
    assert verdict.ok is True, verdict.reasons
    assert _state(db_path, sid) == "passed"
    stored = W.verdict_for(db_path, KEY)
    assert stored is not None
    assert stored["ok"] is True and stored["submission_id"] == sid
    assert stored["sha"] == sha and stored["base"] == base, "base is the merge-base"
    assert stored["l1"]["ok"] and stored["l3"]["ok"]
    assert stored["l4"]["killed"] >= 1, "mutation is on"
    assert stored["reasons"] == []


def test_an_assert_true_test_fails_at_l1(ws):
    root, repo, base, db_path = ws
    sha = _flip(repo, HOLLOW)
    sid = _submit(db_path, sha)
    (verdict,) = W.run_once(db_path, root)
    assert verdict.ok is False and _state(db_path, sid) == "failed"
    stored = W.verdict_for(db_path, KEY)
    assert stored is not None and stored["ok"] is False
    assert stored["base"] == base, "a commit on main is measured against its parent"
    assert stored["reasons"][0].startswith("L1: every assert has a constant condition")
    assert stored["l1"]["ok"] is False and stored["l3"] == {}


def test_an_unknown_sha_fails_without_running_the_check(ws):
    root, _, _, db_path = ws
    sid = _submit(db_path, "0123456789abcdef0123456789abcdef01234567")
    ran = []
    (verdict,) = W.run_once(db_path, root, checker=lambda *a: ran.append(a) or {})
    assert ran == [] and _state(db_path, sid) == "failed"
    assert "is not a commit" in verdict.reasons[0]


def test_a_sha_on_a_branch_no_lease_names_is_refused(ws):
    root, repo, _, db_path = ws
    _git(repo, "switch", "-q", "-c", "elsewhere")
    sha = _flip(repo, GOOD)
    _git(repo, "switch", "-q", "main")
    sid = _submit(db_path, sha)
    (verdict,) = W.run_once(db_path, root, checker=lambda *a: {"ok": True})
    assert _state(db_path, sid) == "failed"
    assert "is not reachable from heads/main" in verdict.reasons[0]


@pytest.mark.parametrize(
    ("key", "sha", "expected"),
    [
        ("demo", "abc1234", "is not a project:row key"),
        ("../x:core.double", "abc1234", "is not a project:row key"),
        (KEY, "--output=x", "is not a commit id"),
        ("absent:core.double", "abc1234", "no checkout of absent"),
    ],
)
def test_malformed_submissions_are_refused(ws, key, sha, expected):
    root, _, _, db_path = ws
    _submit(db_path, sha, key=key)
    (verdict,) = W.run_once(db_path, root)
    assert verdict.ok is False and expected in verdict.reasons[0]


def test_a_check_that_raises_leaves_the_database_consistent(ws):
    root, repo, _, db_path = ws
    sha = _flip(repo, GOOD)
    first, second = _submit(db_path, sha), _submit(db_path, sha)

    def boom(*args):
        raise RuntimeError("the runner fell over")

    verdicts = W.run_once(db_path, root, limit=None, checker=boom)
    assert [v.submission_id for v in verdicts] == [first, second], "oldest first"
    assert _state(db_path, first) == _state(db_path, second) == "failed"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT COUNT(*) FROM submissions WHERE state IN ('queued', 'running')"
            ).fetchone()[0]
            == 0
        )
    finally:
        db.close()
    stored = W.verdict_for(db_path, KEY)
    assert stored is not None and stored["submission_id"] == second
    assert stored["reasons"] == ["crash: RuntimeError: the runner fell over"]


def test_a_timeout_fails_the_submission_with_the_reason(ws):
    root, repo, _, db_path = ws
    sha = _flip(repo, GOOD)
    sid = _submit(db_path, sha)
    (verdict,) = W.run_once(db_path, root, timeout=0.01)
    assert verdict.ok is False and _state(db_path, sid) == "failed"
    assert verdict.reasons[0].startswith("timeout: the check timed out")


@pytest.mark.parametrize(
    "record",
    [
        {"ok": True, "rows": []},
        {"ok": False, "rows": [{"row": KEY, "ok": True}]},
        {"ok": True, "rows": [{"row": "demo:other", "ok": True}]},
        {"ok": True, "head": "f" * 40, "rows": [{"row": KEY, "ok": True}]},
    ],
)
def test_never_passed_without_the_checks_ok_for_this_row_at_this_sha(ws, record):
    root, repo, _, db_path = ws
    sha = _flip(repo, GOOD)
    sid = _submit(db_path, sha)
    record = {"head": sha, **record}
    (verdict,) = W.run_once(db_path, root, checker=lambda *a: record)
    assert verdict.ok is False and _state(db_path, sid) == "failed"
    assert verdict.reasons


def test_no_queued_submission_is_not_an_error(ws, capsys):
    root, _, _, db_path = ws
    assert W.run_once(db_path, root) == []
    assert W.verdict_for(db_path, KEY) is None
    assert W.main(["--once", "--root", str(root), "--db", str(db_path)]) == 0
    assert "no queued submission" in capsys.readouterr().out


def test_the_database_path_prefers_the_projecthub_name(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_LEASES_DB", str(tmp_path / "old.sqlite"))
    monkeypatch.delenv("PROJECTHUB_LEASES_DB", raising=False)
    assert W.default_db() == tmp_path / "old.sqlite"
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(tmp_path / "new.sqlite"))
    assert W.default_db() == tmp_path / "new.sqlite"
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path / "a"))
    monkeypatch.setenv("PROJECTHUB_ROOT", str(tmp_path / "b"))
    assert W.default_root() == tmp_path / "b"
