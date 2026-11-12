# @NAME@

@DESCRIPTION@

Status: scaffold. Nothing here is implemented beyond `name()`.

## Use

    cargo add @NAME@    # after the first publish; the name is not yet reserved
    cargo run

## Develop

    cargo fmt --check
    cargo clippy --all-targets -- -D warnings
    cargo test
    cargo doc --no-deps

Minimum supported Rust version: @MSRV@ (see `docs/msrv.md`). Contributing: `CONTRIBUTING.md`.
