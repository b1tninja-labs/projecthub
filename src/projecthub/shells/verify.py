"""Verification driver for the generated protocol SDK projects.

For every qualifying project under the workspace root (projecthub.paths.ROOT) this runs the
five gates the generated shells promise (compile, pytest, ruff lint, ruff format, mypy) with the
interpreter that is executing this script and the project directory as the working
directory, then prints one status line per project, a per-gate summary, and the
captured output of every failing gate. A gate that runs longer than VERIFY_TIMEOUT
seconds (default 900) is killed and reported as a failure, so one hung suite cannot
stall the whole pass.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET  # noqa: N817
from pathlib import Path

from projecthub import paths
from projecthub.data import PROJECTS as REGISTERED
from projecthub.db import parser as roadmap

ROOT = paths.ROOT
TIMEOUT = float(os.environ.get("VERIFY_TIMEOUT") or 900)
MAX_LINES = 60

# The registry's project list (projecthub/data/registry.json), in its order. The comment below is the
# reasoning the list carried when it was written out here.
# certledger qualifies the way the shells do (ROADMAP.md + pyproject.toml) and has real code
# since phase 0, so it carries the same five gates. asn1core was absent because it plans in
# PLAN.md/VECTORS.md and so failed the literal-ROADMAP.md predicate; qualifies() now asks the roadmap
# grammar where a project's matrix actually is, which puts asn1core and registrata back under the gates.
PROJECTS: list[str] = list(REGISTERED)

GATES = ["compile", "pytest", "ruff", "format", "mypy"]

# The same five obligations for a Cargo project, in the same order. `mypy` has no Rust counterpart:
# types are checked by the compiler, so "compile" covers it and the slot is reported n/a rather than
# silently dropped. "doc" is Rust-only and is a sixth gate, not a replacement for one of the five.
RUST_GATES = {
    "compile": ["check", "--all-targets"],
    "pytest": ["test"],  # the test gate; the key stays aligned with GATES
    "ruff": ["clippy", "--all-targets", "--", "-D", "warnings"],  # the lint gate
    "format": ["fmt", "--check"],
    "doc": ["doc", "--no-deps"],
}
RUST_NOT_APPLICABLE = {"mypy": "n/a (the compiler type-checks; covered by compile)"}
RUST_LABEL = {
    "compile": "compile",
    "pytest": "test",
    "ruff": "clippy",
    "format": "fmt",
    "doc": "doc",
}


def is_rust_project(path: Path) -> bool:
    return (path / "Cargo.toml").is_file()


def run_cargo(
    args: list[str], cwd: Path, timeout: float | None = None
) -> tuple[int, str, str]:
    """Run `cargo <args>` in cwd with the same timeout rule as run(); 127 when cargo is not installed."""
    cargo = shutil.which("cargo")
    if cargo is None:
        return 127, "", "cargo not found on PATH"
    limit = TIMEOUT if timeout is None else timeout
    env = {**os.environ, "RUSTDOCFLAGS": "-D warnings"}
    try:
        proc = subprocess.run(  # noqa: S603
            [cargo, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            env=env,
        )
    except subprocess.TimeoutExpired as expired:
        note = f"timed out after {limit:g} seconds"
        err = _text(expired.stderr)
        return 124, _text(expired.stdout), f"{err}\n{note}" if err else note
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def verify_rust(cwd: Path, runner=run_cargo) -> list[tuple[str, str, str, str]]:
    """Run the Rust gates on one project, print its status line, and return the failures."""
    failures = []
    results = []
    for gate, args in RUST_GATES.items():
        code, out, err = runner(args, cwd)
        label = RUST_LABEL[gate]
        results.append(f"{label}={'OK' if code == 0 else 'FAIL'}")
        if code != 0:
            failures.append((cwd.name, label, out, err))
    results += [f"{gate}={why}" for gate, why in RUST_NOT_APPLICABLE.items()]
    print(f"{cwd.name} " + " ".join(results), flush=True)
    return failures


def run(
    cmd: list[str], cwd: Path, timeout: float | None = None
) -> tuple[int, str, str]:
    limit = TIMEOUT if timeout is None else timeout
    try:
        proc = subprocess.run(
            [sys.executable, *cmd],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
        )
    except subprocess.TimeoutExpired as expired:
        # 124 is timeout(1)'s status; the partial output is kept so the hang can be located.
        note = f"timed out after {limit:g} seconds"
        err = _text(expired.stderr)
        return 124, _text(expired.stdout), f"{err}\n{note}" if err else note
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _text(data: str | bytes | None) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def mypy_targets(cwd: Path, pkg: str) -> list[str]:
    """The package and its tests: a test that calls the API wrongly is a type error mypy can see."""
    return [pkg, "tests"] if (cwd / "tests").is_dir() else [pkg]


def typing_warnings(cwd: Path) -> list[str]:
    """Typing-policy findings that WARN and never FAIL (research section 5.2, rollout step 3).

    They become failures by decision once the list is empty everywhere, not before: failing 30 projects at
    once on a policy they were generated without would make the gate red for a reason nobody can act on.
    """
    try:
        data = tomllib.loads((cwd / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ["pyproject.toml does not parse"]
    found = []
    if data.get("tool", {}).get("mypy", {}).get("strict") is not True:
        found.append("mypy not strict")
    floor = data.get("project", {}).get("requires-python", "")
    if floor and not _admits(floor, "3.12.0"):
        found.append(f"requires-python excludes 3.12.0 ({floor})")
    return found


def _admits(specifier: str, version: str) -> bool:
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        return SpecifierSet(specifier).contains(version)
    except InvalidSpecifier:
        return False


def project_dir(name: str) -> Path:
    """Where a registered project is checked out.

    Most projects are named after their folder; `registrata` lives in `pyrwhois/` and `nistlib` in
    `nistlibrary/`, and the roadmap grammar's DIRECTORIES is the one place that mapping is written down.
    Reading it here rather than assuming name == folder is what lets a renamed project be gated at all.
    """
    return roadmap.project_dir(ROOT, name)


def qualifies(name: str) -> Path | None:
    """The directory to run the five gates in, or None with the reason why not ("" when it qualifies).

    The predicate used to be a literal `ROADMAP.md` next to a `pyproject.toml`, which silently excluded
    every project that plans somewhere else: asn1core keeps its matrix in PLAN.md and registrata's folder is
    named for the old project, so neither was ever gated. Now the matrix is found through the roadmap grammar
    - ROADMAP.md, then PLAN.md, then docs/roadmap.md, with a per-project override - and it must hold rows, so
    a file that merely exists no longer passes. A directory without a `pyproject.toml` still stays out: there
    is no package to compile and mypy has nothing to name, which is why the plan-only projects (keyproof,
    localns, trustatlas) are reported as skipped rather than run.
    """
    cwd = project_dir(name)
    if not cwd.is_dir():
        return None
    if not (cwd / "pyproject.toml").is_file():
        return None
    return cwd if roadmap.matrix_path(ROOT, name) is not None else None


def skip_reason(name: str) -> str:
    """Why `qualifies` refused a project, in the words the summary line prints."""
    cwd = project_dir(name)
    if not cwd.is_dir():
        return "absent"
    if not (cwd / "pyproject.toml").is_file():
        return "no pyproject.toml"
    return "no obligation matrix"


def refuse_path(path: Path) -> str:
    """Why `path` may not hold an evidence record, or "" if it may.

    Inside the workspace or inside any git work tree, a results file is one `git add -A` away from being
    committed as if it were source, which this workspace has already had to amend out once (AGENTS.md).
    """
    target = path.resolve()
    if target == ROOT.resolve() or ROOT.resolve() in target.parents:
        return f"{target} is inside the workspace {ROOT}; write evidence outside it"
    probe = next((p for p in [target, *target.parents] if p.is_dir()), None)
    if probe is not None:
        done = subprocess.run(  # noqa: S603, S607
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if done.returncode == 0:
            return f"{target} is inside the git work tree {done.stdout.strip()}"
    return ""


def node_ids(junit: Path, cwd: Path) -> list[dict[str, object]]:
    """pytest node ids and outcomes from a JUnit XML report, mapped back to file paths under `cwd`."""
    if not junit.is_file():
        return []
    out: list[dict[str, object]] = []
    for case in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314 - our own pytest's output
        parts = (case.get("classname") or "").split(".")
        name = case.get("name") or ""
        node = name
        for k in range(len(parts), 0, -1):
            module = cwd.joinpath(*parts[:k]).with_suffix(".py")
            if module.is_file():
                node = "::".join([module.relative_to(cwd).as_posix(), *parts[k:], name])
                break
        outcome = "passed"
        for tag, label in (
            ("failure", "failed"),
            ("error", "error"),
            ("skipped", "skipped"),
        ):
            if case.find(tag) is not None:
                outcome = label
                break
        out.append(
            {
                "node_id": node,
                "outcome": outcome,
                "seconds": float(case.get("time") or 0),
            }
        )
    return sorted(out, key=lambda r: str(r["node_id"]))


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def tool_versions() -> dict[str, str]:
    found = {"python": sys.version.split()[0]}
    for name in ("pytest", "ruff", "mypy"):
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = "absent"
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub shells verify", description=__doc__
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="also write an evidence record (commit, gates, every test node and its outcome) to PATH, "
        "which must be outside the workspace and outside any git work tree",
    )
    parser.add_argument(
        "--project", action="append", help="restrict to this project; repeatable"
    )
    parser.add_argument(
        "--rust",
        action="append",
        metavar="PATH",
        help="run the Rust gates (check, test, clippy, fmt, doc) on this Cargo project; repeatable. "
        "Exit 2 if cargo is missing or PATH has no Cargo.toml",
    )
    args = parser.parse_args(argv)
    if args.rust:
        if shutil.which("cargo") is None:
            print("verify: --rust needs cargo, which is not on PATH", file=sys.stderr)
            return 2
        failed = []
        for item in args.rust:
            path = Path(item).resolve()
            if not is_rust_project(path):
                print(f"verify: {path} has no Cargo.toml", file=sys.stderr)
                return 2
            failed += verify_rust(path)
        for name, gate, out, err in failed:
            print(f"\nFAIL {name} :: {gate}")
            for text in (out, err):
                print("\n".join(text.splitlines()[:MAX_LINES]))
        return 1 if failed else 0
    record_path = Path(args.json) if args.json else None
    if record_path is not None:
        refusal = refuse_path(record_path)
        if refusal:
            print(f"verify: refusing --json: {refusal}", file=sys.stderr)
            return 2
    junit_dir = Path(tempfile.mkdtemp(prefix="verify-junit-")) if record_path else None
    records: list[dict[str, object]] = []
    failures: list[tuple[str, str, str, str]] = []
    counts = {gate: {"OK": 0, "FAIL": 0} for gate in GATES}
    skipped: list[str] = []
    warned: dict[str, list[str]] = {}
    checked = 0

    for name in args.project or PROJECTS:
        cwd = qualifies(name)
        if cwd is None:
            skipped.append(name)
            continue
        checked += 1
        pkg = name.replace("-", "_")
        specs = [
            ("compile", ["-m", "compileall", "-q", pkg]),
            (
                "pytest",
                ["-m", "pytest", "-q", "-p", "no:cacheprovider"]
                + ([f"--junitxml={junit_dir / (name + '.xml')}"] if junit_dir else []),
            ),
            ("ruff", ["-m", "ruff", "check", "."]),
            ("format", ["-m", "ruff", "format", "--check", "."]),
            ("mypy", ["-m", "mypy", *mypy_targets(cwd, pkg)]),
        ]
        results = []
        for gate, cmd in specs:
            code, out, err = run(cmd, cwd)
            status = "OK" if code == 0 else "FAIL"
            counts[gate][status] += 1
            results.append(f"{gate}={status}")
            if status == "FAIL":
                failures.append((name, gate, out, err))
        print(f"{name} " + " ".join(results), flush=True)
        notes = typing_warnings(cwd)
        if notes:
            warned[name] = notes
        if junit_dir is not None:
            tests = node_ids(junit_dir / f"{name}.xml", cwd)
            records.append(
                {
                    "project": name,
                    "repo": _git(cwd, "remote", "get-url", "origin"),
                    "sha": _git(cwd, "rev-parse", "HEAD"),
                    "dirty": bool(_git(cwd, "status", "--porcelain")),
                    "gates": {r.split("=")[0]: r.split("=")[1] for r in results},
                    "warnings": warned.get(name, []),
                    "tests": tests,
                    "counts": {
                        o: sum(1 for x in tests if x["outcome"] == o)
                        for o in ("passed", "failed", "error", "skipped")
                    },
                }
            )

    print()
    print("summary")
    for gate in GATES:
        print(f"  {gate:8s} OK={counts[gate]['OK']:3d} FAIL={counts[gate]['FAIL']:3d}")
    print(f"  projects checked = {checked}")
    if skipped:
        print(
            "  skipped = "
            + ", ".join(f"{name} ({skip_reason(name)})" for name in skipped)
        )

    if warned:
        print()
        print("warnings (reported, never failing)")
        kinds: dict[str, list[str]] = {}
        for name, notes in warned.items():
            for note in notes:
                kinds.setdefault(note.split(" (", 1)[0], []).append(name)
        for kind, names in kinds.items():
            print(f"  WARN {kind} ({len(names)}): {', '.join(names)}")

    if failures:
        print()
        print("failure output")
        for name, gate, out, err in failures:
            print()
            print("=" * 72)
            print(f"FAIL {name} :: {gate}")
            print("=" * 72)
            for label, text in (("stdout", out), ("stderr", err)):
                lines = text.splitlines()
                if not lines:
                    continue
                suffix = f", capped at {MAX_LINES}" if len(lines) > MAX_LINES else ""
                print(f"--- {label} ({len(lines)} lines{suffix}) ---")
                for line in lines[:MAX_LINES]:
                    print(line)
    if record_path is not None:
        record = {
            "schema": 1,
            "generated": datetime.datetime.now(datetime.UTC).isoformat(
                timespec="seconds"
            ),
            "root": str(ROOT),
            "tool_versions": tool_versions(),
            "projects": records,
        }
        record_path.parent.mkdir(parents=True, exist_ok=True)
        partial = record_path.with_suffix(record_path.suffix + ".partial")
        partial.write_text(json.dumps(record, indent=1), encoding="utf-8")
        partial.replace(record_path)
        nodes = sum(len(r["tests"]) for r in records if isinstance(r["tests"], list))
        print(f"evidence record: {record_path} ({nodes} test nodes)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
