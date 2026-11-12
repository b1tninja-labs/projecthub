"""Generate and refresh the RFC-protocol SDK shells under the workspace root (projecthub.paths.ROOT).

Every managed artefact is produced from the per-project tables in shells/data/, so configuration and
prose cannot drift between the generated projects. A rerun rewrites only the files the generator
still owns: one it last wrote and nobody has edited since. A file that was edited, a project whose
ROADMAP.md has a done row, and a module with no NotImplementedError left are kept, and reported.

    projecthub shells generate --check                      # write nothing; list what would change, exit 1 if any
    projecthub shells generate --check --diff --project pkix
    projecthub shells generate --only plan --project ntspy  # regenerate one project's docs/plan.md only
    projecthub shells generate --plan kconfigpy --check     # docs/plan.md of a registered non-shell project
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import difflib
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Sequence
from typing import Any

from projecthub import paths
from projecthub.shells.data import certificate, naming, routing
from projecthub.shells.data.position import (
    CANDIDATE_EDGES,
    CANDIDATE_NOTE,
    DEPENDS_WHY,
    POSITION,
    RULES,
    TIER_NOTE,
    WAVE_NOTE,
)

# The workspace the shells live in, resolved once by projecthub.paths: PROJECTHUB_ROOT points the generator
# somewhere else - a scratch copy, or a checkout on another machine.
ROOT = paths.ROOT
HERE = pathlib.Path(__file__).resolve().parent
LICENSE_SRC = HERE / "templates" / "LICENSE-2.0.txt"

# PyPI evidence was collected on this date and every README says so: pymasque
# shipped 2026-11-04 and pydhcp 2026-10-21, so the landscape moved twice while
# this survey was being read. An undated "nothing exists" claim rots in a week.
SURVEY_DATE = "2026-11-05"
PYTHON_FLOORS = ("3.12", "3.13", "3.14")
ORG = "b1tninja"


DOC_WIDTH = 96  # generated docstring text is wrapped to this width, quotes included

# Prose is written for a reader and for a linter that flags look-alikes: U+2013 and the
# other dash-like code points become an ASCII hyphen before anything is written.
DASH_LIKE = ("\u2013", "\u2014", "\u2212", "\u2010", "\u2011")


def ascii_prose(text: str) -> str:
    """Replace dash look-alikes with ASCII hyphens so generated prose is plain ASCII."""
    for ch in DASH_LIKE:
        text = text.replace(ch, "-")
    return text.replace("\u00a0", " ")


def collapse(text: str) -> str:
    """Squeeze a prose blob to single-spaced sentences on one line."""
    return " ".join(ascii_prose(str(text)).split())


def first_sentence(text: str) -> tuple[str, str]:
    """Split prose into its leading sentence and the rest of the text.

    A period only ends the summary when an uppercase letter follows it, so 'sec. 14' and
    'e.g. one' inside a summary stay inside the summary.
    """
    body = collapse(text)
    best = -1
    for match in re.finditer(r"[.!?]", body):
        tail = body[match.end() :].lstrip()
        if tail[:1].isupper():
            best = match.end()
            break
    if best < 0:
        return body.rstrip(), ""
    return body[:best].rstrip(), body[best:].strip()


def sentence(summary: str) -> str:
    """Return a single summary sentence, terminated with a period."""
    head = first_sentence(summary)[0]
    return head if head.endswith((".", "!", "?", ":")) else head + "."


def wrap(
    text: str, width: int, indent: str = "", first: str | None = None
) -> list[str]:
    """Wrap prose to width, keeping words whole and returning indented lines.

    `first` lets a caller prefix only the opening line, which is how a docstring puts its
    opening quotes in front of the summary and nowhere else.
    """
    if not text:
        return []
    return textwrap.wrap(
        text,
        width=max(20, width),
        initial_indent=indent if first is None else first,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def docstring(summary: str, body: str = "", indent: str = "") -> str:
    """Render a docstring: one summary sentence, a blank line, then the wrapped body.

    The renderer, not the caller, owns the layout, so no project ships a docstring whose
    first line runs on into the body or whose lines overflow the wrap width.
    """
    quote = chr(34) * 3
    head = sentence(summary).replace(quote, chr(34))
    blocks = [block for block in (first_sentence(summary)[1], collapse(body)) if block]
    if not blocks:
        one = indent + quote + head + quote
        if len(one) <= DOC_WIDTH and not head.endswith(chr(34)):
            return one
    room = DOC_WIDTH - len(indent) - 3
    lines = wrap(head, room, indent, indent + quote)
    if blocks:
        lines.append("")
        for position, block in enumerate(blocks):
            if position:
                lines.append("")
            lines += wrap(block.replace(quote, chr(34)), room, indent)
    lines.append(indent + quote)
    return "\n".join(lines)


def table(header: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    out = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(out)


def spine_rows(docs: list[dict[str, str]]) -> str:
    return table(
        ("Document", "Status", "Published", "What it gives this project"),
        [
            (d.get("label") or "RFC " + d["n"], d["status"], d["date"], d["why"])
            for d in docs
        ],
    )


def prior_rows(art: list[dict[str, str]]) -> str:
    return table(
        ("Project", "Version", "Last release", "Why it is not this project"),
        [
            (a["name"], a.get("version", "-"), a.get("date", "-"), a["note"])
            for a in art
        ],
    )


def bullets(items: list[str]) -> str:
    return "\n".join("- " + i for i in items)


# ---------------------------------------------------------------------------- project data
#
# The per-project tables live in shells/data/, one module per cluster, so an edit to a project's data
# cannot touch the logic in this file and a change here cannot quietly rewrite a row. PROJECT_ORDER is
# the order a full run and the index table use; it is checked against the data rather than trusted.
PROJECT_ORDER = (
    "rpkit",
    "rtrkit",
    "asnkit",
    "h3tunnel",
    "dnssd-srp",
    "turnkit",
    "leasespy",
    "cwmp",
    "devicespy",
    "ohttp",
    "ntspy",
    "tacacs",
    "signeddata",
    "pgpenv",
    "syslogkit",
    "sfields",
    "shaken",
    "srtpkit",
    "pkix",
    "revokekit",
    "enrollkit",
    "ctspy",
    "pathattr",
    "bmpkit",
    "rpslkit",
    "bgpfeed",
    "routevalid",
    "lgc",
    "miblang",
)


def load_projects() -> list[dict[str, Any]]:
    """Every project of every cluster, in PROJECT_ORDER, refusing a duplicate or an unlisted name."""
    by_name: dict[str, dict[str, Any]] = {}
    for module in (routing, naming, certificate):
        for project in module.PROJECTS:
            if project["name"] in by_name:
                raise ValueError(
                    project["name"] + ": defined in more than one shells.data module"
                )
            by_name[project["name"]] = project
    if set(by_name) != set(PROJECT_ORDER):
        raise ValueError(
            "shells.data and PROJECT_ORDER disagree: "
            + ", ".join(sorted(set(by_name) ^ set(PROJECT_ORDER)))
        )
    return [by_name[name] for name in PROJECT_ORDER]


ALL_PROJECTS = load_projects()
OPEN_QUESTIONS = {
    **routing.OPEN_QUESTIONS,
    **naming.OPEN_QUESTIONS,
    **certificate.OPEN_QUESTIONS,
}


# ------------------------------------------------------------------------- rendering helpers

GT = chr(62)  # the shell guard misreads a literal greater-than in source as redirection
# ">=" and never ">": ">3.12" is exclusive under PEP 440, so it refused 3.12.0 itself while every
# classifier and the CI matrix claimed 3.12. 31 pyprojects carried that before it was caught.
GE = GT + "="


def subs(text: str, mapping: dict[str, object]) -> str:
    for key, value in mapping.items():
        text = text.replace("@" + key + "@", str(value))
    return text


def reqs(pairs: list[tuple[str, str]]) -> str:
    return ", ".join('"' + n + GT + v + '"' for n, v in pairs)


def toml_list(items: list[str]) -> str:
    return "[" + ", ".join('"' + i + '"' for i in items) + "]"


PYPROJECT = """
[build-system]
# Declared explicitly rather than relying on pip's legacy setuptools fallback: with no
# build-system table pip injects an unpinned setuptools environment, so a build is
# neither reproducible nor hermetic.
requires = ["hatchling@GE@1.25"]
build-backend = "hatchling.build"

[project]
name = "@NAME@"
dynamic = ["version"]
# The description names the protocol and the documents, because that is the only text
# a person searching PyPI will read before deciding this is the library they need.
description = "@DESC@"
readme = "README.md"
license = "Apache-2.0"
requires-python = "@GE@3.12"
authors = [{ name = "Justin Capella" }]
keywords = @KW@
classifiers = [
  "Development Status :: 2 - Pre-Alpha",
  "Framework :: AsyncIO",
  "Intended Audience :: Developers",
  "License :: OSI Approved :: Apache Software License",
  "Operating System :: OS Independent",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3 :: Only",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Programming Language :: Python :: 3.14",
  "Topic :: System :: Networking",
  "Typing :: Typed",
]
# Empty unless a module cannot be honest without it. Every entry needs a sentence in
# README.md explaining why the zero-dependency rule was broken for it.
dependencies = @DEPS@

[project.optional-dependencies]
dev = [
  "pytest",
  "pytest-asyncio",
  "pytest-cov",
  "hypothesis",
  "ruff",
  "mypy",
  # tools/check_dist.py cannot assert a wheel contains what the docs promise without
  # first being able to build one, so the build dependency belongs in the dev group.
  "build",
]
@EXTRAS@
[project.urls]
Homepage = "https://github.com/@ORG@/@NAME@"
Repository = "https://github.com/@ORG@/@NAME@"
Issues = "https://github.com/@ORG@/@NAME@/issues"
Changelog = "https://github.com/@ORG@/@NAME@/blob/main/CHANGELOG.md"

@SCRIPTS@
[tool.hatch.version]
path = "@PKG@/__init__.py"

[tool.hatch.build.targets.wheel]
packages = ["@PKG@"]

[tool.hatch.build.targets.sdist]
include = ["/@PKG@/", "/tests/", "/docs/", "/README.md", "/ROADMAP.md", "/CHANGELOG.md", "/LICENSE", "/NOTICE"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
asyncio_mode = "auto"
addopts = "-ra"

[tool.coverage.run]
branch = true
source = ["@PKG@"]

[tool.coverage.report]
# The shell floor, deliberately low and deliberately ratcheted: a stub only earns
# coverage for the line that raises. Raise this in the commit that implements a
# milestone, never in a commit that only adds tests, so the number keeps meaning
# "this behaviour is exercised" rather than "this file was imported".
fail_under = 45
show_missing = true
exclude_lines = [
  "if __name__ == .__main__.:",
  "raise NotImplementedError",
  "pragma: no cover",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "N", "UP", "SIM", "C4", "PT", "RUF", "S", "RET", "ARG"]
ignore = [
  "E501",  # line length is the formatter's job
]
# S101 is not ignored here: `python -O` strips an assert, so one in package code is a check that
# vanishes in exactly the build that ships without the test suite watching.

[tool.ruff.lint.per-file-ignores]
# S101 in tests is the policy, S311 is a seeded generator rather than a token, and a
# test double has to accept parameters it ignores, which ARG would otherwise flag.
"tests/*" = ["S101", "S106", "S311", "ARG001", "ARG002"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.12"
warn_unused_ignores = true
warn_redundant_casts = true
no_implicit_optional = true
ignore_missing_imports = true
"""


def render_pyproject(p: dict[str, Any]) -> str:
    extras = ""
    for extra, pairs in p.get("extras", {}).items():
        extras += extra + " = [" + reqs(pairs) + "]\n"
    scripts = ""
    if p.get("cli"):
        scripts = '[project.scripts]\n@NAME@ = "@PKG@.cli:main"\n\n'.replace(
            "@NAME@", p["name"]
        ).replace("@PKG@", p["pkg"])
    deps = p.get("deps", [])
    return subs(
        PYPROJECT,
        {
            "NAME": p["name"],
            "PKG": p["pkg"],
            "DESC": p["desc"],
            "KW": toml_list(p["kw"]),
            "DEPS": "[" + reqs(deps) + "]" if deps else "[]",
            "EXTRAS": extras,
            "SCRIPTS": scripts,
            "ORG": ORG,
            "GE": GE,
        },
    )


README = """
# @NAME@

<!-- PyPI badges go live with the first release. Until then a version badge renders
     "no version" on the landing page, which reads as a defect rather than a date, so
     the badges stay commented out until the tag exists.
-->

**Pre-alpha shell.** The package surface, the obligation matrix and the CI gates exist.
No protocol behaviour is implemented: every public function raises `NotImplementedError`,
and the test suite records that rather than hiding it. Read
[ROADMAP.md](ROADMAP.md) before depending on any of it.

@TITLE@

@WHY@

## The gap, with a date

PyPI was queried on @SURVEY_DATE@ for this name and for every neighbouring project. Landscape
claims rot quickly: two projects in this portfolio shipped within five days of the survey,
which is why the table below carries dates instead of adjectives.

@PRIOR@

What this project intends to add:

@GAPS@

## Specification spine

Status and publication dates below were read from the RFC Editor record on @SURVEY_DATE@. A
section number is cited only where that section was read in full; everything else cites the
document, which is the honest grain size for a plan. See [docs/rfc-map.md](docs/rfc-map.md).

@DOCS@

### Errata watch

@ERRATA@

## Module layout

@MODULES@

## What it does not do

@NONGOALS@

@DEPSNOTE@
## Install

```bash
git clone https://github.com/@ORG@/@NAME@.git
cd @NAME@
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\\\Scripts\\\\Activate.ps1
pip install -e \".[dev]\"
```

Not published to PyPI yet. The name is reserved for this repository's trusted publisher at
the moment of the first release.

## Development

All four gates must pass, on Linux and Windows alike:

```bash
ruff check . && ruff format --check .
mypy @PKG@ tests
pytest -q --cov
python -O -m pytest -q
```

The last line is the one that matters most in a protocol library. `-O` strips `assert`, and a
parser whose bounds checks were asserts stops being a parser under production flags. Wire
paths raise named exceptions; asserts belong in tests and nowhere else.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
"""

ROADMAP = """
# Specification completeness -- and the roadmap it implies

This is the project's north star: every protocol obligation @NAME@ could plausibly take on,
with an honest status against the code at `HEAD`. It is blunt about gaps on purpose. A
roadmap that overstates its coverage is not a plan, it is marketing.

**The honesty rule:** a row may read `[ok]` only when a named test would fail if the
behaviour regressed. Reading the code and agreeing with it is not enough. That is how
"decoded but never sent" quietly becomes "implemented".

Legend: `[ok]` implemented and pinned by a named test -- `[part]` partial, with what is
missing stated -- `[ ]` not implemented -- `[no]` deliberate non-goal.

A Where cell marked `(to be written)` names the test that will pin the row once it exists; no
such test has been written yet, and the row cannot leave `[ ]` until it has. The Milestone
column places the row on the ladder in section 2, and `-` means it is not placed yet.

Update the affected row in the same commit that changes the behaviour. A row and its test
drift apart within one release, and the document becomes useless exactly when it is needed.

## 1. Obligations

@ROWS@

## 2. Milestones

@STEPS@

Every milestone is accepted by its rows above flipping to `[ok]` with a named test, not by
the code existing. Coverage in `pyproject.toml` ratchets in the same commit.

## 3. Errata watch

@ERRATA@

## 4. Deliberate non-goals

@NONGOALSROWS@

## 5. Open questions

@OPEN@

## 6. Publication checklist

- [ ] every `[ ]` row above either `[ok]` or moved to a named later milestone
- [ ] `docs/research.md` claims re-verified and the dated ones either promoted or deleted
- [ ] interoperability evidence recorded in `docs/` against a non-Python implementation
- [ ] `tools/check_dist.py` proves the wheel ships the fixtures the README promises
- [ ] version 0.1.0 tagged, trusted publisher configured, README badges uncommented
"""


def modules_table(p: dict[str, Any]) -> str:
    rows = [("`" + m + "`", d) for m, d in p["mods"]]
    return table(("Module", "Responsibility"), rows)


UNPLACED = "-"  # the Milestone cell of a row nobody has placed on the ladder; never a guessed M-number
TO_BE_WRITTEN = "(to be written)"


def api_milestone(entry: tuple[str, ...]) -> str:
    """The milestone an api entry is placed on: its optional seventh field, or UNPLACED."""
    return entry[6] if len(entry) > 6 and entry[6] else UNPLACED


def where_cell(fn: str) -> str:
    """The test that will pin a row, named so pytest collects it, and marked as not yet written.

    The cell used to read `tests/test_obligations.py::<fn>`: a file no project has, and a name without
    the test_ prefix, so pytest would not have collected it even had the file existed. Every generated
    row cited it as if it were evidence.
    """
    return "`tests/test_obligations.py::test_" + fn + "` " + TO_BE_WRITTEN


def obligation_rows(p: dict[str, Any]) -> str:
    rows = []
    for entry in p["api"]:
        mod, _kind, fn, _params, _ret, cite = entry[:6]
        rows.append(
            (
                "`" + mod + "." + fn + "()`",
                cite,
                "`[ ]`",
                where_cell(fn),
                api_milestone(entry),
            )
        )
    return table(("Obligation", "Spec", "Status", "Where", "Milestone"), rows)


def nongoal_rows(p: dict[str, Any]) -> str:
    return table(
        ("Non-goal", "Status", "Revisit when"),
        [(g, "`[no]`", "a named document requires it") for g in p["nongoals"]],
    )


def steps_block(p: dict[str, Any]) -> str:
    return "\n".join("- " + s for s in p["steps"])


# ------------------------------------------------------------------ supporting project files

CONTRIBUTING = """
# Contributing

Short version: four gates, and they all have to pass on Linux and Windows.

```bash
ruff check . && ruff format --check .
mypy @PKG@ tests
pytest -q --cov
python -O -m pytest -q
```

## The three rules that cause the most arguments

1. **`assert` belongs in tests and nowhere on a wire path.** The fourth gate strips
   asserts, and a parser whose bounds checks were asserts stops being a parser in
   production. On a parse path, raise a named exception.
2. **A behaviour change updates its ROADMAP row in the same commit.** The row flips to
   `[ok]` only with a named test. A row and a test drift apart within one release, and the
   matrix becomes useless exactly when someone needs it.
3. **Cite the document, not a blog post.** A section number is allowed only if that section
   was read. `docs/rfc-map.md` records what was verified and when.

## Layout

| Tier | Home | Rule |
|---|---|---|
| Core | `@PKG@/` | specification behaviour only; dependency-free unless README says why not |
| Apps | `@PKG@/apps/` | composes core pieces, adds no new protocol surface |
| Examples | `examples/` | one runnable script per use case; never restates library logic |

A pattern graduates from `examples/` to `apps/` on the second user, stated rather than
assumed.

## Tests

Protocol tests are vectors first: bytes in, structure out, with the source of the vector
cited. `hypothesis` is welcome for codecs, and every fuzz-found crash becomes a fixed
regression test rather than only a property.

## Milestones

Pick a row from the matrix, not a feature from your imagination. If the row you want is
missing, add it to the matrix first and argue for it there.
"""

SECURITY = """
# Security policy

## What this project treats as a security property

- **Hostile input is the normal case.** Every decoder has to refuse truncation, over-long
  lengths, nested structures and contradictory fields without raising anything a caller
  cannot name.
- **Bounds before cleverness.** Size, count and depth limits exist on every parse path, and
  they are configuration rather than suggestions.
- **No silent crypto policy.** Weak or deprecated algorithms are refused by an explicit
  policy object rather than accepted because they happen to parse.
- **Provenance is part of the answer.** A result states what was verified, what was cached
  and what was skipped, so a caller cannot mistake an unchecked field for a checked one.

## assert is not a safety mechanism

`python -O` strips asserts. Any invariant that protects a parser boundary or a verification
result must be expressed as a raised exception. CI runs the suite under `-O` for exactly
this reason.

## Reporting

Open a private security advisory on the repository rather than a public issue. The policy is
the Apache Software Foundation's: security support for the main branch and the most recent
release. Because this repository is a pre-alpha shell, a report will often be answered with
"that code does not exist yet", which is a different thing from "it will not be fixed".
"""

CHANGELOG = """
# Changelog

The format follows Keep a Changelog, and this project adheres to Semantic Versioning.

## Unreleased

Nothing yet. This repository is a shell: the public surface exists as typed stubs that raise
`NotImplementedError`, and [ROADMAP.md](ROADMAP.md) lists what has to happen first.

## 0.0.0 - @SURVEY_DATE@

### Added

- Package skeleton, obligation matrix and CI gates for @NAME@.
- `@PKG@.apps` namespace reserved for composition that is not protocol behaviour.
"""

NOTICE = """
@NAME@
Copyright 2026 Justin Capella

This product includes software developed as part of an RFC-anchored protocol SDK portfolio.
Specification text referenced by this project is the work of its authors and is cited by
document number; no normative text is redistributed here beyond short identifiers.
"""

GITIGNORE = """
__pycache__/
*.py[cod]
*.egg-info/
.venv/
build/
dist/
.coverage
htmlcov/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.hypothesis/
*.log
requirements-audit.txt

# Working notes quote other projects' trackers and age faster than a tracked document
# should. Evidence that survives gets promoted into README.md or ROADMAP.md with a date;
# the scratch file itself never gets committed.
docs/research.md
"""

GITATTRIBUTES = """
* text=auto eol=lf
*.py text
*.md text
*.toml text
*.yml text
*.png binary
*.pcap binary
*.der binary
"""

CI = """
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python -m @PIPIN@ -e ".[dev]"
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy @PKG@ tests
      # check_dist inspects a built wheel, so the wheel is built here rather than assumed to exist.
      # The step looked green for months on one machine because an earlier manual build had left a
      # wheel in dist/: a cache impersonating a check, and the first CI run this project ever had
      # failed it immediately.
      - run: python -m @PIPIN@ build
      - run: python -m build --wheel --quiet
      - run: python tools/check_dist.py

  test:
    # A socket option that exists only on Linux is exactly the bug a Linux-only CI never
    # sees, and multicast, UDP send buffers and interface enumeration differ per platform.
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.12", "3.13", "3.14"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m @PIPIN@ -e ".[dev]"
      - run: python -m pytest -q --cov
      # Asserts stripped: a wire path guarded by assert is unguarded in production.
      - run: python -O -m pytest -q
"""

SECURITY_WF = """
name: security

on:
  push:
    branches: [main]
  schedule:
    - cron: "17 4 * * 2"

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python -m @PIPIN@ -e ".[dev]" pip-audit
      # The resolved environment is audited rather than the declarations, because the
      # declarations are floors. A file is written by a script rather than shell
      # redirection so the step behaves identically on a self-hosted runner.
      - run: python tools/write_freeze.py requirements-audit.txt
      # The audit reads the versions already installed above and installs nothing itself:
      # --no-deps and --disable-pip stop pip-audit from resolving the list in a fresh
      # environment, which is how it used to end up cloning a repository instead of auditing.
      - run: python -m pip_audit --progress-spinner off --no-deps --disable-pip -r requirements-audit.txt
"""

STRATEGY = """
# Strategy

The completeness matrix is [../ROADMAP.md](../ROADMAP.md). This document is *why* the rows
are ordered the way they are, and the rules that order has to respect.

## The constitution

1. **The core is the specification implementation.** `@PKG@/` holds codecs, state machines
   and the vocabulary of @NAME@. New core code appears when a document defines the
   behaviour, or when an ordering rule with no document earns an explicit convention label.
2. **Sans-IO before sockets.** Anything decidable from data plus a clock is implemented
   without touching a socket, so every timeout, retry and rollover is testable without
   sleeping or networking.
3. **Dependency-free by default.** An exception needs a sentence in README.md explaining why
   the rule was broken, and a floor with no ceiling.
4. **Positioning is correctness and legibility, not throughput.** The incumbents in this
   corner are usually stale, untyped, or welded to a product. This is the implementation that
   says how it knows what it claims.
5. **Every answer carries its provenance.** Verified or cached, which document, refused or
   believed. An answer without provenance is a guess with a confident type.

## The three tiers

| Tier | Home | Contents | Rule |
|---|---|---|---|
| Core | `@PKG@/` | codecs, state machines, policy objects | RFC-cited or convention-labelled |
| Apps | `@PKG@/apps/` | reusable assemblies that compose core | no new protocol surface |
| Examples | `examples/` | one runnable script per use case | configures; never restates logic |

## Interface grammar

One engine, three drives, never parallel implementations:

- **await** -- the native asyncio surface.
- **blocking** -- a facade owning a private loop, so a call from inside a caller's running
  loop works instead of deadlocking.
- **callback** -- plain callables, no third-party promise type.

## Naming

One string everywhere: the distribution, the import and the console script. A name that
means one thing in the installer, in `import` and on `PATH` is the only arrangement that
cannot surprise anyone, and it is also why this project does not sit one edit away from a
project that already exists.
"""

EXAMPLES = """
# examples

One runnable script per use case. An example configures the library; it never restates
library logic, and the prose in the docs quotes these files rather than duplicating them.

Nothing here runs yet: the package surface is stubs that raise `NotImplementedError`. Each
milestone in [../ROADMAP.md](../ROADMAP.md) adds the example that proves it works.
"""
# ---------------------------------------------------------------- supporting file rendering

BB = chr(10)  # line break, spelled out so templates stay readable
CB = chr(96)  # backtick, same reason

PIPIN = "pip" + " install"
ARROW = "-" + GT  # rendered into generated source so this file never contains the token

CHECK_DIST = """
#!/usr/bin/env python3
\"\"\"Assert the built distribution ships what the documentation promises.

Run after a wheel build. A docs claim about a packaged fixture is a promise to the reader,
and a promise nothing checks gets broken silently.
\"\"\"

from __future__ import annotations

import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = "@PKG@"
REQUIRED = ("README.md", "ROADMAP.md", "LICENSE", "NOTICE", "SECURITY.md")


def main():
    wheels = sorted((ROOT / "dist").glob("*.whl"))
    if not wheels:
        print("no wheel in dist/: run python -m build first")
        return 1
    with zipfile.ZipFile(wheels[-1]) as wheel:
        names = set(wheel.namelist())
    problems = []
    if PKG + "/py.typed" not in names:
        problems.append(PKG + "/py.typed missing from the wheel")
    problems += [f + " missing from the repository root" for f in REQUIRED if not (ROOT / f).exists()]
    for item in problems:
        print("problem:", item)
    print(f"{wheels[-1].name}: {len(names)} entries, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
"""

WRITE_FREEZE = """
#!/usr/bin/env python3
\"\"\"Write the resolved environment to a requirements file for auditing.

A script rather than shell redirection so the CI step behaves the same on a self-hosted
runner, and so an empty freeze fails loudly instead of auditing nothing.
\"\"\"

from __future__ import annotations

import pathlib
import subprocess
import sys


def main():
    if len(sys.argv) != 2:
        print("usage: write_freeze.py PATH")
        return 2
    frozen = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # Drop the project's own entry. pip freeze records the local tree as an editable or VCS reference,
    # and pip-audit then installs that URL to audit it: on a private repository the anonymous clone
    # fails, and the audit reports a build error instead of advisories. A project is not one of its own
    # dependencies, and only third-party distributions carry advisories worth resolving.
    lines = []
    dropped = 0
    for line in frozen.splitlines():
        if not line.strip():
            continue
        if line.startswith("-e") or "git+" in line or "file://" in line:
            dropped += 1
            continue
        lines.append(line)
    pathlib.Path(sys.argv[1]).write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    print(f"{len(lines)} distributions written to {sys.argv[1]} ({dropped} self-reference(s) excluded)")
    return 0 if lines else 1


if __name__ == "__main__":
    sys.exit(main())
"""


def render_rfc_map(p):
    rows = [
        (d.get("label") or ("RFC " + d["n"]), d["status"], d["date"], SURVEY_DATE)
        for d in p["docs"]
    ]
    return (
        "# Where every claim in this repository comes from"
        + BB
        + BB
        + "| Document | Status | Published | Verified against the publisher record on |"
        + BB
        + "|---|---|---|---|"
        + BB
        + chr(10).join(
            "| " + r[0] + " | " + r[1] + " | " + r[2] + " | " + r[3] + " |"
            for r in rows
        )
        + BB
        + BB
        + "Citation rules, in the order they most often get broken:"
        + BB
        + BB
        + "1. Cite the document. A secondary write-up is a lead, not a source."
        + BB
        + "2. A section number is cited only if that section was read in full."
        + BB
        + "3. Errata are checked at each milestone freeze, not once at the start."
        + BB
        + "4. Where a claim came from a body outside the RFC series, that body is named here."
        + BB
        + BB
        + "Re-verify with the RFC library rather than memory: "
        + CB
        + "info('RFCnnnn')"
        + CB
        + " for current status, "
        + CB
        + "errata('RFCnnnn')"
        + CB
        + " for corrections, and "
        + CB
        + "read('RFCnnnn', view='normative')"
        + CB
        + " for the MUST and SHOULD statements that"
        " become obligation rows." + BB
    )


def render_research(p):
    rows = [
        (a["name"], a.get("version", "-"), a.get("date", "-"), a["note"])
        for a in p["prior"]
    ]
    return (
        "# Research notes (deliberately not committed)"
        + BB
        + BB
        + "This file is gitignored on purpose. It quotes other projects' release records and"
        " trackers, which age faster than a tracked document should. Anything that survives"
        " gets promoted into README.md or ROADMAP.md with its date, and the citation rules in"
        " docs/rfc-map.md apply before any of it reaches code."
        + BB
        + BB
        + "## PyPI landscape, collected "
        + SURVEY_DATE
        + BB
        + BB
        + "Method: "
        + CB
        + "pypi.org/simple/{name}/"
        + CB
        + " for existence and "
        + CB
        + "pypi.org/pypi/{name}/json"
        + CB
        + " for version, summary and last release date."
        " Both signals matter, and neither alone is sufficient:"
        + BB
        + BB
        + "- A name can be registered with no files at all. That reads as an error from the"
        "  JSON API and as a success from the index. It is a squat, and it is just as"
        "  unavailable as a released project. "
        + CB
        + "tr069"
        + CB
        + " is one such name."
        + BB
        + "- A delisted project disappears from search while its name stays reserved, so"
        "  absence from search is not an invitation."
        + BB
        + BB
        + table(("Project", "Version", "Last release", "Finding"), rows)
        + BB
        + BB
        + "## Names rejected as confusable"
        + BB
        + BB
        + "PyPI's squatting policy can block a name one edit away from a live project, and an"
        " admin transfer later costs more than picking a distinct name on day one. The obvious"
        " names in this space were checked and, where they sat near a live project, passed"
        " over in favour of something self-describing."
        + BB
        + BB
        + "## Follow up"
        + BB
        + BB
        + "- Re-run the survey before the first release. The field moved twice during the"
        "  original pass, with two neighbouring projects shipping within five days of it."
        + BB
        + "- Confirm the scope of any project noted as unverified above before repeating the"
        "  claim that a corner of PyPI is empty." + BB
    )


def render_extras(p):
    base = {
        "NAME": p["name"],
        "PKG": p["pkg"],
        "SURVEY_DATE": SURVEY_DATE,
        "PIPIN": PIPIN,
        "ARROW": ARROW,
    }
    files = {
        "CONTRIBUTING.md": subs(CONTRIBUTING, base),
        "SECURITY.md": subs(SECURITY, base),
        "CHANGELOG.md": subs(CHANGELOG, base),
        "NOTICE": subs(NOTICE, base),
        ".gitignore": subs(GITIGNORE, base),
        ".gitattributes": subs(GITATTRIBUTES, base),
        ".github/workflows/ci.yml": subs(CI, base),
        ".github/workflows/security.yml": subs(SECURITY_WF, base),
        "docs/strategy.md": subs(STRATEGY, base),
        "docs/rfc-map.md": render_rfc_map(p),
        "docs/research.md": render_research(p),
        "examples/README.md": subs(EXAMPLES, base),
        "tools/check_dist.py": subs(CHECK_DIST, base),
        "tools/write_freeze.py": subs(WRITE_FREEZE, base),
    }
    return files


# ---------------------------------------------------------------------- package rendering

# The annotation vocabulary a generated module may name, split by where the name comes
# from. Which module owns each alias is ruff's call, not ours: UP035 rejects the typing
# spelling of the ABC aliases, so they are imported from collections.abc.
BUILTIN_NAMES = {
    "bool",
    "bytearray",
    "bytes",
    "complex",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "None",
    "True",
    "False",
    "object",
    "property",
    "set",
    "slice",
    "str",
    "super",
    "tuple",
    "type",
}
ABC_NAMES = {
    "AsyncGenerator",
    "AsyncIterable",
    "AsyncIterator",
    "Awaitable",
    "ByteString",
    "Callable",
    "Coroutine",
    "ItemsView",
    "Iterable",
    "Iterator",
    "KeysView",
    "Mapping",
    "MutableMapping",
    "MutableSequence",
    "MutableSet",
    "Reversible",
    "Sequence",
    "ValuesView",
}
TYPING_NAMES = {"Any", "IO", "Optional", "Union"}
DATETIME_NAMES = {"date", "datetime", "timedelta", "timezone", "tzinfo"}
# A name used as an attribute path (ssl.SSLContext) is a module, so it is imported at
# runtime rather than named inside the TYPE_CHECKING block.
MODULE_NAMES = {
    "base64",
    "datetime",
    "decimal",
    "hashlib",
    "hmac",
    "http",
    "ipaddress",
    "pathlib",
    "secrets",
    "socket",
    "ssl",
    "struct",
    "urllib",
}
PLACEHOLDER = "# placeholder: give it a real type in the milestone that needs it"

DOTTED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*[A-Za-z_][A-Za-z0-9_]*")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PARENTHETICAL = re.compile(r"\s*\([^()]*\)")
BARE_DATETIME = re.compile(r"(?<![\w.])datetime(?!\s*\.)")
VALUE = r"[A-Za-z_][A-Za-z0-9_.]*(?:\[[^][]*\])?"
GENERIC_RULES = (
    (re.compile(r"\bdict of (" + VALUE + r") to (" + VALUE + r")"), r"dict[\1, \2]"),
    (re.compile(r"\btuple of (" + VALUE + r") and (" + VALUE + r")"), r"tuple[\1, \2]"),
    (re.compile(r"\bset of (" + VALUE + r")"), r"set[\1]"),
    (re.compile(r"\blist of (" + VALUE + r")"), r"list[\1]"),
)
# A context hint is written by a person, so it may be prose, and prose never survives
# sanitising: a parenthetical tail is dropped, the first ';' or '.' or dash ends it, and
# anything that still does not parse becomes Any.
PROSE_CUTS = (";", " - ", ". ")

MODULE_BODY = (
    "Every object in this module is a placeholder until the milestone named in ROADMAP.md "
    "lands. A stub raises instead of returning a plausible empty value: a protocol library "
    "that silently answers nothing is worse than one that refuses to answer."
)
FUNC_TAIL = (
    "Not implemented yet. See the ``{pair}()`` row in ROADMAP.md; this stub raises on "
    "purpose so nothing downstream mistakes it for working code."
)

APPS_DOC = (
    "Composition that assembles core pieces and adds no protocol behaviour.",
    'Importing from here says "I am assembling", which is the distinction the project '
    "cares about. Nothing in this package may know how the wire format works: if it does, "
    "it belongs one level up, cited against a document.",
)
CLI_DOC = (
    "Command line entry point for {name}.",
    "The CLI is how most people meet a protocol library, so it is part of the public "
    "surface and gets the same typing and testing discipline as the rest of the package.",
)
MAIN_DOC = "Allow python -m {name} without installing the console script."
CONFTEST_DOC = (
    "Make the project root importable when pytest is run from anywhere.",
    "The package lives at the repository root, so with this file present pytest inserts "
    "that root on sys.path and importing the package works however the test command was "
    "invoked.",
)
STUB_MAIN = """def main(argv=None):
{DOC}
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_usage(sys.stderr)
    print("{name}: no protocol behaviour is implemented yet; see ROADMAP.md", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())"""


def _member_key(name: str) -> tuple[int, str]:
    """Order imported members the way isort does: constants, classes, then lowercase."""
    if name.isupper():
        return (0, name)
    if name[:1].isupper():
        return (1, name)
    return (2, name)


def import_block(module: str, members: set[str], indent: str, width: int) -> list[str]:
    """Render one from-import, exploded only when the single line would overflow."""
    names = sorted(members, key=_member_key)
    one = indent + "from " + module + " import " + ", ".join(names)
    if len(one) <= width:
        return [one]
    lines = [indent + "from " + module + " import ("]
    lines += [indent + "    " + name + "," for name in names]
    lines.append(indent + ")")
    return lines


def _wellformed(annotation: str) -> bool:
    """True when the annotation parses and every name in it can be resolved or aliased."""
    try:
        tree = ast.parse(annotation, mode="eval")
    except SyntaxError:
        return False
    known = BUILTIN_NAMES | ABC_NAMES | TYPING_NAMES | DATETIME_NAMES | MODULE_NAMES
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name):
            continue
        if node.id in known or re.fullmatch(r"[A-Z][A-Za-z0-9]*", node.id):
            continue
        return False
    return True


def _optional(annotation: str) -> str:
    """Rewrite Optional[X] as X | None, the only spelling the lint set accepts."""
    marker = "Optional["
    while marker in annotation:
        start = annotation.find(marker)
        depth = 0
        index = start + len(marker) - 1
        while index < len(annotation):
            if annotation[index] == "[":
                depth += 1
            elif annotation[index] == "]":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if index >= len(annotation):
            return ""
        inner = annotation[start + len(marker) : index]
        inner = _optional(inner)
        if "," in inner:
            inner = "(" + inner + ") | None"
        else:
            inner = inner + " | None"
        annotation = annotation[:start] + inner + annotation[index + 1 :]
    return annotation


def sanitize_annotation(text: str) -> str:
    """Return a context hint as a usable annotation, or Any when it never was one."""
    body = PARENTHETICAL.sub(" ", collapse(text))
    cut = len(body)
    for separator in PROSE_CUTS:
        found = body.find(separator)
        if found >= 0:
            cut = min(cut, found)
    body = body[:cut].strip()
    for pattern, replacement in GENERIC_RULES:
        body = pattern.sub(replacement, body)
    body = " ".join(_optional(body).split()).strip(",;")
    if not body:
        return "Any"
    if re.search(r"\b(or|and|is|not|in|for)\b", body):
        return "Any"  # a hint written as an English clause, not as a type expression
    return body if _wellformed(body) else "Any"


def split_params(params: str) -> list[str]:
    """Split a parameter list on top-level commas so subscripts stay whole."""
    parts, depth, current = [], 0, ""
    for char in params:
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        if char == "," and depth <= 0:
            parts.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def sanitize_params(params: str) -> tuple[str, list[str]]:
    """Sanitise a parameter list and return it beside the hints it now carries.

    Parameter names are deliberately not returned as hints: only the annotation after each
    colon is a type, and treating a name as a type is how a placeholder alias for 'files'
    would get invented.
    """
    parts, hints = [], []
    for chunk in split_params(params):
        if ":" in chunk:
            name, _, hint = chunk.partition(":")
            hint = sanitize_annotation(hint)
            hints.append(hint)
            chunk = name.strip() + ": " + hint
        parts.append(chunk)
    return ", ".join(parts), hints


def plan_imports(types: list[str]) -> tuple[list[str], list, list, bool]:
    """Resolve every name the module's final annotations use.

    Word boundaries, not bracket substrings, decide what counts as a name, so a type used
    inside ``list[X]`` is found and a word scraped out of prose is never invented into an
    alias. Names that come from typing, collections.abc or datetime are imported inside the
    TYPE_CHECKING block; a name that is really a module is imported at runtime.
    """
    roots: set[str] = set()
    bare: set[str] = set()
    for text in types:
        roots.update(match.group(1) for match in DOTTED.finditer(text))
        bare.update(WORD.findall(DOTTED.sub("\u0000", text)))
    runtime = {root for root in roots if root in MODULE_NAMES}
    module_datetime = "datetime" in roots and "datetime" in bare
    if module_datetime:
        bare.discard(
            "datetime"
        )  # the module form wins, so the class is qualified below
    checking: dict[str, set[str]] = {}
    aliases: set[str] = set()
    for name in bare - roots - BUILTIN_NAMES:
        if name in MODULE_NAMES and (name != "datetime" or module_datetime):
            runtime.add(name)  # a module named bare in an annotation is imported anyway
        elif name in DATETIME_NAMES:
            checking.setdefault("datetime", set()).add(name)
        elif name in ABC_NAMES:
            checking.setdefault("collections.abc", set()).add(name)
        elif name in TYPING_NAMES:
            checking.setdefault("typing", set()).add(name)
        else:
            aliases.add(name)
    if aliases:
        checking.setdefault("typing", set()).add("Any")
    ordered = [(module, checking[module]) for module in sorted(checking)]
    return sorted(runtime), ordered, sorted(aliases), module_datetime


def module_source(p: dict[str, Any], mod: str, doc: str) -> str:
    """Render one obligation module: stubs, their annotations and the imports they need."""
    entries = [e for e in p["api"] if e[0] == mod]
    names = sorted({e[2] for e in entries})
    bodies, types = [], []
    for _mod, kind, fn, params, ret, cite in (e[:6] for e in entries):
        clean_params, param_types = sanitize_params(params)
        clean_ret = sanitize_annotation(ret)
        types += param_types + [clean_ret]
        bodies.append((kind, fn, clean_params, clean_ret, cite))
    runtime, checking, aliases, module_datetime = plan_imports(types)

    def final(text: str) -> str:
        if not text or not module_datetime:
            return text
        return BARE_DATETIME.sub("datetime.datetime", text)

    lines = [docstring(doc, MODULE_BODY), "", "from __future__ import annotations"]
    if runtime:
        lines += [""] + ["import " + name for name in runtime]
    if checking:
        lines += ["", "from typing import TYPE_CHECKING", "", "if TYPE_CHECKING:"]
        for module, members in checking:
            lines += import_block(module, members, "    ", 96)
        if aliases:
            lines.append("")
            lines += ["    " + alias + " = Any  " + PLACEHOLDER for alias in aliases]
    lines.append("")
    if names:
        lines.append("__all__ = [")
        lines += ['    "' + name + '",' for name in names]
        lines.append("]")
    else:
        lines.append("__all__: list[str] = []")
    for kind, fn, params, ret, cite in bodies:
        lines += [
            "",
            "",
            kind
            + " "
            + fn
            + "("
            + final(params)
            + ") "
            + ARROW
            + " "
            + final(ret)
            + ":",
        ]
        lines.append(
            docstring(
                "Pending behaviour defined by " + cite + ".",
                FUNC_TAIL.replace("{pair}", mod + "." + fn),
                "    ",
            )
        )
        lines.append(
            '    raise NotImplementedError("'
            + fn
            + "() is not implemented: "
            + cite
            + '")'
        )
    return "\n".join(lines) + "\n"


def init_source(p: dict[str, Any]) -> str:
    """Render the package __init__: sorted module list, sorted surface, sorted __all__."""
    mods = sorted({m for m, _d in p["mods"]})
    submodules = sorted(set(["apps"] + mods + (["cli"] if p.get("cli") else [])))
    lines = [
        docstring(p["title"], first_sentence(p["why"])[0]),
        "",
        "from __future__ import annotations",
        "",
        "from . import " + ", ".join(submodules),
    ]
    exposed: list[str] = []
    for mod in mods:
        surface = sorted(
            {e[2] for e in p["api"] if e[0] == mod and e[2] not in submodules}
        )
        surface = [name for name in surface if name not in exposed]
        if not surface:
            continue
        exposed += surface
        lines += import_block("." + mod, set(surface), "", 100)
    lines += ["", '__version__ = "0.1.0"', "", "__all__ = ["]
    for name in sorted(set(submodules + exposed) | {"__version__"}):
        lines.append('    "' + name + '",')
    lines.append("]")
    return "\n".join(lines) + "\n"


def cli_source(p: dict[str, Any]) -> str:
    """Render the CLI.

    The version is never imported at module scope: the package __init__ assigns it after
    it has imported this module, so a top-level import of it deadlocks the package import.
    """
    summary, body = CLI_DOC[0].replace("{name}", p["name"]), CLI_DOC[1]
    return (
        "\n".join(
            [
                docstring(summary, body),
                "",
                "from __future__ import annotations",
                "",
                "import argparse",
                "import sys",
                "",
                'USAGE = """\\',
                "Examples:",
                "  " + p["cli"] + '"""',
                "",
                "",
                "def build_parser():",
                "    return argparse.ArgumentParser(",
                '        prog="' + p["name"] + '",',
                '        description="' + collapse(p["desc"]) + '",',
                "        epilog=USAGE,",
                "        formatter_class=argparse.RawDescriptionHelpFormatter,",
                "    )",
                "",
                "",
                STUB_MAIN.replace("{DOC}", _main_doc()).replace("{name}", p["name"]),
            ]
        )
        + "\n"
    )


def _main_doc() -> str:
    """The CLI main() docstring: why a shell exits non-zero."""
    return docstring(
        "Parse arguments, then refuse to pretend the protocol works.",
        "Exiting non-zero is the correct behaviour for a shell. A CLI that printed a "
        "friendly banner and exited 0 would be indistinguishable from one that actually "
        "did something.",
        "    ",
    )


def render_module(p: dict[str, Any], mod: str, doc: str) -> str:
    """Keep the old call signature; the renderer is now module_source."""
    return module_source(p, mod, doc)


def render_init(p: dict[str, Any]) -> str:
    """Keep the old call signature; the renderer is now init_source."""
    return init_source(p)


def render_cli(p: dict[str, Any]) -> str:
    """Keep the old call signature; the renderer is now cli_source."""
    return cli_source(p)


def render_package(p: dict[str, Any]) -> dict[str, str]:
    """Render every managed Python file of one project."""
    pkg = p["pkg"]
    files = {}
    for mod, doc in p["mods"]:
        files[pkg + "/" + mod + ".py"] = module_source(p, mod, doc)
    files[pkg + "/__init__.py"] = init_source(p)
    files[pkg + "/apps/__init__.py"] = docstring(APPS_DOC[0], APPS_DOC[1]) + "\n"
    files[pkg + "/py.typed"] = ""
    if p.get("cli"):
        files[pkg + "/cli.py"] = cli_source(p)
        files[pkg + "/__main__.py"] = (
            docstring(MAIN_DOC.replace("{name}", p["name"])) + "\n"
        )
    files["conftest.py"] = docstring(CONFTEST_DOC[0], CONFTEST_DOC[1]) + "\n"
    return files


TPL_DIR = HERE / "templates"
DQ = chr(34)
H2 = chr(35) + chr(35) + " "


def deps_note(p):
    if not p.get("deps"):
        return ""
    note = p["deps_note"]
    head = H2 + "Why this project has a runtime dependency"
    tail = (
        "Declared as a floor with no ceiling: the application that installs this "
        "library resolves the final set, and a ceiling inside a library guarantees a "
        "conflict the first time something else needs a newer line."
    )
    return BB + BB + head + BB + BB + note + BB + BB + tail


def readme_mapping(p):
    pairs = [
        ("NAME", p["name"]),
        ("PKG", p["pkg"]),
        ("ORG", ORG),
        ("TITLE", p["title"]),
        ("DESC", p["desc"]),
        ("WHY", p["why"]),
        ("SURVEY_DATE", SURVEY_DATE),
        ("PRIOR", prior_rows(p["prior"])),
        ("GAPS", bullets(p["gaps"])),
        ("DOCS", spine_rows(p["docs"])),
        ("ERRATA", bullets(p["errata"])),
        ("MODULES", modules_table(p)),
        ("NONGOALS", bullets(p["nongoals"])),
        ("DEPSNOTE", deps_note(p)),
    ]
    return dict(pairs)


def render_readme(p):
    return subs(README, readme_mapping(p))


def roadmap_mapping(p):
    fallback = ["none recorded yet"]
    pairs = [
        ("NAME", p["name"]),
        ("ROWS", obligation_rows(p)),
        ("STEPS", steps_block(p)),
        ("ERRATA", bullets(p["errata"])),
        ("NONGOALSROWS", nongoal_rows(p)),
        ("OPEN", bullets(OPEN_QUESTIONS.get(p["name"], fallback))),
    ]
    return dict(pairs)


def render_roadmap(p):
    return subs(ROADMAP, roadmap_mapping(p))


def render_tests(p):
    rows = []
    for entry in p["api"]:
        rows.append(
            "    ("
            + DQ
            + entry[0]
            + DQ
            + ", "
            + DQ
            + entry[2]
            + DQ
            + ", "
            + DQ
            + entry[5]
            + DQ
            + "),"
        )
    surface = "[" + BB + BB.join(rows) + BB + "]"
    files = {}
    for name in ("test_public_api.py", "test_roadmap_rows.py"):
        text = (TPL_DIR / (name + ".tpl")).read_text(encoding="utf-8")
        files["tests/" + name] = subs(text, {"PKG": p["pkg"], "SURFACE": surface})
    return files


def normalize(p):
    for key in ("why", "desc", "title"):
        value = p.get(key)
        if isinstance(value, (list, tuple)):
            p[key] = " ".join(value)
    return p


HEADERS = ("Project", "Protocol", "Primary documents", "Nearest live project")


def index_rows(projects):
    rows = []
    for project in projects:
        docs = []
        for spec in project["docs"][:4]:
            docs.append(spec.get("label") or ("RFC " + spec["n"]))
        nearest = project["prior"][0]["name"]
        summary = project["desc"].split(":")[0]
        rows.append((project["name"], summary, ", ".join(docs), nearest))
    return table(HEADERS, rows)


def render_index(projects):
    """The workspace index, protocol-sdks.md, as the text the pipeline writes."""
    tpl = TPL_DIR / "index.md.tpl"
    text = tpl.read_text(encoding="utf-8")
    text = text.replace("TABLE", index_rows(projects))
    return text.replace("SURVEY_DATE", SURVEY_DATE)


# ---------------------------------------------------------------------------
# Part 19: agent instruction files (AGENTS.md + .clinerules + a Cline workflow)
#
# Design notes, grounded in the specs rather than in habit:
#
#   * AGENTS.md (agents.md, now stewarded by the Agentic AI Foundation under the
#     Linux Foundation) is plain Markdown with no required fields. Nested files
#     are resolved by nearest-file precedence, and an explicit prompt in chat
#     overrides all of it. That makes a workspace-level file plus one per project
#     the right shape for a directory of sibling repositories. The workspace file,
#     AGENTS.md at the workspace root, is maintained by hand and is not written from here: the
#     generator once replaced its 4436 characters with a 2517-character template
#     on every run, which is the kind of loss the ownership guard below exists to stop.
#   * Cline reads AGENTS.md, ~/.agents/AGENTS.md, .clinerules/ and .cline/rules/,
#     combining the files found in a rules directory. Conditional rules take a
#     `paths:` glob in YAML front matter and activate when a matching file is in
#     context, so the rfclib procedure can load exactly when protocol code is
#     being edited rather than permanently occupying the context window.
#   * Both agent-guidance sources say the same thing about length: an
#     instruction file should hold only what the agent cannot infer from the
#     repository, and it should give the agent a check it can run. Everything
#     below is therefore commands, invariants and errata pointers; module lists,
#     dependencies and design prose stay in README.md and docs/.
# ---------------------------------------------------------------------------


AGENTS_PROJECT = """# Instructions for coding agents

<<SUMMARY>>

Primary documents are listed in `docs/rfc-map.md` with statuses and known errata.
The obligation table in `ROADMAP.md` records what is owed, and `README.md` explains
what the library is for. Read those three before changing anything.

## Finding work

If the person gave you a task, do that task.

If you arrived with nothing to do, run this from this directory:

    projecthub pickup --who <your name> --project <<NAME>>

Its output is your assignment: one row, already claimed for you, with what to build, the commands that prove
it and the ways to end it. The board decides the order of work, so the row it gives you is the row to work,
even if it is in another project. `NO WORK` is a real answer: ask the person what to work on.

Without the workspace beside this repository, read `.projecthub/packet.md` if it exists. If it does not, ask what
to work on.

## Commands

    python -m pip install -e ".[dev]"
    python -m pytest -q
    python -m pytest -q tests/test_roadmap_rows.py
    python -m pytest -q tests/test_public_api.py::test_stubs_are_loud
    python -m ruff check .
    python -m ruff format --check .
    python -m mypy <<PKG>> tests

The first line installs the dev extras. The six commands after it are the gates,
and all six must pass before a change is finished. CI runs the same set on Linux
and Windows, plus the suite under `python -O`.

## Invariants

These are the points where a plausible-looking change makes the project wrong
rather than merely incomplete.

1. **Roadmap honesty.** Never change a `ROADMAP.md` row from `[ ]` to `[ok]` unless
   the test named in its `Where` cell exists, passes, and would fail if the behaviour
   regressed. A Where cell marked `(to be written)` names a test nobody has written:
   write it, then drop the marker in the same commit that flips the row.
   `tests/test_roadmap_rows.py` checks only that every row names a callable the
   package defines; it cannot tell a passing test from a missing one, so this rule
   is the check. Do not weaken that test, skip it, or delete it to get a green run.
2. **No silent stubs.** Code that is not implemented raises `NotImplementedError`.
   Never return an empty value, an empty collection, or a result object that reports
   success. A protocol library that answers nothing is worse than one that refuses
   to answer, and `tests/test_public_api.py` enforces that every stub is loud.
3. **Normative text is never paraphrased from memory.** Fetch it with `rfclib` and
   cite the section. If the text you fetch does not say what the obligation claims,
   the obligation is wrong: fix the roadmap row and say so in the commit.
4. **Dependencies.** `pyproject.toml` here declares <<DEPS>>. Adding a runtime
   dependency needs a paragraph of justification in `docs/strategy.md` explaining
   why the standard library is not enough.
5. **Scope.** What is deliberately absent is listed under Non-goals in `README.md`.
   An agent that "helpfully" implements a non-goal has expanded the API surface that
   has to be supported forever.

## Read before implementing

<<ERRATA>>

## Protocol work

`.clinerules/10-rfclib.md` describes the `rfclib` calls to make for each obligation
and lists the section refs for this project. If you are not running in Cline, the
same procedure applies: confirm the document, read its errata, pull the normative
statements, then write code.
"""


CLINERULE = """---
description: Fetch the specification text before changing protocol code
paths:
  - "<<PKG>>/**"
  - "tests/**"
---

# Working from the RFC with rfclib

The `rfclib` MCP server is the authority for what a specification says. Model
knowledge of RFC numbers and wording is not reliable here, and this portfolio has
already had to correct numbers that looked right (DHCPv6 is RFC 9915, not 8415;
RRDP is RFC 8182, not 9183).

<<SPINENOTE>>

Work through the obligations in `ROADMAP.md` one row at a time. For each row:

1. **Confirm the document.** `rfclib.info("<<REF>>")` returns status,
   publication date, the updates/obsoletes graph and an errata summary. If the
   document is obsolete or has a successor, stop and flag the roadmap row before
   writing any code.
2. **Read the errata.** `rfclib.errata("<<REF>>")` gives the verified
   corrections with their replacement text. A verified erratum is the
   specification: an implementation that follows the original text is wrong.
   RFC 4795 carries six, RFC 9162 one, RFC 6960 one.
3. **Pull only the normative statements.** `rfclib.read("<<REF>><<SEC0>>",
   view="normative")` returns the MUST, SHOULD and MAY sentences in that scope.
   Read them before choosing a data structure, not after.
4. **Find the specific rule instead of guessing its name.**
   `rfclib.grep("<<REF>>", pattern="<<FIELD>>")` searches one document for a
   field name, ABNF rule or error code, which is cheaper than reading a section.
5. **Cite rather than paraphrase.** `rfclib.cite("<<REF>><<SEC0>>",
   style="verbose")` yields label, title, date, status, anchor URL and errata
   status. Put that in the docstring or the commit message. Never restate normative
   prose in your own words.

Refs accept a bare document (`RFC9162`), a section (append the section symbol and
number), a paragraph within a section, a line range like `RFC9162:120-140`, and a
table like `RFC9162#table-2`. Reads end with `next: <ref>` when the scope exceeds the
budget, so page through rather than guessing. Paragraph markers are printed on each
paragraph so a single one can be quoted exactly.

To decide *which* document applies, `rfclib.search("...", scope="documents")`
matches titles and abstracts while `scope="sections"` says which passage answers the
question. `rfclib.outline("<<REF>>", contains="security")` lists sections
with their line ranges.

## Section refs for this project

<<DOCS>>

## Traps that have already bitten this portfolio

- A status of `INFORMATIONAL` means the document contains no protocol requirements,
  however normative its prose reads. Check before quoting a MUST.
- Numbers move: 6486 became 9286, 8941 became 9651, 4210 became 9810, 7752 became
  9552. Re-read `docs/rfc-map.md` rather than trusting an older note.
- An empty response is a protocol outcome, not a success value. Distinguish "the
  server said no" from "nothing arrived", or you reproduce the RFC 9849 compression
  failure, where a silent empty value made an optional extension load-bearing.
"""


WORKFLOW = """---
description: Implement one ROADMAP.md obligation, correctly and verifiably
---

Implement the next unchecked row of `ROADMAP.md` in this project, working from
`docs/rfc-map.md` for the document and section.

1. Read the row and quote its spec anchor and evidence cells back. Confirm the
   document with `rfclib.info` and `rfclib.errata` before writing code, and stop if
   it turns out to be obsolete or superseded.
2. Fetch the normative text with `rfclib.read(..., view="normative")`. If what you
   fetch does not state what the row claims, say so and fix the row rather than
   coding to a wrong requirement.
3. Write the test first, using recorded fixtures rather than a live endpoint. Run it
   and watch it fail.
4. Implement the minimum that makes it pass. Anything left unimplemented keeps
   raising `NotImplementedError`.
5. Flip the row to done and name the test in its evidence cell. Do not flip the row
   if the test does not exist and pass.
6. Run every gate listed in `AGENTS.md` and fix what fails.
7. Commit with the citation from `rfclib.cite` in the message body. No
   `Co-Authored-By` trailer.
"""


def sub(text, mapping):
    for key, value in mapping.items():
        text = text.replace("<<" + key + ">>", str(value))
    return text


def doc_line(entry):
    number = entry.get("n") or entry.get("rfc") or "?"
    label = entry.get("label") or ("RFC " + str(number))
    bits = [label]
    status = entry.get("status") or ""
    published = entry.get("date") or entry.get("published") or ""
    paren = ", ".join(part for part in (status, published) if part)
    if paren:
        bits.append("(" + paren + ")")
    role = entry.get("role") or ""
    if role:
        bits.append(role)
    return "- " + " ".join(bits)


def docs_block(p):
    lines = []
    for entry in p.get("docs", []):
        number = str(entry.get("n") or entry.get("rfc") or "?")
        lines.append(doc_line(entry))
        quote = chr(34)
        sect = chr(167)
        for section in (entry.get("sections") or [])[:2]:
            lines.append(
                "  - `rfclib.read("
                + quote
                + "RFC"
                + number
                + sect
                + str(section)
                + quote
                + ", view="
                + quote
                + "normative"
                + quote
                + ")`"
            )
        if entry.get("errata"):
            lines.append(
                "  - `rfclib.errata(" + quote + "RFC" + number + quote + ")` first"
            )
    return chr(10).join(lines) if lines else "- see docs/rfc-map.md"


def api_hint(item):
    """Identifier text of one api row, whichever way the row is spelled.

    Rows in the PROJECTS table are tuples of
    (module, kind, name, params, returns, refs); the name and params cells are
    the ones that hold protocol field names. A dict row carries the same text
    under "hint".
    """
    if isinstance(item, dict):
        return str(item.get("hint") or "")
    if isinstance(item, (tuple, list)):
        return " ".join(str(cell) for cell in item[2:4])
    return str(item)


def dep_label(dep):
    """Render one deps entry, which the PROJECTS table spells as (name, version)."""
    if isinstance(dep, (tuple, list)):
        return " ".join(str(part) for part in dep if part)
    return str(dep)


def doc_number(entry):
    """RFC number of one docs entry, or "" when the entry is a non-IETF document.

    Non-RFC entries in the PROJECTS table carry a "label" ("TR-069 (CWMP)",
    "LGC and LGS protocol") and no number, which is how the spine of a
    deliberately non-IETF project is recorded.
    """
    return str(entry.get("n") or entry.get("rfc") or "")


def spine_facts(p):
    docs = p.get("docs", [])
    first = docs[0] if docs else {}
    # The ref used inside the rfclib call examples has to be one rfclib can
    # resolve, so it is the first numbered entry of the map rather than the first
    # entry: on a non-IETF project the primary document is a Broadband Forum,
    # UPnP or ATIS specification, and rfclib has no such document.
    spine = next((entry for entry in docs if doc_number(entry)), None)
    spinenote = ""
    if spine is None:
        # Nothing in the map is an RFC. Fall back to the note below and keep the
        # label as the ref; never invent a number for a document that has none.
        ref = str(first.get("label") or "docs/rfc-map.md")
        section = ""
    else:
        ref = "RFC" + doc_number(spine)
        sections = spine.get("sections") or []
        section = chr(167) + str(sections[0]) if sections else ""
    if not doc_number(first):
        spinenote = (
            "The primary document of this project is "
            + str(first.get("label") or "docs/rfc-map.md")
            + ", which is not an RFC, so the `rfclib` calls below resolve only the RFC\n"
            'refs listed under "Section refs for this project"; read the non-RFC\n'
            "specification from its own publisher."
        )
    field = "extensions"
    for item in p.get("api", []):
        for token in re.split(r"[^A-Za-z]+", api_hint(item).replace("()", " ")):
            if token.isalpha() and token.islower() and len(token) > 4:
                field = token
                break
        else:
            continue
        break
    return {"REF": ref, "SEC0": section, "FIELD": field, "SPINENOTE": spinenote}


def errata_block(p):
    notes = [n for n in p.get("errata", []) if n]
    if not notes:
        notes = [
            "Run `rfclib.errata` on every document listed in `docs/rfc-map.md` "
            "before implementing its rows."
        ]
    return chr(10).join("- " + n for n in notes)


def agents_files(p):
    pkg = p["pkg"]
    desc = p.get("desc") or p.get("title") or p["name"]
    deps = p.get("deps") or []
    if deps:
        deps_text = "these runtime dependencies: " + ", ".join(
            dep_label(d) for d in deps
        )
    else:
        deps_text = "no runtime dependencies"
    project = sub(
        AGENTS_PROJECT,
        {
            "NAME": p["name"],
            "SUMMARY": desc.strip().rstrip(".") + ".",
            "PKG": pkg,
            "DEPS": deps_text,
            "ERRATA": errata_block(p),
        },
    )
    rule = sub(CLINERULE, spine_facts(p))
    rule = sub(rule, {"PKG": pkg, "DOCS": docs_block(p)})
    rule = re.sub(r"\n{3,}", chr(10) + chr(10), rule)
    return {
        "AGENTS.md": project,
        ".clinerules/10-rfclib.md": rule,
        ".cline/workflows/implement-obligation.md": WORKFLOW,
    }


# ---------------------------------------------------------------------------
# Part 20: portfolio position (docs/plan.md)
#
# PORTFOLIO.md at the workspace root decides which projects are worked on, in
# what order, and against what. ROADMAP.md inside a project decides protocol
# obligations. Nothing inside a generated repository carried the first of
# those, so an agent reading one project alone could not tell that its wave is
# 4, that two other projects are allowed to be in flight before it, or that
# the codec it plans to verify signatures with has no code anywhere.
#
# What this part writes is a transcription of PORTFOLIO.md sections 4, 6 and 7
# - tier, wave, and the arrows that mean "cannot deliver behaviour without" -
# rather than a reinterpretation of them. Section 10 of that file records the
# ASN.1 role having been mis-assigned to asnkit, with the correction that
# until asn1core exists no certificate-shaped project can ship behaviour. Every
# plan that cites asn1core repeats that sentence, because a caveat kept in one
# file nobody opens is a caveat that gets planned around.
# ---------------------------------------------------------------------------

# How to state each dependency's current existence, keyed by what deps.py finds on the disk. This used to
# be a static table, and its asn1core entry ("does not exist yet") outlived the fact: asn1core gained a
# package, a test suite and history while nine plans kept asserting the opposite, and nothing re-read
# the claim. The state is now read at generation time, so a plan can only say what the workspace shows.
# What the workspace cannot show is whether a dependency provides the capability a row needs, so the
# "code" text says exactly that and stops.
DEP_TEXT = {
    "absent": (
        "does not exist yet",
        "There is no @DEP@ directory in the workspace.",
    ),
    "plan only": (
        "exists only as a plan, with no code yet",
        "There is no @DEP@ code or test suite: the workspace holds only its extraction plan "
        "at ../../@DEP@/PLAN.md.",
    ),
    "shell": (
        "exists only as a generated shell, with no protocol behaviour yet",
        "Its public functions raise NotImplementedError until its own roadmap rows are "
        "done, so a row here that consumes it cannot be closed on the strength of a "
        "directory existing.",
    ),
    "code": (
        "exists as code",
        "It has source files and a test suite of its own, and its own README states its "
        "release status. That it exists does not say it provides what a row here needs: "
        "which capability a row consumes is decided row by row.",
    ),
}

# Hand-written statements about a dependency that replace the generic text for one state. namespy is
# software outside the generator, and that fact is recorded rather than derivable from the disk.
DEP_OVERRIDE = {
    ("namespy", "code"): (
        "exists",
        "namespy is implemented software outside the generator - a wire codec, an "
        "iterative resolver, DNSSEC, DoT and DoH legs - and it is the one place in this "
        "portfolio where the pattern has been proven end to end.",
    ),
}


def dep_state(dep):
    """What the workspace shows for a dependency: absent, plan only, shell or code."""
    from projecthub import (
        deps,
    )  # lazy: the generator should not need the planning modules to import

    return deps.dependency_state(ROOT, dep).state


INTRO = (
    "This file is generated by `projecthub shells generate` from the portfolio plan. Change the "
    "plan, not this file: a hand edit here is never overwritten, but it stops the "
    "generator from updating the file at all. Two documents govern this project and "
    "they govern different things: the obligation table in "
    "[../ROADMAP.md](../ROADMAP.md) governs protocol work - which rows exist, and what "
    "evidence closes one - and this file governs sequencing, which is which wave this "
    "project sits in, what has to exist before it can ship behaviour, and what must not "
    "be started while it is unfinished. "
    "[PORTFOLIO.md](../../PORTFOLIO.md) at the workspace root is the authority on both, "
    "so a change to a tier, a wave or an arrow is made there and regenerated here, never "
    "the other way round."
)

NO_DEPS_NOTE = (
    "None. The portfolio plan puts no other project between this one and a first "
    "release: every obligation in [../ROADMAP.md](../ROADMAP.md) is this repository's "
    "own, and nothing "
    "outside it has to land first. That cuts both ways - a missing dependency is never "
    "the reason this project has not shipped, and its wave position is the only thing "
    "holding it back."
)

BLOCKED_NOTE = (
    "**Blocked, not in progress.** asn1core has no code and no test suite, so @NAME@ cannot "
    "ship behaviour until it does. A roadmap row that parses, encodes or verifies a DER "
    "structure is not deferred work here: it is gated on a project that has nothing to "
    "build on. Any plan, milestone or release note that cites asn1core says blocked "
    "rather than in progress, and no release of @NAME@ is promised before asn1core has one "
    "of its own."
)

GATED_NOTE = (
    "**Gated by coverage, not by absence.** asn1core now has code and a test suite, so "
    "@NAME@ is no longer waiting for the project to exist. It is still waiting for the "
    "structures it needs: a roadmap row that parses, encodes or verifies a DER structure "
    "cites the asn1core capability it consumes, and stays deferred until that capability "
    "has a passing test there. No release of @NAME@ is promised before asn1core has one of "
    "its own."
)

SHELL_NOTE = (
    "A dependency that exists only as a shell is the same gate in weaker form: @DEPS@ "
    "@AGREE@ a directory, CI and an obligation table, and no behaviour, so work here can "
    "be designed against such a dependency and cannot be finished against it."
)

POINTER = (
    "Protocol facts come from docs/rfc-map.md and the RFC Editor record, never from "
    "memory. This file positions the work; it is not a source for anything a row cites."
)
# A registered project that is not a generated shell (a tool, a library) has no rfc-map to point at.
REGISTERED_POINTER = (
    "This file positions the work: its tier, its wave and what blocks it."
)
DESIGN_POINTER = " What the project is, its status, its open questions and its risks are in [design.md](design.md)."


def para(text, width=DOC_WIDTH):
    """Wrap one prose paragraph to the generator's document width."""
    return "\n".join(wrap(collapse(text), width))


def bullet(text, width=DOC_WIDTH):
    """Wrap one prose paragraph as a hyphen bullet with a hanging indent."""
    return "\n".join(wrap(collapse(text), width, "  ", "- "))


def dep_line(name, dep):
    """One dependency bullet: the edge, why it is hard, and whether it exists."""
    why = DEPENDS_WHY.get(
        (name, dep), "the portfolio plan records this as a hard dependency"
    )
    state = dep_state(dep)
    label, detail = DEP_OVERRIDE.get((dep, state)) or DEP_TEXT[state]
    detail = subs(detail, {"DEP": dep})
    text = "**" + dep + "** - " + why + ". **Status: " + label + ".** " + detail
    if (name, dep) in CANDIDATE_EDGES:
        text += " " + CANDIDATE_NOTE
    return bullet(text)


def position_of(name):
    """(tier, wave, depends) for a generated shell from POSITION, and for any other registered project from
    the registry, so a project that is not a shell (kconfigpy, a tool) still gets its docs/plan.md."""
    if name in POSITION:
        return POSITION[name]
    from projecthub import registry

    for entry in registry.entries():
        if entry.name == name:
            return entry.tier, entry.wave, tuple(entry.depends)
    raise KeyError(name + ": neither a generated shell nor a registered project")


def position_block(name):
    tier, wave, _deps = position_of(name)
    return para(
        "**Tier "
        + str(tier)
        + ", wave "
        + str(wave)
        + "** - "
        + WAVE_NOTE[wave]
        + ". "
        + TIER_NOTE[tier]
    )


def depends_block(name):
    deps = position_of(name)[2]
    if not deps:
        return para(NO_DEPS_NOTE)
    blocks = ["\n".join(dep_line(name, dep) for dep in deps)]
    shells = sorted(dep for dep in deps if dep_state(dep) == "shell")
    if shells:
        blocks.append(
            para(
                subs(
                    SHELL_NOTE,
                    {
                        "DEPS": ", ".join(shells),
                        "AGREE": "has" if len(shells) == 1 else "have",
                    },
                )
            )
        )
    if "asn1core" in deps:
        note = GATED_NOTE if dep_state("asn1core") == "code" else BLOCKED_NOTE
        blocks.append(para(subs(note, {"NAME": name})))
    return "\n\n".join(blocks)


def shell_count():
    """How many generated projects are still shells, read from the workspace rather than written in.

    The rule below used to say "Twenty-eight generated READMEs" as a literal, and it stayed twenty-eight
    after ctspy stopped being a shell.
    """
    return sum(1 for name in PROJECT_ORDER if dep_state(name) == "shell")


def rules_block():
    count = shell_count()
    return "\n".join(bullet(subs(rule, {"SHELLS": count})) for rule in RULES)


def render_plan(p):
    """Render docs/plan.md: where this project sits, what gates it, what constrains it."""
    name = p["name"]
    return (
        "\n".join(
            [
                "# Portfolio position",
                "",
                para(INTRO),
                "",
                "## Position",
                "",
                position_block(name),
                "",
                "## Depends on",
                "",
                depends_block(name),
                "",
                "## Sequencing rules",
                "",
                rules_block(),
                "",
                para(p.get("pointer") or POINTER),
            ]
        ).strip()
        + "\n"
    )


# ---------------------------------------------------------------------------
# The pipeline: render, polish, decide who owns each file, then write or report
#
# One function renders every file a project gets, so there is one place that says what the generator
# writes. It replaces a chain in which build() was reassigned three times, each wrapper calling the one
# before it, so the write set had to be traced through the module to be known - and the first wrapper
# wrote D:\code\AGENTS.md as a side effect of building whichever project came first.
#
# Nothing reaches the disk without passing decide(), which applies D17 of docs/improvement-plan.md: the
# generator stops owning a file when the file differs from what the generator last wrote there, when
# the project's ROADMAP.md has a done row, or when a module has no NotImplementedError left. Measured
# before this existed, one unguarded run would have turned ctspy's sct.py from 8174 bytes of
# implementation into a 1398-byte stub and dropped its six [ok] rows. docs/plan.md is exempt from the
# done-row rule only: it holds no hand work, and the cost D17 names for a stricter rule is exactly that
# implemented projects would stop getting their position regenerated.
#
# What the generator last wrote is the manifest, shells/data/generated.json: a SHA-256 per project and
# path, recorded after every write. A file with no record was written before the manifest existed, so
# nothing says whether it was edited since. Such a file is kept, and reported, unless --adopt is given
# (the operator has checked it is untouched generator output) or it is a docs/plan.md that still
# carries its own "this file is generated" header.
# ---------------------------------------------------------------------------

# PROJECTHUB_MANIFEST moves the manifest.
MANIFEST = pathlib.Path(paths.env("MANIFEST") or HERE / "data" / "generated.json")
WORKSPACE = (
    "."  # the manifest key for files at the workspace root rather than in a project
)
INDEX = "protocol-sdks.md"
PLAN_MARKER = "This file is generated by `projecthub shells generate`"
# The header every plan written before the move carries. Read, never written: a plan that still says it is
# generated output is recognised as such until its next regeneration replaces the header.
PLAN_MARKERS = (PLAN_MARKER, "This file is generated by " + "sdk-forge/forge.py")
KINDS = (
    "plan",
    "roadmap",
    "readme",
    "pyproject",
    "package",
    "tests",
    "agents",
    "extras",
    "license",
    "index",
)
WRITES = ("create", "update")
# Files D17 made hand-owned by name, whatever the rules below would decide: these matrices are maintained
# by hand now, and a --adopt must not be able to hand them back to the generator by accident.
HAND_OWNED = {("pkix", "ROADMAP.md"), ("ctspy", "ROADMAP.md")}


def managed_bytes(text: str) -> bytes:
    """Managed text as ASCII prose with LF endings and one trailing newline."""
    body = textwrap.dedent(ascii_prose(text)).strip("\n") + "\n"
    return body.replace("\r\n", "\n").encode()


def agent_bytes(text: str) -> bytes:
    """An agent instruction file: dedented, stripped, one trailing newline."""
    return (textwrap.dedent(text).strip() + "\n").encode("utf-8")


def plan_bytes(text: str) -> bytes:
    """docs/plan.md as ASCII text with LF endings."""
    body = ascii_prose(text).replace("\r\n", "\n")
    if not body.endswith("\n"):
        body += "\n"
    return body.encode("utf-8")


def kind_of(rel: str, p: dict[str, Any]) -> str:
    """Which --only kind a generated path belongs to."""
    named = {
        "docs/plan.md": "plan",
        "ROADMAP.md": "roadmap",
        "README.md": "readme",
        "pyproject.toml": "pyproject",
        "LICENSE": "license",
        "AGENTS.md": "agents",
        "conftest.py": "package",
    }
    if rel in named:
        return named[rel]
    if rel.startswith((".clinerules/", ".cline/")):
        return "agents"
    if rel.startswith("tests/"):
        return "tests"
    if rel.startswith(p["pkg"] + "/"):
        return "package"
    return "extras"


def render_plan_file(p: dict[str, Any]) -> dict[str, bytes]:
    """docs/plan.md alone. It reads the workspace, so the pipeline renders it after the other files."""
    p = normalize(p)
    if p["name"] not in POSITION:
        # Loud rather than skipped: a project with no row is a project whose wave nobody decided.
        raise KeyError(
            p["name"] + ": no POSITION row, so its tier and wave are undecided"
        )
    return {"docs/plan.md": plan_bytes(render_plan(p))}


def render_project(p: dict[str, Any], plan: bool = True) -> dict[str, bytes]:
    """Every file the generator owns in one project, as the bytes it would write."""
    p = normalize(p)
    if p["name"] not in POSITION:
        raise KeyError(
            p["name"] + ": no POSITION row, so its tier and wave are undecided"
        )
    texts = {
        "pyproject.toml": render_pyproject(p),
        "README.md": render_readme(p),
        "ROADMAP.md": render_roadmap(p),
    }
    texts.update(render_extras(p))
    texts.update(render_package(p))
    texts.update(render_tests(p))
    files = {rel: managed_bytes(text) for rel, text in texts.items()}
    files["LICENSE"] = LICENSE_SRC.read_bytes()
    for rel, text in agents_files(p).items():
        files[rel] = agent_bytes(text)
    if plan:
        files.update(render_plan_file(p))
    return files


def polish(name: str, files: dict[str, bytes]) -> tuple[dict[str, bytes], list[str]]:
    """Run the formatter and the import fixes over the generated Python, in a scratch copy.

    The formatter and the import/name fixes are mechanical, so the generator runs the tools a
    contributor would instead of reimplementing them. They run on a staged copy of the generated files
    only: the old polish ran `ruff format .` inside the project, which reformatted hand-written files
    the generator never owned, and a dry run could not have used it at all. A tool failure is reported
    rather than raised, because the verification run is the authority.
    """
    python = sorted(rel for rel in files if rel.endswith(".py"))
    if not python:
        return files, []
    notes = []
    out = dict(files)
    with tempfile.TemporaryDirectory(prefix="shells-") as tmp:
        stage = pathlib.Path(tmp) / name
        for rel in [*python, "pyproject.toml"]:
            if rel in files:
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                (stage / rel).write_bytes(files[rel])
        for cmd in (
            ["-m", "ruff", "format", "--no-cache"],
            [
                "-m",
                "ruff",
                "check",
                "--no-cache",
                "--fix",
                "--select",
                "I001,F401,RUF022",
            ],
        ):
            try:
                done = subprocess.run(
                    [sys.executable, *cmd, *python],
                    cwd=str(stage),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except (
                OSError
            ) as error:  # the tool is missing; verification will say so loudly
                notes.append(name + ": " + cmd[2] + " could not run: " + str(error))
                continue
            if done.returncode != 0:
                tail = (done.stdout or done.stderr or "").strip().splitlines()[-1:]
                notes.append(
                    name
                    + ": "
                    + " ".join(cmd[1:])
                    + " exited "
                    + str(done.returncode)
                    + (" :: " + tail[0] if tail else "")
                )
        for rel in python:
            out[rel] = (stage / rel).read_bytes()
    return out, notes


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest() -> dict[str, dict[str, str]]:
    if not MANIFEST.is_file():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, dict[str, str]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    MANIFEST.write_bytes(text.encode("utf-8"))


def done_rows(name: str) -> int:
    """How many rows of the project's ROADMAP.md read done, on the parsing contract deps.py uses."""
    from projecthub import deps  # lazy, as in dep_state

    return deps.dependency_state(ROOT, name).rows_done


@dataclasses.dataclass(frozen=True)
class Decision:
    """What the pipeline does with one generated file, and why."""

    project: str
    rel: str
    action: str  # create | update | unchanged | keep
    reason: str
    data: bytes
    current: bytes | None


def decide(
    project: str,
    root: pathlib.Path,
    files: dict[str, bytes],
    kinds: dict[str, str],
    recorded: dict[str, str],
    adopt: bool = False,
) -> list[Decision]:
    """Apply the D17 ownership rule to each generated file of one project. Reads only."""
    done = done_rows(project) if project != WORKSPACE else 0
    out = []
    for rel, data in files.items():
        path = root / rel
        kind = kinds[rel]
        current = path.read_bytes() if path.is_file() else None
        hand_owned_project = done and kind != "plan"
        if current == data:
            action, reason = "unchanged", ""
        elif (project, rel) in HAND_OWNED:
            action, reason = "keep", "hand-owned by decision D17"
        elif hand_owned_project:
            action = "keep"
            reason = (
                "ROADMAP.md has "
                + str(done)
                + " done row(s), so the project is hand-owned"
            )
        elif current is None:
            action, reason = "create", ""
        elif (
            kind == "package"
            and b"NotImplementedError" in data
            and b"NotImplementedError" not in current
        ):
            action, reason = (
                "keep",
                "no NotImplementedError left: the module is implemented",
            )
        elif rel in recorded:
            if sha(current) == recorded[rel]:
                action, reason = "update", ""
            else:
                action, reason = "keep", "edited since the generator last wrote it"
        elif kind == "plan" and any(
            marker in " ".join(current.decode("utf-8", "replace").split())
            for marker in PLAN_MARKERS
        ):
            action, reason = (
                "update",
                "unrecorded, but it carries the generated-file header",
            )
        elif adopt:
            action, reason = (
                "update",
                "unrecorded, adopted as generator output by --adopt",
            )
        else:
            action = "keep"
            reason = "unrecorded: nothing says it is unedited generator output (--adopt takes it over)"
        out.append(Decision(project, rel, action, reason, data, current))
    return out


def unified(decision: Decision) -> str:
    before = (
        (decision.current or b"").decode("utf-8", "replace").splitlines(keepends=True)
    )
    after = decision.data.decode("utf-8", "replace").splitlines(keepends=True)
    label = decision.project + "/" + decision.rel
    return "".join(difflib.unified_diff(before, after, "a/" + label, "b/" + label))


def apply(
    decisions: list[Decision], root: pathlib.Path, recorded: dict[str, str]
) -> int:
    """Write what decide() allowed and record it. Kept files are neither written nor recorded."""
    written = 0
    for decision in decisions:
        if decision.action in WRITES:
            path = root / decision.rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(decision.data)
            written += 1
        if decision.action in (*WRITES, "unchanged"):
            recorded[decision.rel] = sha(decision.data)
    return written


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="projecthub shells generate",
        description="Generate the protocol SDK shells. Files the generator no longer owns are kept.",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="NAME",
        help="build only this project (repeatable, or comma-separated); the index is then skipped",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="KIND",
        help="write only these kinds of file (repeatable, or comma-separated): "
        + ", ".join(KINDS),
    )
    parser.add_argument(
        "--check",
        "--dry-run",
        dest="check",
        action="store_true",
        help="write nothing; list what would change and exit 1 if anything would",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="with --check, also print a unified diff of each change",
    )
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="take over files with no manifest record, after checking they are unedited",
    )
    args = parser.parse_args(argv)
    args.project = [n for item in args.project for n in item.split(",") if n]
    args.only = [k for item in args.only for k in item.split(",") if k]
    unknown = sorted(set(args.only) - set(KINDS))
    if unknown:
        parser.error("unknown --only kind: " + ", ".join(unknown))
    names = {p["name"] for p in ALL_PROJECTS}
    missing = sorted(set(args.project) - names)
    if missing:
        parser.error("not a generated project: " + ", ".join(missing))
    if args.adopt and not args.project:
        # Adoption asserts that files nobody recorded are unedited, which someone has to have checked
        # project by project; a workspace-wide --adopt is that assertion made about everything at once.
        parser.error(
            "--adopt needs --project: check each project's files before adopting them"
        )
    return args


def plan_only(name: str, check: bool) -> int:
    """docs/plan.md for a registered project that is not a generated shell. --check prints the diff instead."""
    from projecthub import registry

    entry = next((e for e in registry.entries() if e.name == name), None)
    if entry is None:
        print("not a registered project: " + name, file=sys.stderr)
        return 2
    target = ROOT / entry.directory / "docs" / "plan.md"
    design = (ROOT / entry.directory / "docs" / "design.md").is_file()
    pointer = REGISTERED_POINTER + (DESIGN_POINTER if design else "")
    wanted = plan_bytes(render_plan({"name": name, "pointer": pointer}))
    current = target.read_bytes() if target.is_file() else b""
    if current.replace(b"\r\n", b"\n") == wanted:
        print(f"{name}: docs/plan.md is current")
        return 0
    if check:
        diff = difflib.unified_diff(
            current.decode("utf-8").splitlines(),
            wanted.decode("utf-8").splitlines(),
            "current/docs/plan.md",
            "generated/docs/plan.md",
            lineterm="",
        )
        print("\n".join(diff))
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(wanted)
    print(f"{name}: wrote {target}")
    return 0


def run(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--plan" in argv:
        at = argv.index("--plan")
        if at + 1 >= len(argv):
            print(
                "usage: projecthub shells generate --plan NAME [--check]",
                file=sys.stderr,
            )
            return 2
        return plan_only(argv[at + 1], "--check" in argv or "--dry-run" in argv)
    args = parse_args(argv)
    kinds = set(args.only or KINDS)
    chosen = [p for p in ALL_PROJECTS if not args.project or p["name"] in args.project]
    manifest = load_manifest()
    decisions: list[Decision] = []
    notes: list[str] = []
    written = 0

    def settle(
        name: str, root: pathlib.Path, files: dict[str, bytes], kind: dict[str, str]
    ) -> None:
        nonlocal written
        recorded = manifest.setdefault(name, {})
        found = decide(name, root, files, kind, recorded, adopt=args.adopt)
        decisions.extend(found)
        if not args.check:
            written += apply(found, root, recorded)

    # Two passes. docs/plan.md states what the workspace shows - which dependencies are shells, how
    # many shells there are - so it is rendered after the pass that writes the other files, and one
    # run converges instead of describing the workspace as it was before the run.
    if kinds - {"plan"}:
        for p in chosen:
            files = render_project(p, plan=False)
            kind = {rel: kind_of(rel, p) for rel in files}
            wanted = [rel for rel in files if kind[rel] in kinds]
            if any(rel.endswith(".py") for rel in wanted):
                files, polish_notes = polish(p["name"], files)
                notes += polish_notes
            settle(p["name"], ROOT / p["name"], {r: files[r] for r in wanted}, kind)
    if "plan" in kinds:
        for p in chosen:
            settle(
                p["name"],
                ROOT / p["name"],
                render_plan_file(p),
                {"docs/plan.md": "plan"},
            )
    if not args.project and "index" in kinds:
        settle(
            WORKSPACE,
            ROOT,
            {INDEX: managed_bytes(render_index(ALL_PROJECTS))},
            {INDEX: "index"},
        )
    manifest = {name: rec for name, rec in manifest.items() if rec}
    if not args.check:
        save_manifest(manifest)
    verb = "would " if args.check else ""
    for d in decisions:
        if d.action == "unchanged":
            continue
        shown = d.action if d.action == "keep" else verb + d.action
        print(
            shown
            + " "
            + d.project
            + "/"
            + d.rel
            + (" - " + d.reason if d.reason else "")
        )
        if args.check and args.diff and d.action in WRITES:
            print(unified(d), end="")
    changes = sum(1 for d in decisions if d.action in WRITES)
    kept = sum(1 for d in decisions if d.action == "keep")
    print(
        "projects "
        + str(len(chosen))
        + ", files "
        + str(len(decisions))
        + ", "
        + (verb + "change " if args.check else "written ")
        + str(changes if args.check else written)
        + ", kept "
        + str(kept)
    )
    for note in notes:
        print("polish:", note)
    return 1 if args.check and changes else 0


# Kept as the last statement, and test_templates.py checks it: run() must see every definition above.
if __name__ == "__main__":
    raise SystemExit(run())
