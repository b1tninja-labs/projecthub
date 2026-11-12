"""Shallow clones of every registered public repository, laid out the way the workspace lays them out.

A workflow has no D:\\code, so the scheduled sync builds one: each project lands in the directory the registry
names (`itulib` in `itulibrary`), cloned from wherever GitHub now keeps it. A repository moved to the
organization is found there, one left on the personal account is found there, and a private or missing one is
reported and skipped, never guessed.

    projecthub checkout --dest "$RUNNER_TEMP/workspace"
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable, Sequence

from projecthub import registry

OWNERS = ("b1tninja", "b1tninja-labs")
Runner = Callable[[list[str]], tuple[int, str]]


def run(argv: list[str]) -> tuple[int, str]:
    env = {
        **os.environ,
        "CLICOLOR_FORCE": "0",
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    done = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )  # noqa: S603
    return done.returncode, done.stdout + done.stderr


def public_repos(owners: Sequence[str], runner: Runner = run) -> dict[str, str]:
    """Lower-cased repository name -> `owner/name`, later owners winning a name both hold."""
    found: dict[str, str] = {}
    for owner in owners:
        code, out = runner(
            [
                "gh",
                "repo",
                "list",
                owner,
                "--visibility",
                "public",
                "--limit",
                "500",
                "--json",
                "nameWithOwner",
            ]
        )
        if code != 0:
            raise SystemExit(
                f"error: could not list {owner}'s repositories: {out.strip()[:200]}"
            )
        for repo in json.loads(out):
            found[repo["nameWithOwner"].split("/")[1].lower()] = repo["nameWithOwner"]
    return found


def plan(repos: dict[str, str]) -> tuple[list[tuple[str, str]], list[str]]:
    """(directory, owner/name) for each registered project with a public repository, and the projects without."""
    wanted, missing = [], []
    for entry in registry.entries():
        full = repos.get(entry.name.lower()) or repos.get(entry.directory.lower())
        if full:
            wanted.append((entry.directory, full))
        else:
            missing.append(entry.name)
    return wanted, missing


def main(argv: Sequence[str] | None = None, runner: Runner = run) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub checkout", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--dest", type=pathlib.Path, required=True)
    parser.add_argument(
        "--owner", action="append", help="owners to look in (default: both)"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    wanted, missing = plan(public_repos(args.owner or OWNERS, runner))
    args.dest.mkdir(parents=True, exist_ok=True)
    failed = []
    for directory, full in wanted:
        target = args.dest / directory
        if (target / ".git").exists():
            continue
        code, out = runner(
            [
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                f"https://github.com/{full}.git",
                str(target),
            ]
        )
        if code != 0:
            failed.append(f"{full}: {out.strip()[:200]}")
    print(f"checked out {len(wanted) - len(failed)} of {len(wanted)} into {args.dest}")
    if missing:
        print(f"no public repository: {', '.join(sorted(missing))}")
    for line in failed:
        print(f"error: {line}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
