"""roadmap-evidence: a row may flip to [ok] only when its named test would catch the behaviour regressing.

Two of the research pipeline's stages (docs/research-mcp-planning-quality.md section 5.3), for every row that
reads [ok] at HEAD and did not at BASE:

L1 Asserts       the named test's body asserts something: an `assert` whose condition is not a constant, a
                 `pytest.raises`/`pytest.warns`/`pytest.fail`, an `assert*` method or function, or a helper in
                 the same module that itself asserts. `assert True` is not an assertion.
L3 Differential  head's tests run against base's source must FAIL - a real test failure, not a collection or
                 import error - and the same node must PASS on head. A test that passes on base was not
                 written for this change; one that only errors on base proves the module is missing, not that
                 the behaviour is checked.
L4 Kill          the named test kills at least one refusal-weakening mutant of the row's function
                 (mutate.py): a test that only asserts True on honest inputs kills none. Off with --no-mutate.

Both trees are taken with `git archive` into a temporary directory outside the repository, so the check never
writes to the work tree and never registers a worktree. Before trusting a run it confirms the package was
imported from the tree under test and not from an editable install pointing elsewhere.

    python evidence_check.py --repo ../pkix --project pkix --base origin/main [--head HEAD] [--json OUT]
    python evidence_check.py ... --row anchors.load_anchor    # audit named rows whether or not they flipped

Exit 0 when every audited row passes (or none flipped), 1 when any fails, 2 on a usage or environment error.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from projecthub.db import parser as roadmap

SCHEMA = "sdk-forge/roadmap-evidence/1"
ASSERTING_CALLS = {"raises", "warns", "fail", "deprecated_call"}
# A failure whose exception is one of these says the code under test is absent, not that it misbehaves.
NOT_BEHAVIOURAL = ("ImportError", "ModuleNotFoundError")


# ---------------------------------------------------------------------------------------------- L1


def _constant(node: ast.expr) -> bool:
    """Is this condition decided without running the code under test: a literal, or a tuple of literals?"""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Tuple | ast.List):
        # `assert (x, "msg")` is the classic slip: a non-empty tuple is always true, whatever it holds.
        return True
    if isinstance(node, ast.UnaryOp):
        return _constant(node.operand)
    if isinstance(node, ast.Compare):
        return _constant(node.left) and all(_constant(c) for c in node.comparators)
    if isinstance(node, ast.BoolOp):
        return all(_constant(v) for v in node.values)
    return False


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _find(
    tree: ast.Module, parts: Sequence[str]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    *classes, name = parts
    scope: list[ast.stmt] = tree.body
    for cls in classes:
        found = next(
            (n for n in scope if isinstance(n, ast.ClassDef) and n.name == cls), None
        )
        if found is None:
            return None
        scope = found.body
    return next(
        (
            n
            for n in scope
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
        ),
        None,
    )


def asserts(tree: ast.Module, parts: Sequence[str]) -> tuple[bool, str]:
    """(asserts, why) for the test `parts` names in `tree`: L1 of the row audit."""
    target = _find(tree, parts)
    if target is None:
        return False, f"defines no {'::'.join(parts)}"
    helpers = {
        n.name: n
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n is not target
    }
    seen: set[str] = set()
    constant_only = False

    def check(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        nonlocal constant_only
        for node in ast.walk(fn):
            if isinstance(node, ast.Assert):
                if _constant(node.test):
                    constant_only = True
                    continue
                return f"assert at line {node.lineno}"
            if isinstance(node, ast.Call):
                name = _callee(node)
                if name in ASSERTING_CALLS:
                    return f"pytest.{name} at line {node.lineno}"
                if name.startswith(("assert", "_assert")):
                    return f"{name}() at line {node.lineno}"
                if name in helpers and name not in seen:
                    seen.add(name)
                    found = check(helpers[name])
                    if found:
                        return f"{found} in helper {name}"
        return ""

    found = check(target)
    if found:
        return True, found
    if constant_only:
        return (
            False,
            "every assert has a constant condition (assert True proves nothing)",
        )
    return (
        False,
        "no assert, pytest.raises/warns/fail, assert* call or asserting helper; a test that relies on the code under test raising cannot tell an accept from a refusal, so assert the result or add a refusal case",
    )


# ---------------------------------------------------------------------------------------------- git


def git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )  # noqa: S603, S607


def resolve(repo: pathlib.Path, ref: str) -> str:
    done = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if done.returncode != 0:
        raise SystemExit(f"error: {ref!r} is not a commit in {repo}")
    return done.stdout.decode().strip()


def extract(repo: pathlib.Path, sha: str, into: pathlib.Path) -> pathlib.Path:
    """The tree at `sha`, unpacked into `into` (git archive: no worktree is registered, nothing is written)."""
    done = git(repo, "archive", "--format=tar", sha)
    if done.returncode != 0:
        raise SystemExit(
            f"error: git archive {sha} failed: {done.stderr.decode(errors='replace').strip()}"
        )
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(into, filter="data")
    return into


# ---------------------------------------------------------------------------------------------- L3


@dataclass
class Run:
    outcome: str  # passed, failed, error, skipped, not collected
    message: str = ""


def run_node(
    tree: pathlib.Path, node: str, scratch: pathlib.Path, python: str = sys.executable
) -> Run:
    """Run one pytest node with `tree` as the working directory, and say how it ended."""
    junit = scratch / f"junit-{abs(hash((str(tree), node)))}.xml"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(tree), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(  # noqa: S603
        [
            python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            node,
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-15:])
    if done.returncode in (2, 3, 4):
        return Run(
            "error",
            f"pytest exit {done.returncode} (collection or usage error)\n{tail}",
        )
    if done.returncode == 5 or not junit.is_file():
        return Run("not collected", tail)
    cases = ET.parse(junit).getroot().iter("testcase")  # noqa: S314 - our own pytest's report
    outcomes = []
    for case in cases:
        failure, error, skipped = (
            case.find("failure"),
            case.find("error"),
            case.find("skipped"),
        )
        if error is not None:
            outcomes.append(Run("error", error.get("message", "")))
        elif failure is not None:
            outcomes.append(Run("failed", failure.get("message", "")))
        elif skipped is not None:
            outcomes.append(Run("skipped", skipped.get("message", "")))
        else:
            outcomes.append(Run("passed"))
    if not outcomes:
        return Run("not collected", tail)
    for state in ("error", "failed", "skipped"):
        hit = next((o for o in outcomes if o.outcome == state), None)
        if hit:
            return hit
    return Run("passed")


def imported_from(
    tree: pathlib.Path, package: str, python: str = sys.executable
) -> str:
    """Where `package` is imported from when run from `tree`: an editable install elsewhere would be a lie."""
    env = {**os.environ, "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(  # noqa: S603
        [
            python,
            "-c",
            f"import {package}, os; print(os.path.abspath({package}.__file__))",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def overlay(head: pathlib.Path, base: pathlib.Path, test_file: str) -> None:
    """Copy head's test tree (the top directory holding the named test, plus root conftest.py) over base."""
    top = pathlib.PurePosixPath(test_file).parts[0]
    sources = [head / top] if (head / top).is_dir() else [head / test_file]
    if (head / "conftest.py").is_file():
        sources.append(head / "conftest.py")
    for source in sources:
        files = (
            [source]
            if source.is_file()
            else [p for p in source.rglob("*") if p.is_file()]
        )
        for path in files:
            target = base / path.relative_to(head)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())


def differential(
    head: pathlib.Path,
    base: pathlib.Path,
    node: str,
    scratch: pathlib.Path,
    runner: Callable[..., Run] = run_node,
) -> tuple[bool, dict[str, object]]:
    """L3: head's tests against base's source must fail behaviourally, and pass on head."""
    overlay(head, base, node.split("::", 1)[0])
    on_base = runner(base, node, scratch)
    on_head = runner(head, node, scratch)
    detail: dict[str, object] = {"base": asdict(on_base), "head": asdict(on_head)}
    if on_head.outcome != "passed":
        return False, {
            **detail,
            "reason": f"the named test does not pass on head ({on_head.outcome})",
        }
    if on_base.outcome == "passed":
        return False, {
            **detail,
            "reason": "the named test already passes on base: it does not detect this change",
        }
    if on_base.outcome != "failed":
        return False, {
            **detail,
            "reason": f"on base the test {on_base.outcome}s rather than fails: that shows the code is absent, "
            "not that the behaviour is checked",
        }
    if any(
        on_base.message.startswith(name) or f"{name}:" in on_base.message[:200]
        for name in NOT_BEHAVIOURAL
    ):
        return False, {
            **detail,
            "reason": "on base the test fails with an import error, not a behavioural failure",
        }
    return True, {**detail, "reason": "fails on base, passes on head"}


# ---------------------------------------------------------------------------------------------- rows


@dataclass
class Audit:
    row: str
    title: str
    node: str = ""
    l1: dict[str, object] = field(default_factory=dict)
    l3: dict[str, object] = field(default_factory=dict)
    l4: dict[str, object] = field(default_factory=dict)
    ok: bool = False
    reason: str = ""


def matrix_rows(tree: pathlib.Path, project: str) -> dict[str, dict[str, str]]:
    path = roadmap.matrix_path(tree, project)
    if path is None:
        return {}
    return {r["name"]: r for r in roadmap.rows_of(path.read_text(encoding="utf-8"))}


def flipped(
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]
) -> list[str]:
    """Row ids that read done at head and did not at base (new rows that arrive done included)."""
    return [
        name
        for name, row in after.items()
        if row["state"] == "done" and before.get(name, {}).get("state") != "done"
    ]


def audit_row(
    row: dict[str, str],
    project: str,
    heads: pathlib.Path,
    bases: pathlib.Path,
    scratch: pathlib.Path,
    runner: Callable[..., Run] = run_node,
    mutate: bool = True,
) -> Audit:
    out = Audit(f"{project}:{row['name']}", row["title"])
    node, state = roadmap.named_test(heads, project, row["where"], row["evidence"])
    out.node = node
    if state != "exists" or "::" not in node:
        out.reason = (
            f"no collectable named test ({state}): a row cannot be [ok] without one"
        )
        return out
    head = roadmap.project_dir(heads, project)
    path, *parts = node.split("::")
    tree = ast.parse((head / path).read_text(encoding="utf-8"))
    ok, why = asserts(tree, parts)
    out.l1 = {"ok": ok, "reason": why}
    if not ok:
        out.reason = f"L1: {why}"
        return out
    ok, detail = differential(
        head, roadmap.project_dir(bases, project), node, scratch, runner
    )
    out.l3 = {"ok": ok, **detail}
    out.ok = ok
    out.reason = "" if ok else f"L3: {detail['reason']}"
    if not ok or not mutate:
        return out
    from projecthub.evidence import mutate as M

    target = M.locate(head, row["name"])
    if target is None:
        out.l4 = {
            "ok": False,
            "error": f"{row['name']} names no module.function in the package",
        }
    else:
        out.l4 = M.audit(head, target[0], target[1], [node], scratch)
    out.ok = bool(out.l4["ok"])
    if not out.ok:
        out.reason = "L4: " + (
            f"the named test kills 0 of {out.l4['total']} refusal mutants: it cannot tell an accept from a refusal"
            if out.l4.get("total")
            else str(out.l4["error"])
        )
    return out


def check(
    repo: pathlib.Path,
    project: str,
    base_ref: str,
    head_ref: str = "HEAD",
    rows: Iterable[str] = (),
    package: str = "",
    runner: Callable[..., Run] = run_node,
    mutate: bool = True,
) -> dict[str, object]:
    """The roadmap-evidence record for `repo` between two commits. `mutate` adds L4 (mutate.py)."""
    base_sha, head_sha = resolve(repo, base_ref), resolve(repo, head_ref)
    folder = roadmap.DIRECTORIES.get(project, project)
    with tempfile.TemporaryDirectory(prefix="roadmap-evidence-") as tmp:
        scratch = pathlib.Path(tmp)
        heads, bases = scratch / "head", scratch / "base"
        extract(repo, head_sha, heads / folder)
        extract(repo, base_sha, bases / folder)
        after, before = matrix_rows(heads, project), matrix_rows(bases, project)
        wanted = list(rows) or flipped(before, after)
        unknown = [r for r in wanted if r not in after]
        if unknown:
            raise SystemExit(
                f"error: no row {', '.join(unknown)} in {project}'s matrix at {head_ref}"
            )
        isolation = ""
        name = package or folder.replace("-", "_")
        if wanted and runner is run_node:
            for side in (heads, bases):
                where = imported_from(side / folder, name)
                tree = (side / folder).resolve()
                if where and tree not in pathlib.Path(where).resolve().parents:
                    isolation = f"{name} imports from {where}, not from the tree under test {tree}"
                    break
        audits = (
            []
            if isolation
            else [
                audit_row(after[r], project, heads, bases, scratch, runner, mutate)
                for r in wanted
            ]
        )
    return {
        "schema": SCHEMA,
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": project,
        "base": base_sha,
        "head": head_sha,
        "audited": wanted,
        "error": isolation,
        "rows": [asdict(a) for a in audits],
        "ok": not isolation and all(a.ok for a in audits),
    }


def annotate(record: dict[str, object]) -> list[str]:
    """GitHub Actions error annotations, one per failing row."""
    rows = record["rows"]
    assert isinstance(rows, list)
    return [
        f"::error title=roadmap-evidence {r['row']}::{r['reason']}"
        for r in rows
        if not r["ok"]
    ]


def _passed(row: dict[str, object]) -> str:
    l3, l4 = row["l3"], row["l4"]
    assert isinstance(l3, dict) and isinstance(l4, dict)
    kills = f"; kills {l4['killed']}/{l4['total']} refusal mutants" if l4 else ""
    return f"{l3['reason']}{kills}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--base", required=True, help="the commit the change is measured against"
    )
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--row",
        action="append",
        default=[],
        help="audit this row whether or not it flipped",
    )
    parser.add_argument(
        "--package", default="", help="import name, if not the directory name"
    )
    parser.add_argument(
        "--json",
        type=pathlib.Path,
        help="write the record here (outside any work tree)",
    )
    parser.add_argument("--no-mutate", action="store_true", help="skip L4 (mutation)")
    args = parser.parse_args(argv)
    if args.json is not None:
        from projecthub import paths

        refused = paths.refuse_path(args.json)
        if refused:
            print(f"error: {refused}", file=sys.stderr)
            return 2
    record = check(
        args.repo.resolve(),
        args.project,
        args.base,
        args.head,
        args.row,
        args.package,
        mutate=not args.no_mutate,
    )
    if args.json is not None:
        args.json.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if record["error"]:
        print(f"error: {record['error']}", file=sys.stderr)
        return 2
    rows = record["rows"]
    assert isinstance(rows, list)
    if not rows:
        print("roadmap-evidence: no row flipped to [ok]; nothing to audit")
        return 0
    for r in rows:
        print(
            f"{'PASS' if r['ok'] else 'FAIL'}  {r['row']}  {r['node'] or '-'}  {r['reason'] or _passed(r)}"
        )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("\n".join(annotate(record)))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
