"""The report compares prose with the disk. These trees are built in tmp, never from this machine's layout."""

from __future__ import annotations

import pathlib

import deps as d

PLAN = """# Portfolio position

## Depends on

- **core** - needs a codec. **Status: does not
  exist yet.** There is no core code.
- **other** - shared plumbing. **Status: exists.**

## Sequencing rules
"""


def make(root: pathlib.Path, name: str, *, source: bool, tests: bool) -> None:
    folder = root / name
    (folder / name).mkdir(parents=True)
    if source:
        (folder / name / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    if tests:
        (folder / "tests").mkdir()
        (folder / "tests" / "test_a.py").write_text(
            "def test_a(): pass\n", encoding="utf-8"
        )


def plan_for(root: pathlib.Path, project: str) -> None:
    (root / project / "docs").mkdir(parents=True)
    (root / project / "docs" / "plan.md").write_text(PLAN, encoding="utf-8")


def test_edges_read_each_bullet_and_only_its_own_claim(tmp_path):
    plan_for(tmp_path, "app")
    found = {e.dependency: e.claims_missing for e in d.edges(tmp_path, "app")}
    assert found == {"core": True, "other": False}, (
        "a wrapped sentence must still match"
    )


def test_state_needs_source_and_tests_together(tmp_path):
    make(tmp_path, "both", source=True, tests=True)
    make(tmp_path, "no_tests", source=True, tests=False)
    (tmp_path / "docs_only").mkdir()
    assert d.dependency_state(tmp_path, "both").state == "code"
    assert d.dependency_state(tmp_path, "no_tests").state == "plan only"
    assert d.dependency_state(tmp_path, "docs_only").state == "plan only"
    assert d.dependency_state(tmp_path, "nowhere").state == "absent"


def test_tests_and_tools_do_not_count_as_source(tmp_path):
    make(tmp_path, "thin", source=False, tests=True)
    (tmp_path / "thin" / "tools").mkdir()
    (tmp_path / "thin" / "tools" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    assert d.dependency_state(tmp_path, "thin").source_files == 0


def test_a_missing_claim_is_stale_only_when_the_disk_shows_code(tmp_path):
    plan_for(tmp_path, "app")
    assert d.stale_claims(tmp_path, ["app"]) == [], "core absent: the claim is right"
    make(tmp_path, "core", source=True, tests=False)
    assert d.stale_claims(tmp_path, ["app"]) == [], "no tests: still not evidence"
    make(tmp_path, "scratch", source=True, tests=True)  # unrelated, must not matter
    (tmp_path / "core" / "tests").mkdir()
    (tmp_path / "core" / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    stale = d.stale_claims(tmp_path, ["app"])
    assert [(e.project, e.dependency) for e in stale] == [("app", "core")]


def test_report_never_says_a_dependent_is_unblocked(tmp_path):
    plan_for(tmp_path, "app")
    make(tmp_path, "core", source=True, tests=True)
    text = "\n".join(d.report(tmp_path, ["app"]))
    assert "STALE" in text
    assert "unblocked" not in text.lower()


def test_check_exits_nonzero_on_a_stale_plan(tmp_path, monkeypatch, capsys):
    plan_for(tmp_path, "app")
    make(tmp_path, "core", source=True, tests=True)
    monkeypatch.setattr(d, "PROJECTS", ["app"])
    monkeypatch.setattr("sys.argv", ["deps.py", "--root", str(tmp_path), "--check"])
    assert d.main() == 1
    assert "STALE" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["deps.py", "--root", str(tmp_path)])
    assert d.main() == 0


def test_a_shell_with_every_row_open_is_not_code_whatever_its_file_count(tmp_path):
    make(tmp_path, "shell", source=True, tests=True)
    (tmp_path / "shell" / "ROADMAP.md").write_text(
        "| `a.b()` | RFC 1 | `[ ]` | `tests/test_a.py::test_a` |\n", encoding="utf-8"
    )
    assert d.dependency_state(tmp_path, "shell").state == "shell"
    (tmp_path / "shell" / "ROADMAP.md").write_text(
        "| `a.b()` | RFC 1 | `[ok]` | `tests/test_a.py::test_a` |\n", encoding="utf-8"
    )
    assert d.dependency_state(tmp_path, "shell").state == "code"


def test_a_shell_dependency_does_not_make_a_missing_claim_stale(tmp_path):
    plan_for(tmp_path, "app")
    make(tmp_path, "core", source=True, tests=True)
    (tmp_path / "core" / "ROADMAP.md").write_text(
        "| `a.b()` | RFC 1 | `[ ]` | `tests/test_a.py::test_a` |\n", encoding="utf-8"
    )
    assert d.stale_claims(tmp_path, ["app"]) == []
