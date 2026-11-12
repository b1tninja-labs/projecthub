# Contributing to @NAME@

1. Install Rust with `rustup`; `rust-toolchain.toml` selects the stable channel and the components.
2. Make a change with a test that fails without it.
3. Run the four commands in `AGENTS.md`. CI runs the same ones.
4. Keep commits small, with a message that says why. Open a pull request against `main`.

Compatibility: the crate builds on Rust @MSRV@ and newer. A change that needs a newer compiler is a
breaking decision recorded in `docs/msrv.md`, not a drive-by.
