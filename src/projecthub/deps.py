"""Compare what each generated plan claims about a dependency with what is on disk.

Why this exists. Every dependent's `docs/plan.md` says `asn1core` "does not exist yet", and the sentence
was true when it was generated. `asn1core` now has a package, a test suite and history, and nothing
re-read the claim. A board that copied "Blocked: asn1core" from that prose would be asserting something
nobody checked, which is the failure this portfolio is built against.

What it deliberately does not do. It reports the *state* of a dependency (absent, plan only, code) and
which plans contradict it. It does not say a dependent is unblocked: whether a package that exists provides
the capability an edge needs (a CMS codec, an OCSP parser) is a per-edge question that prose cannot answer
and a file count cannot either. A stale claim is a finding; "unblocked" is a decision for a person.

Usage:
    python deps.py                 # table of dependencies and which plans are stale
    python deps.py --check         # exit 1 when any plan contradicts the disk
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
from dataclasses import dataclass

from projecthub.github.sync import STATUSES, rows_of
from projecthub.data import PROJECTS

ROOT = pathlib.Path(os.environ.get("FORGE_ROOT") or "D:/code")

MISSING_CLAIM = "does not exist yet"
# `- **name** - prose ...` up to the next bullet or heading.
EDGE = re.compile(r"^- \*\*([\w-]+)\*\*(.*?)(?=^- \*\*|^## |\Z)", re.M | re.S)
NOT_SOURCE = {
    "tests",
    "tools",
    "docs",
    "examples",
    "dist",
    "build",
    "__pycache__",
    ".git",
    ".venv",
    "node_modules",
}


@dataclass(frozen=True)
class DepState:
    name: str
    state: str  # "absent" | "plan only" | "shell" | "code"
    source_files: int
    test_files: int
    has_pyproject: bool
    has_roadmap: bool
    gated: bool  # covered by verify.py's five gates
    rows: int = 0
    rows_done: int = 0


@dataclass(frozen=True)
class Edge:
    project: str
    dependency: str
    claims_missing: bool


def _source_files(folder: pathlib.Path) -> int:
    """Python files in the project's own package directories, not its tests, tools or docs."""
    count = len(list(folder.glob("*.py"))) - len(list(folder.glob("conftest.py")))
    for child in folder.iterdir():
        if child.is_dir() and child.name not in NOT_SOURCE:
            count += len(list(child.glob("*.py")))
    return count


def dependency_state(root: pathlib.Path, name: str) -> DepState:
    folder = root / name
    if not folder.is_dir():
        return DepState(name, "absent", 0, 0, False, False, name in PROJECTS)
    sources = _source_files(folder)
    tests = len(list((folder / "tests").glob("test_*.py")))
    roadmap = folder / "ROADMAP.md"
    rows = rows_of(roadmap.read_text(encoding="utf-8")) if roadmap.is_file() else []
    done = sum(1 for r in rows if STATUSES.get(r["status"], ("", "open"))[1] == "close")
    # Source files and tests together are the floor: a package with no tests is a claim, not evidence.
    # A generated shell passes that floor too, because its stubs raise NotImplementedError and its tests
    # check that they do. So a project with an obligation matrix and no done row is a shell, whatever
    # its file count says. Only a done row (whose named test is guarded elsewhere) counts as behaviour.
    if not (sources and tests):
        state = "plan only"
    elif rows and not done:
        state = "shell"
    else:
        state = "code"
    return DepState(
        name,
        state,
        sources,
        tests,
        (folder / "pyproject.toml").is_file(),
        roadmap.is_file(),
        name in PROJECTS,
        len(rows),
        done,
    )


def edges(root: pathlib.Path, project: str) -> list[Edge]:
    """The dependencies a project's plan lists, and whether each one says it does not exist."""
    plan = root / project / "docs" / "plan.md"
    if not plan.is_file():
        return []
    text = plan.read_text(encoding="utf-8")
    section = re.search(r"^## Depends on\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not section:
        return []
    return [
        Edge(project, match.group(1), MISSING_CLAIM in " ".join(match.group(2).split()))
        for match in EDGE.finditer(section.group(1))
    ]


def stale_claims(root: pathlib.Path, projects: list[str]) -> list[Edge]:
    """Edges whose plan says the dependency does not exist while the disk shows code and tests."""
    found: list[Edge] = []
    cache: dict[str, DepState] = {}
    for project in projects:
        for edge in edges(root, project):
            if not edge.claims_missing:
                continue
            state = cache.setdefault(
                edge.dependency, dependency_state(root, edge.dependency)
            )
            if state.state == "code":
                found.append(edge)
    return found


def report(root: pathlib.Path, projects: list[str]) -> list[str]:
    lines: list[str] = []
    wanted = sorted({e.dependency for p in projects for e in edges(root, p)})
    for name in wanted:
        s = dependency_state(root, name)
        users = [
            e.project for p in projects for e in edges(root, p) if e.dependency == name
        ]
        lines.append(
            f"{name}: {s.state}; {s.source_files} source files, {s.test_files} test files, "
            f"pyproject={'yes' if s.has_pyproject else 'no'}, roadmap matrix={'yes' if s.has_roadmap else 'no'}, "
            f"covered by verify.py={'yes' if s.gated else 'no'}; rows done {s.rows_done}/{s.rows}; "
            f"listed by {len(users)}"
        )
    stale = stale_claims(root, projects)
    for edge in stale:
        lines.append(
            f"STALE: {edge.project}/docs/plan.md says {edge.dependency} {MISSING_CLAIM}, "
            f"but it has source and tests on disk"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = pathlib.Path(args.root)
    lines = report(root, PROJECTS)
    print("\n".join(lines))
    stale = [line for line in lines if line.startswith("STALE")]
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
