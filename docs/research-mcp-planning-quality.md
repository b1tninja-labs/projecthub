# MCP, planning and spot-checking: research and design

Scope: this synthesizes six reviewed research lanes. No files were edited and no GitHub resources were created. Local paths are under `D:\code`. A claim is marked **(unverified)** where no reviewer confirmed it. Section 7 lists those claims together.

---

## 1. Summary: the five changes that matter most

**1. Decide the MCP write question before building more agent tooling, and enforce the decision in a test.** D23 ([improvement-plan.md:56](D:\code\sdk-forge\docs\improvement-plan.md)) says "Add only read-only MCP toolsets" because "an agent can move the board without evidence". `planner.py`'s MCP server already does two things that conflict with this:
- `forge_propose_status(write=true)` writes ROADMAP.md (planner.py:188-200, 324-333).
- `forge_claim` reassigns issues through `gh` (planner.py:203-276, 318-322).

The only guard on `[ok]` is `roadmap.evidence`. Its own docstring calls it "a floor, not the rule" (planner.py:141-150), and `def test_x(): pass` satisfies it. Record a D24 that does one of two things:
- (a) moves both write paths to a CLI, or
- (b) allows lease and handoff writes only, and makes `forge_propose_status` preview-only over MCP.

Whichever is chosen, add a test that fails if any registered MCP tool can write ROADMAP.md.

**2. Make "evidence" mean CI evidence, and make CI able to enforce it.** Four gaps today:
- `verify.py` persists no results, so nothing can show that a named test passed on a given commit.
- No branch protection exists. On `b1tninja/rtrkit`: 0 rulesets, and `branches/main/protection` returns 404 "Branch not protected".
- Every agent runs as the repo owner.
- The row audit on ctspy found that the named tests for both `[ok]` Merkle rows kill **0 of 7** targeted mutants, and 3 mutants survive the whole suite.

Fixes:
- `verify.py --json` writes `{sha, node ids, outcome}` outside the tree.
- A `roadmap-evidence` check runs head tests against base source, rejects tests with no assertion, and runs scoped mutation.
- The owner adds a ruleset that requires that check with no admin bypass.
- Agent sessions get a separate token that has no Administration permission.

Until all of that exists, the docs should describe evidence gating as advisory.

**3. Bring the read-only MCP up to the 2026-07-28 spec, and stop silent truncation.**
- Add `outputSchema` plus `structuredContent` with a TextContent mirror. Servers "MUST" conform to a declared schema ([tools spec](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).
- Add annotations: `readOnlyHint` and friends. The schema defaults are `readOnlyHint=false`, `destructiveHint=true`, `idempotentHint=false`, `openWorldHint=true` ([schema](https://modelcontextprotocol.io/specification/2026-07-28/schema)).
- Return refusals as `isError` results instead of a success that reads "REFUSED:".
- Add `pageInfo{hasNextPage}` to every connection. `projects_mcp.py:117-136` truncates silently at `subIssues(first:50)`, `blockedBy/blocking(first:20)`, `projectItems(first:10)`, `fieldValues(first:30)` and `assignees(first:10)`. `ghprojects.py:168` uses the same `first:30`.
- Replace `next_actions`, which is file order plus a mislabelled `named_test`, with a readiness filter.
- Add a context-packet tool and a server-minted lease.

**4. Compute planning state from git, and stop typing it by hand.** `PORTFOLIO.md:59` said `dk.printable-charset` "is still unimplemented". It had been `[x]` in `asn1core/PLAN.md:159` since commit 290715a, which landed 93 minutes before the last PORTFOLIO commit. Planned changes:
- A read-only `flow.py` derives throughput, row age, born-done share, and corrections versus regressions from `git log -p ROADMAP.md`.
- PORTFOLIO status moves into generated blocks that a `--check` verifies, the same pattern `sync_roadmap` uses.
- Decisions become one-file-per-decision ADRs ([Nygard](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions)) that are superseded rather than edited.
- Every threshold, date or wave exit test an agent proposes enters as a *proposed* decision, and takes effect only when the owner accepts it.

**5. Adopt a low-noise, free baseline for scanning and typing, rolled out in stages.**
- Turn on private vulnerability reporting. `ctspy/SECURITY.md:23` points reporters at it, but it is `enabled:false` on pkix, ctspy and rpkit.
- Turn on push protection and Dependabot security updates everywhere.
- Enable CodeQL default setup only on active-wave repos.
- Fix the mypy gate so it checks `tests` (`verify.py:120` runs `mypy <pkg>` only).
- Fix `requires-python = ">3.12"`. It is exclusive, and appears in 31 pyprojects (forge.py:238/271/394).
- Remove the global `S101` ignore.
- Pin the toolchain centrally through `-c` constraints.

Two ordering constraints:
- Do not switch to bare `python -m mypy`, and do not FAIL on non-strict configs yet. Only 3 of 35 projects set `files` or `strict`, and bare mypy exits 2 without a target, so 32 projects would go red at once.
- Do not template Dependabot pip updates for the toolchain per repo, because they would fight the central pins.

---

## 2. "Jira on GitHub": capability table

| Jira feature | GitHub native today (b1tninja, user account, Free) | What our MCP/CLI layer adds | How (computed from what) | Effort |
|---|---|---|---|---|
| Issue types | **Not available here.** Live query returns `issueTypes:null`, `viewerCanSeeIssueFields:false`. Org-only ([docs](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-types-in-an-organization)) | `Kind` single-select field (Obligation/Milestone/Decision/Wave/Project) already set by board.py | From the ROADMAP row kind; treat `issueType` as nullable in code | done; hours to harden |
| Custom fields | Projects v2 fields (text, number, date, single-select, iteration). Issue fields not visible here; GA date 2026-07-02 **(unverified)** | Level/Kind/State/Priority/Wave/Active/Cycle/Spec/Named test, plus computed `Unblocks` and `Horizon` | board.py projects them from ROADMAP/PORTFOLIO; owner sets `Active` only | hours |
| Epics / hierarchy | Sub-issues: 100 per parent, 8 levels ([docs](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)); limits not re-checked | Wave → Project → Milestone → Obligation tree; `item_context` returns a `truncated` flag | GraphQL `subIssues` with paging | hours |
| Issue links (blocks, relates, duplicates) | Native. `blockedBy/blocking` ([REST](https://docs.github.com/en/rest/issues/issue-dependencies)), `addRelatesTo/removeRelatesTo` in live schema | Blocked-by from deps.py; hold off using relatesTo for decisions until it is documented | deps.py → `addBlockedBy`; read with paging | hours |
| JQL | No JQL. Three dialects: Projects filter ("does not currently support logical OR filters across multiple fields", [docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects)), `items(query:)`, Search API | `forge_query_items(expr)`: AND/OR/NOT plus field comparisons over a cached snapshot | Local evaluator over `ghprojects` snapshot; fixture-tested | days |
| Saved filters / boards | Views; `create/update/deleteProjectV2View` exist in live schema. **gh-projects-mcp.md:30 is wrong** to say views cannot be created | `forge_run_view(name)`: read-only, after a test compares view-visible items with `items(query: view.filter)` | View filter string → items query | days |
| Workflows: transitions, conditions, validators | Built-in automations only; `deleteProjectV2Workflow` exists with no create mutation ([automations](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations)) | `forge_transitions(row)`: each condition reports `pass`/`fail`/`unknown`, and unknown never becomes pass | ROADMAP row + lease db + verify results JSON + CI check status | days |
| Required-field validators on Done | None | Reconciliation: Done on board without `[ok]` on default branch is reported as drift | `sync_roadmap --check` | hours |
| Sprints | Iteration field | `Cycle` field; the cycle report is rows flipped per cycle | git history of ROADMAP.md | hours |
| Velocity / burndown / CFD | Insights charts ([docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/viewing-insights-from-your-project/about-insights-for-projects)); no reporting API; iteration and number changes leave no timeline event **(plausible, unverified)** | Throughput, row age, born-done share (section 4); no board snapshots | `git log -p ROADMAP.md` parse | days |
| Cycle time | `ProjectV2ItemStatusChangedEvent` (lane measurement, not re-run) | Cycle time = merge-to-default of `[ok]` minus first non-`[ ]` commit | git | hours |
| Estimates / story points | Number field | Not added: no consumer | — | — |
| Versions / releases | Milestones, releases | Milestone = ROADMAP milestone | sync_roadmap | done |
| Components | Labels, or the repo itself | Repo is the component | — | — |
| Worklogs, watchers | None (watcher API absence unverified) | Not modelled | — | — |
| Automation rules / webhooks | `projects_v2*` webhooks are **organization-only**; `sub_issues` and `issue_dependencies` are repo-level ([webhooks](https://docs.github.com/en/webhooks/webhook-events-and-payloads)) | Scheduled or manual `--check` drift detection, not webhooks | CLI | hours |
| Status reports | Project status updates: write access, Markdown, defaults from previous ([docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/sharing-project-updates)) | `board.py --status-update` dry-run by default; owner runs `--apply` locally only on change | flow.py output | hours |
| Permissions / roles | Owner = admin on personal repos | Separate fine-grained agent token; ruleset with no bypass (owner action) | — | hours (human) |
| Audit log | Issue/PR timelines | Local append-only events table plus `forge_events` read tool; mirror only acquire/release/block/expire to comments | SQLite in `sdk-forge/cache/` (gitignored, `.gitignore:22`) | hours |
| Jira MCP (Rovo) parity | — | Read/write split by server, modelled on [Atlassian](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/), [github-mcp-server](https://github.com/github/github-mcp-server) and [Linear](https://linear.app/docs/mcp) (none re-read). If github-mcp-server is used, require `--read-only` | — | — |

**Org migration.** Moving to a free organization would unlock issue types, issue fields and the `projects_v2*` webhooks. The cost is more than repo transfers:
- Project #4 is user-owned. Whether a project can be moved between owners is undocumented as far as the lanes found, so assume it must be rebuilt **(unverified)**.
- The owner constants change (`planner.py` hardcodes `ORG`).
- A `sync_roadmap --check` pass is needed afterwards.

Record this as a PORTFOLIO decision. Do not let it happen as a side effect of tooling work.

---

## 3. Agent workflow MCP design

### 3.1 Server split (the structural guarantee)

Annotations are hints. Clients "MUST consider tool annotations to be untrusted unless they come from trusted servers" ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). Read-only behaviour must therefore come from which tools a server registers, not from annotations.

| Entry point | Registered tools | Writes to |
|---|---|---|
| `forge-read` (MCP, default) | All read tools below | nothing |
| `forge-lease` (MCP, opt-in, only if D24 option (b)) | lease and block tools | local SQLite only |
| `planner.py` / `board.py` CLI (human) | flip row, steal lease, reopen, set Active, accept decision, accept equivalence write-off, post status update | ROADMAP.md, GitHub |

### 3.2 Tools

Annotation notation is RO/Destr/Idem/OpenWorld. All tools return `structuredContent` that conforms to `outputSchema`, with a JSON TextContent mirror. Refusals are `isError:true` with `{code, reason, fix}`.

| Tool | R/W | Annotations | Inputs | Outputs | Guard |
|---|---|---|---|---|---|
| `forge_status` | R | RO, Idem, OW=false | `project?` | per-project counts by level, Active, wave | local files only |
| `forge_next` | R | RO, Idem, OW=false | `project?`, `limit` | ranked rows `{row_id, where, named_test, spec, why, unblocks}` plus `skipped{reason: count}` | Readiness filter (3.6) |
| `forge_packet` | R | RO, Idem, OW=false | `row_id` | Context packet (3.5), under 50,000 chars | refuses plan dirs / no pyproject |
| `forge_transitions` | R | RO, Idem, OW=true | `row_id` | `[{to, conditions:[{name, value: pass/fail/unknown, source}]}]` | unknown is never pass |
| `forge_evidence` | R | RO, Idem, OW=true | `row_id`, `sha?` | latest verify JSON / CI check-run result for the named test | reads only |
| `forge_propose_status` | R | RO, Idem, OW=false | `row_id`, `to` | unified diff of ROADMAP.md, plus evidence check result | preview only, never writes |
| `forge_events` | R | RO, Idem, OW=false | `row_id?`, `since?` | event log | — |
| `projects_auth`, `projects_list`, `project_schema`, `project_items`, `item_context`, `portfolio_overview` | R | RO, Idem, OW=true | as today, plus `fields` projection | as today, plus `truncated:bool` per connection | `isError` with `gh auth refresh -s read:project` when scope missing |
| `forge_query_items` | R | RO, Idem, OW=true | `expr` (AND/OR/NOT, `field op value`) | items | cached snapshot |
| `forge_lease_acquire` | W (local) | RO=false, Destr=false, Idem=false, OW=false | `row_id`, `scope_files[]`, `ttl_s` (capped) | `{lease_id, expires_at}` | row Ready, no live lease, project Active |
| `forge_lease_heartbeat` | W (local) | Destr=false, Idem=false | `lease_id` | `{expires_at}` | expired lease returns isError `LEASE_EXPIRED` |
| `forge_lease_release` | W (local) | Destr=false | `lease_id`, `outcome: handoff/pr_opened/abandoned`, `note`, `pr_url?` | `{state}` | lease owner only |
| `forge_block` | W (local) | Destr=false | `lease_id`, `kind` (dependency/spec_question/decision_needed/env), `detail` | `{state: Blocked}` | escalates to human after 2 recurrences (Hermes rule) |

Human-only operations stay CLI-only: steal, reopen, Active, accept-decision and write-off. If any of them is ever exposed over MCP, mark it with Claude Code's `requiresUserInteraction` ([docs](https://code.claude.com/docs/en/mcp)). That flag is not a boundary: "The Agent SDK's canUseTool callback does receive these calls and can approve them", and an agent with a shell and `gh` bypasses it anyway.

Transport note: Claude Code repeats `idempotentHint`/`readOnlyHint` calls only for HTTP servers and claude.ai connectors. Over stdio, the remaining benefit is that read-only calls run in parallel.

### 3.3 Task state machine

Board STATUS values come from board.py:64-75: Backlog, Ready, In progress, In review, Blocked, Done.

| From → To | Who | Trigger / condition |
|---|---|---|
| Backlog → Ready | Machine (derived) | All blocked-by closed, project `Active` (owner-set), not a plan directory |
| Ready → In progress | Agent | `forge_lease_acquire` succeeds |
| In progress → Ready | Machine | Lease expired (no heartbeat within TTL), or release `abandoned` |
| In progress → Blocked | Agent | `forge_block` |
| Blocked → Ready | Machine or human | Blocker closed (derived); a `decision_needed` block clears only when the owner accepts the decision |
| Blocked → Triage | Machine | Same block kind recurs more than 2 times, or 3 consecutive protocol violations ([Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban)) |
| In progress → In review | Machine (derived) | A PR from the lease branch exists and references the row |
| In review → Done | **Machine only** | Row is `[ok]` on the default branch **and** `roadmap-evidence` is green on that merge commit. The named test exists, passes, fails on base source, has an assertion, and kills at least 1 scoped mutant |
| Done → Reopened | Human (CLI) | Regression or audit demotion; machine may *propose* via a decision issue |
| Done on board but row not `[ok]` | Machine reports drift; human reverts | `sync_roadmap --check`. Needed because "Item closed" automation can set Done without evidence (automation page not re-read) |
| Correction (`[ok]` → `[part]`) | Agent may propose via PR; counted neutrally | Not a defect metric (see section 4) |

"Machine only" holds only once the ruleset in section 6 exists and agents lack Administration permission. Until then it is advisory.

### 3.4 Lease / claim semantics

- The current `claim()` cannot distinguish agents. Every session is `b1tninja`, and `holders == [who]` returns "already yours" (planner.py:255).
- Replace it with a server-minted opaque `lease_id`: UUIDv4, a TTL stated in the tool description, and a tool-execution error on expiry. This matches the spec's "explicit handle from a creation tool" guidance and SEP-2567's removal of sessions ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).
- Store leases in SQLite in `sdk-forge/cache/`, with acquire done in a single write transaction (`BEGIN IMMEDIATE`).
- The assignee and issue comments are display-only mirrors, with no heartbeat mirroring. Never decide ownership by comment order: comment-id monotonicity is undocumented **(unverified)**.
- Cross-host conflicts surface at PR time. `scope_files` is advisory, and a PR-time diff-versus-scope check *warns* rather than fails.
- If MRTR `requestState` is used, HMAC it with the principal, TTL, tool+args digest and `lease_id`, and enforce single use server-side ([MRTR](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)). Do not use Sampling: SEP-2577 deprecates it.
- Defer MCP Tasks. They are a CLI public beta (v2.1.304+), and progress does not extend the hard timeout.

### 3.5 Task context packet (`forge_packet`)

The packet has a 50,000-character limit, matching Claude Code's persistence threshold.

| Field | Source |
|---|---|
| `row_id`, project, milestone, wave, level, history (commits touching the row) | ROADMAP.md + git |
| `where` (module/function) and `named_test` (pytest node id taken from **Evidence** when `Where` is a module) | roadmap.py. Fixes the mislabel: certledger `cl.multi-path` currently reports `named_test='certledger/fanout.py (planned)'` |
| `test_state`: exists / to-be-written / stub | ast scan |
| `spec`: RFC number plus section if the cell has one; **no normative text** | Row Spec cell. rtrkit's is only "RFC 8210", so the agent must use rfclib per AGENTS.md |
| `acceptance`: exact command (`cd <proj>; python -m pytest <node> -q`) and the audit stages it must pass | template |
| Blockers and dependents (`unblocks` count) | deps.py + board, with `truncated` flags |
| Linked decisions (accepted only) | ADR files |
| Invariants pointer: project `AGENTS.md` path and headings | file |
| Lease `{lease_id, expires_at}` if held | lease db |
| Evidence so far: last verify JSON for this node, last CI check | results file |
| `resource_link`s to the ROADMAP section and test file | MCP resource links |

### 3.6 Readiness filter for `forge_next`

A row is ready only when all of these hold:
- the project is owner-`Active`, which enforces "at most two projects carry real effort at once" (PORTFOLIO.md ~299)
- the project has a `pyproject.toml` and is not a plan directory
- the row is open and has no live lease
- every blocked-by item is closed
- the named test is either written, or the lease is explicitly for writing it

Rank by milestone order, then `Unblocks` descending. Return `why` and `skipped` counts.

### 3.7 Completion evidence record

`verify.py --json <path outside tree>` and the CI job emit:

`{repo, sha, base_sha, row_id, node_id, outcome, collected:true, diff_on_base: fail|pass|error, has_assert:bool, mutants:{killed, total, scope:[fn...]}, run_url, tool_versions}`

Done requires this record on the merge commit with `outcome=pass`, `diff_on_base=fail` (not a collection or import error), `has_assert=true` and `killed≥1`. Survivors written off as "equivalent" count only with human CLI approval (approver plus mutant diff hash), or with a committed equivalence property test. A change to the function's hash invalidates prior write-offs.

### 3.8 Resources and prompts

- Resources (read-only):
  - `forge://portfolio` (generated state)
  - `forge://project/{p}/roadmap`
  - `forge://row/{id}/packet`
  - `forge://decisions/{id}`
  - `forge://evidence/{repo}/{sha}`

  Claude Code's resource @-mention behaviour was not re-checked.
- Prompts:
  - `start-row(row_id)`: read packet, acquire lease, write the failing test first
  - `handoff(lease_id)`
  - `review-row(row_id)`: run the audit stages and report survivors
- Protocol baseline to record in gh-projects-mcp.md:
  - pin spec 2026-07-28
  - deterministic tool order
  - no per-connection tool variance
  - `ttlMs`/`cacheScope` on `tools/list` ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools))
  - descriptions under 4,096 characters
  - `alwaysLoad` only for `forge_next` and `forge_packet`
- Also fix gh-projects-mcp.md:273 ("Completed cards are immutable"), because completion must be reversible by a human.

---

## 4. Planning practice

### 4.1 Metrics to compute automatically (read-only `flow.py`)

| Metric | Formula | Data source |
|---|---|---|
| Throughput | rows entering `[ok]`/`[x]` on the default branch per week, per project; zero weeks counted | `git log -p -- ROADMAP.md PLAN.md` parsed with roadmap.py grammar |
| Row age (WIP age) | now − first commit where the row left `[ ]` (Kanban "Work Item Age", [guide v2025.5](https://kanbanguides.org/english/)) | git |
| Cycle time | merge time of `[ok]` − first non-`[ ]` commit | git |
| Born-done share | rows whose first appearance is `[ok]` ÷ rows reaching `[ok]` | git. This is the defect signal; queue those rows for audit |
| Corrections | `[ok]`→`[part]` with the gap named and the test unchanged; reported neutrally or credited | git (example: itulibrary `ia.budgets`, 6ce9538 → 8fbbc6a) |
| Regressions | named test that passed on an earlier evidence record now fails | evidence records |
| Audit strength | per `[ok]` row: stage reached (L0-L6), `killed/total` | row-audit.json |
| Decision age (weighted) | Σ over open decisions of (age days × rows blocked) | ADR files + deps/blocked-by |
| Observed vs declared Active | commits per project in 14 days vs `Active` field (example: certledger 29, ctspy 26, asn1core 23, trustatlas 15, itulibrary 11, pkix 10 since 2026-11-04) | git + board |
| Stall rule | Active project with 2 consecutive weeks of no merged obligation is reported, nothing changes (PORTFOLIO §8) | git |
| Unblocks | count of transitive dependents of a row or milestone | deps.py + blocked-by |
| Board drift | Done without `[ok]`; `[ok]` without a green evidence record; ROADMAP vs issues | `sync_roadmap --check` |
| Portfolio drift | generated blocks out of date | `portfolio --check` in the portfolio repo's CI, **not** pre-commit in project repos |

Prioritization: sort by milestone order, then `Unblocks`. Drop the division by row count, or label it "rows", not effort.

### 4.2 What to stop doing

| Stop | Why / replace with |
|---|---|
| Hand-typed status claims in PORTFOLIO.md §3/§4 | They drift within hours. Use generated blocks plus `--check`; do not parse prose |
| Stale docs and decisions | gh-projects-mcp.md:30 (views), :273 (immutable cards); pkix ROADMAP "Project V2 #2"; "28 SDK shells"; D2/D4/D12/D26, which assume private repos and Pro while trustatlas is PUBLIC. Supersede them via ADR |
| Two decision namespaces (improvement-plan D7 duplicates PORTFOLIO §9.5) | One ADR file per decision |
| Board snapshots, burndown, Monte Carlo | Fewer than 10 completed items and an unstable system ([ProKanban](https://prokanban.org/kanban-playbook/forecasting-delivery)); report age instead |
| WSJF/RICE scores | No users, so Reach is undefined. Use `Unblocks` |
| Agent-authored dates, thresholds, kill dates, wave exit tests | These are decisions (D25: "Invented dates ... invite false commitments"). They enter as proposed ADRs with owner acceptance |
| Reopen as a defect counter | Penalises honest downgrades. Separate corrections from regressions |
| Scheduled status-update workflow holding a project-scoped PAT | Standing credential on a public board; local `--apply` on change only |
| Numeric WIP cap on decisions | Not until D7 is decided |

---

## 5. Code scanning, typing and spot-checking

### 5.1 Per-repo CI set (free on public repos, low noise)

Actions are free on standard hosted runners in public repos ([plans](https://docs.github.com/en/get-started/learning-about-github/githubs-plans)).

| Control | Where | How | Noise | Note |
|---|---|---|---|---|
| Private vulnerability reporting | all repos | `PUT /repos/{o}/{r}/private-vulnerability-reporting` ([REST](https://docs.github.com/en/rest/repos/repos#enable-private-vulnerability-reporting-for-a-repository)) | none | Highest value; SECURITY.md already promises it |
| Secret scanning + push protection | all | `PATCH /repos` `security_and_analysis.*.status=enabled` ([REST](https://docs.github.com/en/rest/repos/repos#update-a-repository)) | none | pkix/ctspy disabled, rpkit enabled. What "disabled" means on public repos is unexplained; test on pkix first |
| Dependabot alerts + security updates | all | `PUT /repos/{o}/{r}/automated-security-fixes` (not via security_and_analysis) | low | |
| Dependabot version updates | **sdk-forge only** (pip toolchain + github-actions, monthly, grouped) | dependabot.yml | low | Projects get pins by re-rendering; no per-repo toolchain bumps |
| CodeQL default setup | active-wave repos only | `PATCH .../code-scanning/default-setup` with `query_suite` default→extended pilot on pkix/ctspy ([docs](https://docs.github.com/en/code-security/code-scanning/enabling-code-scanning/configuring-default-setup-for-code-scanning)) | low | Expect low yield: dataflow queries need modelled sources **(unverified)**. Absence of alerts is never row evidence |
| ci.yml hardening | all (template) | `permissions: contents: read`; SHA pins from one `ACTION_PINS` table in forge.py (checkout is v7.0.1; forge emits @v4/@v5) | none | test_templates asserts SHAs |
| Toolchain pins | all | `pip install -e .[dev] -c <sdk-forge requirements-dev.txt @ SHA>` (ruff 0.15.12, mypy 1.20.2, pytest 9.0.3) | none | forge `--check` flags drift |
| zizmor | sdk-forge CI on rendered templates | zizmor-action v0.8.1 | none | Catches problems once at the source, not 35 times |
| pip-audit / dependency-review | repos with runtime deps (pkix) | one generator recipe; drop the stale "private repo" `--skip-editable` rationale (pkix security.yml:25-28); retire FORGE_PAT | low | |
| Python floor | all | assert `SpecifierSet(requires-python).contains("3.12.0")`; a 3.12 test job | none | `>3.12` excludes 3.12.0 ([PEP 440 spec](https://packaging.python.org/en/latest/specifications/version-specifiers/)) |
| `roadmap-evidence` check | active repos | section 5.3 | only fails on flips | Required by ruleset (owner) |
| Settings drift check | sdk-forge scheduled workflow, one issue on drift | settings CLI `--check` | low | **Not** a verify.py gate or hook: it needs network and an admin token |
| Scorecard | one manual run | — | — | README/release version disagree; don't pin from README |

Ruff: remove `S101` from the global ignore (pkix:100, ctspy:106, forge.py:352) and keep it under `tests/*`. Skip TRY and PLR2004. Keep Bandit and Semgrep out of the baseline.

### 5.2 Typing policy with measured baselines

| Measure | Baseline (2026-11-07) | Target |
|---|---|---|
| Projects with `[tool.mypy] strict = true` | 3 / 35 | 35 / 35 after regeneration |
| Projects with `files` set | 3 / 35 | n/a (gate passes paths explicitly) |
| Gate scope | `mypy <pkg>` (verify.py:120), tests unchecked | `mypy <pkg> tests` |
| ctspy `--strict` errors | 6 in package (measured); 74 with tests (lane) | 0 |
| pkix `--strict` errors | 8 (lane, not re-run) | 0 |
| `requires-python ">3.12"` | 31 pyprojects | 0 |
| Global `ignore_missing_imports` | template default | per-module overrides |
| `py.typed` | asn1core yes, ctspy yes, certledger **missing** | all, checked in wheel by verify.py |
| mypy pin | 1.20.2; 2.4.1 available **(unverified)** | bump in its own commit after the gate fix; set `strict_bytes` explicitly |

Rollout:
1. Gate runs `mypy <pkg> tests`.
2. Template emits `strict` plus curated zero-noise error codes, with `redundant-expr` off because it conflicts with adversarial isinstance guards. Regenerate unimplemented shells (forge refuses to overwrite implemented work, f3563ae). Hand-fix ctspy and pkix.
3. verify.py WARNs, listing non-strict projects.
4. WARN becomes FAIL once the list is empty, recorded as a decision.

Deferred: basedpyright `--verifytypes`, typeguard/beartype (optional job only, needs install approval), `NewType` for `LogId`/`Sha256` in certledger.

Bug found by typing review: `certledger.walk._entries_of` (walk.py:376) drops non-dict members, which shifts positions (`_store_batch` 320-345).
- It is not a false "complete", because `WalkReport.complete` checks the root (140-158).
- It does cause four problems: no Gap names the bad member, records are persisted at wrong positions, the page cap is lowered (270), and an all-non-object page is classified `EMPTY_WITHIN_TREE` rather than `UNREACHABLE`.
- Fix: end the page as `UNREACHABLE` and add two regression tests. This is for certledger's owner.

### 5.3 Row-audit pipeline

| Stage | Check | Tool | Cost per row | Pass condition |
|---|---|---|---|---|
| L0 Exists | named test parses and defines the name (today's floor) | roadmap.evidence | ms | present, not skip/xfail **(skip/xfail handling unverified)**; not an unedited rendered template |
| L1 Asserts | test body contains `assert`, `pytest.raises` or a helper known to assert | ast | ms | has assertion |
| L2 Reach | named test executes the `Where` function | coverage `--cov-context=test` ([docs](https://coverage.readthedocs.io/en/latest/contexts.html)), COVERAGE_FILE outside repo | seconds | lines in function hit; necessary, never shown as a pass alone |
| L3 Differential | head's test file against base's source: must fail (not collection/import error), and pass on head | CI checkout overlay | one extra pytest run | fail→pass |
| L4 Kill | scoped mutation on the `Where` function plus same-module callees from L2 | in-house AST mutator; cross-check with [mutmut](https://mutmut.readthedocs.io/en/latest/) in Linux CI (needs fork, so WSL on Windows); timeout counts as kill | 1-5 min | killed ≥ 1; report `k/n` |
| L5 Property | Hypothesis property for parser/verifier rows, derandomized `ci` profile; precedent `namespy/tests/test_fuzz_wire.py` | hypothesis | seconds-minutes | — |
| L6 Oracles | golden vectors with provenance docstring (test_merkle.py:1-13 style); Wycheproof subset for DER/crypto after file names are confirmed | vendored pinned files | ms | — |
| Fuzz (deferred) | Atheris (Linux/macOS only, [README](https://github.com/google/atheris)); OSS-Fuzz needs a significant user base ([criteria](https://google.github.io/oss-fuzz/getting-started/accepting-new-projects/)) | — | — | crashes become ROADMAP rows via CLI |

Reporting:
- Write a check run with `GITHUB_TOKEN` `checks: write` ([workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)). REST check creation needs a GitHub App, but Actions can do it.
- Keep audit stamps in `row-audit.json` and a board number field (filters support `> >= < <= ..`). Edit ROADMAP.md only on promotion or demotion, and demote via a decision issue.

Pilot result (ctspy, ROADMAP.md:24-25):
- The named tests (test_merkle.py:61-68, 93-96) assert only acceptance.
- The 3 suite-wide survivors are `leaf_index > tree_size`, and `sn == 0` removal at lines 197 and 250.
- `verify_consistency` accepts a proof for a larger claimed second up to the next power of two. This was measured for first=2 (accepts 3-4) and first=4 (accepts 5-8). The docstring says first==1 only.
- rfclib is empty on this machine. Run `rfclib init` and read RFC 9162 before changing anything.
- Reproduced by `mutate.py` (slice 10, 2026-11-07) with its fixed refusal operators instead of the pilot's hand-picked 7: each named test kills 0 of 11 per row, test_merkle.py kills 7 of 11, and the pilot's three survivors are among those left. The count differs from 7 because the operators are mechanical; the verdict does not.

---

## 6. Build order

Each slice takes 1-3 days or less, and each is independent unless its line says otherwise.

| # | Slice | Who | Acceptance test |
|---|---|---|---|
| 0 | Owner decisions: D24 (MCP writes), certledger plan-dir status (AGENTS.md vs db9870c), user vs org, supersede D2/D4/D12/D26 | owner | ADR files committed with status `accepted` |
| 0h | Ruleset on active repos' default branch requiring `roadmap-evidence` with no bypass; separate fine-grained agent token without Administration (check docs for permission names first) | owner | `gh api repos/b1tninja/rtrkit/rulesets` length ≥ 1; an admin-token merge of a red PR is refused |
| 1 | Apply D24: remove `write=true` and `forge_claim` from MCP (or split server) | agent | `test_mcp_tools_cannot_write_roadmap`: tools/list contains no ROADMAP writer |
| 2 | MCP hygiene: annotations, outputSchema, structuredContent+text, isError refusals, deterministic order | agent | snapshot test of tools/list; each tool's fixture output validates against its schema offline |
| 3 | Pagination: pageInfo on every connection in projects_mcp/ghprojects | agent | fixture with `hasNextPage:true` gives `truncated:true` (or raises) |
| 4 | `verify.py --json PATH` (refuses a path inside the repo) | agent | test asserts record fields and that an in-tree path is rejected |
| 5 | `forge_next` readiness filter plus named_test fix | agent | fixtures: certledger row gives node id from Evidence; inactive or plan-dir project appears in `skipped` |
| 6 | Lease store plus tools (needs 0, 1) | agent | two concurrent acquires give exactly one lease_id; expired heartbeat gives `LEASE_EXPIRED` |
| 7 | `forge_packet` (needs 5) | agent | packet for a large row is under 50,000 chars and contains no RFC body text |
| 8 | `roadmap-evidence` L1 + L3 action (needs 4) | agent | fixture repo: `assert True` test rejected; head-tests-on-base fail→pass accepted |
| 9 | ctspy Merkle refusal tests plus power-of-two docstring (after rfclib check) | ctspy owner | the 3 survivors are killed |
| 10 | Scoped AST mutator, L2 reach scope (needs 8) | agent | on pre-slice-9 ctspy it reports 0/7 for the named tests |
| 11 | Typing stage 1: gate `mypy <pkg> tests`, `>=3.12` fix, S101, WARN list | agent | forge self-test: rendered specifier contains 3.12.0; verify prints a non-strict list without failing |
| 12 | Settings CLI (dry-run/`--apply`/`--check`) plus scheduled drift workflow | agent builds; owner applies | `--check` fails when SECURITY.md names advisories and PVR is `enabled:false` |
| 13 | Workflow pins/permissions table plus zizmor in sdk-forge | agent | test_templates fails on a non-SHA `uses:`; zizmor clean on rendered templates |
| 14 | `flow.py` metrics | agent | reproduces itulibrary `ia.budgets` as a *correction*, and certledger's commit count since 2026-11-04 |
| 15 | PORTFOLIO generated blocks plus `--check` in portfolio CI | agent | stale block fails the check; asn1core flip does not fail any project repo's hook |
| 16 | certledger `_entries_of` fix | certledger owner | all-non-object page gives `UNREACHABLE`; non-object first member is recorded as a Gap |

---

## 7. Not verified

- Moving a user-owned Project v2 to an organization: undocumented, assume rebuild.
- Issue-fields GA changelog (2026-07-02) and its plan coverage.
- Sub-issue limits (100 per parent, 8 levels), not reopened.
- `items(query:)` matching view filter semantics exactly.
- Search API limits (1,000 results, 5 operators, 256 chars).
- Iteration and number-field changes leaving no timeline event; status-changed event introspection not re-run.
- `MULTI_SELECT` preview status; no watcher API.
- github-mcp-server feature gating; Linear and Atlassian MCP tool names; Copilot coding agent on Free plan; beads, Task Master, SWE-agent, OpenHands and Devin claims.
- GitHub comment ids being monotonic (needed for any comment-order lease rule).
- Built-in "Item closed → Done" automation behaviour (page not re-opened).
- Rulesets and required checks on Free personal public repos. Docs name Team/Enterprise ([about-rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)); the 404 "Branch not protected" (not 403) suggests availability. Admin-enforcement and fine-grained token permission names are also unconfirmed.
- Meaning of `secret_scanning: disabled` on public repos, given "runs automatically for free".
- Fine-grained PAT scope for code-scanning default-setup and PVR endpoints.
- CodeQL yield on libraries without modelled remote sources; query-suite membership; GitHub Code Quality status.
- Action lockfile preview and the repo-level SHA-pinning setting.
- Whether `roadmap.py` treats skip/xfail tests as present.
- mypy 2.4.1 on PyPI and its 2.0 default changes; basedpyright bundling Node; typeguard `TYPE_CHECKING` behaviour; `NewType` absence across packages.
- pkix `--strict` count (8) and ctspy with-tests count (74), not re-run.
- MCP SDK `mcp==2.3.0` server-side support for `CreateTaskResult`; SDK annotation/outputSchema measurements not re-run.
- Claude Code resources @-mention and prompts-as-commands behaviour.
- RFC 9162 §2.1.4.2 consistency-proof semantics (rfclib empty); whether ctspy's vectors match the RFC.
- Wycheproof P-256/SHA-256 file names (listing truncated).
- Annotation cap of 10 warnings/errors per step (not on the workflow-commands page).
- Little's law, Duke, ProdPad, WSJF/CD3, Scrum Guide and Rust RFC sources (secondary or not opened); "postponed" ADR status.
- Actions billing page for 3.12 test jobs (plans page says free on public repos; billing page not opened).
- Hermes details beyond those quoted; CooperBench study (secondhand).