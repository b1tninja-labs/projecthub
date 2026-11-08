"""Scoped mutation: an accept-only test kills no refusal mutant, a refusal test does, and the source survives."""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from projecthub.evidence import mutate as M

VERIFIER = '''\
def _even(value: int) -> bool:
    return value % 2 == 0


def unrelated(value: int) -> bool:
    if value > 100:
        return False
    return True


def verify(value: int, limit: int) -> bool:
    """True only for an even value in 0..limit."""
    if value < 0:
        return False
    if limit is None or limit > 10**6:
        raise ValueError("limit")
    return value <= limit and _even(value)


def countdown(n: int) -> int:
    if n < 0:
        raise ValueError("negative")
    while n != 0:
        n -= 1
    return n
'''
CONFTEST = (
    "import pathlib, sys\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
)


def _tree(tmp_path: pathlib.Path, tests: str) -> pathlib.Path:
    root = tmp_path / "vf"
    (root / "vf").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (root / "vf" / "__init__.py").write_text("", encoding="utf-8")
    (root / "vf" / "check.py").write_text(VERIFIER, encoding="utf-8")
    (root / "tests" / "test_check.py").write_text(
        "import pytest\nfrom vf.check import verify, countdown\n\n"
        + textwrap.dedent(tests),
        encoding="utf-8",
    )
    return root


ACCEPT_ONLY = """
def test_honest():
    assert verify(4, 10)
"""
REFUSALS = """
def test_honest():
    assert verify(4, 10)

def test_refusals():
    assert not verify(-2, 10)
    assert not verify(12, 10)
    assert not verify(3, 10)
"""


def test_the_operators_weaken_refusals_and_result_is_only_for_the_rows_own_function():
    planted = M.mutants(VERIFIER, "verify", ["verify", "_even", "unrelated"])
    seen = {(m.function, m.operator) for m in planted}
    assert {
        ("verify", op) for op in ("guard", "accept", "boundary", "weaken", "result")
    } <= seen
    assert ("_even", "result") not in seen, (
        "a callee's answer is the row function's business, not L4's"
    )
    assert ("unrelated", "accept") in seen
    assert len({m.source for m in planted}) == len(planted), (
        "no two mutants are the same program"
    )
    guard = next(m for m in planted if m.operator == "guard" and m.line == 13)
    assert (
        "if value < 0" not in guard.source and "return value <= limit" in guard.source
    )


def test_an_accept_only_test_kills_no_refusal_mutant(tmp_path):
    root = _tree(tmp_path, ACCEPT_ONLY)
    got = M.audit(
        root, "vf/check.py", "verify", ["tests/test_check.py::test_honest"], tmp_path
    )
    assert got["ok"] is False and got["killed"] == 0 and got["total"] > 0, got
    assert got["scope"] == ["verify", "_even"], (
        "the scope is the row's function plus what the test reached"
    )
    outcomes = {(m["line"], m["operator"]): m["outcome"] for m in got["mutants"]}
    assert outcomes[(14, "accept")] == "unreached", (
        "a line the test never ran is not run as a mutant"
    )


def test_refusal_tests_kill_and_name_the_survivor(tmp_path):
    root = _tree(tmp_path, REFUSALS)
    got = M.audit(root, "vf/check.py", "verify", ["tests/test_check.py"], tmp_path)
    assert got["ok"] is True and got["killed"] >= 3, got
    survivors = {m["description"] for m in got["mutants"] if m["outcome"] == "survived"}
    assert any("value <= limit" in s for s in survivors), (
        "verify(10, 10) is never tried: the boundary is open"
    )
    assert (root / "vf" / "check.py").read_text(encoding="utf-8") == VERIFIER, (
        "the source is restored"
    )


def test_widening_the_killing_tests_does_not_widen_the_scope(tmp_path):
    root = _tree(
        tmp_path,
        REFUSALS
        + "\ndef test_other():\n    from vf.check import unrelated\n    assert unrelated(1)\n",
    )
    got = M.audit(
        root,
        "vf/check.py",
        "verify",
        ["tests/test_check.py"],
        tmp_path,
        scope_tests=["tests/test_check.py::test_refusals"],
    )
    assert "unrelated" not in got["scope"], got["scope"]
    assert got["reach"]["by"] == ["tests/test_check.py::test_refusals"]


def test_a_mutant_that_hangs_is_killed_by_its_timeout(tmp_path):
    root = _tree(
        tmp_path,
        "def test_refuses():\n    with pytest.raises(ValueError):\n        countdown(-1)\n",
    )
    got = M.audit(
        root, "vf/check.py", "countdown", ["tests/test_check.py"], tmp_path, timeout=5
    )
    by = {m["operator"]: m["outcome"] for m in got["mutants"]}
    assert by["guard"] == "timeout", got["mutants"]
    assert got["ok"] is True


def test_a_failing_baseline_or_unknown_function_is_an_error_not_a_score(tmp_path):
    root = _tree(tmp_path, "def test_broken():\n    assert verify(3, 10)\n")
    got = M.audit(root, "vf/check.py", "verify", ["tests/test_check.py"], tmp_path)
    assert got == {
        "error": "the tests do not pass on the unmutated source",
        "ok": False,
    }
    assert (
        M.audit(root, "vf/check.py", "nope", [], tmp_path)["error"]
        == "vf/check.py defines no nope"
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ("check.verify", ("vf/check.py", "verify")),
        ("check.multi-path", ("vf/check.py", "multi_path")),
        ("gone.x", None),
    ],
)
def test_a_row_id_locates_its_module_and_function(tmp_path, row, expected):
    root = _tree(tmp_path, ACCEPT_ONLY)
    assert M.locate(root, row) == expected
