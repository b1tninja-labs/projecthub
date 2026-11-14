# cardkit roadmap

| Crate | Source | Roadmap | `[ok]` | `[unread]` | `[open]` | Tests |
|---|---|---|---|---|---|---|
| `usb-descriptors` | USB 2.0 ch. 9, CCID 1.1 §5.1, ICCD 1.0 | `crates/usb-descriptors/ROADMAP.md` | 50 | 10 | 7 | 72 |
| `ccid-proto` | USB CCID 1.1 §6 | `crates/ccid-proto/ROADMAP.md` | 69 | 4 | 15 | 70 |

## Across the crates

| Status | Obligation | Anchor | Test |
|---|---|---|---|
| [ok] | The crates build and their tests pass on Rust 1.85.0, the declared MSRV | `docs/msrv.md` | `cargo +1.85.0 test` passes for the whole workspace (run by hand on 2026-11-13; the `msrv` job of GitHub Actions run 43543617695 on `main`, 2026-11-14, ran `cargo test` on 1.85 for the workspace and concluded success). Clippy on 1.85.0 is a separate row in `crates/ccid-proto/ROADMAP.md`, now passing after two parenthesisations |
| [open] | The crates are checked against bytes from a real reader and a real card trace | none | none: every byte string is constructed from the field tables, none from a device |
