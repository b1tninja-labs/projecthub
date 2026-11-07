"""roadmap-evidence: an [ok] row's test must assert, fail on the old source and pass on the new."""

from __future__ import annotations

import ast
import pathlib
import subprocess
import textwrap

import pytest

import evidence_check as E

MATRIX = """# demo

| Obligation | Spec | Status | Where |
|---|---|---|---|
| `core.double` | RFC 0000 | {status} | `tests/test_demo.py::test_double` |
"""
STUB = "def double(n):\n    raise NotImplementedError\n"
REAL = "def double(n):\n    return 2 * n\n"
CONFTEST = (
    "import pathlib, sys\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
)


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


@pytest.fixture
def repo(tmp_path):
    """A one-row project whose base has the stub, the row open and no test yet."""
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-q")
    base = _commit(
        root,
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
    return root, base


def _flip(root: pathlib.Path, test_body: str, source: str = REAL) -> None:
    _commit(
        root,
        {
            "demo/core.py": source,
            "ROADMAP.md": MATRIX.format(status="[ok]"),
            "tests/test_demo.py": textwrap.dedent(test_body),
        },
        "flip",
    )


def test_an_assert_true_test_is_rejected_at_l1(repo):
    root, base = repo
    _flip(
        root,
        "from demo import double\n\ndef test_double():\n    double(2)\n    assert True\n",
    )
    record = E.check(root, "demo", base)
    assert record["audited"] == ["core.double"]
    (row,) = record["rows"]
    assert row["ok"] is False and record["ok"] is False
    assert row["reason"].startswith("L1: every assert has a constant condition"), row
    assert row["l3"] == {}, (
        "L3 is not run for a test that cannot fail on a wrong answer"
    )


def test_head_tests_on_base_fail_then_pass_on_head_is_accepted(repo):
    root, base = repo
    _flip(
        root,
        "from demo import double\n\ndef test_double():\n    assert double(21) == 42\n",
    )
    record = E.check(root, "demo", base)
    (row,) = record["rows"]
    assert row["ok"] is True, row
    assert row["l1"]["ok"] and row["l3"]["base"]["outcome"] == "failed"
    assert "NotImplementedError" in row["l3"]["base"]["message"]
    assert row["l3"]["head"]["outcome"] == "passed"
    assert record["ok"] is True


def test_a_test_that_already_passes_on_base_is_rejected(repo):
    root, base = repo
    _commit(root, {"demo/core.py": REAL}, "the code lands without its row")
    _flip(
        root,
        "from demo import double\n\ndef test_double():\n    assert double(1) == 2\n",
    )
    (row,) = E.check(root, "demo", base_ref="HEAD~1")["rows"]
    assert row["ok"] is False
    assert "already passes on base" in row["reason"]


def test_an_import_error_on_base_is_not_a_behavioural_failure(repo):
    root, base = repo
    _flip(
        root,
        "def test_double():\n    from demo.extra import triple\n    assert triple(1) == 3\n",
        source=REAL,
    )
    _commit(root, {"demo/extra.py": "def triple(n):\n    return 3 * n\n"}, "extra")
    (row,) = E.check(root, "demo", base)["rows"]
    assert row["ok"] is False
    assert "import error" in row["reason"], row


def test_a_module_level_import_of_missing_code_is_a_collection_error_on_base(repo):
    root, base = repo
    _commit(root, {"demo/extra.py": "def triple(n):\n    return 3 * n\n"}, "extra")
    _flip(
        root,
        "from demo.extra import triple\n\ndef test_double():\n    assert triple(1) == 3\n",
    )
    (row,) = E.check(root, "demo", base)["rows"]
    assert row["ok"] is False and row["l3"]["base"]["outcome"] == "error"
    assert "rather than fails" in row["reason"]


def test_no_flip_means_nothing_to_audit_and_unchanged_rows_are_not_rechecked(repo):
    root, base = repo
    _commit(root, {"README.md": "docs only\n"}, "docs")
    record = E.check(root, "demo", base)
    assert record["audited"] == [] and record["rows"] == [] and record["ok"] is True


def test_a_row_flipped_without_a_named_test_fails(repo):
    root, base = repo
    _commit(
        root,
        {"ROADMAP.md": MATRIX.format(status="[ok]"), "demo/core.py": REAL},
        "flip, no test",
    )
    (row,) = E.check(root, "demo", base)["rows"]
    assert row["ok"] is False and "no collectable named test (missing)" in row["reason"]


def test_the_check_writes_nothing_into_the_repository(repo):
    root, base = repo
    _flip(
        root,
        "from demo import double\n\ndef test_double():\n    assert double(21) == 42\n",
    )
    E.check(root, "demo", base)
    assert _git(root, "status", "--porcelain", "--ignored") == ""
    assert _git(root, "worktree", "list").count("\n") == 0


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("assert x == 1", True),
        ("assert True", False),
        ("assert 1 == 1", False),
        ("assert (x, 'always true')", False),
        ("with pytest.raises(ValueError):\n        f()", True),
        ("pytest.fail('no')", True),
        ("self.assertEqual(a, b)", True),
        ("np.testing.assert_array_equal(a, b)", True),
        ("_check(x)", True),
        ("_quiet(x)", False),
        ("f(x)", False),
    ],
)
def test_l1_recognises_what_asserts(body, expected):
    source = (
        "import pytest\n\ndef _check(v):\n    assert v > 0\n\ndef _quiet(v):\n    return v\n\n"
        f"def test_it():\n    {body}\n"
    )
    ok, why = E.asserts(ast.parse(source), ["test_it"])
    assert ok is expected, why


def test_l1_reads_a_method_inside_a_test_class():
    source = "class TestX:\n    def test_a(self):\n        assert self.v == 2\n"
    assert E.asserts(ast.parse(source), ["TestX", "test_a"])[0] is True
    assert E.asserts(ast.parse(source), ["TestX", "test_b"]) == (
        False,
        "defines no TestX::test_b",
    )


def test_the_cli_refuses_a_record_path_inside_a_work_tree(repo, capsys):
    root, base = repo
    assert (
        E.main(
            [
                "--repo",
                str(root),
                "--project",
                "demo",
                "--base",
                base,
                "--json",
                str(root / "r.json"),
            ]
        )
        == 2
    )
    assert "inside the git work tree" in capsys.readouterr().err


VERIFIER = "def double(n) -> bool:\n    if n < 0:\n        return False\n    return n % 2 == 0\n"


def test_a_test_that_never_tries_a_refusal_fails_at_l4(repo):
    root, base = repo
    _flip(
        root,
        "from demo import double\n\ndef test_double():\n    assert double(4)\n",
        source=VERIFIER,
    )
    (row,) = E.check(root, "demo", base)["rows"]
    assert row["l3"]["ok"] is True
    assert row["ok"] is False and row["l4"]["killed"] == 0, row["l4"]
    assert row["reason"].startswith("L4: the named test kills 0 of"), row["reason"]


def test_l4_passes_with_a_kill_and_can_be_switched_off(repo):
    root, base = repo
    _flip(
        root,
        "from demo import double\n\ndef test_double():\n    assert double(4)\n    assert double(-2) is False\n",
        source=VERIFIER,
    )
    (row,) = E.check(root, "demo", base)["rows"]
    assert row["ok"] is True and row["l4"]["killed"] >= 1, row["l4"]
    (off,) = E.check(root, "demo", base, mutate=False)["rows"]
    assert off["ok"] is True and off["l4"] == {}
