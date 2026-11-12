"""projecthub: one command in front of the database, the GitHub sync, the MCP servers and the evidence checks."""

from __future__ import annotations

import importlib
import sys

COMMANDS = {
    "app": ("projecthub.app", "register the GitHub App from its manifest, once"),
    "checkout": (
        "projecthub.checkout",
        "shallow-clone every registered public repo, as the workspace lays them out",
    ),
    "db": ("projecthub.db.store", "lossless matrix store: roundtrip, check-markers"),
    "import": (
        "projecthub.db.importer",
        "import every ROADMAP/PLAN into typed entries, with reports",
    ),
    "sync": ("projecthub.github.sync", "project obligations onto GitHub issues"),
    "board": ("projecthub.github.board", "build and check the GitHub Project"),
    "deps": ("projecthub.deps", "dependency state from disk"),
    "pickup": (
        "projecthub.mcp.loop",
        "claim the next row and print it as your assignment",
    ),
    "next": ("projecthub.mcp.loop", "name the next startable obligation"),
    "leases": (
        "projecthub.mcp.work",
        "list or steal leases; with no arguments, the work MCP server",
    ),
    "mcp-read": (
        "projecthub.mcp.read",
        "the read-only planning MCP server, and the status and claim commands",
    ),
    "mcp-projects": (
        "projecthub.github.projects_mcp",
        "the read-only GitHub Projects MCP server",
    ),
    "evidence": (
        "projecthub.evidence.check",
        "L1, L3 and L4 for rows that flipped to done",
    ),
    "mutate": ("projecthub.evidence.mutate", "scoped mutation for one row"),
    "site": (
        "projecthub.site.live",
        "write the site's data from the product repos and Project 1",
    ),
    "export": ("projecthub.site.export", "write portfolio.json for the site"),
    "shells": ("projecthub.shells.cli", "generate, verify and hook the SDK shells"),
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print("usage: projecthub <command> [args]\n")
        for name, (_, what) in COMMANDS.items():
            print(f"  {name:<13} {what}")
        return 0 if argv[:1] in ([], ["-h"], ["--help"]) else 2
    module = importlib.import_module(COMMANDS[argv[0]][0])
    rest = ["pickup", *argv[1:]] if argv[0] == "pickup" else argv[1:]
    sys.argv = [f"projecthub {argv[0]}", *rest]
    result = (
        module.main(sys.argv[1:])
        if module.__name__ == "projecthub.mcp.work"
        else module.main()
    )
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
