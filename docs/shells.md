# Shells: the protocol SDK generator, its gates and the git hook

`projecthub.shells` is the generator that writes the RFC-protocol SDK shells in the workspace, the gates that
judge every generated project, and the pre-commit hook the workspace runs. It came from `sdk-forge`, which is
retired: the planning modules had already moved here, and this is the rest.

## Commands

    projecthub shells generate [--dry-run | --check] [--diff] [--project NAME] [--only KIND] [--adopt]
    projecthub shells generate --plan NAME [--check]      # docs/plan.md of a registered non-shell project
    projecthub shells verify [--project NAME] [--rust PATH] [--json PATH]
    projecthub shells names [--index] [--crates] [--portfolio DIR] NAME ...
    projecthub shells hygiene [--install] [--repo DIR]
    projecthub shells hook pre-commit
    projecthub shells rust new NAME --dest DIR [--bin | --workspace]
    projecthub shells workspace --check | --bootstrap PROJECT [--dry-run]

`--dry-run` and `--check` are the same flag: nothing is written, every change is listed, and the exit status
is 1 if anything would change. `--diff` adds a unified diff to a dry run. A run without `--dry-run` or
`--check` writes into the workspace, including with `--diff`, and records what it wrote in the manifest.

## Where things are

| Was (sdk-forge)          | Now (src/projecthub/shells)  |
|--------------------------|------------------------------|
| `forge.py`               | `generate.py`                |
| `forge_data/`            | `data/`                      |
| `forge_data/generated.json` | `data/generated.json` (the manifest) |
| `templates/`             | `templates/`                 |
| `rustforge.py`           | `rust.py`                    |
| `verify.py`              | `verify.py`                  |
| `names.py`               | `names.py`                   |
| `check_hygiene.py`       | `hygiene.py`                 |
| `workspace.py`           | `workspace.py`               |
| `githooks/pre-commit`    | `githooks/pre-commit`        |
| `requirements-dev.txt`   | `requirements-dev.txt`       |

The templates, the data tables, the manifest and the hook are package data: they ship in the wheel, and the
generator finds them beside `generate.py`.

## Environment

The workspace root is `projecthub.paths.ROOT`: `PROJECTHUB_ROOT`, else `FORGE_ROOT`, else `D:/code`. Every
variable this package introduces is read under its `PROJECTHUB_` name first and its old `FORGE_` name second:

| Variable | Falls back to | What it does |
|----------|---------------|--------------|
| `PROJECTHUB_ROOT` | `FORGE_ROOT` | the workspace the shells live in |
| `PROJECTHUB_MANIFEST` | `FORGE_MANIFEST` | where the generator records what it last wrote |
| `PROJECTHUB_ALLOW_HEADING_LOSS` | `FORGE_ALLOW_HEADING_LOSS` | let a commit delete a markdown heading on purpose |
| `PROJECTHUB_SRC` | (the checkout the hook sits in) | the `src` directory the hook imports projecthub from |

`VERIFY_TIMEOUT` (seconds per gate, default 900) and `PYTHON` (the interpreter the hook uses) keep their names.

## The git hook

The workspace at `D:\code` sets `core.hooksPath` to the hooks directory of a projecthub checkout at
`D:\code\projecthub`, which is `D:\code\projecthub\src\projecthub\shells\githooks`. The setting belongs to
the workspace, and nothing in this package changes it on its own. `projecthub shells hygiene --install
--repo DIR` writes it for one repository when someone runs that on purpose.

The hook runs `python -m projecthub shells hook pre-commit`. It puts its own checkout's `src` directory on
`PYTHONPATH` first, so it works whether or not projecthub is installed. It refuses a commit that deletes a
markdown heading or adds scratch (`probe_*.py`, `*.log`, `*.out`, `*.err`, `*.diff`). When it cannot import
projecthub, or finds no interpreter, it **exits 1, not 0**. A gate that cannot run and exits 0 reports the
same green as a clean tree. `git commit --no-verify` skips the hook, and that should be a choice someone makes
on purpose, not a habit.

The hook is a POSIX shell script with LF line endings, and `.gitattributes` keeps it that way on Windows
checkouts.

## What generated files say

Generated files now name `projecthub shells generate` as their source. The per-project `AGENTS.md` tells an
agent to run `projecthub pickup --who <your name> --project <name>`. A `docs/plan.md` written before the move
still carries the old generated-file header, and it is still recognised as generator output, so its next
regeneration replaces the header instead of keeping the file as hand-edited.
