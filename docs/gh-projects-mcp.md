# A GitHub Projects interface for the forge

Status: built, and registered with the Cline CLI. Corrected 2026-11-07 by the change that added `loop.py`. The
sections below keep the original reasoning and the measured API facts, which are still what the design rests on;
two claims in them are now false, and both were checked rather than assumed.

- **The scope blocker in section 1 is gone.** `gh auth status` on this machine now reports `gist`, `project`,
  `read:org`, `repo`, `workflow`, and `gh project list --owner b1tninja` returns board #4.
  `ghprojects.MissingScope` still names `gh auth refresh -s project`, which remains the right answer for a
  machine that has not been refreshed.
- **`projects_mcp.py` exists and runs.** Verified over stdio with a real MCP client on 2026-11-07: server
  `gh-projects`, protocol `2025-11-25`, six tools (`projects_auth`, `projects_list`, `project_schema`,
  `project_items`, `item_context`, `portfolio_overview`), and `portfolio_overview` answered
  "0 of 2 active slots used" from the live board. It is registered in
  `~/.cline/data/settings/cline_mcp_settings.json` with all six reads auto-approved - safe only because there
  is no write tool (decision D23), so a future write tool must not join that list by reflex.

What the read side is for: `sdk-forge/loop.py` composes these same views to name the one obligation that can be
started next, and nothing else. It decides no more than the board has been told. `board.py` leaves every row at
Backlog and states that it never moves Status forward, so a row outside an Active project needs a person to mark
it Ready, and Active itself stays a person's field. That limit is deliberate: the portfolio's failure mode is
prose outrunning code, and a selector that could promote its own third project would be prose with a cron
behind it. As of 2026-11-07 the portfolio has 51 of 384 obligation rows done, across 4 projects of 35.

## 1. Blocker: the token has no project scope

`gh auth status` on this machine reports the active account `b1tninja` with scopes
`gist`, `read:org`, `repo`, `workflow`. Both `gh project list --owner b1tninja` and a raw GraphQL
`viewer { projectsV2 }` query fail with `INSUFFICIENT_SCOPES` (needs `read:project`; writes need `project`).

Likely cause: the scope was granted in another terminal or for another account/host, and this keyring
entry was not the one refreshed. The fix is the user's to run, because it changes the credential:

    gh auth refresh -s project

Design consequence: the tool must detect this itself and say exactly that, instead of returning an empty
board. `sync_roadmap.graphql()` today prints the error and returns `""`, which `board_urls()` turns into
"the board has no issues", so a scope failure is indistinguishable from an empty board.

## 2. What GitHub offers (from the docs, fetched 2026-11-06)

- **GraphQL** is the complete surface. `read:project` to query, `project` to mutate. Items are added with
  `addProjectV2ItemById` (idempotent: returns the existing item) or `addProjectV2DraftIssue`; values are set
  with `updateProjectV2ItemFieldValue`; removal is `deleteProjectV2Item`. You cannot add and update an item
  in one call. Assignees, labels, milestone and repository are not project fields: they change through
  issue mutations. Items the caller cannot see come back as `REDACTED`.
- **REST** now exists (API version `2026-03-10`): items (list, add by `{type, owner, repo, number}`, get,
  PATCH `fields: [{id, value}]` with `null` to clear, delete), fields (list, get, **create** - no update or
  delete documented), and views (**create**, with layout table/board/roadmap, filter, visible fields,
  grouping). Cursor pagination, `per_page` max 100. Views could not be created at all via GraphQL.
- **Token limits**: the views page states the user-owned endpoint does not work with GitHub App tokens or
  fine-grained PATs. `b1tninja` is a user account, so the working credential is the classic `gh` OAuth token.
  Unverified: the same restriction for user-owned items and fields; the docs did not say.
- **Prior art**: the official `github/github-mcp-server` has an opt-in `projects` toolset with three tools,
  `projects_list`, `projects_get`, `projects_write` (50 per page, 50 items per write, titles only unless
  `fields`/`field_names` is given, skipped under `--read-only`). It is generic CRUD. It knows nothing about
  ROADMAP rows, evidence or drift.

Not read: the remaining ~31k characters of the MCP server page, REST item PATCH for user-owned projects,
GraphQL rate-limit costs, and project size limits. None of the pages fetched stated limits.

## 2b. Capability inventory (docs.github.com, fetched 2026-11-06)

| Capability | What it is | API reach |
|---|---|---|
| Items | Issues, PRs and draft issues. Edits sync both ways with the issue or PR. | GraphQL and REST. Draft to issue: `convertProjectV2DraftIssueItemToIssue`. Archive: `archiveProjectV2Item` / `unarchiveProjectV2Item`. |
| Fields | Built-ins (assignees, labels, milestone, repository, reviewers, linked PRs, parent issue, sub-issue progress, issue type) plus custom text, number, date, single select, iteration. **50 fields per project, built-ins included.** | GraphQL: create, `updateProjectV2Field`, `deleteProjectV2Field`. REST: list, get, create only. Built-ins other than title are changed through issue mutations, not project field writes. |
| Views | Table, board, roadmap; filter, sort, group, slice, visible fields; saved and shared. | REST create (and list/update; I only read the create page). Not creatable through GraphQL. |
| Charts / insights | Configurable charts over item data, visible to anyone who can view the project. | No API found in the pages read. Treat as UI-only until shown otherwise. |
| Built-in workflows | Set a field when an item is added or changed; auto-archive; auto-add from a repository by filter. | No API found. Configured in the UI; templates carry workflows except auto-add. |
| Status updates | On track / At risk, start and target dates, Markdown body; shown in the header and project lists. | GraphQL `create`/`update`/`deleteProjectV2StatusUpdate`. |
| Templates | Org-level, carry views, fields, draft issues, workflows (not auto-add), insights. | `markProjectV2AsTemplate` / `copyProjectV2` exist by name in the schema; behaviour not confirmed. |
| Events | `projects_v2_item` webhook on item changes. | Webhook; useful for a drift alarm instead of polling. |

Limits found: 50 fields per project; GraphQL `first`/`last` 1 to 100 (so `first: 200` in
`board_urls()` is wrong, now confirmed against the rate-limit page); 500,000 nodes per request; 5,000
points per hour per user token; 2,000 points per minute secondary; at most 100 concurrent requests; a
mutation counts 5 points toward the secondary limit. A maximum for items per project was not stated on any
page read. Sub-issue hierarchy and issue types are listed as fields but their semantics were not covered by
the pages read.

Consequence for the forge: the board's schema is bounded at 50 fields, so section 5's field list must stay
small, and views and workflows are configured once by a human or a one-off script, not synced.

## 2c. Is there already a native MCP interface? Yes.

`github/github-mcp-server` has a `projects` toolset, off by default (defaults are context, repos, issues,
pull_requests, users). Enable with `--toolsets projects` or `GITHUB_TOOLSETS=projects`; the hosted endpoint
is `https://api.githubcopilot.com/mcp/`. Three tools, each dispatching on a `method` argument:

- `projects_list` (`read:project`): `list_projects`, `list_project_items`, `list_project_fields`,
  `list_project_views`, `list_project_status_updates`. `perPage` max 50; `query` takes project filter
  syntax; `owner_type` auto-detected.
- `projects_get` (`read:project`): `get_project_field`, `get_project_item`, `get_project_view`,
  `get_project_status_update`. Titles only unless `fields` or `field_names` is passed.
- `projects_write` (`project`): `create_project`, `add_project_item`, `update_project_item`,
  `update_project_items` (max 50), `delete_project_item`, `create_iteration_field`,
  `create_project_status_update`, plus view create/update/delete (method names not on the part of the page I
  read). Field values can be addressed by `name` as well as `id`; `null` clears.

I read the first ~100k of ~120k characters of its README, so a method listed after that point could be missing
from this list. What it does **not** appear to offer: creating text/number/date/single-select fields (only
iteration), updating or deleting a field, archiving items, converting drafts, linking a repository, batching
more than 50, any scope pre-check, any notion of a source of truth, and any drift report. The `method`-dispatch
shape is also worth copying or avoiding on purpose: three tools keep context cost low, at the price of a
wider argument schema per tool.

## 3. Decision: build the semantic layer, not another CRUD wrapper

**Revised after 2c.** Because a native server already covers generic list/get/write, a standalone
`gh-project-mcp` would mostly duplicate it. The extraction candidate shrinks to what the official server does
not do (schema-by-name resolution, scope pre-check, aliased batch writes, field create/update, typed errors),
and may not be worth a repository at all: propose those upstream, or keep them as a small client library.
Recommended order: run the official server for ad-hoc work, build `board.py` on a thin `ghprojects.py`, and
decide on extraction only after the thin client has outgrown the forge.

The official server already covers ad-hoc browsing and edits. What it cannot do is the forge's actual job,
which `planner.py` states: ROADMAP.md is authoritative, a row flips only against an existing named test, and
every read names its source. So two layers, with a hard boundary between them:

| Layer | Module | Knows about | Becomes |
|---|---|---|---|
| Transport and schema | `ghprojects.py` | Projects v2 only. No ROADMAP, no `b1tninja`, no `D:/code`. | the extracted `gh-project-mcp` repo |
| Roadmap projection | `board.py` | rows, `STATUSES`, markers, drift | stays in sdk-forge |

The boundary is the extraction plan: `ghprojects.py` may import nothing from `sync_roadmap`, `planner` or
`verify`, and gets its own test file. When it is ready to leave, it moves with `git filter-repo --path`
and the forge depends on it. Until then it is one flat module like the rest of this directory.

## 4. `ghprojects.py` (transport)

- Shell out to `gh api graphql` as `sync_roadmap` does, so the keyring token is the only credential.
  Inject the runner (`run: Callable[[list[str]], Result]`) so tests use a fake and never touch the network.
- Pass values as GraphQL **variables** (`-F`/`-f`), not f-string interpolation. `board_urls()` currently
  interpolates the owner into the query text.
- Look up owners with `repositoryOwner(login:)`; `user(login:)` is hard-coded today and fails for an org.
- Page every list with `pageInfo { hasNextPage endCursor }`. A connection's `first` is capped at 100;
  `board_urls()` asks for `first: 200`, which I expect GitHub to reject (not verified; no scope to try).
- Typed errors, never `""`: `MissingScope(needed, fix_command)`, `NotFound`, `RateLimited(reset_at)`,
  `Redacted`. Read the granted scopes from the `X-OAuth-Scopes` header (`gh api -i user`) once per process.
- Schema cache: one query returns project id, fields, single-select options and iterations. Writers take
  field **names** and resolve them to ids from this cache, and refuse an unknown option name loudly (no
  fuzzy matching; listing the valid options is the error message).
- Batched mutations via aliased fields (the pattern `sync_board` already uses), capped at 50 per request,
  with per-alias error reporting so one failure does not hide the rest.
- Value encoder per data type: text, number, date (ISO), single select (option id), iteration (id),
  clear (`clearProjectV2ItemFieldValue`).
- Write surface is deliberately missing: no `deleteProjectV2`, no `deleteProjectV2Item`, no field deletion.
  A board can be rebuilt from ROADMAP.md; a deleted human edit cannot.

## 5. `board.py` (roadmap projection)

Board fields, all derived from the file except where marked:

| Field | Type | Source |
|---|---|---|
| Status | single select | row status via `STATUSES` (Unimplemented, Partial, Done, Non-goal) |
| Milestone | single select | `Mn` cited by the row |
| Project | single select | the repo name |
| Spec | text | Spec cell |
| Named test | text | Where cell |
| Wave / Tier | single select | the project's `docs/plan.md` |
| Blocked | text | "does not exist yet" dependency from `docs/plan.md` |
| Owner | (not a field) | issue assignee, set by `forge_claim` |

Open design problems, in order of how much they change the result:

1. **Done rows have no issue** (`sync_roadmap` skips them on purpose: the test carries the claim). So the
   board can show only open work, and has no burn-up. Options: (a) accept it; (b) draft items for done rows,
   which keeps issues clean but doubles the identity surface; (c) a read-only "Done" count in the project
   README via `updateProjectV2`. Recommendation: (a) now, (c) later.
2. **Identity.** The `<!-- roadmap:name -->` marker lives in the issue body, so the item key is the issue.
   Keep that. Draft items would need a marker in the draft body and a second lookup path.
3. **Human edits on the board.** Someone drags a card to Done. That is a claim with no evidence. The sync
   never writes it to ROADMAP.md and never silently overwrites it either: `--check` reports
   `board-ahead` drift (board says Done, row says open) next to the existing `file-ahead` drift, and the
   fix is `forge_propose_status`, which runs the existing test-exists guard. Direction of authority stays
   file to board.
4. **Milestone percentages** are untrustworthy for the reason `next_actions` already documents, so the
   board must not display GitHub's milestone completion as progress.

## 6. MCP surface

`planner.py` caps itself at five tools because "a tool list is not free" (Task Master: 36 tools, ~21k
tokens). Keep that discipline by making the board an opt-in toolset, selected with `FORGE_TOOLSETS`
(default `core`; `core,board` adds these), so the default context cost does not change:

| Tool | Reads or writes | Behaviour |
|---|---|---|
| `forge_board` | read | items grouped by a field (default Status) for one project, each with its source row and any drift |
| `forge_board_drift` | read | file-ahead and board-ahead differences, nothing written |
| `forge_board_sync` | write | plan by default; `write=true` applies; batch cap 50; prints the mutation list first |
| `forge_board_set` | write | one non-Status field on one item; Status is refused and redirected to `forge_propose_status` |
| `forge_board_auth` | read | granted scopes, owner, project number, and the exact fix command when scope is missing |

Guards, in the style of `rewrite_row`: allow-list of `(owner, project number)` pairs in config; a
`FORGE_READ_ONLY` switch that unregisters the write tools (the official server does the same); every write
tool returns what it changed and what it read it from.

`sync_roadmap.py --board` should call `board.py` instead of its own copy, so there is one implementation.

## 7. Auth and CI

- Local: classic `gh` OAuth token with `project`.
- CI: the default `GITHUB_TOKEN` has no access to a user-owned project. A scheduled `--check` needs a PAT
  secret, or the board must live in an organisation where an app installation token can be used. That is a
  real decision (section 9).
- Never read or print the token; the tool shells out to `gh` and only inspects the scopes header.

## 8. Tests and rollout

- Unit: fake runner with recorded GraphQL responses for schema, pagination across two pages, a
  `REDACTED` item, `INSUFFICIENT_SCOPES`, a partial aliased-mutation failure, unknown option name.
- Property: sync is idempotent (second run produces an empty mutation list).
- Guard tests for `forge_board_set` refusing Status, and for read-only mode removing write tools.
- Live test: skipped unless `FORGE_LIVE_BOARD` names a sandbox project the user created for it.
- Phases:
  1. User refreshes the scope. Read-only probe (`forge_board_auth`, schema dump) so section 2's open
     questions are answered by evidence.
  2. `ghprojects.py` with fakes and tests; replace `graphql()`, `board_urls()`, `sync_board()`.
  3. `board.py` projection and drift, wired into `--check`.
  4. Toolset and MCP tools.
  5. Views and project status updates (REST view creation; GraphQL status updates), only if wanted.
  6. Extract `ghprojects.py` to its own repo.

## 9. Decisions needed

1. Keep the board on the user account (classic token only, CI needs a PAT) or move it to an organisation?
2. Done rows: accept an open-work-only board, or carry draft items?
3. Board-ahead drift: report only (recommended), or also allow a "request evidence" comment on the issue?
4. Is the toolset name and 5-tool shape right, or should writes stay CLI-only and MCP be read-only?

## 11. Probe results (live, read-only, 2026-11-07)

Scope now `gist, project, read:org, repo, workflow`. Findings:

- `b1tninja` owns exactly one project: #1, "@b1tninja's untitled project", **closed**, private, 0 items,
  created 2026-11-07T03:59Z. `gh project list` hides closed projects (it printed `projects: []` with
  `totalCount: 1`), so a "does the board exist" check built on it would wrongly create a second board.
  The "Protocol SDK portfolio" board that `sync_roadmap --board` creates does not exist: that path has never
  succeeded.
- A new project already carries 13 fields counting against the 50: Title, Assignees, Status (Todo, In
  Progress, Done), Labels, Linked pull requests, Milestone, Repository, Reviewers, Parent issue, Sub-issues
  progress, Created, Updated, Closed. That leaves 37 custom fields. Status is pre-made: project the row
  status into it (adding options) rather than creating a second status field.
- `items(first: 200)` is rejected with `EXCESSIVE_PAGINATION`, so `board_urls()` is broken, confirmed.
- REST on a user-owned project works with this token: `users/b1tninja/projectsV2/1/fields` and `/items`
  return 200, with both a numeric field `id` and the GraphQL `node_id`. `/views` returned **404** (not
  tested further; views may need a different path or the list endpoint may be absent).
- Primary rate limit: a one-point query showed `remaining: 697` of 5000, reset 04:37Z. About 4,300 points were
  already spent this hour by something other than this probe, so budget matters: a sync must read `rateLimit`
  and stop early rather than assume headroom.

## 12. Using Projects more effectively

### 12a. For the portfolio and the ROADMAPs

GitHub's own best-practices page recommends: sub-issues for decomposition, issue dependencies ("blocked by" /
"blocking"), one view per purpose, iteration fields for velocity, a single source of truth for each date, and
status updates ("On track" / "At risk" with dates and a Markdown body). Mapped onto this portfolio:

| Practice | Use here | API reach |
|---|---|---|
| One board, many repos | One "Protocol SDK portfolio" board across the 29 projects, `Project` as a field | GraphQL |
| Purpose-built views | By project (board, grouped by Project); Next actions (table, Status Ready, sorted by Wave); Blocked; Milestone ladder | REST view create (the user-owned `/views` list returned 404; unresolved) |
| Dependencies | Native "blocked by" for the "dependency does not exist yet" notes in each `docs/plan.md`, so blocked work is visible instead of prose | Not read; check the issue-dependency API before relying on it |
| Sub-issues | A milestone Mn as a parent issue with its rows as children, which gives native progress | Not read; same check |
| Status updates | One per wave, written by the sync from computed row counts, so "At risk" is derived rather than typed | GraphQL `createProjectV2StatusUpdate` |
| Iterations | Only if ROADMAPs gain dates. They have none, and dates must not be invented to fill a roadmap view | GraphQL, REST create |
| Charts, auto-add, built-in workflows | Configure once in the UI | No API found |

Cautions specific to this repository:

- The built-in "item closed sets Status Done" workflow would let a human close an issue and mark a row
  done with no named test. Keep it, but make `--check` report board-ahead drift, as section 5 says.
- Budget the 50-field cap: 13 are built in, so a design of about 8 custom fields (Project, Milestone, Wave,
  Spec, Named test, Blocked reason, Evidence, Row id) leaves room.
- Milestone completion percentages stay untrustworthy for the reason `next_actions` records; chart row
  counts from the matrix instead.

### 12b. "Hermes fashion" (interpretation stated: Nous Research's Hermes Agent)

"Hermes" is ambiguous. I read it as Hermes Agent's multi-agent **Kanban**, the closest match online. If you meant
the messenger pattern in general (agents handing work to each other through a shared board), the design below
still applies.

What Hermes Kanban is (official docs): a durable board in a local SQLite file shared by agent profiles. States
`triage`, `todo`, `ready`, `running`, `blocked`, `review`, `done`, `archived`. A dispatcher ticks every 60 s:
reclaims stale claims, promotes `todo` to `ready` when parents are `done`, atomically claims and spawns the
assigned profile, and auto-blocks after repeated spawn failures. Workers get a `kanban_*` toolset (`show`,
`complete`, `request_review`, `block`, `create`, `link`) and a worker that exits without a terminal call is a
protocol violation, retried a bounded number of times. Blocks are typed (`dependency`, `needs_input`,
`capability`, `transient`). Completed cards are immutable; follow-up work is a child card. Every transition
appends to `task_events`. A card can carry a completion contract that checks a PR's required checks through
`gh` before it may complete.

Documented limits: **single host only** (local SQLite, host-local PIDs), no exactly-once delivery, and
re-running an ambiguous attempt can repeat side effects. I found no Hermes integration with GitHub Issues or
Projects, and no MCP in the Kanban docs (a Linear integration through a Hermes MCP catalog is described only by
a third-party guide). So this is an open slot, not an existing feature.

How the two fit:

| Hermes | GitHub Projects equivalent | Gap |
|---|---|---|
| `triage/todo/ready/running/blocked/review/done` | Status options (add Triage, Ready, Blocked, In Review to the pre-made Todo / In Progress / Done) | none; options are editable |
| parents gate children | native issue dependencies, or a `Blocked reason` field | dispatcher must compute readiness; the board will not |
| atomic claim | assignee | **not atomic**: `planner.claim()` lists then edits, so two agents can both pass the check. Mitigation: assign, re-read, and back off unless the caller is the sole assignee |
| `task_events` log | issue timeline plus the `projects_v2_item` webhook | no local write-ahead log; poll or webhook |
| completion contract | the existing named-test guard, plus required PR checks green | add the PR-checks half in `board.py` |
| typed blocks | a `Blocked reason` single-select | none |
| single host | cloud board, many hosts | this is the real advantage over Hermes' local board |

Recommended shape: **keep GitHub Projects as the multi-host record and ROADMAP.md as the authority; let a
Hermes (or any) dispatcher be a client.** The forge would expose `ready` items (rows open, dependencies met),
`claim` with the re-read check, a `report` step that comments evidence and requests review, and a `complete`
that only `propose_status` can perform after the named test and PR checks are verified. A worker's protocol is
then the same as Hermes' own: read the card, do the work, end with a terminal call, never edit authority files
by hand. Copilot coding agent uses the same hook (assigned from Projects; it sees the issue only as of
assignment, so later notes go on its PR).

Caution from the sources: one cited study (CooperBench, reported by InfoWorld; I did not read it) found agents
about 30% less successful when collaborating than alone, and GitHub's multi-agent "Copilot Projects" coverage
is secondary. Keep cards independent, and keep the existing two-projects-at-once rule in PORTFOLIO.md.

## 10. Defects found in the existing code (not fixed)

- `sync_roadmap.graphql()` swallows errors and returns `""`; callers treat that as empty.
- `board_urls()` requests `items(first: 200)`; connection limit is 100 (unverified against the live API).
- `board_urls()` and `sync_board()` use `user(login:)` and f-string query building.
- `sync_board`, `board_urls` and `issue_refs` have no tests.
