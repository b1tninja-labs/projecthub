"""Turn a portfolio edge into a declared dependency, and an environment that can resolve it.

Why this exists. `shells/data/position.py` records that pkix depends on asn1core, and
`render_pyproject` fills `[project] dependencies` from the project's own spec. The two never met: the
edge became a paragraph in `docs/plan.md` and nothing else, so pkix carried a `dependencies` list
that omitted the codec it cannot run without. Nothing in the generator installs a sibling either, so
resolving one was invented by hand, once per project, differently each time: `certledger` injects
`../asn1core` into pytest's `pythonpath` and mypy's `mypy_path`; `pkix` checks the sibling out into
`.deps/` in CI; `asn1core` needs neither, having no dependencies at all.

Why nobody noticed at home. Every project in this workspace is installed editable into the *user*
site-packages (`_editable_impl_asn1core.pth`, `__editable__.binwalker-0.1.0.pth`, and the rest), so a
sibling import resolves out of machine state. A fresh clone gets nothing, and the gap surfaces as a
`ModuleNotFoundError` in a checkout that passes elsewhere for reasons no repository records.

The mechanism, measured rather than recalled:

* `pip install -e ../asn1core` works because hatchling implements PEP 660's `build_editable` hook.
  pip requires the target directory to hold a `pyproject.toml` or `setup.py`.
* Once installed it satisfies a bare `asn1core` requirement: `pip install -e ".[dev]"` then reports
  `Requirement already satisfied: asn1core in ... (from pkix==0.1.0) (0.0.1)` and queries no index.
  Two commands are enough because of that line, and it is why the declaration carries no version
  specifier -- a specifier naming a version that exists only in an unpublished checkout breaks the
  moment the sibling's version moves.
* The editable install maps the import to the sibling's own source, so source edits take effect with
  no reinstall. Metadata is different: per PEP 660 a changed dependency or entry point needs this
  run again.

Usage:
    projecthub shells workspace --check                     # does every project declare what it imports
    projecthub shells workspace --bootstrap pkix --dry-run  # the commands a fresh clone needs
    projecthub shells workspace --bootstrap pkix            # run them into .venv
"""

from __future__ import annotations

import argparse
import ast
import os
import pathlib
import subprocess
import sys
import tomllib
from dataclasses import dataclass

from projecthub import paths

ROOT = paths.ROOT

# Projects installable from an index. Publication of asn1core was sanctioned on 2026-11-07, and
# adding its name here is what retires pkix's `.deps/` checkout step and its sibling entries in the
# pytest and mypy configuration, because from then on there is an index to resolve it from. An entry
# here claims that `pip install` succeeds on a clean machine, so it is written after the release.
PUBLISHED: frozenset[str] = frozenset()

NOT_PROJECTS = {
    ".git",
    ".claude",
    ".cline",
    ".clinerules",
    ".venv",
    "node_modules",
    "__pycache__",
}


@dataclass(frozen=True)
class Sibling:
    """A workspace project that another project imports and no index can supply."""

    name: str
    import_name: str
    path: pathlib.Path


@dataclass(frozen=True)
class Command:
    """One install step with its directory. The cwd matters: `.[dev]` is a relative path."""

    argv: tuple[str, ...]
    cwd: pathlib.Path


def workspace_projects(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Directories that are installable projects: a pyproject.toml at their top."""
    found: dict[str, pathlib.Path] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in NOT_PROJECTS:
            continue
        if (child / "pyproject.toml").is_file():
            found[child.name] = child
    return found


def own_packages(folder: pathlib.Path) -> set[str]:
    return {
        child.name
        for child in folder.iterdir()
        if child.is_dir()
        and (child / "__init__.py").is_file()
        and child.name not in NOT_PROJECTS
    }


def import_name_of(folder: pathlib.Path) -> str:
    """The module a project provides, which is not always its directory name.

    `itulibrary` ships `itulib`. Reading the folder name instead would call the edge undeclared
    while the import works, so the package directory is what gets inspected.
    """
    packages = own_packages(folder)
    if packages:
        return sorted(packages)[0]
    return folder.name


def _project_table(folder: pathlib.Path) -> dict:
    data = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))
    return data.get("project", {})


def declared_dependencies(folder: pathlib.Path) -> set[str]:
    """Normalised names from `[project] dependencies`.

    Normalisation is PEP 503 (lower-case, dashes), because `asn1_core` and `asn1-core` name one
    distribution and a comparison that misses the difference reports a problem that is not there.
    """
    names: set[str] = set()
    for entry in _project_table(folder).get("dependencies", []):
        head = str(entry)
        for separator in (";", "<", ">", "=", "!", "~", "[", "@"):
            head = head.split(separator, 1)[0]
        names.add(head.strip().lower().replace("_", "-"))
    return names


def source_imports(folder: pathlib.Path, packages: set[str]) -> set[str]:
    """Top-level modules imported by the project's own source, its own package excluded."""
    imports: set[str] = set()
    for package in sorted(packages):
        for script in sorted((folder / package).rglob("*.py")):
            tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif (
                    isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
                ):
                    imports.add(node.module.split(".")[0])
    return imports - packages


def siblings_used(root: pathlib.Path, project: str) -> list[Sibling]:
    """Workspace projects whose module this project's source actually imports.

    Deliberately derived from the imports rather than from "everything in the workspace": the first
    version of this function used the whole workspace, and `--dry-run` promptly proposed installing
    thirty projects to build a pkix environment. Nor is it read from `docs/plan.md`: that file states
    which edge *gates* the project, and a paragraph is not evidence of an import. The imports are the
    evidence, and the plan's edges are what `--check` compares the declaration against.
    """
    folder = root / project
    packages = own_packages(folder)
    if not packages:
        return []
    imports = source_imports(folder, packages)
    projects = workspace_projects(root)
    return [
        Sibling(name, import_name_of(path), path)
        for name, path in projects.items()
        if name != project and name not in PUBLISHED and import_name_of(path) in imports
    ]


def undeclared_siblings(root: pathlib.Path, project: str) -> list[str]:
    """Siblings the source imports that `[project] dependencies` omits."""
    declared = declared_dependencies(root / project)
    return sorted(
        f"{sibling.name} (imported as {sibling.import_name})"
        for sibling in siblings_used(root, project)
        if sibling.name.lower().replace("_", "-") not in declared
    )


def install_plan(
    root: pathlib.Path, project: str, venv: pathlib.Path = pathlib.Path(".venv")
) -> list[Command]:
    """The commands that take a fresh clone to a working environment, in the order that matters.

    Siblings first with `--no-deps`: a sibling's own requirements are not this project's decision,
    and resolving them could reach an index that has never heard of it. The project itself last, so
    its `[dev]` extras find every sibling already satisfied and pip reports "already satisfied"
    instead of failing to find it.
    """
    folder = root / project
    if not folder.is_dir():
        raise SystemExit(f"no such project in the workspace: {project}")
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    plan = [Command((sys.executable, "-m", "venv", str(venv)), folder)]
    for sibling in siblings_used(root, project):
        plan.append(
            Command(
                (
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--no-deps",
                    "-e",
                    str(sibling.path),
                ),
                folder,
            )
        )
    plan.append(
        Command(
            (str(python), "-m", "pip", "install", "--quiet", "-e", ".[dev]"), folder
        )
    )
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub shells workspace", description=__doc__
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--check", action="store_true", help="report siblings imported but undeclared"
    )
    parser.add_argument("--bootstrap", metavar="PROJECT")
    parser.add_argument("--venv", default=".venv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root)

    if args.check:
        problems = 0
        for name in workspace_projects(root):
            missing = undeclared_siblings(root, name)
            if missing:
                problems += 1
                print(f"UNDECLARED {name}: imports {'; '.join(missing)}")
        if problems:
            print(
                f"{problems} project(s) import a workspace sibling they do not declare"
            )
            return 1
        print("every workspace project declares the siblings it imports")
        return 0

    if args.bootstrap:
        for command in install_plan(root, args.bootstrap, pathlib.Path(args.venv)):
            printable = " ".join(command.argv)
            if args.dry_run:
                print(f"[{command.cwd}] {printable}")
                continue
            print(f"$ {printable}", flush=True)
            completed = subprocess.run(command.argv, cwd=str(command.cwd), check=False)
            if completed.returncode:
                print(f"failed ({completed.returncode}): {printable}", file=sys.stderr)
                return completed.returncode
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
