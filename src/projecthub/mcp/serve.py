"""One MCP entry point: `projecthub mcp --toolset read|work|projects` serves that server on stdio.

`read` is the default and only reads. `work` is opt-in because its tools write the local lease store
(FORGE_LEASES_DB). `projects` reads GitHub Projects through `gh`; PROJECTS_MCP_TOOLSETS still selects its
groups. Each server is built by its own module's `build()`; this module only chooses one and runs it.

The servers still read FORGE_ROOT and FORGE_LEASES_DB. PROJECTHUB_ROOT and PROJECTHUB_LEASES_DB are read
first and, when set, win: `build` copies them over the old names before it imports a server module, which
reads them at import time.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from typing import Any

TOOLSETS = {
    "read": "projecthub.mcp.read",
    "work": "projecthub.mcp.work",
    "projects": "projecthub.github.projects_mcp",
}
DEFAULT = "read"
# The projecthub name, then the name the server modules still read.
ENV_ALIASES = {
    "PROJECTHUB_ROOT": "FORGE_ROOT",
    "PROJECTHUB_LEASES_DB": "FORGE_LEASES_DB",
}
MISSING_MCP = (
    "the MCP servers need the mcp extra: pip install 'projecthub[mcp]', or with uvx: "
    "uvx --from 'projecthub[mcp] @ git+https://github.com/b1tninja-labs/projecthub' projecthub mcp"
)


def bridge_env(environ: dict[str, str] | None = None) -> None:
    """Copy each PROJECTHUB_* variable that is set over the FORGE_* variable the servers read."""
    env = os.environ if environ is None else environ
    for new, old in ENV_ALIASES.items():
        if env.get(new):
            env[old] = env[new]


def build(toolset: str = DEFAULT) -> Any:
    """The MCPServer for one toolset, built by that toolset's module."""
    if toolset not in TOOLSETS:
        raise ValueError(
            f"unknown toolset {toolset!r}; choose one of {', '.join(TOOLSETS)}"
        )
    bridge_env()
    return importlib.import_module(TOOLSETS[toolset]).build()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub mcp",
        description="Serve one projecthub MCP toolset on stdio.",
    )
    parser.add_argument(
        "--toolset",
        choices=sorted(TOOLSETS),
        default=DEFAULT,
        help="read (default): planning reads; work: leases, writes the local lease store; "
        "projects: GitHub Projects reads",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        import anyio
        import mcp  # noqa: F401
    except ImportError:
        print(MISSING_MCP, file=sys.stderr)
        return 2

    anyio.run(build(args.toolset).run_stdio_async)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
