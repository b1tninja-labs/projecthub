# Connecting an agent to projecthub

One command serves every projecthub MCP server on stdio:

```bash
projecthub mcp --toolset read      # the default
projecthub mcp --toolset work      # opt-in: claims on Project 1, and the local outbox
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
| `work` | `claim` | Claim a ready row on Project 1: written there, re-read, and won only if it stood |
| `work` | `heartbeat` | Extend a live claim; LEASE_LOST means someone else holds it now |
| `work` | `release` | End a claim: handoff, pr_opened, abandoned or parked |
| `work` | `block` | Mark a claimed row blocked |
| `work` | `leases` | This machine's mirror of live claims, and recent lease events (read-only) |
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

Every `read` and `projects` tool is annotated read-only. `work` is the only toolset that writes. `claim` writes
to GitHub Project 1 directly, because the caller must know whether it won; every other `work` tool writes the
local store and its outbox, which `projecthub drain` sends to the Project. It never edits a ROADMAP. Add it as
a second server, `projecthub-work`, only for an agent that should hold claims.

## Claims on Project 1

Project 1 is the authority on who works what. The local SQLite store is a write queue (the outbox) and a
short-lived read cache; it decides nothing. Note, block and release text stays in it and is never published.

A claim writes four fields to the item in one request, then waits a few seconds and reads the item again:

| Field | Value |
|---|---|
| Agent | `<holder> [<first 8 characters of the lease id>]`, the claim token |
| Claimed | the UTC date |
| Status | In progress |
| Claim expires | `YYYY-MM-DDTHH:MM:SSZ` in UTC (a TEXT field: a DATE would hold a crashed claim until midnight) |

If the re-read shows another token, a later writer won and the claim is refused `LEASE_HELD`. An item is
claimable when its Status is Backlog, Ready or In progress and it has no live claim: no Agent, or a Claim
expires that is missing or past. So a claim whose holder stopped heartbeating is taken over once it expires.

| Call | What reaches the Project |
|---|---|
| `claim` | Agent, Claimed, Status In progress, Claim expires, Last activity, at once |
| `heartbeat`, `note` | a later Claim expires (and the Agent token, to restore one a withdrawn rival cleared) |
| `release handoff`, `abandoned`, `parked` | Status Ready; Agent and Claim expires cleared |
| `release pr_opened`, `submit` | Status In review; release clears Claim expires and keeps Agent |
| `block` | Status Blocked; Claim expires cleared, Agent kept to show who is waiting |

Everything after the claim goes through the outbox, so it works offline; the drain writes a queued field only
to an item whose Agent is empty or still this claim's token. A claim itself is refused when the Project cannot
be reached (`OFFLINE`), when the rate budget is under its write floor (`RATE_BUDGET_LOW`, with the reset time),
or when the Project has no Claim expires field (`NO_CLAIM_FIELD`). A person creates that field once:

```bash
projecthub leases ensure-claim-field
```

`projecthub pickup` and `next` read the Project's items once per run, through a cache of about 60 seconds, and
pass over any item someone holds with an unexpired claim. `pickup` exits 3 for NO WORK, 4 when the caller
already holds a claim, and 5 (`CANNOT CLAIM`) when the Project cannot be reached or the budget is low. A row
the matrix declares `[no]` is a non-goal and is never ready.

After a drain writes anything, it sends one `repository_dispatch` of type `projecthub-state-changed` to the
portfolio repository, at most once every ten minutes; that workflow rebuilds the public site from Project 1.
`projecthub budget` prints the GraphQL and REST budget, and [rate-budget.md](rate-budget.md) has the numbers.

## Environment

| Variable | Meaning |
|---|---|
| `PROJECTHUB_ROOT` | The workspace holding the projects (default `D:/code`) |
| `PROJECTHUB_LEASES_DB` | The local store: lease mirror, outbox and board cache (default `leases.sqlite` in the state directory) |
| `PROJECTHUB_OFFLINE` | Set to make no GitHub request at all: claims are refused, everything else queues. The tests set it |
| `PROJECTHUB_READ_FLOOR`, `PROJECTHUB_WRITE_FLOOR` | GraphQL points under which reads use the cache (50) and writes stop (250) |
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
