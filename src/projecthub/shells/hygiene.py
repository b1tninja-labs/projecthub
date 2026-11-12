"""Refuse a commit that deletes structure by accident or adds scratch to a tracked tree.

Both checks exist because they were needed, repeatedly, in one working session: markdown headings were
dropped five separate times by replacement edits that matched a heading line and omitted it from the
replacement text, and scratch output landed in repository trees where a later `git add -A` swept it into
a commit. Neither is caught by a linter or a type checker. Both produce a diff that looks like a
documentation edit, so they survive review until someone counts headings, which nobody does.

The rule is deliberately narrow and it never rewrites anything: if headings vanished, the commit stops
and says which ones. Decide on purpose, then commit again with PROJECTHUB_ALLOW_HEADING_LOSS=1.

    projecthub shells hygiene              # inspect the staged index
    projecthub shells hygiene --install    # wire it up as this repository's pre-commit hook
    projecthub shells hook pre-commit      # what the installed hook runs
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
from collections import Counter

from projecthub import paths

# probe_* was the shape scratch actually took here; .log/.out/.err were the redirection targets that got
# committed. Anything matching is refused on the way in, and only reported (not refused) if a commit
# adds nothing new to it.
SCRATCH_RE = re.compile(r"(?:^|/)(?:probe_[^/]+\.py|[^/]+\.(?:log|out|err|diff))$")
HEADING_RE = re.compile(r"^#{1,6} \S.*$", re.MULTILINE)
# Read through projecthub.paths.env, so the spelling commands and notes used before the rename still works.
ALLOW = "PROJECTHUB_ALLOW_HEADING_LOSS"


def git(args: list[str], cwd: pathlib.Path) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return done.stdout if done.returncode == 0 else ""


def staged_markdown(cwd: pathlib.Path) -> list[str]:
    """Markdown paths added or modified in the index."""
    out = git(
        ["diff", "--cached", "--name-only", "--diff-filter=ACM", "--", "*.md"], cwd
    )
    return [line for line in out.splitlines() if line]


def staged_scratch(cwd: pathlib.Path) -> list[str]:
    """Newly added files whose names mark them as scratch."""
    out = git(["diff", "--cached", "--name-only", "--diff-filter=A"], cwd)
    return [line for line in out.splitlines() if SCRATCH_RE.search(line)]


def heading_regression(before: str, after: str) -> list[str]:
    """Headings present in `before` and gone from `after`, order-insensitive, duplicates preserved.

    Duplicates matter: a document with the same sub-heading twice loses one silently if the comparison
    is a set difference, which is exactly the case a careless replace produces most often.
    """
    lost: list[str] = []
    remaining = Counter(HEADING_RE.findall(after))
    for heading in HEADING_RE.findall(before):
        if remaining[heading] > 0:
            remaining[heading] -= 1
        else:
            lost.append(heading)
    return lost


def check(cwd: pathlib.Path) -> list[str]:
    """Every reason this staged state should not become a commit."""
    problems: list[str] = []
    if not paths.env("ALLOW_HEADING_LOSS"):
        for path in staged_markdown(cwd):
            before = git(["show", f"HEAD:{path}"], cwd)
            if not before:
                continue  # new file: nothing to regress against
            after = git(["show", f":{path}"], cwd)
            for heading in heading_regression(before, after):
                problems.append(f"{path}: heading vanished: {heading}")
    for path in staged_scratch(cwd):
        problems.append(
            f"{path}: looks like scratch; commit it outside the tree or rename it"
        )
    return problems


HOOK = """#!/bin/sh
# Installed by `projecthub shells hygiene --install`; it lives in src/projecthub/shells/githooks of a
# projecthub checkout, and the workspace points core.hooksPath at that directory.
# Stops a commit that deletes a markdown heading by accident or adds scratch to the tree.
# Escape hatch: git commit --no-verify, or PROJECTHUB_ALLOW_HEADING_LOSS=1 for the heading check only.

# A path the interpreter can read: MSYS sh on Windows prints /d/code, which python does not understand,
# and `pwd -W` gives D:/code there. Elsewhere `pwd -W` fails and plain `pwd` is the answer.
native() { (cd "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd; }); }

# This file is three directories below the checkout's src directory. PROJECTHUB_SRC overrides that, for
# a hooks directory copied elsewhere; with neither, an installed projecthub has to be importable.
SRC="${PROJECTHUB_SRC:-$(native "$(dirname "$0")/../../..")}"
case "$(uname -s 2>/dev/null)" in
  MINGW* | MSYS* | CYGWIN*) SEP=";" ;;
  *) SEP=":" ;;
esac
if [ -n "$SRC" ] && [ -f "$SRC/projecthub/shells/hygiene.py" ]; then
  PYTHONPATH="$SRC${PYTHONPATH:+$SEP$PYTHONPATH}"
  export PYTHONPATH
fi

# `python` and `py` are not interchangeable on Windows boxes: one is often absent while the other is
# the launcher, and a `python` on PATH can be a stub that runs nothing. The import below is the test.
PY="${PYTHON:-python}"
command -v "$PY" >/dev/null 2>&1 || PY=py

if ! command -v "$PY" >/dev/null 2>&1 || ! "$PY" -c "import projecthub.shells.hygiene" >/dev/null 2>&1; then
  # Deliberately fatal. A gate that cannot run and exits 0 reports the same green as a clean tree,
  # which is how a check becomes decoration without anyone deciding to remove it.
  echo "pre-commit: cannot run the projecthub hygiene check (projecthub not importable or no interpreter)." >&2
  echo "pre-commit: set PROJECTHUB_SRC or PYTHON, or bypass with --no-verify if you accept no check ran." >&2
  exit 1
fi
exec "$PY" -m projecthub shells hook pre-commit
"""

MARK = "# Installed by `projecthub shells hygiene --install`"
# The first line of every hook the old generator installed; one of those may be replaced too.
OLD_MARK = "# Installed by " + "sdk-forge/check_hygiene.py --install."
HOOKS = pathlib.Path(__file__).resolve().parent / "githooks"


def install(cwd: pathlib.Path) -> int:
    """Point this repository's hooks path at projecthub's githooks directory, writing the hook if needed."""
    HOOKS.mkdir(parents=True, exist_ok=True)
    hook = HOOKS / "pre-commit"
    if hook.exists():
        text = hook.read_text(encoding="utf-8")
        if MARK not in text and OLD_MARK not in text:
            print(f"refusing to overwrite an existing hook at {hook}", file=sys.stderr)
            return 1
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    done = subprocess.run(  # noqa: S603
        [
            "git",
            "config",
            "core.hooksPath",
            str(HOOKS.relative_to(cwd) if HOOKS.is_relative_to(cwd) else HOOKS),
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        print(
            f"git config core.hooksPath failed: {done.stderr.strip()}", file=sys.stderr
        )
        return 1
    print(f"hook installed: {cwd} -> {HOOKS}")
    return 0


def report(cwd: pathlib.Path) -> int:
    """Run the checks on `cwd`'s staged index and print why it may not be committed; 1 if it may not."""
    problems = check(cwd)
    for problem in problems:
        print(f"hygiene: {problem}", file=sys.stderr)
    if problems:
        print(
            f"{len(problems)} problem(s); {ALLOW}=1 to override the heading check",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub shells hygiene", description=__doc__
    )
    parser.add_argument(
        "--install", action="store_true", help="set core.hooksPath and write the hook"
    )
    parser.add_argument(
        "--repo", default=".", help="repository to inspect or install into"
    )
    args = parser.parse_args(argv)
    cwd = pathlib.Path(args.repo).resolve()

    if args.install:
        return install(cwd)
    return report(cwd)


if __name__ == "__main__":
    raise SystemExit(main())
