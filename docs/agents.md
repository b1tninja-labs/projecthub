# Connecting an agent to projecthub

One command serves every projecthub MCP server on stdio:

```bash
projecthub mcp --toolset read      # the default
projecthub mcp --toolset work      # opt-in: writes the local lease store
projecthub mcp --toolset projects  # GitHub Projects, through gh
```

Register it under the name `projecthub`; the client namespaces the tools under that name. The servers call
themselves `projecthub` (read), `projecthub-work` (work) and `gh-projects` (projects).

## Tools

| Toolset | Tool | What it does |
|---|---|---|
| `read` | `status` | Rows by state for every project with a matrix, plus open issue counts |
| `read` | `next` | Rows an agent may start now, ranked, with why others were held back |
| `read` | `packet` | Everything needed to start one row, in one answer |
| `read` | `context` | The prose around a row: blocked notes and dead ends |
| `read` | `propose_status` | Preview a status change and run the guard; never writes |
| `read` | `query` | Rows across projects by state, milestone, test, lease and text |
| `read` | `graph` | Project dependencies, their order and cycles |
| `read` | `plan` | What to do now: ready rows, what is blocked and on what, work in progress |
| `work` | `claim` | Lease a ready row |
| `work` | `heartbeat` | Extend a live lease |
| `work` | `release` | End a lease: handoff, pr_opened, abandoned or parked |
| `work` | `block` | Mark a leased row blocked |
| `work` | `leases` | Live leases and recent lease events (read-only) |
| `work` | `note` | Log a note on the leased row; extends the lease |
| `work` | `park` | End the lease with a resume note for the next holder |
| `work` | `propose` | Propose new work; it lands in the board's Triage |
| `work` | `submit` | Queue an evidence check of a commit for the leased row |
| `work` | `log` | Recent events on one row (read-only) |
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
| `PROJECTHUB_ROOT` | The workspace holding the projects (default `D:/code`) |
| `PROJECTHUB_LEASES_DB` | The lease store for `work` (default `leases.sqlite` in the state directory) |
| `PROJECTHUB_BOARD` | The Project number `projecthub board` works on (default `PROJECTHUB_NUMBER`, then 1) |
| `PROJECTHUB_ORG` | `projecthub sync`: owner for a project whose checkout has no remote (default `b1tninja`) |
| `PROJECTHUB_ISSUE_REPO` | `projecthub sync`: the one repository every roadmap issue lives in; set it empty for one repository per project |
| `PROJECTHUB_EVIDENCE` | A `projecthub shells verify --json` record that `packet` quotes |
| `PROJECTHUB_MANIFEST` | Where `projecthub shells generate` records what it last wrote |
| `PROJECTHUB_ALLOW_HEADING_LOSS` | Let a commit delete a markdown heading on purpose (the pre-commit hook) |
| `PROJECTS_MCP_TOOLSETS` | `projects` groups to serve; default `projects,portfolio` |

The state directory is `projecthub` under `LOCALAPPDATA` (else `XDG_STATE_HOME`, else `~/.local/state`). A
machine that already has the `forge` directory there from before the rename, and no `projecthub` one, keeps
using it, so an existing lease database is not left behind.

### Names from before the rename

This system used to be called sdk-forge. Each `PROJECTHUB_` variable above is still read under its old name
when the new one is unset or empty, so an existing CI secret, profile or script keeps working. New
configuration should use the new names.

| Variable | Old name, read as a fallback |
|---|---|
| `PROJECTHUB_ROOT` | `FORGE_ROOT` |
| `PROJECTHUB_LEASES_DB` | `FORGE_LEASES_DB` |
| `PROJECTHUB_BOARD` | `FORGE_BOARD` |
| `PROJECTHUB_ORG` | `FORGE_ORG` |
| `PROJECTHUB_ISSUE_REPO` | `FORGE_ISSUE_REPO` |
| `PROJECTHUB_EVIDENCE` | `FORGE_EVIDENCE` |
| `PROJECTHUB_MANIFEST` | `FORGE_MANIFEST` |
| `PROJECTHUB_ALLOW_HEADING_LOSS` | `FORGE_ALLOW_HEADING_LOSS` |

The MCP tools lost their prefix in the same change:

| Was | Now |
|---|---|
| `forge_status`, `forge_next`, `forge_packet`, `forge_context`, `forge_propose_status` | `status`, `next`, `packet`, `context`, `propose_status` |
| `forge_lease_acquire`, `forge_lease_heartbeat`, `forge_lease_release` | `claim`, `heartbeat`, `release` |
| `forge_block`, `forge_leases` | `block`, `leases` |
| server `sdk-forge`, server `forge-lease` | server `projecthub`, server `projecthub-work` |

Matrix regions marked `<!-- forge:matrix ... -->` by older exports are still read; new exports mark them
`<!-- projecthub:matrix ... -->`.

The `projects` toolset calls `gh`, so `gh auth status` must pass with the `read:project` scope.

## The command

`mcp` is a dependency of projecthub, so uvx needs only the repository:

```bash
uvx --from git+https://github.com/b1tninja-labs/projecthub projecthub mcp --toolset read
```

Installed instead, the command is `projecthub` and the arguments are `mcp --toolset read`:

```bash
pip install git+https://github.com/b1tninja-labs/projecthub
```

The older `projecthub[mcp] @ git+...` form still works: the `mcp` extra is kept, empty.

The blocks below use uvx. For the installed package, replace `uvx` and its arguments up to `projecthub` with
`projecthub`.

## Claude Code

From the project directory (`--scope project` writes `.mcp.json`; leave it out to keep the server to yourself):

```bash
claude mcp add --scope project --env PROJECTHUB_ROOT=D:/code --transport stdio projecthub -- uvx --from git+https://github.com/b1tninja-labs/projecthub projecthub mcp --toolset read
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
        "git+https://github.com/b1tninja-labs/projecthub",
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
      - "git+https://github.com/b1tninja-labs/projecthub"
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
        "git+https://github.com/b1tninja-labs/projecthub",
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
