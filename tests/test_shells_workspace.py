"""A portfolio edge must become a declaration, and a fresh clone must be able to resolve it.

The bug these tests hold shut is real and recent: `pkix/chain.py` imported `asn1core` while
`pkix/pyproject.toml` declared only `cryptography`, and every gate passed, because the machine had
the sibling installed editable. Detection and the install plan are the two halves that were missing.
"""

from __future__ import annotations

import pathlib

import pytest

from projecthub.shells import workspace


def make_project(
    root: pathlib.Path,
    name: str,
    *,
    module: str | None = None,
    deps: tuple[str, ...] = (),
    imports: tuple[str, ...] = (),
) -> pathlib.Path:
    """A minimal installable project: pyproject, one package, one module with imports."""
    package = module or name
    folder = root / name
    # exist_ok: several tests re-declare a project to change its dependencies, which is the whole
    # point of the helper -- the pyproject is rewritten and the package directory already exists.
    (folder / package).mkdir(parents=True, exist_ok=True)
    (folder / package / "__init__.py").write_text("", encoding="utf-8")
    body = "".join(f"import {target}\n" for target in imports)
    (folder / package / "core.py").write_text(body + "VALUE = 1\n", encoding="utf-8")
    listed = ", ".join(f'"{entry}"' for entry in deps)
    (folder / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['hatchling']\nbuild-backend = 'hatchling.build'\n\n"
        f"[project]\nname = '{name}'\nversion = '0.0.1'\ndependencies = [{listed}]\n",
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def tree(tmp_path: pathlib.Path) -> pathlib.Path:
    make_project(tmp_path, "asn1core")
    make_project(tmp_path, "pkix", imports=("asn1core",))
    return tmp_path


def test_an_imported_sibling_that_is_not_declared_is_a_finding(
    tree: pathlib.Path,
) -> None:
    assert workspace.undeclared_siblings(tree, "pkix") == [
        "asn1core (imported as asn1core)"
    ]


def test_declaring_the_sibling_clears_the_finding(tree: pathlib.Path) -> None:
    make_project(tree, "pkix", deps=("asn1core",), imports=("asn1core",))
    assert workspace.undeclared_siblings(tree, "pkix") == []


def test_a_project_without_imports_reports_nothing(tmp_path: pathlib.Path) -> None:
    make_project(tmp_path, "lonely")
    assert workspace.undeclared_siblings(tmp_path, "lonely") == []


def test_a_project_is_not_its_own_sibling(tree: pathlib.Path) -> None:
    assert [s.name for s in workspace.siblings_used(tree, "pkix")] == ["asn1core"]
    # asn1core imports nothing, so it has no sibling at all -- the edge is read from the source.
    assert workspace.siblings_used(tree, "asn1core") == []


def test_a_workspace_full_of_projects_installs_only_what_is_imported(
    tmp_path: pathlib.Path,
) -> None:
    # The bug this guards: "sibling" first meant "every project on disk", and a dry run proposed
    # installing thirty of them to build one environment.
    for name in ("asn1core", "ctspy", "itulibrary", "namespy", "rpkit"):
        make_project(tmp_path, name)
    make_project(tmp_path, "pkix", imports=("asn1core",))
    plan = workspace.install_plan(tmp_path, "pkix")
    installed = [
        argument
        for command in plan
        for argument in command.argv
        if str(tmp_path) in argument
    ]
    assert installed == [str(tmp_path / "asn1core")]


def test_a_project_that_imports_nothing_gets_two_commands(tree: pathlib.Path) -> None:
    plan = workspace.install_plan(tree, "asn1core")
    tails = [command.argv[-1] for command in plan]
    assert tails == [".venv", ".[dev]"]


def test_a_dependency_with_a_specifier_still_counts_as_declared(
    tree: pathlib.Path,
) -> None:
    make_project(tree, "pkix", deps=("asn1core>=0.0.1",), imports=("asn1core",))
    assert workspace.undeclared_siblings(tree, "pkix") == []


def test_underscore_and_dash_spell_the_same_distribution(
    tmp_path: pathlib.Path,
) -> None:
    # Its own tree: reusing `tree` would leave two projects providing the module `asn1core`, and
    # the finding that reports is about the other one, not about name normalisation.
    make_project(tmp_path, "asn1_core", module="asn1core")
    make_project(tmp_path, "pkix", deps=("asn1-core",), imports=("asn1core",))
    assert workspace.undeclared_siblings(tmp_path, "pkix") == []
    make_project(tmp_path, "pkix", deps=("asn1_core",), imports=("asn1core",))
    assert workspace.undeclared_siblings(tmp_path, "pkix") == []


def test_a_module_named_differently_from_its_folder_is_still_matched(
    tmp_path: pathlib.Path,
) -> None:
    # itulibrary ships the module itulib. Matching on the folder name would call this undeclared.
    make_project(tmp_path, "itulibrary", module="itulib")
    assert workspace.import_name_of(tmp_path / "itulibrary") == "itulib"
    make_project(tmp_path, "consumer", imports=("itulib",))
    assert workspace.undeclared_siblings(tmp_path, "consumer") == [
        "itulibrary (imported as itulib)"
    ]


def test_install_plan_installs_siblings_before_the_project(tree: pathlib.Path) -> None:
    plan = workspace.install_plan(tree, "pkix", pathlib.Path(".venv"))
    tails = [command.argv[-1] for command in plan]
    assert tails[0] == ".venv"
    assert tails[1].endswith("asn1core")
    assert tails[-1] == ".[dev]"
    assert all("--no-deps" in command.argv for command in plan[1:-1])


def test_install_plan_runs_every_step_in_the_project_directory(
    tree: pathlib.Path,
) -> None:
    for command in workspace.install_plan(tree, "pkix"):
        assert command.cwd == tree / "pkix"


def test_a_published_sibling_is_not_installed_from_the_checkout(
    tree: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace, "PUBLISHED", frozenset({"asn1core"}))
    tails = [command.argv[-1] for command in workspace.install_plan(tree, "pkix")]
    assert tails == [".venv", ".[dev]"]


def test_a_nonexistent_project_fails_loudly(tree: pathlib.Path) -> None:
    with pytest.raises(SystemExit):
        workspace.install_plan(tree, "nosuchproject")
