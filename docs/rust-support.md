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
