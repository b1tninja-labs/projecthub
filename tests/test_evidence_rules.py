"""The evidence check's rules for rows that do not fit "one named test, one function, code in this commit":
the test after `Test:` is the named one, another cited test may prove a change, a concept row skips L4, and a
sibling checkout is extracted beside the tree under test."""

from __future__ import annotations

import pathlib

import test_evidence_check as T

from projecthub.db import parser as roadmap
from projecthub.evidence import check as E

TWO_TESTS = """# demo

| Obligation | Spec | Status | Where |
|---|---|---|---|
| `core.double` | RFC 0000 | {status} | doubling; the halving case is `tests/test_demo.py::test_half`. Test: `tests/test_demo.py::test_double` |
"""
CONCEPT = """# demo

| Obligation | Spec | Status | Where |
|---|---|---|---|
| `core.concept` | RFC 0000 | {status} | Test: `tests/test_demo.py::test_double` |
"""


def test_the_reference_after_test_label_is_the_named_test(tmp_path):
    (tmp_path / "demo" / "tests").mkdir(parents=True)
    (tmp_path / "demo" / "tests" / "test_a.py").write_text(
        "def test_mine():\n    assert 1 == 1\n", encoding="utf-8"
    )
    where = (
        "the key arrives through asn1core (pinned there by `tests/test_other.py::test_theirs`). "
        "Test: `tests/test_a.py::test_mine`"
    )
    assert roadmap.named_test(tmp_path, "demo", where) == (
        "tests/test_a.py::test_mine",
        "exists",
    )
    assert roadmap.cited_tests(tmp_path, "demo", where) == [
        "tests/test_a.py::test_mine"
    ]


def test_a_new_cited_test_proves_a_row_whose_named_test_is_unchanged(tmp_path):
    root = _repo(tmp_path, TWO_TESTS)
    T._commit(
        root,
        {
            "demo/core.py": T.REAL + "\ndef half(n):\n    raise NotImplementedError\n",
            "tests/test_demo.py": "from demo.core import double\n\ndef test_double():\n    assert double(2) == 4\n",
        },
        "the row's named test exists and passes",
    )
    T._commit(
        root,
        {
            "demo/core.py": T.REAL + "\ndef half(n):\n    return n // 2\n",
            "tests/test_demo.py": (
                "from demo.core import double, half\n\ndef test_double():\n    assert double(2) == 4\n\n"
                "def test_half():\n    assert half(4) == 2\n"
            ),
            "ROADMAP.md": TWO_TESTS.format(status="[ok]"),
        },
        "a new behaviour with its own test",
    )
    (row,) = E.check(root, "demo", base_ref="HEAD~1", mutate=False)["rows"]
    assert row["ok"] is True, row
    assert row["l3"]["proved_by"] == "tests/test_demo.py::test_half"
    assert row["l3"]["tried"][0]["reason"] == E.ALREADY_PASSES


def test_a_concept_row_with_no_function_skips_l4_and_stands_on_l1_and_l3(tmp_path):
    root = _repo(tmp_path, CONCEPT)
    T._commit(
        root,
        {
            "demo/core.py": T.REAL,
            "tests/test_demo.py": "from demo import double\n\ndef test_double():\n    assert double(21) == 42\n",
            "ROADMAP.md": CONCEPT.format(status="[ok]"),
        },
        "flip",
    )
    (row,) = E.check(root, "demo", base_ref="HEAD~1")["rows"]
    assert row["ok"] is True, row
    assert row["l4"]["ok"] is None and "no module.function" in row["l4"]["skipped"]


def test_a_sibling_is_extracted_beside_the_tree_under_test(tmp_path):
    sibling = tmp_path / "helper"
    sibling.mkdir()
    T._git(sibling, "init", "-q")
    T._commit(sibling, {"VALUE": "42\n"}, "sibling")
    root = _repo(tmp_path, T.MATRIX)
    T._flip(
        root,
        "import pathlib\nfrom demo import double\n\n"
        "def test_double():\n"
        "    value = int((pathlib.Path(__file__).parents[2] / 'helper' / 'VALUE').read_text())\n"
        "    assert double(21) == value\n",
    )
    without = E.check(root, "demo", base_ref="HEAD~1", mutate=False)["rows"][0]
    assert without["ok"] is False
    (row,) = E.check(root, "demo", base_ref="HEAD~1", mutate=False, siblings=[sibling])[
        "rows"
    ]
    assert row["ok"] is True, row


def _repo(tmp_path: pathlib.Path, matrix: str) -> pathlib.Path:
    root = tmp_path / "demo"
    root.mkdir()
    T._git(root, "init", "-q")
    T._commit(
        root,
        {
            "pyproject.toml": "[project]\nname = 'demo'\n",
            "conftest.py": T.CONFTEST,
            "demo/__init__.py": "from demo.core import double\n",
            "demo/core.py": T.STUB,
            "ROADMAP.md": matrix.format(status="[ ]"),
            "tests/__init__.py": "",
        },
        "base",
    )
    return root


NOTES = """# demo

| Obligation | Spec | Status | Notes |
|---|---|---|---|
| `core.double` | RFC 0000 | {status} | `tests/test_demo.py::test_double` |
"""


def test_a_row_testless_at_the_commit_takes_its_evidence_from_the_branch_tip(tmp_path):
    """asn1core headed its evidence column "Notes" until a later fix: rows read as testless at every older sha."""
    root = _repo(tmp_path, NOTES)
    T._commit(
        root,
        {
            "demo/core.py": T.REAL,
            "tests/test_demo.py": "from demo import double\n\ndef test_double():\n    assert double(21) == 42\n",
            "ROADMAP.md": NOTES.format(status="[ok]"),
        },
        "flip under the old header",
    )
    flipped = T._git(root, "rev-parse", "HEAD")
    before = E.check(root, "demo", f"{flipped}~1", flipped, mutate=False)["rows"][0]
    assert before["ok"] is False and "none" in before["reason"]
    T._commit(root, {"ROADMAP.md": T.MATRIX.format(status="[ok]")}, "header fixed")
    (row,) = E.check(root, "demo", f"{flipped}~1", flipped, mutate=False)["rows"]
    assert row["ok"] is True, row
    assert row["l1"]["evidence_from"] == "branch tip"
