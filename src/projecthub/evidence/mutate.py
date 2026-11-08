"""Scoped mutation for one ROADMAP row: does the named test notice when the row's function stops refusing?

Stages L2 and L4 of the row audit (docs/research-mcp-planning-quality.md section 5.3).

L2 Reach  run the chosen tests once under coverage and record which lines of the row's module executed. The
          scope is the row's function plus every same-module function the tests reached. Reach is necessary
          and is never reported as a pass on its own.
L4 Kill   plant one bug at a time in that scope and re-run the tests. A failure, an error or a timeout kills
          the mutant; a pass lets it survive. A mutant on a line the tests never executed is reported as
          `unreached` without being run, because nothing in the run could have killed it.

The operators are refusal-shaped, deliberately. L3 (evidence_check.py) has already shown the test fails on the
old source, so it exercises the honest computation. What L3 cannot see is a test that only asserts True on
honest inputs: a verifier that accepts everything passes it. So every operator here weakens a refusal:

  guard     remove an `if` whose body only refuses (`return False`, `return None`, `raise`)
  accept    `return False` becomes `return True`
  boundary  `>=` <-> `>` and `<=` <-> `<`
  weaken    drop one conjunct of an `and` in a return value, or one disjunct of an `or` in a refusing guard
  result    in the row's own function only: a boolean result becomes `True`, any other result `None`

    python mutate.py --repo ../ctspy --project ctspy --row merkle.verify_inclusion [--tests named|file|suite]

Exit 0 when at least one mutant is killed, 1 when none is, 2 on a usage or environment error.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass

from projecthub.db import parser as roadmap

SCHEMA = "sdk-forge/row-mutation/1"
FLIP = {ast.GtE: ast.Gt, ast.Gt: ast.GtE, ast.LtE: ast.Lt, ast.Lt: ast.LtE}

Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class Mutant:
    id: str
    function: str
    line: int
    operator: str
    description: str
    source: str = ""
    outcome: str = ""  # killed, survived, unreached, timeout


# ---------------------------------------------------------------------------------------------- operators


def _refuses(body: list[ast.stmt]) -> bool:
    """Does this block only refuse: one `return False`/`return None`/bare `return`, or one `raise`?"""
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Raise):
        return True
    return isinstance(stmt, ast.Return) and (
        stmt.value is None
        or (isinstance(stmt.value, ast.Constant) and stmt.value.value in (False, None))
    )


def _boolean(fn: Function, value: ast.expr) -> bool:
    if fn.returns is not None and ast.unparse(fn.returns) == "bool":
        return True
    if isinstance(value, ast.Compare | ast.BoolOp):
        return True
    return isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)


def _functions(tree: ast.Module) -> Iterator[tuple[str, Function]]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for inner in node.body:
                if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield f"{node.name}.{inner.name}", inner


def _sites(fn: Function, own: bool) -> Iterator[tuple[ast.AST, str, str, int]]:
    """(node, operator, description, variant) for every mutation site in `fn`."""
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and not node.orelse and _refuses(node.body):
            yield node, "guard", f"drop the guard `if {ast.unparse(node.test)}`", 0
            test = node.test
            if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
                for i, value in enumerate(test.values):
                    yield (
                        test,
                        "weaken",
                        f"drop `{ast.unparse(value)}` from the guard",
                        i,
                    )
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and node.value.value is False
        ):
            yield node, "accept", "`return False` becomes `return True`", 0
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in FLIP:
                    yield (
                        node,
                        "boundary",
                        f"`{ast.unparse(node)}`: operator {i + 1} moves its boundary",
                        i,
                    )
        if isinstance(node, ast.Return) and node.value is not None:
            value = node.value
            if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.And):
                for i, part in enumerate(value.values):
                    yield (
                        value,
                        "weaken",
                        f"drop `{ast.unparse(part)}` from the result",
                        i,
                    )
            if own and not isinstance(value, ast.Constant):
                yield (
                    node,
                    "result",
                    "the result becomes "
                    + ("`True`" if _boolean(fn, value) else "`None`"),
                    0,
                )


def _apply(node: ast.AST, operator: str, variant: int, fn: Function) -> ast.AST | None:
    """The replacement for `node` (a copy), or None to delete the statement."""
    if operator == "guard":
        return ast.Pass()
    if operator == "accept":
        return ast.Return(value=ast.Constant(True))
    if operator == "boundary":
        assert isinstance(node, ast.Compare)
        new = copy.deepcopy(node)
        new.ops[variant] = FLIP[type(node.ops[variant])]()
        return new
    if operator == "weaken":
        assert isinstance(node, ast.BoolOp)
        rest = [v for i, v in enumerate(node.values) if i != variant]
        return rest[0] if len(rest) == 1 else ast.BoolOp(op=node.op, values=rest)
    if operator == "result":
        assert isinstance(node, ast.Return) and node.value is not None
        return ast.Return(
            value=ast.Constant(True if _boolean(fn, node.value) else None)
        )
    raise ValueError(operator)


class _Swap(ast.NodeTransformer):
    def __init__(self, target: ast.AST, replacement: ast.AST | None) -> None:
        self.target, self.replacement = target, replacement

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if node is self.target:
            return self.replacement if self.replacement is not None else ast.Pass()
        return super().generic_visit(node)


def mutants(source: str, own: str, scope: Sequence[str] = ()) -> list[Mutant]:
    """Every refusal-weakening mutant of `own` and the functions in `scope`, deduplicated by resulting source."""
    original = ast.unparse(ast.parse(source))
    seen = {original}
    out: list[Mutant] = []
    names = [own, *[s for s in scope if s != own]]
    for name in names:
        tree = ast.parse(source)
        found = dict(_functions(tree))
        if name not in found:
            continue
        sites = list(_sites(found[name], own=name == own))
        for index, (node, operator, description, variant) in enumerate(sites):
            fresh = ast.parse(source)
            fn = dict(_functions(fresh))[name]
            twin = list(_sites(fn, own=name == own))[index][0]
            mutated = _Swap(twin, _apply(twin, operator, variant, fn)).visit(fresh)
            text = ast.unparse(ast.fix_missing_locations(mutated))
            if text in seen:
                continue
            seen.add(text)
            line = getattr(node, "lineno", fn.lineno)
            out.append(
                Mutant(
                    f"{name}:{line}:{operator}:{variant}",
                    name,
                    line,
                    operator,
                    description,
                    text,
                )
            )
    return out


# ---------------------------------------------------------------------------------------------- running


def _env(tree: pathlib.Path) -> dict[str, str]:
    path = os.pathsep.join(
        p for p in (str(tree), os.environ.get("PYTHONPATH", "")) if p
    )
    return {**os.environ, "PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1"}


def run_tests(
    tree: pathlib.Path,
    tests: Sequence[str],
    timeout: float,
    python: str = sys.executable,
) -> str:
    """passed, failed or timeout for one pytest run of `tests` (an empty list runs the configured suite)."""
    try:
        done = subprocess.run(  # noqa: S603
            [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
            cwd=tree,
            capture_output=True,
            env=_env(tree),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    return "passed" if done.returncode == 0 else "failed"


def reach(
    tree: pathlib.Path,
    module: str,
    tests: Sequence[str],
    scratch: pathlib.Path,
    python: str = sys.executable,
) -> tuple[str, set[int]]:
    """(outcome of the unmutated run, lines of `module` it executed). Coverage data stays in `scratch`."""
    rc = scratch / "empty.coveragerc"
    rc.write_text("[run]\nbranch = False\n", encoding="utf-8")
    data, report = scratch / "reach.coverage", scratch / "reach.json"
    target = (tree / module).resolve()
    done = subprocess.run(  # noqa: S603
        [
            python,
            "-m",
            "coverage",
            "run",
            f"--rcfile={rc}",
            f"--data-file={data}",
            f"--include={target}",
        ]
        + ["-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=tree,
        capture_output=True,
        env=_env(tree),
        check=False,
    )
    outcome = "passed" if done.returncode == 0 else "failed"
    subprocess.run(  # noqa: S603
        [
            python,
            "-m",
            "coverage",
            "json",
            f"--rcfile={rc}",
            f"--data-file={data}",
            "-o",
            str(report),
        ],
        cwd=tree,
        capture_output=True,
        env=_env(tree),
        check=False,
    )
    lines: set[int] = set()
    if report.is_file():
        files = json.loads(report.read_text(encoding="utf-8")).get("files", {})
        for name, info in files.items():
            if (
                pathlib.Path(
                    name if pathlib.Path(name).is_absolute() else tree / name
                ).resolve()
                == target
            ):
                lines = set(info.get("executed_lines", []))
    return outcome, lines


def _span(fn: Function) -> set[int]:
    return {n.lineno for n in ast.walk(fn) if isinstance(n, ast.stmt)} - {fn.lineno}


def audit(
    tree: pathlib.Path,
    module: str,
    function: str,
    tests: Sequence[str],
    scratch: pathlib.Path,
    timeout: float = 0.0,
    scope_tests: Sequence[str] | None = None,
) -> dict[str, object]:
    """L2 + L4 for `function` in `module` (a path under `tree`, which this mutates and restores).

    The scope comes from what `scope_tests` reach (the row's named test, normally); `tests` are the ones that
    try to kill. Widening `tests` to the whole file or suite must not widen the scope, or a row would be
    credited with kills in functions its own test never touches. With no `timeout`, each mutant gets ten
    times the unmutated run, and at least ten seconds; a timeout counts as a kill.
    """
    path = tree / module
    source = path.read_text(encoding="utf-8")
    functions = dict(_functions(ast.parse(source)))
    if function not in functions:
        return {"error": f"{module} defines no {function}", "ok": False}
    started = time.monotonic()
    baseline, executed = reach(tree, module, tests, scratch)
    elapsed = time.monotonic() - started
    if baseline != "passed":
        return {"error": "the tests do not pass on the unmutated source", "ok": False}
    scoping = list(tests if scope_tests is None else scope_tests)
    named = (
        executed if scoping == list(tests) else reach(tree, module, scoping, scratch)[1]
    )
    reached = {name for name, fn in functions.items() if _span(fn) & named}
    limit = timeout or max(10.0, 10 * elapsed)
    own_span = _span(functions[function])
    own_reach = own_span & named
    scope = [function, *sorted(n for n in reached if n != function)]
    planted = mutants(source, function, scope)
    try:
        for mutant in planted:
            fn = functions[mutant.function]
            if mutant.line not in executed and mutant.line in _span(fn):
                mutant.outcome = "unreached"
                continue
            path.write_text(mutant.source, encoding="utf-8")
            result = run_tests(tree, tests, limit)
            mutant.outcome = {"passed": "survived", "failed": "killed"}.get(
                result, result
            )
    finally:
        path.write_text(source, encoding="utf-8")
    killed = sum(m.outcome in ("killed", "timeout") for m in planted)
    return {
        "module": module,
        "function": function,
        "tests": list(tests) or ["(configured suite)"],
        "reach": {
            "executed": len(own_reach),
            "statements": len(own_span),
            "by": scoping or ["(configured suite)"],
        },
        "scope": scope,
        "killed": killed,
        "total": len(planted),
        "mutants": [
            {k: v for k, v in asdict(m).items() if k != "source"} for m in planted
        ],
        "error": ""
        if planted
        else "no refusal to mutate in scope: L4 cannot judge this row",
        "ok": killed >= 1,
    }


# ---------------------------------------------------------------------------------------------- rows


def locate(folder: pathlib.Path, row_name: str) -> tuple[str, str] | None:
    """(module path, function) a row id names: `merkle.verify_inclusion` -> (<pkg>/merkle.py, verify_inclusion)."""
    module, _, function = row_name.partition(".")
    package = folder.name.replace("-", "_")
    for candidate in (
        folder / package / f"{module}.py",
        folder / package / module / "__init__.py",
    ):
        if candidate.is_file() and function:
            return candidate.relative_to(folder).as_posix(), function.replace("-", "_")
    return None


def select(node: str, which: str) -> list[str]:
    if which == "named":
        return [node]
    if which == "file":
        return [node.split("::", 1)[0]]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    from projecthub.evidence import check as evidence_check

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--project", required=True)
    parser.add_argument("--row", required=True)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--tests", choices=("named", "file", "suite"), default="named")
    parser.add_argument(
        "--function",
        default="",
        help="module.py:function, when the row id does not name it",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="seconds per mutant (default: scaled)",
    )
    parser.add_argument("--json", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.json is not None:
        from projecthub import paths

        refused = paths.refuse_path(args.json)
        if refused:
            print(f"error: {refused}", file=sys.stderr)
            return 2
    repo = args.repo.resolve()
    sha = evidence_check.resolve(repo, args.ref)
    folder_name = roadmap.DIRECTORIES.get(args.project, args.project)
    with tempfile.TemporaryDirectory(prefix="row-mutation-") as tmp:
        scratch = pathlib.Path(tmp)
        root = scratch / "tree"
        folder = evidence_check.extract(repo, sha, root / folder_name)
        rows = evidence_check.matrix_rows(root, args.project)
        if args.row not in rows:
            print(
                f"error: no row {args.row} in {args.project}'s matrix at {args.ref}",
                file=sys.stderr,
            )
            return 2
        row = rows[args.row]
        node, state = roadmap.named_test(
            root, args.project, row["where"], row["evidence"]
        )
        if state != "exists":
            print(
                f"error: the row's named test is {state}: {node or '-'}",
                file=sys.stderr,
            )
            return 2
        target = (
            tuple(args.function.split(":", 1))
            if args.function
            else locate(folder, args.row)
        )
        if target is None or len(target) != 2:
            print(
                f"error: {args.row} names no module.function; pass --function",
                file=sys.stderr,
            )
            return 2
        record = audit(
            folder,
            target[0],
            target[1],
            select(node, args.tests),
            scratch,
            args.timeout,
            [node],
        )
    record = {
        "schema": SCHEMA,
        "project": args.project,
        "row": args.row,
        "sha": sha,
        "named_test": node,
        **record,
    }
    if args.json is not None:
        args.json.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if record.get("error") and not record.get("total"):
        print(f"error: {record['error']}", file=sys.stderr)
        return 2
    print(
        f"{args.project}:{args.row}  {record['killed']}/{record['total']} killed by {args.tests} tests"
    )
    for m in record["mutants"]:  # type: ignore[attr-defined]
        print(
            f"  {m['outcome']:<9} {m['function']}:{m['line']}  {m['operator']:<8} {m['description']}"
        )
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
