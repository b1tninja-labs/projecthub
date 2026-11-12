# How the Projects board can support development here

> Written before the rename, when this system was sdk-forge, and kept as the record it was. Tool, script,
> server and variable names below are the old ones; [agents.md](agents.md#names-from-before-the-rename)
> maps them to the current ones.

Companion to [gh-projects-mcp.md](gh-projects-mcp.md), which covers the API and the client. This document
asks a different question: given how this portfolio is actually run, what should the board do for the work.
Every number below was measured on 2026-11-07 from the files and the live board, and the places where a
claim could not be checked are marked.

## 1. What the work looks like

- 29 projects have an obligation matrix: **268 rows, 249 open, 19 done** (certledger 13, ctspy 6). Only
  certledger and ctspy have any done row; 27 projects have none.
- `PORTFOLIO.md` puts the open work at about 245 obligations, "a thousand to fifteen hundred hours", and says
  the portfolio "will be pruned, not completed".
- The rules that govern the work are human-enforced today: at most two projects carry real effort at once;
  no new shell while any Tier 1 project has an unimplemented obligation; freeze a project when its first
  release finds no user; cut scope when two consecutive weeks produce no merged obligation.
- Each project already gates itself: `tests/test_roadmap_rows.py` fails on a done row with no named test, and
  pkix's `roadmap.yml` runs `sync_roadmap.py --check` on pull requests (using a `FORGE_PAT` secret to check
  out the private forge, because `uses:` cannot reach a private repository).

So the scarce resources are attention (two slots) and truth (no unevidenced claim). A board earns its place
only if it spends the first well and never weakens the second.

## 2. What the board is today

Project #2 "Protocol SDK portfolio" on `b1tninja`: 12 pkix issues, all Status `Todo` (set by GitHub's
built-in "item added" workflow, not by us), no custom fields. Nothing else is on it. The 13 built-in fields
are the whole schema.

Two data gaps showed up while checking that the board is correct:

1. **All 12 pkix issues have no milestone**, although pkix defines M1 to M7 and GitHub has all seven
   milestones. The ladder is prose (`- M1 anchors and chain discovery ...`) and no row cites an `Mn`;
   `milestone_of()` deliberately never infers one. So the Milestone field cannot be filled from the file as
   it stands. Fixing this is a decision about the ROADMAP format (add a milestone cell per row), not a code
   change, and it must be made per project.
2. **Done rows have no issue** (by design), so the board can never show burn-down. 19 of 268 rows are done;
   the other 249 open rows are the whole board.

## 3. The finding that matters most: "blocked" is stale

The generated `docs/plan.md` in 28 projects carries a Tier, a wave and a "Depends on" list. I parsed them.
Reading the prose literally, `asn1core` "does not exist yet" and blocks rpkit (Tier 1), ctspy, pkix,
revokekit, signeddata, enrollkit, pgpenv, shaken and srtpkit directly, and rtrkit, routevalid, asnkit and
bgpfeed transitively. Under that reading the only Tier 1 project and most of Tier 2 are blocked.

But `D:\code\asn1core` now has an `asn1core` package (cert, crl, der, length, name, node, oid), a test file
with fixtures, a `pyproject.toml` and git history (latest commit "RFC 7468 armour closes dk.pem ..."). The
dependents' plans were generated before that and still say it does not exist.

This is the exact failure `planner.py` was written against: an assertion that was true once and was never
re-read. A board that copied "Blocked: asn1core" from the prose would tell both active slots that the
chokepoint is closed when it may not be. Consequences for the design:

- **Blocked must be computed from the dependency's current state**, not copied from prose: project directory
  exists, `pyproject.toml` exists, package imports, tests exist, and (the real test) the rows the dependent
  needs are done with passing named tests. "Exists" and "usable for pkix" are different claims; the second
  needs a per-edge statement of which asn1core capability pkix needs.
- Regeneration of `plan.md` is `forge.py`'s job, so the stale text is a forge bug to fix at the source, not on
  the board. I did not edit any plan.
- `asn1core` is absent from `verify.py` `PROJECTS`, so none of the five gates cover it. Its own comment says
  that is a recorded decision (PORTFOLIO.md section 10); whether the decision still holds now that it has
  code and tests is the portfolio owner's call.
- I could not tell from the files which asn1core capabilities exist beyond the module names. The claim
  "unblocked" is therefore not made here.

### 3b. Audit result (2026-11-07, `deps.py`)

`asn1core`: 14 source files (3,076 lines), 11 test files, `pyproject.toml`, 17 commits; its 435 tests pass
(run directly in its directory). README status: "pre-alpha, in progress ... no release claim". It has no
`ROADMAP.md`, so it has no obligation matrix and no row can be called done there. Its own `PLAN.md` still
opens with "Does not exist yet". What it exports: BER/DER decode and encode with original bytes kept,
OIDs, names, times, certificates, CRLs, PEM armour. It has **no CMS or OCSP module**, and rpkit, signeddata
and revokekit each describe CMS or OCSP as the thing they need from it. So the claim "asn1core does not
exist" is false and the claim "asn1core unblocks everything" is unproven; the truth is per edge.

File counts alone mislead: pkix, rpkit, rtrkit and asnkit all have source and test files, but every row in
their matrices is open (0 of 12, 10, 7 and 6 done), because a generated shell's stubs raise
`NotImplementedError` and its tests check that they do. `deps.py` therefore classes a project with a matrix
and no done row as a **shell**, and only reports a "does not exist yet" claim as stale when the dependency
is real code. Result: **9 stale claims, all about asn1core** (rpkit, signeddata, pgpenv, shaken, srtpkit,
pkix, revokekit, enrollkit, ctspy). The claims that pkix "does not exist yet" in revokekit and enrollkit are
not reported as stale, because pkix is a shell.

`asn1core` is not covered by `verify.py` and `namespy` (which exists, 39 source files and 83 test files)
is not either; both are outside `PROJECTS`.

## 4. What the board should do for development

Ranked by how much of the stated pain each addresses.

**4a. Make the two-slot rule visible and enforceable.** Add an `Active` field (human-owned, two values at
most) and a `Next actions` view filtered to `Active` and `Status = Ready`. The sync must never write
`Active`: it is a decision, and the portfolio rule makes it a scarce one. A `--check` report line "N projects
marked Active (limit 2)" turns a sentence in PORTFOLIO.md into a failing check.

**4b. Make "blocked" computed and explained.** Fields `Blocked by` (text: the dependency and the missing
capability) and `Blocked` (single select: dependency / needs input / capability / transient, borrowed from
Hermes Kanban's typed blocks). Computed by the sync from section 3's rules, shown with the evidence.

**4c. Make the freeze and stall rules measurable.** "Two consecutive weeks with no merged obligation" is a
count over time. Source it from `git log` on each `ROADMAP.md` (row flips) rather than from issue close dates:
done rows without issues have no close event, and an issue closed by hand is not a merged obligation. A
weekly project status update ("On track" / "At risk") written by the sync from that count replaces a mood with
a number. Unverified: that status updates are writable on a user-owned project with this token; test before
promising it.

**4d. Keep the evidence rule intact.** The board's Status is a projection. The built-in workflow "item closed
sets Done" will let someone close an issue and mark a card Done without a test. Keep the workflow (it is
free) and add `board-ahead` drift to `--check`: board says Done, ROADMAP row is open, report it, do not
write it back. Row flips continue to go through `propose_status`, which already refuses an unevidenced done.

**4e. Give an agent a working queue.** `forge_next_actions` already returns unmet rows with their named test.
On the board that becomes: Ready (unblocked, in an Active project), sorted by wave, each card carrying Spec
and Named test as fields so a worker needs no second lookup. Claiming must become safe (see 4f).

**4f. Fix claiming before any dispatcher exists.** `planner.claim()` lists issues then edits one, so two
agents can both pass the check. Assign, re-read, and back off unless the caller is the sole assignee. That is
cheap and does not need the board at all; do it first.

**4g. Put pull requests on the same card.** GitHub's built-in workflows set a PR to Done when merged and
issues when closed. If each row's issue is referenced from the PR, the card shows the row, the PR and its
check state together. The completion check (named test exists and required PR checks are green) is the
forge's guard extended to checks, taken from Hermes Kanban's "completion contract".

## 5. Field budget (50 total, 13 built in, 37 free)

Proposed custom fields, 9: `Project` (single select, 29), `Wave` and `Tier` (single select), `Milestone`
(single select, filled only where a row cites one), `Active` (human), `Blocked` (single select), `Blocked by`
(text), `Spec` (text), `Named test` (text). `Status` reuses the pre-made field with options Triage, Ready,
In Progress, Blocked, In Review, Done added. That leaves 28 free. Row id stays in the issue marker, not in a
field, so renaming a field cannot orphan an item.

## 6. Views to create once (not synced)

Next actions (table, Active and Ready, by wave); By project (board, grouped by Project); Blocked (table,
grouped by `Blocked by`); Waves (board, grouped by Wave); Drift (table of cards the sync flags). Views are
configured once; the sync owns fields, not layouts. REST view creation exists, but `/views` returned 404 for
this user project, so assume the UI until that is resolved.

## 7. Running it in CI

`GITHUB_TOKEN` cannot reach a project, and for a user-owned project the docs point to a personal access
token. pkix already carries `FORGE_PAT` (read-only, for checkout). A scheduled `sync_roadmap.py --check
--board` needs a PAT with `read:project` in a repository secret, kept separate from the checkout PAT. An
organisation-owned board would allow a GitHub App token instead; that is the decision recorded in
gh-projects-mcp.md section 9. `actions/add-to-project` exists and could replace the add step, but I did not
read its README, so I do not rely on it. Dispatch-style automation (a Hermes-like worker) would hang off the
`projects_v2_item` webhook instead of polling.

## 8. Order of work

1. Fix `claim()` (4f). No dependency on the board.
2. Compute blocked state from reality (section 3), and open the forge bug for stale `plan.md` text.
3. `board.py`: create fields, project rows (including Blocked), add `board-ahead` drift to `--check`.
4. Decide the ROADMAP milestone cell (section 2) and the `Active` convention.
5. Weekly status update and the stall metric (4c), after verifying write access.
6. Only then consider a worker/dispatcher.

## 9. Not verified

Whether asn1core's existing code satisfies any dependent's rows; whether status updates are writable on this
project; the issue-dependency and sub-issue APIs (third-party sources say GA with limits of 50 dependencies
per issue and no cross-repository links, which would rule out cross-repo "blocked by" between projects and
force a field instead); `actions/add-to-project`'s token inputs; why `/views` is 404 for the user project.
