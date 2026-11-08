"""The one roadmap grammar. Fixtures are built in tmp; one test reads the real workspace when it is present.

What these pin is what the three earlier readers got wrong: rows read as headers, an emoji matrix parsed
to nothing, a dotted id dropped without a word, an unknown status skipped silently, and an evidence check
that a longer function name could satisfy.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from planforge.db import parser as r
from planforge.data import PROJECTS

MATRIX = """# Roadmap

| Obligation | Spec | Status | Where | Milestone |
|---|---|---|---|---|
| `anchors.load()` | RFC 5280 sec. 6.1 | `[ok]` | `tests/test_a.py::test_load` | M1 |
| `names.check()` | RFC 5280 | `[part]` | `tests/test_a.py::test_check` | M2 |
| `kp.verify.raw` | Model I3 | [ ] | `verify` | - |

| Document | Status | Published | Note |
|---|---|---|---|
| `RFC 5280` | current | 2008 | not a matrix, so not a row |
"""

EMOJI = """| Obligation | Spec | Status | Where |
|---|---|---|---|
| Header fields, all 12 octets | 1035 §4.1.1 | ✅ | `test_packet_wire.py` |
| `TC=1` re-asked over TCP | 1123 §6.1.3.2 | 🟡 | `test_resolver_truncation.py` |
| Cache persisted | design | ✅ (storage) | `test_cache.py` |
| Zone transfer | 5936 | ⬜ | - |
| Recursion rate limiting | none | ▫️ | - |
"""


def test_statuses_and_their_aliases_normalise_to_the_keys_callers_already_use():
    assert r.status_of("`[ok]`") == ("[ok]", "")
    assert r.status_of("[x]") == ("[x]", "")
    assert r.status_of("partial") == ("[part]", "")
    assert r.status_of("`[ ]`") == ("[ ]", "")
    assert r.status_of("✅ (storage)") == ("[ok]", "(storage)")
    assert r.status_of("▫️") == ("[no]", "")
    assert r.status_of("current") is None
    assert r.status_of("[done]") is None
    assert r.status_of("[okay]") is None, "a token prefix is not a status"


def test_columns_are_found_by_header_and_a_milestone_is_not_evidence():
    rows = r.rows_of(MATRIX)
    assert [(x["name"], x["status"], x["milestone"], x["evidence"]) for x in rows] == [
        ("anchors.load", "[ok]", "M1", ""),
        ("names.check", "[part]", "M2", ""),
        ("kp.verify.raw", "[ ]", "", ""),
    ]
    assert r.parse(MATRIX).problems == []


def test_a_dotted_id_is_a_row_rather_than_silently_dropped():
    assert "kp.verify.raw" in [x["name"] for x in r.rows_of(MATRIX)]


def test_an_evidence_column_is_found_by_header():
    table = "| Id | Spec | Status | Where | Evidence |\n|---|---|---|---|---|\n| `cl.ambient-trust` | Model I1 | [x] | `a.py` | `test_x` |\n"
    (row,) = r.rows_of(table)
    assert row["evidence"] == "`test_x`" and row["state"] == "done"


def test_an_emoji_matrix_with_prose_obligations_parses_without_inventing_ids():
    matrix = r.parse(EMOJI)
    assert matrix.problems == []
    assert [x["status"] for x in matrix.rows] == [
        "[ok]",
        "[part]",
        "[ok]",
        "[ ]",
        "[no]",
    ]
    assert all(x["name"] == "" for x in matrix.rows), (
        "prose is a title, never a made-up id"
    )
    assert matrix.rows[1]["title"] == "`TC=1` re-asked over TCP"
    assert matrix.rows[2]["status_note"] == "(storage)"
    assert r.rows_of(EMOJI) == [], (
        "rows_of keeps the id-only contract sync_roadmap relies on"
    )


def test_a_line_that_looks_like_a_row_but_does_not_parse_is_reported():
    text = (
        "| Obligation | Spec | Status | Where |\n|---|---|---|---|\n"
        "| `a.b()` | s | `[done]` | w |\n"
        "| `Not An Id` | s | [ ] | w |\n"
        "| `c.d()` | s |\n"
        "| `e.f()` | s | [ ] | w |\n"
    )
    matrix = r.parse(text)
    assert [x["name"] for x in matrix.rows] == ["e.f"]
    assert [p.line for p in matrix.problems] == [3, 4, 5]
    assert "not a recognised status" in matrix.problems[0].reason
    assert "not an id" in matrix.problems[1].reason
    assert "2 cells" in matrix.problems[2].reason


def test_headerless_snippets_keep_the_historical_positions():
    pkix = "| `anchors.load_anchor()` | RFC 5280 sec. 6.1 | `[ ]` | `tests/test_obligations.py::load_anchor` |"
    ledger = "| `cl.ambient-trust` | Model I1 | `[x]` | `anchors.py`, `context.py` | `test_environment_is_ignored` |"
    (row,) = r.rows_of(pkix)
    assert (row["name"], row["title"], row["status"], row["evidence"]) == (
        "anchors.load_anchor",
        "anchors.load_anchor()",
        "[ ]",
        "",
    )
    assert r.rows_of(ledger)[0]["evidence"] == "`test_environment_is_ignored`"
    not_a_row = "| Non-goal | Status | Revisit when |\n| a certificate parser | `[no]` | a named document requires it |"
    assert r.rows_of(not_a_row) == [] and r.rows_of("") == []


def test_an_escaped_pipe_does_not_split_a_cell():
    text = "| Id | Spec | Status | Where |\n|---|---|---|---|\n| `x.y` | X.680 \\| ISO | [ ] | w |\n"
    (row,) = r.rows_of(text)
    assert row["spec"] == "X.680 \\| ISO" and row["where"] == "w"


LADDERS = """## 2. Milestones

- M1 anchors and chain discovery: real chains
- M2 name constraints
  and the issuance-time exception

## Phasing

- **P0 — paper.** This document. *Exit:* agreement.
- **P1 — probe and schema.** One script
  that probes every source.

| Phase | Work | New coverage | Exit test |
|---|---|---|---|
| **0** | L1 ambient-trust removal | A2 | proves it |
| **3** | TLSA verification | A6 | impostor lands in UNAUTHENTICATED |

## Adoption ladder, ordered by what can actually be done

1. **Our own libraries, immediately.** `pkix` rejects a listed SPKI.
2. **Distribution blacklists.** They exist.

## Something else

1. **Not a rung.** A numbered list outside the ladder.
"""


def test_every_ladder_form_is_read_where_it_is_written():
    m = r.ladder_of(LADDERS, "M")
    assert list(m) == ["M1", "M2"] and m["M2"].endswith(
        "and the issuance-time exception"
    )
    p = r.ladder_of(LADDERS, "P")
    assert (
        list(p) == ["P0", "P1"]
        and p["P1"] == "probe and schema. One script that probes every source."
    )
    assert r.ladder_of(LADDERS, "phase") == {
        "phase 0": "L1 ambient-trust removal",
        "phase 3": "TLSA verification",
    }
    rungs = r.ladder_of(LADDERS, "rung")
    assert list(rungs) == ["rung 1", "rung 2"], (
        "a numbered list outside the ladder section is not a rung"
    )


def test_ladder_keys_normalise_and_are_never_invented():
    assert r.ladder_key("M3") == "M3"
    assert r.ladder_key("Phase 4") == "phase 4"
    assert r.ladder_key("**P2**") == "P2"
    assert r.ladder_key("2", "rung") == "rung 2"
    assert r.ladder_key("-") == ""
    assert r.ladder_key("2") == "", (
        "a bare number outside a Phase or Rung column is not a milestone"
    )
    table = "| Id | Spec | Status | Where | Rung |\n|---|---|---|---|---|\n| `kp.a` | s | [ ] | w | 1 |\n"
    assert r.rows_of(table)[0]["milestone"] == "rung 1"


def test_errata_lines_match_any_rfc_named_on_the_line():
    text = (
        "## Notes\n\n- RFC 9162 is cited in prose, not as an erratum.\n\n"
        "## 3. Errata watch\n\n- RFC 6962 and RFC 9162 carry 3 verified errata.\n- RFC 9999 carries 1 verified erratum.\n\n"
        "## 4. Non-goals\n\n- RFC 9162 again, outside the section.\n"
    )
    assert r.errata_lines(text, "RFC 9162 section 2.1") == [
        "RFC 6962 and RFC 9162 carry 3 verified errata."
    ]
    assert r.errata_lines(text, "design") == []


def test_replace_status_edits_only_the_status_cell():
    line = "| `a.b()`  |  RFC 5280,   sec. 6  | `[ ]` | `tests/t.py::test_x`   |"
    assert r.replace_status(line, "[ok]") == line.replace("`[ ]`", "`[ok]`")
    bare = "| `a.b` | s | [ ] | w | e |"
    assert r.replace_status(bare, "[part]") == "| `a.b` | s | [part] | w | e |"
    noted = "| x | s | ✅ (storage) | w |"
    assert r.replace_status(noted, "[part]") == "| x | s | [part] (storage) | w |"
    escaped = "| `a.b` | X \\| Y | [ ] | w |"
    assert r.replace_status(escaped, "[ok]") == "| `a.b` | X \\| Y | [ok] | w |"


def test_status_column_is_found_by_header():
    text = (
        "| Id | Status | Spec | Where |\n|---|---|---|---|\n| `a.b` | [ ] | s | w |\n"
    )
    assert r.status_column(text, 3) == 1
    assert r.status_column("| `a.b` | s | [ ] | w |", 1) == 2


# ------------------------------------------------------------------------------------- discovery


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_matrix_discovery_follows_the_aliases_and_the_overrides(tmp_path):
    row = "| Id | Spec | Status | Where |\n|---|---|---|---|\n| `x.y` | s | [ ] | w |\n"
    _write(tmp_path / "shell" / "ROADMAP.md", row)
    _write(tmp_path / "shell" / "PLAN.md", row)
    _write(tmp_path / "planner" / "ROADMAP.md", "# no matrix here\n")
    _write(tmp_path / "planner" / "PLAN.md", row)
    _write(tmp_path / "itulibrary" / "ROADMAP.md", row)
    _write(tmp_path / "namespy" / "ROADMAP.md", row)
    _write(tmp_path / "namespy" / "docs" / "roadmap.md", EMOJI)
    assert r.matrix_path(tmp_path, "shell") == tmp_path / "shell" / "ROADMAP.md"
    assert r.matrix_path(tmp_path, "planner") == tmp_path / "planner" / "PLAN.md"
    assert r.matrix_path(tmp_path, "itulib") == tmp_path / "itulibrary" / "ROADMAP.md"
    assert (
        r.matrix_path(tmp_path, "namespy")
        == tmp_path / "namespy" / "docs" / "roadmap.md"
    )
    assert r.matrix_path(tmp_path, "absent") is None


def test_the_ladder_locator_reads_each_project_in_its_own_form(tmp_path):
    _write(tmp_path / "certledger" / "ROADMAP.md", MATRIX)
    _write(tmp_path / "certledger" / "TRUST-MODEL.md", LADDERS)
    _write(tmp_path / "trustatlas" / "PLAN.md", LADDERS)
    _write(tmp_path / "keyproof" / "PLAN.md", LADDERS)
    _write(tmp_path / "pkix" / "ROADMAP.md", MATRIX + "\n" + LADDERS)
    assert list(r.ladder_for(tmp_path, "certledger")) == ["phase 0", "phase 3"]
    assert list(r.ladder_for(tmp_path, "trustatlas")) == ["P0", "P1"]
    assert list(r.ladder_for(tmp_path, "keyproof")) == ["rung 1", "rung 2"]
    assert list(r.ladder_for(tmp_path, "pkix")) == ["M1", "M2"]
    assert r.ladder_for(tmp_path, "absent") == {}


# ------------------------------------------------------------------------------------- evidence

TESTS = '''"""def test_in_a_docstring(): not a definition"""
# def test_in_a_comment(): not a definition either


def load_anchor_helper():
    pass


def load_anchor():
    pass


def test_load_anchor_helper():
    pass


def test_parse_3164():
    pass


def test_exact():
    def test_nested():
        pass


async def test_async_exact():
    pass


class TestAnchors:
    def test_method(self):
        pass


class Helpers:
    def test_method(self):
        pass
'''


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    _write(tmp_path / "demo" / "tests" / "test_a.py", TESTS)
    _write(
        tmp_path / "demo" / "tests" / "unit" / "test_b.py",
        "def test_bare_name():\n    pass\n",
    )
    return tmp_path


def test_a_longer_function_name_does_not_satisfy_a_shorter_reference(project):
    ok, why = r.evidence(project, "demo", "`tests/test_a.py::test_load_anchor`")
    assert not ok and "defines no test_load_anchor" in why
    ok, why = r.evidence(project, "demo", "`tests/test_a.py::test_parse`")
    assert not ok and "defines no test_parse" in why


def test_def_load_anchor_helper_does_not_satisfy_load_anchor_and_neither_does_load_anchor_itself(
    project,
):
    """The 239 phantom rows name `::load_anchor`: a helper of that prefix, or a non-test of that name, is no proof."""
    ok, why = r.evidence(project, "demo", "`tests/test_a.py::load_anchor`")
    assert not ok and "not collectable" in why


def test_an_exact_collectable_name_is_evidence(project):
    assert r.evidence(project, "demo", "`tests/test_a.py::test_exact`") == (
        True,
        "tests/test_a.py defines test_exact",
    )
    assert r.evidence(project, "demo", "`tests/test_a.py::test_async_exact`")[0]


def test_names_in_comments_strings_and_nested_scopes_are_not_definitions(project):
    for name in ("test_in_a_docstring", "test_in_a_comment", "test_nested"):
        ok, why = r.evidence(project, "demo", f"`tests/test_a.py::{name}`")
        assert not ok and f"defines no {name}" in why


def test_a_class_qualified_reference_must_name_a_collectable_class(project):
    assert r.evidence(project, "demo", "`tests/test_a.py::TestAnchors::test_method`")[0]
    ok, why = r.evidence(project, "demo", "`tests/test_a.py::Helpers::test_method`")
    assert not ok and "Test* classes" in why
    ok, why = r.evidence(project, "demo", "`tests/test_a.py::TestMissing::test_method`")
    assert not ok and "defines no class TestMissing" in why


def test_to_be_written_never_counts_even_when_the_function_exists(project):
    ok, why = r.evidence(
        project, "demo", "`tests/test_a.py::test_exact` (to be written)"
    )
    assert not ok and "(to be written)" in why
    # A written test beside a planned one: the planned one is skipped, the written one carries the row.
    ok, why = r.evidence(
        project,
        "demo",
        "`tests/test_a.py::test_exact`, `tests/test_zzz.py::test_later` (to be written)",
    )
    assert ok and "test_later" not in why


def test_every_named_reference_must_resolve(project):
    ok, why = r.evidence(
        project, "demo", "`tests/test_a.py::test_exact`, `tests/test_a.py::test_gone`"
    )
    assert not ok and "defines no test_gone" in why
    ok, why = r.evidence(project, "demo", "`tests/test_zzz.py::test_x`")
    assert not ok and "does not exist" in why
    ok, why = r.evidence(project, "demo", "`verify.py`")
    assert not ok and "names no file::symbol" in why


def test_a_bare_test_name_is_looked_up_by_exact_name_under_tests(project):
    ok, why = r.evidence(project, "demo", "`a.py`", "`test_bare_name`")
    assert ok and why == "tests/unit/test_b.py defines test_bare_name"
    ok, why = r.evidence(project, "demo", "`a.py`", "`test_bare`")
    assert not ok and "defines test_bare" in why


# ------------------------------------------------------------------------------------- the real workspace

ROOT = pathlib.Path(os.environ.get("FORGE_ROOT") or "D:/code")
REAL = list(dict.fromkeys([*PROJECTS, *r.HAND_OWNED, "namespy"]))


def test_every_real_matrix_file_parses_with_no_unparsable_row_lines():
    """Every ROADMAP.md, PLAN.md and docs/roadmap.md of the portfolio's matrix projects, read from disk."""
    present = [p for p in REAL if r.project_dir(ROOT, p).is_dir()]
    if not present:
        pytest.skip(f"no portfolio checkout under {ROOT}; set FORGE_ROOT")
    problems: list[str] = []
    found = 0
    for project in present:
        for name in r.MATRIX_FILES:
            path = r.project_dir(ROOT, project) / name
            if not path.is_file():
                continue
            matrix = r.parse(path.read_text(encoding="utf-8"))
            found += len(matrix.rows)
            problems += [
                f"{project}/{name}:{p.line}: {p.reason}" for p in matrix.problems
            ]
        if r.matrix_path(ROOT, project) is None:
            problems.append(f"{project}: no matrix found in {r.MATRIX_FILES}")
    if problems:
        pytest.fail("row-shaped lines that do not parse:\n" + "\n".join(problems))
    assert found > 0


# ------------------------------------------------------------------------------------- the generated row test

TEMPLATE = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "test_roadmap_rows.py.tpl"
)


def _shell(tmp_path: pathlib.Path, where: str, status: str = "[ok]") -> pathlib.Path:
    project = tmp_path / "demo"
    _write(project / "demo_pkg" / "__init__.py", "")
    _write(project / "demo_pkg" / "anchors.py", "def load():\n    return 1\n")
    _write(
        project / "tests" / "test_anchors.py",
        "def test_load_anchor_helper():\n    pass\n\ndef test_load():\n    pass\n",
    )
    _write(
        project / "ROADMAP.md",
        "| Obligation | Spec | Status | Where | Milestone |\n|---|---|---|---|---|\n"
        f"| `anchors.load()` | RFC 5280 | `{status}` | {where} | M1 |\n",
    )
    _write(
        project / "tests" / "test_roadmap_rows.py",
        TEMPLATE.read_text(encoding="utf-8").replace("@PKG@", "demo_pkg"),
    )
    return project


def _run_rows(project: pathlib.Path) -> tuple[int, str]:
    import subprocess
    import sys

    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_roadmap_rows.py",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(project)},
        check=False,
    )
    return done.returncode, done.stdout + done.stderr


@pytest.mark.parametrize(
    ("where", "status"),
    [
        ("`tests/test_anchors.py::test_load`", "[ok]"),
        ("`tests/test_anchors.py::test_load`", "[part]"),
        ("`tests/test_obligations.py::load_anchor`", "[ ]"),
    ],
)
def test_the_generated_row_test_passes_a_row_whose_test_exists_or_that_claims_nothing(
    tmp_path, where, status
):
    code, output = _run_rows(_shell(tmp_path, where, status))
    assert code == 0, output


@pytest.mark.parametrize(
    "where",
    [
        "`tests/test_anchors.py::test_load_anchor`",  # only test_load_anchor_helper exists
        "`tests/test_obligations.py::load_anchor`",  # the 239 phantom rows' shape
        "`tests/test_anchors.py::test_load` (to be written)",
        "`demo_pkg/anchors.py`",
    ],
)
def test_the_generated_row_test_fails_a_done_row_without_a_real_test(tmp_path, where):
    code, output = _run_rows(_shell(tmp_path, where))
    assert code != 0 and "rows claiming progress without a test" in output, output


def test_the_generated_row_test_has_no_assert_statements():
    """`python -O` strips assert; the generated checks must fail through pytest.fail instead."""
    import ast

    tree = ast.parse(TEMPLATE.read_text(encoding="utf-8").replace("@PKG@", "demo_pkg"))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
