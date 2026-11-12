# Connecting an agent to projecthub

One command serves every projecthub MCP server on stdio:

```bash
projecthub mcp --toolset read      # the default
projecthub mcp --toolset work      # opt-in: writes the local lease store
projecthub mcp --toolset projects  # GitHub Projects, through gh
```

Register it under the name `projecthub`; the client namespaces the tools under that name.

## Tools

| Toolset | Tool | What it does |
|---|---|---|
| `read` | `forge_status` | Rows by state for every project with a matrix, plus open issue counts |
| `read` | `forge_next` | Rows an agent may start now, ranked, with why others were held back |
| `read` | `forge_packet` | Everything needed to start one row, in one answer |
| `read` | `forge_context` | The prose around a row: blocked notes and dead ends |
| `read` | `forge_propose_status` | Preview a status change and run the guard; never writes |
| `work` | `forge_lease_acquire` | Lease a ready row |
| `work` | `forge_lease_heartbeat` | Extend a live lease |
| `work` | `forge_lease_release` | End a lease: handoff, pr_opened or abandoned |
| `work` | `forge_block` | Mark a leased row blocked |
| `work` | `forge_leases` | Live leases and recent lease events (read-only) |
| `projects` | `projects_auth` | Token scopes and GraphQL rate limit |
| `projects` | `projects_list` | Every project an owner has |
| `projects` | `project_schema` | A board's fields and views |
| `projects` | `project_items` | Items on a board, filtered by field values |
| `projects` | `item_context` | One issue in full context, by URL |
| `projects` | `portfolio_overview` | Where the portfolio stands on its board |

Every `read` and `projects` tool is annotated read-only. `work` is the only toolset that writes, and only to
the local lease store; it never edits a ROADMAP and never touches GitHub. Add it as a second server,
`projecthub-work`, only for an agent that should hold leases.

## Environment

| Variable | Meaning |
|---|---|
| `PROJECTHUB_ROOT` | The workspace holding the projects (falls back to `FORGE_ROOT`, then `D:/code`) |
| `PROJECTHUB_LEASES_DB` | The lease store for `work` (falls back to `FORGE_LEASES_DB`) |
| `PROJECTS_MCP_TOOLSETS` | `projects` groups to serve; default `projects,portfolio` |

The `projects` toolset calls `gh`, so `gh auth status` must pass with the `read:project` scope.

## The command

The MCP servers need the `mcp` extra, so the uvx form names it:

```bash
uvx --from "projecthub[mcp] @ git+https://github.com/b1tninja-labs/projecthub" projecthub mcp --toolset read
```

Installed instead, the command is `projecthub` and the arguments are `mcp --toolset read`:

```bash
pip install "projecthub[mcp] @ git+https://github.com/b1tninja-labs/projecthub"
```

The blocks below use uvx. For the installed package, replace `uvx` and its arguments up to `projecthub` with
`projecthub`.

## Claude Code

From the project directory (`--scope project` writes `.mcp.json`; leave it out to keep the server to yourself):

```bash
claude mcp add --scope project --env PROJECTHUB_ROOT=D:/code --transport stdio projecthub -- uvx --from "projecthub[mcp] @ git+https://github.com/b1tninja-labs/projecthub" projecthub mcp --toolset read
```

Or `.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "projecthub": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "projecthub[mcp] @ git+https://github.com/b1tninja-labs/projecthub",
        "projecthub",
        "mcp",
        "--toolset",
        "read"
      ],
      "env": { "PROJECTHUB_ROOT": "D:/code" }
    }
  }
}
```

## Continue

`.continue/mcpServers/projecthub.yaml` in the workspace. Continue offers MCP tools in agent mode only.

```yaml
name: projecthub
version: 0.0.1
schema: v1
mcpServers:
  - name: projecthub
    type: stdio
    command: uvx
    args:
      - --from
      - "projecthub[mcp] @ git+https://github.com/b1tninja-labs/projecthub"
      - projecthub
      - mcp
      - --toolset
      - read
    env:
      PROJECTHUB_ROOT: D:/code
```

## Cline

Cline panel, MCP Servers, Configure, "Configure MCP Servers", which opens `cline_mcp_settings.json`:

```json
{
  "mcpServers": {
    "projecthub": {
      "command": "uvx",
      "args": [
        "--from",
        "projecthub[mcp] @ git+https://github.com/b1tninja-labs/projecthub",
        "projecthub",
        "mcp",
        "--toolset",
        "read"
      ],
      "env": { "PROJECTHUB_ROOT": "D:/code" },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```
