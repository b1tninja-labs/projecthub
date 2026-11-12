"""The generator's templates are guarded here, because nothing else exercises them.

`generate.py` writes managed files into every generated project, and its templates are strings: a linter
checks that the Python compiles, not that the CI it emits actually works. Two defects in these strings
were only found when a generated repository ran CI for the first time - `check_dist.py` inspecting a
wheel nothing had built, and an audit that cloned the private project over an anonymous git URL. This
file is where those two stay fixed.

It also guards the shape of the file itself. `generate.py` was assembled by concatenation and its
`__main__` guard must remain at the very end: a guard placed earlier would call run() before the
wrappers that follow had reassigned build, and artefacts would silently stop being written. An
accidental bare `run()` at module scope would rewrite every generated project the moment something
imported the module to inspect it - which is what these tests do.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import pathlib
import re

import pytest

from projecthub.shells import generate
from projecthub.github import sync

PKG = pathlib.Path(generate.__file__).resolve().parent


def test_ci_template_builds_the_wheel_before_inspecting_it():
    ci = generate.CI
    assert "python -m build --wheel" in ci
    assert ci.index("python -m build --wheel") < ci.index("python tools/check_dist.py")
    # Installing build must precede using it.
    assert ci.index("-m @PIPIN@ build") < ci.index("python -m build --wheel")


def test_freeze_excludes_the_project_under_audit():
    """The audit installs what the freeze lists, so a self-reference becomes an anonymous git clone."""
    script = generate.WRITE_FREEZE
    assert 'line.startswith("-e")' in script
    assert '"git+"' in script
    assert '"file://"' in script
    assert "self-reference" in script


def test_main_guard_is_the_last_statement():
    lines = [
        line
        for line in (PKG / "generate.py").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines[-1].strip() == "raise SystemExit(run())"
    assert lines[-2].strip() == 'if __name__ == "__main__":'


def test_importing_the_generator_cannot_run_it():
    """A bare module-level run() would rewrite every generated project when anything imported generate.

    The first version of this test asserted `not hasattr(generate, "_GENERATED_THIS_IMPORT")`, checking a
    name invented for the purpose - it could not fail, which is worse than having no test because it
    reads as coverage. These two assertions can fail: no module-level call, and projecthub is not itself
    a project the generator writes into.
    """
    module_level = [
        line
        for line in (PKG / "generate.py").read_text(encoding="utf-8").splitlines()
        if re.match(r"^run\(\)", line)
    ]
    assert module_level == [], module_level

    from projecthub.shells.verify import PROJECTS

    assert "projecthub" not in PROJECTS, (
        "the generator must not write into its own source tree"
    )


def test_scratch_and_duplicate_templates_are_gone():
    assert not (PKG / "parts").exists(), (
        "parts/ duplicated template strings that generate.py never read"
    )
    assert not sorted(PKG.glob("probe_*.py")), (
        "probe_* is scratch; the workspace rule keeps it out"
    )


def test_hygiene_gate_would_reject_the_scratch_it_shipped_before():
    from projecthub.shells import hygiene as h

    assert h.SCRATCH_RE.search("probe_ber.py")
    assert not h.SCRATCH_RE.search("generate.py")


# ---------------------------------------------------------------- template fixes (WP03)


def _project(name):
    return copy.deepcopy(next(p for p in generate.ALL_PROJECTS if p["name"] == name))


def test_where_cells_name_a_collectable_test_marked_to_be_written():
    rows = [
        line
        for line in generate.render_roadmap(
            generate.normalize(_project("rpkit"))
        ).splitlines()
        if line.startswith("| `")
    ]
    assert rows
    for row in rows:
        fn = row.split("`")[1].split(".", 1)[1].removesuffix("()")
        assert "`tests/test_obligations.py::test_" + fn + "` (to be written)" in row, (
            row
        )


def test_the_matrix_emits_a_milestone_column_from_the_api_entry():
    p = generate.normalize(_project("rpkit"))
    first = p["api"][0]
    p["api"] = [(*first[:6], "M2"), *(entry[:6] for entry in p["api"][1:])]
    text = generate.render_roadmap(p)
    assert "| Obligation | Spec | Status | Where | Milestone |" in text
    rows = sync.rows_of(text)
    assert rows[0]["milestone"] == "M2"
    assert all(row["milestone"] == "" for row in rows[1:]), (
        "unplaced rows must not get an M-number"
    )
    assert rows[1]["where"].endswith("(to be written)")


def test_readme_clone_line_is_a_clone_then_a_cd():
    text = generate.render_readme(generate.normalize(_project("ntspy")))
    assert (
        "git clone https://github.com/" + generate.ORG + "/ntspy.git\ncd ntspy\n"
        in text
    )


def test_agents_invariant_names_the_status_and_column_the_matrix_uses():
    text = generate.AGENTS_PROJECT
    assert "`[ ]` to `[ok]`" in text
    assert "`Where` cell" in text
    assert "(to be written)" in text
    assert "`Evidence` cell" not in text
    assert "to `[x]`" not in text


def test_security_workflow_audits_what_is_installed_without_resolving_it():
    wf = generate.SECURITY_WF
    audit = next(line for line in wf.splitlines() if "pip_audit" in line)
    assert "--no-deps" in audit and "--disable-pip" in audit
    assert "-r requirements-audit.txt" in audit


def test_the_shell_count_is_read_not_written_in(monkeypatch):
    assert not any("Twenty-eight" in rule for rule in generate.RULES)
    monkeypatch.setattr(generate, "shell_count", lambda: 27)
    assert "The READMEs of the 27 generated shells" in " ".join(
        generate.rules_block().split()
    )


def test_polish_runs_on_a_staged_copy_and_leaves_valid_python():
    if importlib.util.find_spec("ruff") is None:
        pytest.skip("ruff is not installed")
    files = generate.render_project(_project("sfields"), plan=False)
    polished, notes = generate.polish("sfields", files)
    assert notes == [], notes
    assert set(polished) == set(files)
    for rel, data in polished.items():
        if rel.endswith(".py"):
            ast.parse(data.decode("utf-8"))


def _rendered(name: str = "rtrkit") -> dict[str, str]:
    spec = next(p for p in generate.load_projects() if p["name"] == name)
    return {
        k: v.decode("utf-8")
        for k, v in generate.render_project(spec, plan=False).items()
    }


def test_the_rendered_python_floor_admits_3_12_0():
    """`>3.12` is exclusive under PEP 440: it refused 3.12.0 while the classifiers and CI claimed 3.12."""
    import tomllib

    from packaging.specifiers import SpecifierSet

    data = tomllib.loads(_rendered()["pyproject.toml"])
    floor = SpecifierSet(data["project"]["requires-python"])
    assert floor.contains("3.12.0"), floor
    assert not floor.contains("3.11.9")
    backend = data["build-system"]["requires"][0]
    assert SpecifierSet(backend.removeprefix("hatchling")).contains("1.25"), backend


def test_assert_is_refused_outside_tests_and_allowed_inside():
    import tomllib

    lint = tomllib.loads(_rendered()["pyproject.toml"])["tool"]["ruff"]["lint"]
    assert "S" in lint["select"]
    assert "S101" not in lint.get("ignore", []), (
        "python -O strips asserts in package code"
    )
    assert "S101" in lint["per-file-ignores"]["tests/*"]


def test_every_written_mypy_gate_checks_the_tests_too():
    files = _rendered()
    pkg = "rtrkit"
    gates = [
        line.strip()
        for text in files.values()
        for line in text.splitlines()
        if re.search(rf"\bmypy {pkg}\b", line)
    ]
    assert gates, "the gate is written down somewhere"
    assert all(line.endswith(f"mypy {pkg} tests") for line in gates), gates
