"""planforge: one command in front of the database, the GitHub sync, the MCP servers and the evidence checks."""

from __future__ import annotations

import importlib
import sys

COMMANDS = {
    "db": ("planforge.db.store", "lossless matrix store: roundtrip, check-markers"),
    "import": (
        "planforge.db.importer",
        "import every ROADMAP/PLAN into typed entries, with reports",
    ),
    "sync": ("planforge.github.sync", "project obligations onto GitHub issues"),
    "board": ("planforge.github.board", "build and check the GitHub Project"),
    "deps": ("planforge.deps", "dependency state from disk"),
    "next": ("planforge.mcp.loop", "name the next startable obligation"),
    "leases": (
        "planforge.mcp.work",
        "list or steal leases; with no arguments, the work MCP server",
    ),
    "mcp-read": (
        "planforge.mcp.read",
        "the read-only planning MCP server, and the status and claim commands",
    ),
    "mcp-projects": (
        "planforge.github.projects_mcp",
        "the read-only GitHub Projects MCP server",
    ),
    "evidence": (
        "planforge.evidence.check",
        "L1, L3 and L4 for rows that flipped to done",
    ),
    "mutate": ("planforge.evidence.mutate", "scoped mutation for one row"),
    "export": ("planforge.site.export", "write portfolio.json for the site"),
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print("usage: planforge <command> [args]\n")
        for name, (_, what) in COMMANDS.items():
            print(f"  {name:<13} {what}")
        return 0 if argv[:1] in ([], ["-h"], ["--help"]) else 2
    module = importlib.import_module(COMMANDS[argv[0]][0])
    sys.argv = [f"planforge {argv[0]}", *argv[1:]]
    result = (
        module.main(sys.argv[1:])
        if module.__name__ == "planforge.mcp.work"
        else module.main()
    )
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
