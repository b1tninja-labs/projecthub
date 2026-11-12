"""verify.py: where it looks, how long it waits, and the project list other modules import from it."""

from __future__ import annotations

import importlib

import pytest

from projecthub import paths
from projecthub.data import PROJECTS as REGISTERED
from projecthub.shells import verify


@pytest.mark.parametrize("variable", ["PROJECTHUB_ROOT", "FORGE_ROOT"])
def test_root_is_projecthubs_root(monkeypatch, tmp_path, variable):
    before = verify.ROOT
    monkeypatch.delenv("PROJECTHUB_ROOT", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv(variable, str(tmp_path))
    try:
        importlib.reload(paths)
        assert importlib.reload(verify).ROOT == tmp_path
    finally:
        monkeypatch.undo()
        importlib.reload(paths)
        importlib.reload(verify)
    assert verify.ROOT == before


def test_projecthub_root_wins_over_the_older_name(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECTHUB_ROOT", str(tmp_path / "new"))
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path / "old"))
    try:
        assert importlib.reload(paths).ROOT == tmp_path / "new"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)
        importlib.reload(verify)


def test_the_project_list_is_the_registrys():
    assert verify.PROJECTS == list(REGISTERED)


def test_a_gate_that_hangs_is_killed_and_reported_as_a_failure(tmp_path):
    code, out, err = verify.run(
        ["-c", "import sys, time; print('started', flush=True); time.sleep(30)"],
        tmp_path,
        timeout=1,
    )
    assert code != 0
    assert "timed out after 1 seconds" in err


def test_a_gate_that_finishes_reports_its_own_status(tmp_path):
    code, out, err = verify.run(
        ["-c", "import sys; print('ok'); sys.exit(3)"], tmp_path, timeout=60
    )
    assert (code, out.strip(), err) == (3, "ok", "")


def test_qualifies_needs_both_the_matrix_and_the_package(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    project = tmp_path / "demo"
    project.mkdir()
    (project / "ROADMAP.md").write_text("# Roadmap\n", encoding="utf-8")
    assert verify.qualifies("demo") is None
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert verify.qualifies("demo") == project
    assert verify.qualifies("absent") is None


def test_projects_stays_importable_and_covers_every_generated_shell():
    # deps.py and test_templates.py import this name; it must stay a plain list of directory names.
    from projecthub.shells.verify import PROJECTS

    assert isinstance(PROJECTS, list)
    assert all(isinstance(name, str) and name for name in PROJECTS)
    assert len(PROJECTS) == len(set(PROJECTS))
    from projecthub.shells import generate

    assert set(generate.POSITION) <= set(PROJECTS)
    assert "certledger" in PROJECTS


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_merkle" name="test_root" time="0.01"/>
  <testcase classname="tests.test_merkle.TestProofs" name="test_refuses[2]" time="0.02"><failure message="x"/></testcase>
  <testcase classname="tests.test_sth" name="test_skip" time="0"><skipped/></testcase>
</testsuite></testsuites>
"""


def test_node_ids_map_junit_classnames_back_to_files_and_outcomes(tmp_path):
    (tmp_path / "tests").mkdir()
    for name in ("test_merkle.py", "test_sth.py"):
        (tmp_path / "tests" / name).write_text("", encoding="utf-8")
    report = tmp_path / "r.xml"
    report.write_text(JUNIT, encoding="utf-8")
    got = verify.node_ids(report, tmp_path)
    assert got == [
        {
            "node_id": "tests/test_merkle.py::TestProofs::test_refuses[2]",
            "outcome": "failed",
            "seconds": 0.02,
        },
        {
            "node_id": "tests/test_merkle.py::test_root",
            "outcome": "passed",
            "seconds": 0.01,
        },
        {
            "node_id": "tests/test_sth.py::test_skip",
            "outcome": "skipped",
            "seconds": 0.0,
        },
    ]
    assert verify.node_ids(tmp_path / "absent.xml", tmp_path) == []


def test_an_evidence_path_inside_the_workspace_or_a_work_tree_is_refused(
    monkeypatch, tmp_path
):
    import subprocess

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(verify, "ROOT", workspace)
    assert "inside the workspace" in verify.refuse_path(workspace / "out" / "e.json")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert "inside the git work tree" in verify.refuse_path(repo / "e.json")
    outside = tmp_path / "evidence"
    outside.mkdir()
    assert verify.refuse_path(outside / "e.json") == ""


def test_main_refuses_an_in_tree_record_before_running_anything(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(
        verify,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran a gate")),
    )
    assert verify.main(["--json", str(tmp_path / "e.json")]) == 2
    assert not (tmp_path / "e.json").exists()
    assert "refusing --json" in capsys.readouterr().err


def _project(tmp_path, pyproject: str, tests: bool = True):
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if tests:
        (tmp_path / "tests").mkdir()
    return tmp_path


def test_mypy_is_given_the_tests_when_there_are_any(tmp_path):
    assert verify.mypy_targets(_project(tmp_path, ""), "pkg") == ["pkg", "tests"]
    other = tmp_path / "other"
    other.mkdir()
    assert verify.mypy_targets(_project(other, "", tests=False), "pkg") == ["pkg"]


def test_typing_warnings_name_a_non_strict_config_and_an_exclusive_floor(tmp_path):
    loose = _project(tmp_path, '[project]\nrequires-python = ">3.12"\n')
    assert verify.typing_warnings(loose) == [
        "mypy not strict",
        "requires-python excludes 3.12.0 (>3.12)",
    ]
    tight = tmp_path / "tight"
    tight.mkdir()
    _project(
        tight, '[project]\nrequires-python = ">=3.12"\n[tool.mypy]\nstrict = true\n'
    )
    assert verify.typing_warnings(tight) == []


def test_warnings_are_printed_and_never_fail_the_run(monkeypatch, tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "ROADMAP.md").write_text("# demo\n", encoding="utf-8")
    _project(project, '[project]\nrequires-python = ">3.12"\n')
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "run", lambda cmd, cwd, timeout=None: (0, "", ""))
    assert verify.main(["--project", "demo"]) == 0
    out = capsys.readouterr().out
    assert "WARN mypy not strict (1): demo" in out
    assert "WARN requires-python excludes 3.12.0 (1): demo" in out
