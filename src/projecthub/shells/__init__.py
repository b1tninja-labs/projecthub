"""The protocol SDK shells: the generator that writes them, the gates that judge them, and the git hook.

    generate   render every shell from data/ and templates/ (was sdk-forge/forge.py)
    verify     the five gates across the generated projects, or the Rust gates on a Cargo project
    names      whether a PyPI or crates.io name is really free
    hygiene    refuse a staged commit that drops a markdown heading or adds scratch
    hook       what the installed git hook runs (`hook pre-commit`)
    rust       create a Cargo crate or workspace from templates/rust/
    workspace  declared sibling dependencies, and the commands that bootstrap a fresh clone

The workspace root comes from projecthub.paths.ROOT, so every command here reads the same tree the
planning commands do.
"""
