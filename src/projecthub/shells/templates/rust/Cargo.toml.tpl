[package]
name = "@NAME@"
version = "0.1.0"
edition = "@EDITION@"
rust-version = "@MSRV@"
description = "@DESCRIPTION@"
license = "Apache-2.0"
readme = "README.md"
publish = false # remove once the name is confirmed free: python names.py --crates @NAME@

[lints.rust]
unsafe_code = "forbid"
missing_docs = "warn"

[lints.clippy]
all = { level = "warn", priority = -1 }
pedantic = { level = "warn", priority = -1 }
