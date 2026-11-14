# Rust support

`projecthub shells rust new` creates a Cargo crate or workspace from `src/projecthub/shells/templates/rust/`; `projecthub shells names --crates` checks the
name; `projecthub shells verify --rust PATH` runs the gates. Nothing here is wired into the generator's manifest: Rust
projects are created once and are then owned by their repository, unlike the managed Python shells.

    python -m projecthub shells names --crates my-crate
    python -m projecthub shells rust new my-crate --dest D:/code [--bin] [--workspace] [--description TEXT]
    python -m projecthub shells verify --rust D:/code/my-crate

## Choices

- Edition 2024 and `rust-version = "1.85"`: the Edition Guide lists 1.85.0 as the release version of the
  2024 edition, and the Cargo manifest reference says `cargo new` writes edition 2024. Checked against
  cargo 1.99.0 / rustc 1.99.0 on 2026-11-11. `rust-toolchain.toml` pins channel `stable` (development
  only); CI also builds on 1.85 so the MSRV is tested.
- Lints live in the manifest `[lints]` table: `unsafe_code = "forbid"`, `missing_docs`, clippy `all` and
  `pedantic` as warnings, which `-D warnings` turns into failures.

## Crate-name rules

From the Cargo manifest reference, "The name field" (verified 2026-11-11): the name uses only alphanumeric
characters, `-` or `_` and is not empty; `cargo new` also requires a valid Rust identifier that is not a
keyword; crates.io wants ASCII only, no reserved names, no special Windows names such as "nul", and at
most 64 characters. The page does not list the reserved names, so `shells names` checks only what is stated
(the keyword list is the language's, `nul` is the only Windows name the page gives) and leaves the rest to
the registry. A leading digit is refused because `cargo new` does.

Observed against the live API on 2026-11-11, not documented on that page: `/api/v1/crates/<name>` answers
200 for `serde-json` and `SERDE_JSON` with the crate `serde_json`, so case and `-`/`_` are one name;
`serde.json` answers 404; an unpublished name answers 404 with a JSON `errors` body. The checker therefore
canonicalises to lowercase with `_`, and refuses to report FREE unless a known crate (`serde`) answers 200
and a canary (`projecthub-canary-zx17q4`) answers 404, the same canary rule as the PyPI side. Other statuses
are reported as ERROR, RATE-LIMITED or UNKNOWN, never FREE. A free name today is not a reservation:
`publish = false` stays in the scaffold until the first publish.

Requests send `User-Agent: projecthub-names/1.0 (crate name availability check)` and wait 0.15 s between
names. The crates.io crawler policy page could not be fetched (the site is a script-rendered page), so its
rate limit is not quoted here; the request volume is one call per name plus two canaries.

Sources: <https://doc.rust-lang.org/cargo/reference/manifest.html>,
<https://doc.rust-lang.org/edition-guide/rust-2024/index.html>, <https://crates.io/api/v1/crates/>.

## Gates

| Python gate | Rust gate |
| --- | --- |
| compile | `cargo check --all-targets` |
| pytest | `cargo test` |
| ruff | `cargo clippy --all-targets -- -D warnings` |
| format | `cargo fmt --check` |
| mypy | not applicable: the compiler type-checks, so `compile` covers it; printed as `mypy=n/a` |
| (none) | `cargo doc --no-deps` with `RUSTDOCFLAGS=-D warnings` |

`projecthub shells verify --rust` exits 2, not 0, when cargo is missing or the path has no `Cargo.toml`.

## How Rust ROADMAPs are read

`projecthub sync` reads the obligation tables of berkit, cardkit and piv-card (`roadmap.RUST_OWNED`, part of the
default project list). The grammar is the one in `db/parser.py`, with one more table shape:

    | Status | Obligation | Anchor | Test |

Status is the first column and there is no id column. Columns are found by header name. Only a table whose header
starts with `Status` and has an `Obligation` column holds rows: berkit's policy table (`Status | Policy | Why`),
the per-module summary tables and cardkit's assumption register (`Status | Ambiguity | ...`) are skipped.

| Written | State | Issue label | Sync does |
| --- | --- | --- | --- |
| `[ok]` | done | `status:done` | closes the issue if one is open; files none |
| `[unread]` | part | `status:partial` | opens or keeps an issue |
| `[open]` | open | `status:unimplemented` | opens or keeps an issue |

`[unread]` means the test passes but the anchor could not be read cleanly from the standard. That is unfinished
work, so it is never done. The token is kept as written: an issue says `[unread]`, not a `[part]` the file never
contained. The rule that a row is done only when a named test would fail if the behaviour regressed stays:
an `[ok]` row whose Test cell is empty or begins `none` is reported as a problem (`--check` counts it as drift) and
is not counted as a row. A table with no Test column can therefore hold only unfinished rows. The Test cell is
shown in the issue as written; projecthub reads Python tests only, so it does not check that a Rust test exists
and the issue says so.

**Several files.** A project's rows may live in more than one file. `FRAGMENTS` names the globs: berkit keeps
`crates/berkit/roadmap/*.md` and cardkit `crates/*/ROADMAP.md`, and the root file holds only the rows that span
them. `roadmap.matrix_files` lists the root file and then the fragments in path order, and `read_matrices` parses
them all. piv-card has one file (its `.private/ROADMAP.md` is not read). `matrix_path` is unchanged and still
returns the one root file: the readers built on it (status, query, board, export) see only the rows that file
holds for berkit and cardkit.

**Ids.** There is no id cell, so an id is derived: `<scope>.<first six words>-<six hex digits of a hash of the
first 80 normalised characters>`. The scope is `roadmap` for a file at the project's top level, otherwise the
file's stem, or its directory name when the file is `ROADMAP.md`, with `-` written as `_` (`oid`,
`apdu_core`). Two files that derive one scope are refused. Two rows in one file that agree on those 80
characters get `-2`, `-3` in file order. Stability:

- Reordering rows or files, inserting rows, and changing Status, Anchor or Test never change an id.
- An edit to the first 80 normalised characters of the Obligation text renames the row. Sync then reports the
  old issue as "no longer a row" and the new row as having no issue, loudly, and files a new issue on `--apply`
  (the old one stays open until someone closes it, as with a renamed id in a Python project). A row number was
  rejected because an inserted row would silently re-point every later issue at a different obligation.
- An edit after those characters (a count, a date, a test name) changes nothing.
