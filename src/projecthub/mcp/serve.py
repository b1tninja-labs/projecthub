"""One MCP entry point: `projecthub mcp --toolset read|work|projects` serves that server on stdio.

`read` is the default and only reads. `work` is opt-in because its tools write the local lease store
(PROJECTHUB_LEASES_DB). `projects` reads GitHub Projects through `gh`; PROJECTS_MCP_TOOLSETS still selects its
groups. Each server is built by its own module's `build()`; this module only chooses one and runs it.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Any

TOOLSETS = {
    "read": "projecthub.mcp.read",
    "work": "projecthub.mcp.work",
    "projects": "projecthub.github.projects_mcp",
}
DEFAULT = "read"
MISSING_MCP = (
    "the MCP servers need the mcp package, a dependency of projecthub that is not installed here: "
    "pip install git+https://github.com/b1tninja-labs/projecthub, or run it with "
    "uvx --from git+https://github.com/b1tninja-labs/projecthub projecthub mcp"
)


def build(toolset: str = DEFAULT) -> Any:
    """The MCPServer for one toolset, built by that toolset's module."""
    if toolset not in TOOLSETS:
        raise ValueError(
            f"unknown toolset {toolset!r}; choose one of {', '.join(TOOLSETS)}"
        )
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
