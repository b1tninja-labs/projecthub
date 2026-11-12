"""`projecthub shells <command>`: one entry point for the generator, the gates and the git hook."""

from __future__ import annotations

import importlib
import pathlib
import sys
from collections.abc import Callable

# name -> (module, what it does). Each module's main takes argv; the generator's is run().
COMMANDS: dict[str, tuple[str, str]] = {
    "generate": (
        "projecthub.shells.generate",
        "render the shells: --dry-run/--check, --diff, --project NAME, --only KIND, --plan NAME",
    ),
    "verify": (
        "projecthub.shells.verify",
        "the five gates across the projects: --project NAME, --rust PATH, --json PATH",
    ),
    "names": (
        "projecthub.shells.names",
        "is a PyPI or crates.io name free: --index, --crates",
    ),
    "hygiene": (
        "projecthub.shells.hygiene",
        "check the staged index for lost headings and scratch; --install wires the hook",
    ),
    "hook": (
        "projecthub.shells.hygiene",
        "what an installed git hook runs: hook pre-commit",
    ),
    "rust": (
        "projecthub.shells.rust",
        "create a Cargo crate or workspace: rust new NAME --dest DIR",
    ),
    "workspace": (
        "projecthub.shells.workspace",
        "declared sibling dependencies: --check, --bootstrap PROJECT",
    ),
}
HOOKS = ("pre-commit",)


def usage() -> str:
    lines = ["usage: projecthub shells <command> [args]", ""]
    lines += [f"  {name:<10} {what}" for name, (_, what) in COMMANDS.items()]
    return "\n".join(lines)


def hook(argv: list[str]) -> int:
    """Run the named git hook against the repository git invoked it in (the current directory)."""
    if argv[:1] != ["pre-commit"] or len(argv) > 1:
        print("usage: projecthub shells hook pre-commit", file=sys.stderr)
        return 2
    from projecthub.shells import hygiene

    return hygiene.report(pathlib.Path.cwd())


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print(usage())
        return 0 if argv[:1] in ([], ["-h"], ["--help"]) else 2
    name, rest = argv[0], argv[1:]
    if name == "hook":
        return hook(rest)
    module = importlib.import_module(COMMANDS[name][0])
    entry: Callable[[list[str]], int | None] = (
        module.run if name == "generate" else module.main
    )
    sys.argv = [f"projecthub shells {name}", *rest]
    return int(entry(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
