# Portfolio planning improvement plan

> Written before the rename, when this system was sdk-forge, and kept as the record it was. Tool, script,
> server and variable names below are the old ones; [agents.md](agents.md#names-from-before-the-rename)
> maps them to the current ones.

This plan combines seven read-only audits taken on 2026-11-06/07: three ROADMAP clusters, the reference projects, the sdk-forge code, gitops, the portfolio as a whole, and the git inventory. No audit changed anything. Every claim below cites the evidence the audit recorded. Where the audits disagree, the disagreement is listed in section 5.

**Corrections to the brief that launched this run** (all measured by the audits):
- **PORTFOLIO.md and AGENTS.md are not on GitHub.** `b1tninja/sdk-forge` is a subtree split of `sdk-forge/`, and its root holds neither file (`gh api repos/b1tninja/sdk-forge/contents/`; there is no merge base between `main` and `forge/main`).
- **"The workspace root has no git history" is false.** The root is a repo with 42 commits (D:\code\AGENTS.md:4).
- **"certledger is a plan directory" is false.** certledger has `pyproject.toml`, 13 modules and 104 tests (AGENTS.md:42-43).
- **The board is Project #4, but #2 has the same title.** Both are open and both are titled "Protocol SDK portfolio" (#4 has 102 items, #2 has 52).

---

## 1. What is wrong today

| # | Problem | Evidence |
|---|---|---|
| 1 | **Running the forge would destroy implemented work, and a full run is the only way to regenerate docs/plan.md.** `run()` builds all 28 projects with an unconditional `write()`, with no `--project`, `--check` or dry-run flag. Measured without writing anything: ctspy's sct.py would go from 8174 B to 1398 B (stubs), sth.py and merkle.py would also become stubs, ctspy's ROADMAP would lose its 6 [ok] rows, and D:\code\AGENTS.md (4436 chars) would be replaced by the 2517-char template. | sdk-forge/forge.py:5048, :5087, :7199, :117-121 |
| 2 | **namespy is a PUBLIC repo, and its tree holds an untracked, unignored `.continue/` directory with literal `apiKey` values** (24 chars; whether they are real keys is unverified). The pre-commit gate does not run in sub-repos. | namespy `git status` `?? .continue/`; `git check-ignore` matches nothing |
| 3 | **The rule "never cite an RFC from memory" cannot be followed through MCP.** The rfclib server reads an empty 4 KB database because it is registered with `"env": {}`. The 2.4 GB corpus is in `D:\code\rfclibrary\data\rfclib.db`. | ~/.claude.json:2234-2240; %LOCALAPPDATA%\rfclib\rfclib.db |
| 4 | **239 rows cite `tests/test_obligations.py::<fn>`.** That file exists in no project. The names have no `test_` prefix, so pytest would not collect them even if it did. The evidence check is a substring match (`def load_anchor` passes for `def load_anchor_helper`, and `def parse` passes for `def parse_3164`). The per-project row test checks only `hasattr`. | forge.py:3938-3949; rowdoc.py:145-160, :158; pkix/tests/test_roadmap_rows.py:43-49 |
| 5 | **The planning authority and the tooling are unpublished and uncommitted.** PORTFOLIO.md and AGENTS.md have no remote. board.py, deps.py, ghprojects.py, portfolio.py, rowdoc.py, docs/ and 6 test files are untracked, and 7 files are modified (+655/-200). Remote CI therefore tests an older module set, and the board was built from PORTFOLIO text that is not committed (decision 8 "Decided" exists only in the working tree). | `git -C D:/code status`; `git diff PORTFOLIO.md` |
| 6 | **No gate can be enforced, and CI is red or missing.** The account is Free and the repos are private, so branch protection and rulesets return 403. sdk-forge's Actions access is `none`, so `uses: b1tninja/sdk-forge` fails. FORGE_PAT is unset in pkix. ctspy ci is red ("no wheel in dist/"). security.yml fails on ctspy and pkix (pip-audit cannot clone a private repo). certledger and trustatlas have no CI. | `gh api .../branches/main/protection`; `.../actions/permissions/access`; `gh run list`; pkix/.github/workflows/roadmap.yml:36-39 |
| 7 | **The sequencing data disagrees with itself, and the live board shows the wrong version.** forge.POSITION puts rpkit in wave 1, rtrkit and routevalid in wave 2, and 10 more shells in wave 3. PORTFOLIO §7 says wave 2 is rpkit, wave 3 is rtrkit, routevalid and asnkit, and everything else is wave 5. The §6 arrows disagree with POSITION and with the manifests (asn1core→ctspy is false because ctspy has `dependencies = []`; pkix→rpkit is missing from POSITION; routevalid→asnkit and keyproof→lgc are supported by nothing). Seven projects carry effort against the two-project rule. | forge.py:7245-7277, :7298; PORTFOLIO.md:147-158, :280-298; ctspy/pyproject.toml:35; `git log --since` counts |
| 8 | **Status text is stale everywhere.** Nine generated plan.md files say asn1core "does not exist yet". PORTFOLIO §3 and §4 counts are wrong: 28 shells (actually 27); asn1core 418 tests (actually 435); ctspy 187 (209); itulib 8/26 (11/31). localns is described as having "no directory", but it has one. The probes said to "stay in the repo" were deleted in e39ac38. asn1core/PLAN.md:3 says "Does not exist yet." | `rg -l "does not exist yet" */docs/plan.md`; PORTFOLIO.md:57-64, :110, :537; asn1core/PLAN.md:3, :159 |
| 9 | **Milestones and matrices are invisible to the tooling.** Zero shell rows sit on a milestone, because the generator emits no Milestone column. The ladder forms used by certledger (phases), trustatlas (P0-P7), keyproof (rungs), asn1core (none) and itulib (none) cannot be read by `MILESTONE_RE`. Four separate project lists mean trustatlas, asn1core, itulib, keyproof and localns are never synced or gated. namespy's emoji matrix parses to 0 rows. | sync_roadmap.py:44, :139, :404; verify.py:19; portfolio.py; namespy/docs/roadmap.md:14-15 |
| 10 | **The sync tooling can corrupt GitHub state.** `--board` matches by title and picks the first match, which may be stale #2. `--apply` creates a duplicate issue for every open row when the issue read fails, and its `--limit 500` has no truncation check. `--check` misses closed-issue/open-row and open-issue/[ok]-row drift. `board.py --project X` rewrites other projects' bodies and strips their links. | sync_roadmap.py:43, :555, existing(), sync_one(); board.py build() |

---

## 2. Decisions only you can make

| ID | Question | Recommended answer | Cost of each option | Blocks |
|---|---|---|---|---|
| D1 | Is the literal `apiKey` in namespy/.continue a real key, and was namespy made public on purpose? | Rotate the key if it is real; ignore `.continue/` in either case. Confirm public was intended. | Rotate: minutes. Not rotating: a possible leak on the next `git add -A`. | WP01, WP19 |
| D2 | How are gates enforced: GitHub Pro, public repos, or an organization on Team? | **Pro on the personal account** (cheapest and keeps the user-owned board). Public is the free alternative. | Pro: monthly fee (unverified). Public: free, but all code and plans become visible. Team org: per-user fee plus migration. Free org: **does not** add protection. | WP08, WP19 |
| D3 | Move to an organization? | **No, not now.** Revisit when webhooks or App tokens are needed. If you do move, do it *before* pushing the 30 local projects. | Org: issue types and projects webhooks, but 7 remotes change, as do the action reference, ORG constants and the board (board transfer is unverified). User account: polling only. | WP19 |
| D4 | Where does the portfolio authority (PORTFOLIO.md, AGENTS.md, protocol-sdks.md) live on GitHub? | **A new private `b1tninja/portfolio` repo**, which sdk-forge reads as a second checkout. | New repo: one more repo, and CI needs read access to it. Move it into sdk-forge/: the split carries it, but plan and tooling get mixed. Status quo: plan changes cannot go through PRs. | WP09, WP19 |
| D5 | Set sdk-forge Settings > Actions > Access to "Accessible from repositories owned by b1tninja"? | **Yes**, then pin `uses:` to a SHA and delete FORGE_PAT. | Yes: one setting, no PAT in project repos. No: a PAT in every project repo. | WP08, WP17 |
| D6 | Which board is canonical? | **Keep #4 and close #2** (close, do not delete). | Keeping both: `--board` may write to #2. | WP05, WP07, WP18 |
| D7 | Concurrency rule (§9.5, issue sdk-forge#4): restate it or enforce it, and which two projects are active? | Restate it as two feature slots, with reference and maintenance work counted unless it is tied to a named blocked row. **Active: pkix (M1-M2) plus ctspy M3-M5 or the certledger fetch layer**, whichever the anchor/key-pin decision unblocks first. | Enforcing it today freezes 5 of the 7 projects with recent commits. Restating it legitimises the current spread. | WP09 |
| D8 | Decision 8 (ctspy moved up and built) appears only in the uncommitted PORTFOLIO.md. Ratify it? | **Ratify it by committing** (WP02), after reviewing the deleted lines. | Rejecting it means rewinding the board, which already reflects it. | WP02 |
| D9 | Tiers: add an "implemented - maintain" band (asn1core, ctspy, namespy)? Promote pkix to Tier 1, or demote rpkit to Tier 2? Declare wave 1 closed? | **Yes to the band; promote pkix; close wave 1 with a date; split the wave 2 exit test into two halves.** | Status quo: a Tier 1 project (rpkit) depends on a Tier 2 shell. | WP09, WP03 (POSITION) |
| D10 | bgpfeed, bmpkit, pathattr, rpslkit and lgc, and turnkit, ohttp, h3tunnel, sfields and syslogkit: wave 3 (as forge has them) or wave 5 (as PORTFOLIO has them)? | **Wave 5, as PORTFOLIO §7 says.** Change POSITION and WAVE_NOTE[3]. | Wave 3 instead: amend §7 and accept a mislabelled "routing-operations" wave. | WP09 |
| D11 | Arrows: are pkix→rpkit, the candidate edges (h3tunnel→sfields, ohttp→namespy, cwmp→leasespy, dnssd-srp→namespy, routevalid←bmpkit/bgpfeed/rpslkit, lgc←pathattr/asnkit, bmpkit←pathattr) hard or soft? Drop routevalid→asnkit and keyproof→lgc? Relabel asn1core→ctspy? | **pkix→rpkit is hard. The others are "candidate" (M-specific). Drop the two unsupported arrows. Relabel asn1core→ctspy as "via certledger".** | Hard edges block more shells; soft edges risk hidden coupling. | WP09 |
| D12 | trustatlas (1,246 lines of collectors, private repo): ratify it or freeze it (the decision 9 wave clause)? | **Ratify it**: give it a tier and wave, and count it against D7. | Ratify: one certledger row slips. Freeze: 14 commits of work stall. | WP09, WP10 |
| D13 | namespy and RFC 9250 (DoQ): PORTFOLIO wave 4 assigns it, while namespy says "full stop". | **namespy's position wins; take DoQ out of wave 4.** | Keeping DoQ: a QUIC dependency that namespy refuses. | WP09, WP11 |
| D14 | The wave 4 exit test needs localns, which is Tier 5 and barred from getting a shell (decision 3). | **Restate the wave 4 exit test without localns until decision 3 is taken.** | Status quo: wave 4 can never exit. | WP09 |
| D15 | Pruning (§9.6): sfields, tacacs and srtpkit each omit a live-looking PyPI incumbent (http-sfv 0.9.9, tacacs_plus 2.6, pylibsrtp 1.0.0). | **Re-run the landscape check, then demote them if the incumbents are live. Freeze devicespy, cwmp and ntspy by default. Add a stopping rule that does not need a release.** | Keeping all 27 shells: about 470-1,420 h of row work, plus 27 repos of CI once pushed. | WP09, WP19 (fewer repos) |
| D16 | Decision 7: rename ntspy to ntseckit? | **Decide it before ntspy is pushed** (issue migration otherwise). | A later rename means migrating repo and issues. | WP14, WP19 |
| D17 | When does the generator stop owning a project's files? | **When the file differs from the last generated hash, OR its ROADMAP has a done row, OR the module has no NotImplementedError left.** pkix and ctspy ROADMAPs become hand-owned now. | A stricter rule (never regenerate after the first commit) loses docs/plan.md updates. | WP03 |
| D18 | Ladder forms: accept `phase N`/`P\d+`/`rung N` through a per-project ladder locator, or convert everything to M-numbers? | **Accept them through a locator.** Do not invent M-numbers. | Converting: edits to TRUST-MODEL.md, trustatlas PLAN and keyproof PLAN. | WP04, WP10 |
| D19 | Matrix format for namespy (emoji, docs/roadmap.md), asn1core (PLAN.md) and keyproof: convert them, or teach the tooling aliases? Which of these get synced and gated: itulib, trustatlas, asn1core, keyproof, namespy? | **Aliases plus a path override. Sync and gate asn1core, itulib and trustatlas; keyproof and localns stay plan-only.** Record the answer in PORTFOLIO §10. | Converting namespy: a rewrite of about 150 rows. | WP04, WP06, WP11 |
| D20 | Point the rfclib MCP at the real corpus (`RFCLIB_HOME=D:\code\rfclibrary\data` in ~/.claude.json), or migrate data into %LOCALAPPDATA%? | **Set RFCLIB_HOME.** This is your agent configuration, so an agent must not change it. | Migrate: copy 2.4 GB. Neither: spec cells stay unverified. | WP20, and therefore spec edits in WP10-WP14 |
| D21 | rfclibrary's 45 modified files (+4058/-2110, real edits even with CR ignored), the `tmp_*` scratch, and branch `rfcxml-mcp-views`: commit, discard or retire? | **Review the diff and commit what is intended; delete the tmp files; retire the branch** unless you still want it. | Discarding loses 4k lines of edits of unknown provenance. | WP11 |
| D22 | itulib: create a remote (code without the ITU corpus) named `itulib`? Should section 2 rows flip to [ok] on asn1core tests? | **Yes, as `itulib`, private, after reviewing release.yml. Section 2 rows: [part] until itulib quotes the clause.** | No remote: asn1core's evidence chain depends on a local-only repo. | WP11, WP19 |
| D23 | Should planner and board writes stay CLI-only (gh-projects-mcp.md §9 q4)? | **Yes. Add only read-only MCP toolsets.** | MCP writes: an agent can move the board without evidence. | WP04 |
| D24 | Row-level design choices: ctspy policy.acceptable ([no] or M6?); revokekit split parse/verify?; turnkit rest_credentials (rename to an OAuth name?); ohttp gateway.relay (relay or gateway?); bmpkit decode_statistics (M1 or M4?); kp.pkix-hook as a pkix row?; acceptance sentences for "design" specs (pkix store.remember, signeddata policy.refuse_weak, pgpenv limits.check, others); empty rungs (pgpenv M5, shaken M5, dnssd-srp M6, leasespy M3, tacacs M5, syslogkit M5/M6, turnkit M2, ohttp M4, sfields M2/M4, devicespy M5): add rows or drop the rungs? | ctspy: **[no]** under the existing non-goal. revokekit: **split**. turnkit: **rename** (pending rfclib). ohttp: settle the role from the docstring ("relay"). kp.pkix-hook: **add the pkix row**. Empty rungs: **add rows where the ladder text names behaviour; otherwise drop the rung.** Write the acceptance sentences yourself. | Each one left open: an issue with no testable acceptance. | WP10, WP12-WP14 |
| D25 | Workflow cadence: stale-claim timeout N days; keep the Cycle iteration field; set milestone due dates? | **Choose N; remove Cycle; leave due dates empty.** | Invented dates and iterations invite false commitments. | WP08 |
| D26 | Publishing sequence (§9.4): which of the 30 local-only projects get repos, private or public, and do you want 0.0.1 PyPI placeholders? | **Now: asn1core private (certledger CI needs it) and itulib. Shells only after WP16 has fixed their Where/milestone/README, in wave order, minus any pruned in D15. No placeholders until D2 is settled.** | Pushing shells now: 26 repos that start red (security.yml) and about 237 issues that say "does not exist yet". | WP19 |
| D27 | How does a scaffolded project resolve a workspace sibling that is **not on an index**? The forge records the edge in `POSITION` and writes it into `docs/plan.md` prose, but never into `[project] dependencies` and never into an install step, so every project invents its own: `certledger` injects `../asn1core` into pytest `pythonpath` and `mypy_path`, `pkix` checks the sibling into `.deps/` in CI, and the local machine hides all of it because every workspace project is installed editable into **user** site-packages. | **Generate both halves from the edge.** Declare the sibling in `[project] dependencies` with no version specifier (a specifier naming a version that exists only in an unpublished checkout fails the moment the sibling's version moves), and document `python workspace.py --bootstrap <project>`, which creates `.venv`, installs each *imported* sibling `--no-deps -e`, then `-e .[dev]`. Verified in a clean venv on 2026-11-07: pip reports `Requirement already satisfied: asn1core … (from pkix==0.1.0) (0.0.1)` and queries no index, and pkix's suite runs 39 passed / 2 skipped. When asn1core publishes (D26), add its name to `workspace.PUBLISHED` and the `.deps/` step and the sibling `pythonpath`/`mypy_path` entries come out together. | Path injection: no install needed, but mypy, editors and console scripts stay unresolved, and the relative path silently misses on CI where no sibling sits beside the checkout. Per-project CI checkouts: work, verified green, but hand-written once per project. Status quo: green at home, broken on a fresh clone, with the reason recorded in no repository. | WP03 (scaffolding), WP19 |

---

## 3. Work packages (write phase)

### Global rules for every package

- Never run a full `python forge.py` until WP03 has landed.
- Never run a formatter from D:\code; run it from inside the project.
- Write scratch output to the session scratchpad, never into a repo.
- Before each commit, read every deleted line in `git diff`.
- Never use `--no-verify`.
- Commit messages follow D:\code\AGENTS.md: no AI-attribution trailer.
- Change spec sections only to values confirmed by rfclib (WP20) or already read from the corpus by the naming audit (marked "corpus, re-confirm"). Leave everything else as it is.
- Tests named in Where cells are marked `(to be written)`. They are never invented as existing.

### Dependency order

```
WP01, WP02, WP20(D20) ─┬─> WP03 ─┬─> WP12, WP13, WP14 ─> WP16 ─> WP18 ─> WP19(D26)
                       │         └─> WP15
                       ├─> WP04 ─> WP05
                       ├─> WP06, WP07
                       └─> WP08 (after 04-07)
WP03 ─> WP09 (decision parts wait on D7-D15)
WP10, WP11, WP17 run independently (some steps gated)
```

### Package table

| WP | Title / goal | Write scope (may touch) | Must not touch | Steps | Acceptance test | Waits on |
|---|---|---|---|---|---|---|
| WP01 | Contain the namespy secret risk | namespy/.gitignore | everything else; no push | 1. Add `.continue/` to .gitignore. 2. Commit locally. 3. Tell the owner to delete or rotate the key. | `git -C D:/code/namespy check-ignore .continue` prints `.continue`; `git -C D:/code/namespy status --porcelain` is empty | D1 for the rotation only |
| WP02 | Commit the pending sdk-forge and PORTFOLIO work in the root repo | Root repo index only, for the 19 listed entries (sdk-forge/*.py, tests, docs/, ci.yml, PORTFOLIO.md) | the content of those files (commit as-is); scratch (overlap.log, psc.log, cache/) | 1. Run the 95 forge tests. 2. Review `git diff` deleted lines. 3. Commit in planned series (modules, tests, PORTFOLIO). 4. Do not push (WP19). | `git -C D:/code status --porcelain -- sdk-forge PORTFOLIO.md` is empty; `cd D:/code/sdk-forge && python -B -m pytest -q -p no:cacheprovider` passes | D8 |
| WP03 | Make the generator safe and fix its templates | sdk-forge/forge.py; sdk-forge/templates/* **except** test_roadmap_rows.py.tpl; new sdk-forge/forge_data/{position,certificate,routing,naming}.py (created empty of edits: a pure move); new sdk-forge/test_forge_guard.py; sdk-forge/test_forge_plan.py; sdk-forge/test_templates.py | project directories; D:\code\AGENTS.md (it is removed from the generator, not edited) | 1. Move the project data mechanically into forge_data/*.py by cluster, plus POSITION, WAVE_NOTE and CANDIDATE_EDGES into position.py. Prove the move with a golden test: render_* output is unchanged. 2. Add `--check` (writes nothing, exit 1 on differences) and `--only plan --project X`. 3. Add an ownership guard per D17 with a hash manifest. 4. Remove AGENTS_WORKSPACE from the build wrapper. 5. Collapse the build-wrapper chain into one pipeline. 6. Read the root from FORGE_ROOT. 7. Template fixes: Where cell `tests/test_<module>.py::test_<fn>` (to be written); an emitted Milestone column from a per-api `milestone` field; README clone fixed to `.git` + newline + `cd @NAME@` (forge.py:3792); AGENTS invariant wording (forge.py:6921-6923); "Twenty-eight" changed to the count (forge.py:7476); shell security.yml audits installed requirements instead of cloning. | `cd D:/code/sdk-forge && python -B -m pytest -q -p no:cacheprovider test_forge_guard.py test_forge_plan.py test_templates.py` passes (including a test that build() on a tmp copy of ctspy leaves sct.py byte-identical); `python forge.py --check; git -C D:/code/ctspy status --porcelain` shows nothing written | D17 (guard rule) |
| WP04 | One roadmap grammar and a strict evidence check | new sdk-forge/roadmap.py, test_roadmap.py; sdk-forge/rowdoc.py, test_rowdoc.py; sdk-forge/planner.py, test_planner.py; sdk-forge/templates/test_roadmap_rows.py.tpl | sync_roadmap.py (WP05), forge.py | 1. Move row and ladder parsing into roadmap.py. Statuses: `[x]` as [ok], `partial` as [part], and emoji aliases. Ladder locator for `- M<n>`, `phase N`, `P<n>` and `rung N` (D18). 2. Report a line that looks like a row but does not parse. 3. test_evidence: parse with ast, exact name, `test` prefix, `file::Class::test_x` supported. 4. rowdoc.errata_lines: match any RFC number on the line. 5. planner.rewrite_row edits only the Status cell. 6. Template test: [ok]/[part] rows must name an existing test, using `pytest.fail` instead of `assert`. 7. Optional read-only `portfolio` MCP toolset (D23). | `python -B -m pytest -q -p no:cacheprovider test_roadmap.py test_rowdoc.py test_planner.py` passes, including a test that parses every real ROADMAP.md/PLAN.md with 0 unparsable row lines and a test where `def load_anchor_helper` does not satisfy `::load_anchor` | D18, D19, D23 |
| WP05 | Make sync_roadmap safe | sdk-forge/sync_roadmap.py, test_sync_roadmap.py | other modules | 1. Import the grammar from roadmap.py. 2. Delete milestones_of and sync_board (board.py owns the board). 3. existing() and ensure_milestones() raise on a read failure. 4. Paginate, or refuse when the result size equals the limit. 5. Select the board by number from config. 6. `--check` flags: open row with a closed issue, done row with an open issue, milestone mismatch, read failure. 7. Fix the PKIX_ROW fixture. | `python -B -m pytest -q -p no:cacheprovider test_sync_roadmap.py` passes, including "a failed read in apply mode creates 0 issues" and a closed-issue drift case | WP04, D6 |
| WP06 | One project registry | sdk-forge/portfolio.py, test_portfolio.py, verify.py, new test_verify.py, deps.py, test_deps.py, names.py | forge.py (it reads forge_data/position.py) | 1. portfolio.entries() becomes the registry, with flags gated, synced, matrix path and repo (from the remote). 2. verify.PROJECTS is derived from it. 3. Read the org once (FORGE_ORG). 4. Test: the registry covers every directory found by names.portfolio_names(), or lists it as excluded with a reason. 5. EXTRA['localns'].depends = ('namespy',). 6. verify: FORGE_ROOT and a subprocess timeout. 7. A test that the real PORTFOLIO.md parses to more than 0 waves and decisions. | `python -B -m pytest -q -p no:cacheprovider test_portfolio.py test_verify.py test_deps.py` passes | D19 |
| WP07 | Board client correctness | sdk-forge/board.py, test_board.py, ghprojects.py, test_ghprojects.py | sync_roadmap.py | 1. Add `--check` (exit 1 on file-ahead or board-ahead drift, or Active > 2). 2. Make the `--project` filter touch only the filtered projects. 3. `--create` refuses when any open or closed project has the same title. 4. Count nest/block failures and exit non-zero. 5. Drop `D:/code/...` paths from issue bodies. 6. Board number from config. | `python -B -m pytest -q -p no:cacheprovider test_board.py` passes, including a fake-client test that a filtered run is idempotent and touches nothing else; `python board.py --number 4 --check` runs read-only | D6 |
| WP08 | Forge CI, workflows and hooks | sdk-forge/.github/workflows/*, sdk-forge/action.yml, sdk-forge/check_hygiene.py, sdk-forge/githooks/pre-commit, new test_hygiene.py | project repos | 1. ci.yml: `ruff check .`, plus a test that every *.py is gated; mypy for verify.py and names.py. 2. Add board-drift.yml (cron + dispatch, BOARD_PAT, report-only, step summary), board-apply.yml (dispatch only, concurrency group "board") and plan-drift.yml (`forge.py --check`, plus `deps.py --check`). 3. Pin actions to SHAs. 4. Hook: a test that HOOK equals githooks/pre-commit; tell a git error apart from an absent path; `--diff-filter=ACR`. | `gh run list -R b1tninja/sdk-forge -L 1 --json conclusion` shows success after the WP19 push; local `python -B -m pytest -q -p no:cacheprovider test_hygiene.py` passes | WP04-WP07; D2, D5, D25 |
| WP09 | PORTFOLIO.md, root AGENTS.md and sequencing data | D:\code\PORTFOLIO.md, D:\code\AGENTS.md, sdk-forge/forge_data/position.py | other files | **Mechanical now:** 1. AGENTS.md: remove the "no git history" sentence; the plan dirs are keyproof and localns. 2. §3/§4 counts: 27 shells, 435, 209, 20/23, 11/31; localns has a directory; Tier 1 count. 3. §6: correct the DANE sentence to "validation path missing"; RFC 6698 §7.1 (corpus, re-confirm). 4. Cite the probes by commit (7e1d8bd, 7f911a0). 5. Shrink decision 1's body. 6. §9.4: record the actual state (7 repos, namespy public). 7. §6 consequence 1 rewritten from deps state. **After decisions:** tiers and band (D9), waves (D10), arrows (D11), concurrency (D7), trustatlas (D12), DoQ (D13), wave 4 exit (D14), prune (D15); POSITION to match. Add §10 dated entries. | `rg -n "no git history\|does not exist as its own thing\|stay in the repo\|418 tests" D:/code/PORTFOLIO.md D:/code/AGENTS.md` is empty; `cd D:/code/sdk-forge && python -B -m pytest -q -p no:cacheprovider test_portfolio.py` passes | WP03 (AGENTS.md removed from the generator); D7, D9-D15 for steps 8+ |
| WP10 | Certificate cluster: hand-owned plans | asn1core/PLAN.md, asn1core/AGENTS.md; pkix/ROADMAP.md, pkix/pkix/validity.py (docstring only); ctspy/ROADMAP.md; certledger/ROADMAP.md; keyproof/PLAN.md; trustatlas/ROADMAP.md, trustatlas/PLAN.md | forge_data; generated docs/plan.md | 0. pkix already has an uncommitted ROADMAP diff (+16/-14): review and commit it first. 1. asn1core: status line, evidence symbol (PLAN.md:155), counts from a fresh run, marker state, `[part]` tokens, merge defect table rows 8/8b, AGENTS stale "not done" line; reconcile X.680 40.4 against 41.4 only with itulib. 2. pkix/ctspy/certledger/trustatlas: Board projection #2 changed to #4 with dated counts. 3. Where cells: collectable ids marked `(to be written)`; certledger module paths (`certledger/heads.py`, ...). 4. Add a Milestone column per Appendix A. 5. ctspy: delete the retracted "No log serves" bullet. 6. certledger: past-tense phase 1 text; cl.cag-graph spec. 7. keyproof: `certledger/TRUST-MODEL.md` I3/I9/§9; Rung column; pin the PROTOCOL.krl revision (owner supplies it). 8. trustatlas: one id set; say where catalog.json lives. Spec sections only from WP20. | `rg -n 'Project V2 \*\*#2\|"#2' D:/code/{pkix,ctspy,certledger,trustatlas}/ROADMAP.md` is empty; `rg -c "test_obligations.py" D:/code/pkix/ROADMAP.md` is 0; the WP04 real-roadmap parse test passes; `cd D:/code/asn1core && python -m pytest -q` is green | WP04 (format); D18, D24; WP20 for spec cells |
| WP11 | Reference projects | itulibrary/ROADMAP.md, PLAN.md, README.md; namespy/docs/roadmap.md, namespy/namespy/enums.py:260 (comment), new namespy/AGENTS.md; rfclibrary/README.md | rfclibrary code; the itulib corpus | 0. itulibrary has 17 dirty files: commit them first (owner review). 1. itulib: fix symbols :22 and :29, add file prefixes, ia.budgets becomes [part], rewrite the stale §2, add the two marker rows, OID clause (only after `itulib read`), Board #4 text, PLAN.md stale lines, README install note, ladder from PLAN.md §5 (D22). 2. namespy: file::symbol Where cells, stale :235-238 and :223, "RFC " prefixes, enums.py comment §7.1 (corpus, re-confirm). 3. rfclib README: RFC 9280 to 9920 (corpus, re-confirm), tools list, reader-ui note, `init` row, offline wording. | `rg -n "six_tools\|stays .\[ \]. until that commit" D:/code/itulibrary/ROADMAP.md` is empty; `cd D:/code/itulibrary && python -m pytest -q` is green; `rg -n "RFC 9280" D:/code/rfclibrary/README.md` is empty | D19, D21, D22; WP20 |
| WP12 | Certificate shells: data | sdk-forge/forge_data/certificate.py (revokekit, enrollkit, signeddata, pgpenv, shaken, srtpkit entries only) | pkix and ctspy entries; generated files | 1. Per-api milestones per Appendix A. 2. docs lists: revokekit +RFC 5280 (and 8954 if kept); srtpkit +5506/6904/9335 if kept; pgpenv replace "RFC 4880bis". 3. Section cites only from WP20. 4. Design rows per D24. 5. Errata TODO lines resolved with dated rfclib results. | `cd D:/code/sdk-forge && python forge.py --check` diff lists only these six projects; `python -B -m pytest -q -p no:cacheprovider test_forge_plan.py` passes | WP03, WP20, D24 |
| WP13 | Routing shells: data | sdk-forge/forge_data/routing.py | position.py | 1. Milestones per Appendix A. 2. Non-goal fixes: rtrkit "asnkit" becomes pathattr; rpslkit drops "and pkix". 3. asnkit lpm: drop the RFC 6396 cite, state the acceptance. 4. docs lists: rpkit +9674/9829 or drop them; pathattr +6793/4760/7606; rpslkit +7909; routevalid +8481 + the ROV document (WP20). 5. bmpkit open question on RFC 9515/9736. 6. Rows for unrowed ladder items (D24). | as WP12, for the 9 routing projects | WP03, WP20, D24 |
| WP14 | Naming/transport shells: data, plus localns plan | sdk-forge/forge_data/naming.py; localns/PLAN.md | position.py | 1. Milestones per Appendix A. 2. Section cites from the naming audit (corpus, re-confirm via WP20): dnssd-srp, leasespy, ntspy, tacacs, syslogkit, turnkit, h3tunnel, ohttp, sfields, cwmp. 3. Errata lines: tacacs 7754 and RFC 9887 §4; leasespy 2131 Editorial-only and 5007 3763; ntspy and tacacs "re-check" lines resolved; turnkit 6268; h3tunnel RFC 9931 date 2026-03. 4. cwmp Digest cite becomes RFC 7616. 5. sfields: remove the ungrounded "outer/keyed/typed" text, M2 gets display strings and erratum 8869. 6. localns PLAN.md:69, :71, :72 spec fixes; Evidence cells gain files. | as WP12, for the 11 projects; `rg -n "RFC 4795 section 3.2\|RFC 1002 sections 14.2" D:/code/localns/PLAN.md` is empty | WP03, WP20, D16, D24 |
| WP15 | Regenerate docs/plan.md only | `<project>/docs/plan.md` for the 27 shells, one commit per repo | every other file | 1. `python forge.py --only plan --project <p>` for each shell. 2. Review the diff. 3. Commit in each repo. | `rg -l "does not exist yet" D:/code/*/docs/plan.md` lists nothing that deps.py reports as existing; `python sdk-forge/deps.py --check` exits 0 | WP03; D10 for the wave text (or rerun after WP09) |
| WP16 | Regenerate shell artefacts | for the 25 guard-eligible shells: ROADMAP.md, README.md, docs/rfc-map.md, stub docstrings, tests/test_roadmap_rows.py, .github/workflows/security.yml | pkix, ctspy, every non-shell project | 1. `python forge.py --check` and review. 2. Generate the guard-eligible files. 3. Run each project's tests. 4. Commit per repo. | `rg -l "test_obligations.py" D:/code/*/ROADMAP.md` is empty; `rg -l "\.cd " D:/code/*/README.md` is empty; every shell `python -m pytest -q` is green | WP12-WP14, WP04 (template) |
| WP17 | Project CI repairs | ctspy/.github/workflows/*, pkix/.github/workflows/* | the shell templates (WP03) | 1. Port pkix f1091be (build before check_dist) to ctspy. 2. security.yml audits installed requirements. 3. roadmap.yml uses `b1tninja/sdk-forge@<sha>` with GITHUB_TOKEN (after D5); delete the FORGE_PAT step. | `gh run list -R b1tninja/ctspy -L 2 --json conclusion` and the same for pkix show success | D5 (step 3 only) |
| WP18 | Re-project the board | GitHub Project #4 and issues (through board.py and sync_roadmap.py only) | files | 1. Close #2 (D6). 2. Run `board.py --number 4` dry-run plan into the job summary. 3. Owner approves. 4. `--apply`. 5. Re-run sync_roadmap `--apply` per synced repo. 6. Regenerate issues #41 and #42. | `python sdk-forge/board.py --number 4 --check` exits 0; `python sdk-forge/sync_roadmap.py --check` exits 0 | WP05, WP07, WP10, WP11, WP16, D6 |
| WP19 | git init / remote / push sequence | remotes and GitHub repos named in D26; the root's `forge-remote` branch | `--mirror`, `--all`, refs/cline/*; any force push | 1. Preconditions: WP01 done, D1-D5 and D26 decided, every tree clean. 2. sdk-forge: `git subtree split --prefix sdk-forge -b forge-remote`, then `git push forge forge-remote:main` (never `git push forge main`). 3. Portfolio repo per D4. 4. asn1core and itulib: `gh repo create b1tninja/<name> --private --source <dir> --remote origin --push`, named branch only. 5. Shells in wave order, minus the pruned ones (D15), ntspy after D16. 6. Delete or push pkix `backup/sync-roadmap-tool` deliberately. | for each pushed p: `gh repo view b1tninja/<p> --json visibility,defaultBranchRef` succeeds and `git -C D:/code/<p> status -sb` shows `## main...origin/main` with nothing ahead | D1-D5, D15, D16, D22, D26; WP02, WP16 |
| WP20 | Re-verify every protocol citation through rfclib | session scratchpad only (verification table) | every repo file | 1. After D20, run rfclib info, errata and outline for every RFC the audits listed. 2. Re-confirm the corpus-read claims. 3. Produce a verified "row → section" table for WP10-WP14. | rfclib `info('RFC5280')` returns a record, not "library is empty" | D20 |

---

## 4. Milestone placements (Appendix A)

"Q" means the placement quotes the project's own ladder or plan. "J" means it is the auditor's judgement. Approve or strike each line.

**ctspy** (M ladder)
| Row | Milestone | Basis |
|---|---|---|
| merkle.verify_inclusion / verify_consistency [ok] | M1 | Q "static Merkle trees and both proof types" |
| sct.parse / sct.verify / sth.verify [ok] | M2 | Q "SCT and STH parsing plus signature verification" |
| tiles.fetch_tile | M3 | Q "tile fetching with caching and a mirror fallback" |
| client.add_chain | M4 | Q "submission client for chains and pre-certificates" |
| monitor.watch | M5 | Q "monitor with a durable cursor" |
| entry.parse_extra_data [ok] | M6 | J production-entry reconstruction |
| policy.acceptable | M6, or [no] | J; D24 |

**pkix**
| Row | Milestone | Basis |
|---|---|---|
| store.remember | M1 (alternative M5) | J chain.py "discovery against a store" |

**certledger** (TRUST-MODEL.md §10 phases)
| Row | Milestone | Basis |
|---|---|---|
| cl.dane | phase 3 | Q "impostor endpoint ... lands in UNAUTHENTICATED" |
| cl.multi-path | phase 4 | Q "single-path absence claim is refused by I5" |
| cl.self-transparency | phase 4 (alternative 5) | J "mirror diff runs clean" |
| cl.keyproof-consume | keyproof rung 1 | Q own spec cell |
| cl.aup-review | before first public release | J |
| cl.claims-discipline | before first public release | J |

**revokekit**
| Row | Milestone | Basis |
|---|---|---|
| request.build | M1 | Q "request and response codec" |
| response.parse_and_verify | M2 (split per D24) | Q "signature and delegated responder verification" |
| nonce.generate, nonce.check | M3 | Q "nonce rules ... missing-nonce policy" |
| freshest.is_fresh, http.query | M4 | Q "freshness, caching ... bounded HTTP client" |
| crl.fetch, stapling.verify_stapled | M5 | Q "CRL and delta CRL ... stapling check" |

**enrollkit**
| Row | Milestone | Basis |
|---|---|---|
| messages.build_cr, messages.parse | M1 | Q "CMP message codec round trip" |
| protection.protect, verify_protection | M2 | Q "protection both ways" |
| crmf.build_request | M3 | Q "CRMF request building" |
| flow.enroll, poll_until_ready | M4 | Q "transaction state machine ... poll" |
| est.simple_enroll, est.ca_certs | M5 | Q "EST client" |

**signeddata**
| Row | Milestone | Basis |
|---|---|---|
| cms.load, cms.dump | M1 | Q "load and dump for SignedData" |
| verify.verify, ess.signing_certificate_v2 | M2 | Q "verify: digest, signature ... ESS CertID" |
| policy.refuse_weak | M2 | J verify.py "algorithm policy" |
| signer.build_signer_info | M3 | J "signed properties" |
| verify.countersign | M3 | Q "countersignature" |
| tsp.build_request, tsp.verify_token | M4 | Q "timestamp request, response ... token chain" |
| envelope.decrypt | M5 | Q "EnvelopedData and AuthenticatedData" |

**pgpenv**
| Row | Milestone | Basis |
|---|---|---|
| packet.iter_packets, encode_packet, armor.encode, armor.decode | M1 | Q "packet framing and armor" |
| limits.check | M1 | J "adversarial input tests" |
| key.load_key, key.fingerprint | M2 | Q "v6 key parsing and fingerprints" |
| signature.sign, signature.verify | M3 | Q "signature create and verify" |
| session.encrypt, session.decrypt | M4 | Q "classic and AEAD session encryption" |

**shaken**
| Row | Milestone | Basis |
|---|---|---|
| passport.create, passport.verify | M1 | Q "PASSporT create and verify" |
| claims.shaken_claims | M1 | J |
| identity.build_header, parse_header | M2 | Q "Identity header build and parse" |
| credentials.resolve | M3 | Q "credential resolution with x5u bounds" |
| attest.policy_for, verify.verify_request | M4 | Q "attestation policy and ... outcome object" |

**srtpkit**
| Row | Milestone | Basis |
|---|---|---|
| params.select_profile, kdf.derive, context.for_stream | M1 | Q "parameters, KDF and context" |
| transform.protect, unprotect | M2 | Q "AES-GCM protect and unprotect" |
| replay.check | M3 | Q "rollover estimate and replay window" |
| srtcp.protect_rtcp, mki.select_key | M4 | Q "SRTCP and MKI" |
| dtls.export_keys | M5 | Q "DTLS key export" |

**keyproof** (adoption rungs)
| Row | Rung | Basis |
|---|---|---|
| kp.pkix-hook | rung 1 | Q "pkix rejects any chain containing a listed SPKI" |
| kp.feed-signed | rung 2 | Q "signed, machine-readable, transparent feed" |
| kp.ssh-export, kp.ssh-sshsig, kp.ext-critical | rung 3 | Q KRL / SSHSIG / extension rule |
| kp.ordered-hashes | rung 3 | J |
| kp.artifact, verify.raw, oracle-shape, spki-bind, log-append, mirror, no-expiry, no-key-storage, probe-silence, claims-discipline | before rung 1 | J register preconditions |

**trustatlas** (PLAN P0-P7)
| Row | Phase | Basis |
|---|---|---|
| ta.probe_sources | P1 | Q "probe and schema" |
| ta.provenance_fields | P1 | J P1 exit |
| ta.collect_store, collect_bundle, join_programs, spki_key_identity | P2 | Q "Local store export ... cacert.pem ... SPKI join ... through asn1core" |
| ta.readers_remaining | P2 | Q own spec cell |
| ta.compose_collect | P2 | J "a second run reproduces its counts" |
| ta.ctl_decode | P2, blocked on pkix M7 | J |
| ta.collect_certdata | P3 | J |
| ta.derive_issuer_edges | P3 | Q "derivation edges computed" |
| ta.coverage_index | P4 | Q |
| ta.renderer | P5 | Q |
| ta.render_blocklist | P6 | Q |
| ta.publish_bundles | none | J gated by the redistribution decision |

**asn1core**: no ladder exists. dk.printable-charset, dk.indefinite and dk.corpus stay unplaced. The nearest text is the PORTFOLIO wave 1 exit (J).

**rpkit**
| Row | Milestone | Basis |
|---|---|---|
| signedobject.decode_signed_object | M1 | Q "RFC 6488 envelope" |
| repository.open_cache, rrdp.fetch_notification, rrdp.apply_delta | M2 | Q "RRDP notification, snapshot and delta" |
| manifest.check_manifest, next_manifest_number | M3 | Q "hash verification ... number boundary" |
| anchors.load_trust_anchor | M4 | J path walk starts at an anchor |
| validation.validate_roa, validate_origin | M4 | Q "ROA extraction ... ROV result" |
| slurm.apply_slurm | M5 | Q "SLURM filtering" |

**rtrkit**
| Row | Milestone | Basis |
|---|---|---|
| serialize.decode_pdu, encode_reset_query, encode_ipv4_prefix_record, encode_router_key_record | M1 | Q "PDU codec, byte-exact" |
| session.connect | M2 | Q "client session" |
| session.serve | M3 | Q "server session" |
| inject.truncated_record | M4 | Q "fault injection suite" |

**routevalid**
| Row | Milestone | Basis |
|---|---|---|
| observe.record, visibility.announcers | M1 | Q "observation model and visibility tracking" |
| origin.check_origin | M2 | Q |
| leak.check_path | M3 | Q |
| score.confidence, event.emit | M4 | Q "confidence scoring ... event schema" |
| replay.reevaluate | M5 | Q |

**asnkit**
| Row | Milestone | Basis |
|---|---|---|
| mrt.iter_records, rib.iter_rib_entries | M1 | Q "MRT reader for TABLE_DUMP_V2" |
| lpm.longest_match, build_radix | M2 | Q "radix LPM" |
| history.lookup_asn | M3 | Q |
| rov.annotate | M4 | Q "validation join from rtrkit" |

**bgpfeed**
| Row | Milestone | Basis |
|---|---|---|
| mrt.iter_updates | M1 | Q |
| ris.subscribe | M2 | Q "RIS websocket client" |
| routeviews.follow, state.checkpoint | M3 | Q "RouteViews delta poller with checkpoints" |
| state.resume | M3 (alternative M2) | J |
| budget.policy | M4 | Q "politeness and drop accounting" |

**bmpkit**
| Row | Milestone | Basis |
|---|---|---|
| header.decode_common, messages.decode_route_monitoring | M1 | Q "header and message codec" |
| server.serve | M2 | Q |
| rib.view_of | M3 | Q |
| messages.decode_statistics | M4 (conflicts with M1, D24) | Q "statistics" |
| mirror.extract_bgp_message | M4 | Q "mirroring passthrough" |
| replay.from_capture | M5 | Q |

**pathattr**
| Row | Milestone | Basis |
|---|---|---|
| message.decode, message.encode | M1 | Q "message framing" |
| limits.check | M1 | J |
| roles.peer_role | M2 | Q "including ADD-PATH and role" |
| attribute.decode_attributes, aspath.parse_as_path | M3 | Q "path attributes with 4-octet AS" |
| nlri.parse_nlri | M4 | Q "multiprotocol NLRI" (its spec conflicts) |
| communities.decode_communities, decode_extended | M5 | Q |

**rpslkit**
| Row | Milestone | Basis |
|---|---|---|
| parser.parse | M1 | Q |
| client.query | M2 | Q |
| sets.expand | M3 | Q |
| resolve.origins | M4 | Q |
| objects.matches_prefix | M4 | J |
| compare.conflicts | M5 | Q |

**lgc**
| Row | Milestone | Basis |
|---|---|---|
| query.rib, query.detail, guard.assert_read_only | M1 | Q "query and result model plus read only enforcement" |
| protocol.encode_request, decode_response | M2 | Q "LGS and LGC transport" |
| transport.call | M2 | J |
| parsers.parse_rib | M3 | Q "FRR and BIRD parsers" |
| federation.query_many | M5 | Q |

**dnssd-srp**
| Row | Milestone | Basis |
|---|---|---|
| dso.encode_dso, session.open_session | M1 | Q "DSO codec and session" |
| sig0.sign, sig0.verify | M2 | Q |
| srp.build_update | M3 | Q "SRP requester" |
| discovery.registrar_from_zone | M3 | J |
| registrar.validate_update | M4 | Q |
| registrar.ttl_for | M4 | J (alternative M3 "TTL") |
| push.subscribe | M5 | Q |

**leasespy**
| Row | Milestone | Basis |
|---|---|---|
| wire.decode_v6, encode_v6, decode_v4, options.encode_option, decode_options | M1 | Q "wire codec and option registry" |
| validate.check_message | M1 | J |
| fsm.client_step | M2 | Q |
| duid.parse_duid | M3 | Q "DUID stability included" |
| relay.wrap_relay_forward | M4 | Q |
| leasequery.build_query | M5 | Q |
| failover.peer_transition | M6 | Q |

**ntspy**
| Row | Milestone | Basis |
|---|---|---|
| packet.decode_packet, encode_packet | M1 | Q |
| extension.authenticate, verify | M2 | Q |
| ke.encode_record, ke.establish | M3 | Q "NTS-KE client" |
| client.synchronize | M3 | J |
| cookies.mint, validate | M4 | Q |
| server.serve | M5 | Q |

**tacacs**
| Row | Milestone | Basis |
|---|---|---|
| header.decode/encode, obfuscation.obfuscate/deobfuscate, body.authenticate_start/authorize_command/account_command | M1 | Q "header, obfuscation and body codec" |
| client.login | M2 | Q |
| server.serve | M3 | Q |
| tls.connect_tls | M4 | Q "RFC 9887 TLS transport" |

**syslogkit**
| Row | Milestone | Basis |
|---|---|---|
| message.parse, message.format, sd.encode_sd, sd.decode_sd | M1 | Q "message, PRI and structured data codec" |
| legacy.parse_3164 | M1 | J |
| receiver.listen_udp | M2 | Q |
| framing.frame_octet_counted, unframe | M3 | Q |
| receiver.listen_tls, tls.client_context | M4 | Q |

**turnkit**
| Row | Milestone | Basis |
|---|---|---|
| message.encode_message, decode_message | M1 | Q "STUN codec" |
| allocation.allocate, refresh | M3 | Q "allocation lifecycle ... refresh" |
| credentials.rest_credentials | M3 | J |
| permission.create_permission | M4 | Q |
| ice.gather_candidates, schedule_checks, sansio.on_binding_response, trickle.encode_end_of_candidates | M5 | Q "sans-IO ICE agent ... then trickle" |

**h3tunnel**
| Row | Milestone | Basis |
|---|---|---|
| capsule.encode/decode, httpdatagram.encode_datagram | M1 | Q "capsule and HTTP Datagram framing" |
| ctx.allocate_context, sansio.on_datagram | M2 | Q |
| connect_udp.build_request | M2 | J |
| aio.open_udp_tunnel | M3 | Q |
| aio.serve_proxy, limits.clamp_payload | M4 | Q "proxy server ... limits" |
| connect_ip.target_uri | M5 | Q |

**ohttp**
| Row | Milestone | Basis |
|---|---|---|
| keys.generate_config, parse_config | M1 | Q "key configuration codecs" |
| message.decapsulate, client.request | M2 | Q |
| message.encapsulate, crypto.supported_suites | M2 | J "using the hpke extra" |
| gateway.relay | M3 | Q |
| svcb.select_gateway | M5 | Q |

**sfields**
| Row | Milestone | Basis |
|---|---|---|
| parse.parse_list, parse_dictionary, item.parse_item, serialize.serialize_list, serialize_dictionary | M1 | Q "items, lists and dictionaries" |
| keys.parse_key, errors.at | M1 | J |
| coding.inner_parse, inner_serialize | M3 | Q (basis in RFC 9651 unverified) |
| conformance.run_suite | M5 | Q |

**cwmp**
| Row | Milestone | Basis |
|---|---|---|
| envelope.encode/decode | M1 | Q |
| methods.build_inform, set_parameter_values | M2 | Q |
| session.handle_session, auth.digest_challenge | M3 | Q |
| endpoint.run_inform | M4 | Q |
| datamodel.resolve_path | M5 | Q |
| datamodel.coerce_value | M5 | J |
| transfer.build_download | M6 | Q |

**devicespy**
| Row | Milestone | Basis |
|---|---|---|
| xmlsafe.parse, description.render_device_xml, scpd.parse_scpd | M1 | Q |
| advertise.announce, search_response | M2 | Q |
| control.dispatch | M3 | Q |
| events.notify, renew | M4 | Q |

**localns**: no ladder exists, so all 8 rows stay unplaced until you write one.

**itulib** (proposed ladder taken from PLAN.md §5; adopting it is D22)
| Row | Milestone | Basis |
|---|---|---|
| ia.fetch_verify | M1 | Q "answers on a machine that ran fetch" |
| erratum_artefact_ids, catalog_asn1, clause_splitter_fidelity, refs_links_cite, normative_view | M1 | J |
| ia.budgets ([part]) | M2 | Q "pytest passes on one that has not" |
| retire_setof_unsorted, retire_bool_nonzero, retire_int_nonmin, retire_oid_first_arc | M3 | Q "every marker ... retired" |
| repoint_time_leap_second | M3 | Q "or re-pointed" |
| charsets_from_x680, set_vs_setof | M3 | J |
| series_index, packaging, claude_registration | none | J |

**namespy** (W order from docs/roadmap.md:329-330)
| Row | Placement | Basis |
|---|---|---|
| W6 | first | Q |
| W7 | after W6 | Q |
| W8 | after W7, gated on U6 | Q |
| W9 | after W7 | Q |
| W10 | last | Q |
| U6 transfer leg | before W8 | Q |
| U7 | wave 4 | J; D14 |
| DoQ row | D13 | |
| :195 citation row | none | |

---

## 5. Not verified

- **RFC citations.** Every RFC section, status, successor and errata count. The certificate and routing audits had an empty rfclib. The naming and reference audits read `D:\code\rfclibrary\data\rfclib.db` by SQL, so their claims (localns sections, tacacs 7754 / RFC 9887 §4, sfields' absent "outer/keyed/typed", RFC 6698 §7.1, RFC 9280→9920, h3tunnel's RFC 9931 date, the cwmp RFC 7616 cite) must be re-confirmed through the MCP (WP20).
- **ITU-T clauses.** X.680 40.4 vs 41.4, and X.690 8.19.4 vs 8.19.1-2, are taken from asn1core commit 0fc1e5f. itulib was not run.
- **Non-RFC specs.** The TR-069/TR-181 revisions, UDA 2.0 and GENA 1.0, RIS Live and RouteViews versions, the LGC/LGS source, and the PROTOCOL.krl revision.
- **The namespy key.** Whether the 24-char `apiKey` literal is a real key.
- **CI failure.** The cause of ctspy's lint-job failure.
- **GitHub specifics.** Cross-repo blocked-by rules, board transferability between owners, Pro and Team pricing, and the Copilot coding agent's assignment, branch and approval mechanics.
- **Incumbents.** Maintenance status of http-sfv, tacacs_plus and pylibsrtp, and whether PyPI placeholders exist.
- **Counts.** The total of 995 generated files.
- **asn1core figures disagree between audits.** Tests: 11 files / 153 `def test_` vs 8 test files, and 435 collected. Rows: 21 [x] of 24 vs 20 of 23. Re-measure in WP10.
- **itulib row count.** 31 rows (11 [ok]) in one audit vs 27 in another.
- **Overwrite history.** Whether a past forge run actually overwrote pkix's milestone citations (an inference, not reproduced).
- **itulib publication.** What itulib would commit at runtime with respect to the ITU corpus licence.