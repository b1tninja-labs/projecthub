"""The portfolio as data: every project, its kind, tier, wave and state, the waves' exit tests, and the open
decisions. Read from the files that already say so; nothing here is a second copy of a fact.

Sources, in order of authority:

- PORTFOLIO.md section 4 (tiers), section 7 (waves and their exit tests) and section 9 (decisions that need a
  human). Waves and decisions are parsed from the prose, so the board quotes the plan rather than
  paraphrasing it.
- projecthub.shells.data.position.POSITION for the generated shells' tier, wave and dependency arrows (itself
  a transcription of PORTFOLIO.md sections 4 and 6, checked by the shell generator's own tests).
- the disk, through deps.py, for whether a project is absent, a plan, a shell or code, and its obligation
  table (ROADMAP.md, or PLAN.md where a project plans there) for how many rows are done.
- each checkout's git remote for its GitHub repository, so a renamed repository (pyrwhois -> registrata) is
  read, not remembered.

The few projects outside POSITION are listed in EXTRA with the PORTFOLIO.md section they come from. Every one
of them is also a row of that document - sections 3 and 4 - which `mentioned_in` checks, because an entry
whose source nobody wrote down is a project the plan does not know about.
"""

from __future__ import annotations

from projecthub.data import POSITION

import pathlib
import re
import subprocess
from dataclasses import dataclass, field

KINDS = ("Library", "Service", "Plan", "Reference", "Tooling")


def directories() -> dict[str, str]:
    """Project name -> checkout folder, for the projects whose folder carries another name. The map lives in
    the roadmap grammar (`db.parser.DIRECTORIES`), which evidence, mutation and project_dir already read, so
    there is one copy of it."""
    from projecthub.db import parser

    return dict(parser.DIRECTORIES)


# Projects the generator does not position: (kind, tier, wave, directory, what it is, source), plus an
# optional seventh member, the names it depends on (PORTFOLIO.md section 6, "an arrow means cannot deliver
# behaviour without"), which `entries()` carries into `Entry.depends`, so `projecthub graph` and a generated
# docs/plan.md both see it. Tier and wave 0 mean untiered and unsequenced, which is not the same as Wave 0
# (hygiene). The "what" is PORTFOLIO.md's own wording where it has some.
EXTRA: dict[
    str,
    tuple[str, int, int, str, str, str]
    | tuple[str, int, int, str, str, str, tuple[str, ...]],
] = {
    "asn1core": (
        "Library",
        1,
        1,
        "asn1core",
        "DER and X.509 codec that keeps the bytes it parsed",
        "PORTFOLIO.md s4 Tier 1, s7 wave 1",
    ),
    "certledger": (
        "Service",
        1,
        1,
        "certledger",
        "independent CT acquisition and publication",
        "PORTFOLIO.md s4 Tier 1, s7 wave 1",
    ),
    "namespy": (
        "Library",
        1,
        4,
        "namespy",
        "DNS with an iterative resolver, DNSSEC, DoT and DoH",
        "PORTFOLIO.md s4 Tier 1, s7 wave 4",
    ),
    "keyproof": (
        "Plan",
        1,
        3,
        "keyproof",
        "a public register of demonstrated key compromise",
        "PORTFOLIO.md s4 Tier 1: 'merit says Tier 1; cost says wave 3'",
    ),
    "localns": (
        "Plan",
        5,
        4,
        "localns",
        "link-local naming: mDNS, LLMNR, NBNS",
        "PORTFOLIO.md s4 Tier 5, s7 wave 4",
    ),
    "trustatlas": (
        "Plan",
        0,
        0,
        "trustatlas",
        "the trust-anchor supply chain, mapped",
        "PORTFOLIO.md s5: deliberately unbuilt, after certledger's store",
    ),
    "registrata": (
        "Library",
        0,
        0,
        "pyrwhois",
        "registration data: an RFC 2167 RWhois client and server, plus experimental WHOIS and RDAP behind one lookup model",
        "PORTFOLIO.md s3 (implemented, not yet sequenced into a tier)",
    ),
    # Support libraries, created 2026-11-13 under PORTFOLIO.md s9 item 14 and s2's self-sufficiency principle:
    # untiered, unsequenced, outside the two-project rule. `sigprim` merged into `sigwire[verify]` (item 15).
    "archivekit": (
        "Library",
        0,
        0,
        "archivekit",
        "bounded, streaming, byte-faithful readers for tar, cpio, ar, zip and the compression formats under them",
        "PORTFOLIO.md s4 support libraries; s9 item 14",
    ),
    "sigwire": (
        "Library",
        0,
        0,
        "sigwire",
        "lightweight signature formats, with strict verification (`sigwire[verify]`) over `cryptography`",
        "PORTFOLIO.md s4 support libraries; s9 item 15 (sigprim merged in)",
        ("pgpenv",),
    ),
    "gitobj": (
        "Library",
        0,
        0,
        "gitobj",
        "git loose and pack objects, tree ids, SWHIDs and smart HTTP, reading only",
        "PORTFOLIO.md s4 support libraries; s9 items 14 and 15",
        ("archivekit",),
    ),
    "pkgformats": (
        "Library",
        0,
        0,
        "pkgformats",
        "package formats in strict layers: deb, rpm, pacman, apk, xbps, ipk, nix, Gentoo, with PKGBUILD reading",
        "PORTFOLIO.md s4 support libraries; s9 item 15 (owner of the PKGBUILD reader)",
        ("archivekit", "sigwire", "pgpenv"),
    ),
    "pkgtrust": (
        "Tooling",
        0,
        0,
        "pkgtrust",
        "distribution supply-chain measurement: how distributions fetch, verify and patch upstream source",
        "PORTFOLIO.md s3 and s4 measurement tooling",
        (
            "archivekit",
            "sigwire",
            "gitobj",
            "pkgformats",
            "pgpenv",
            "kconfigpy",
            "attestkit",
            "advisoryformats",
        ),
    ),
    # Support libraries scaffolded in round 4 (2026-11-13), laid out by hand like pkgformats: the shell generator
    # positions only the generated protocol shells, so these, like every support library, live here alone.
    "attestkit": (
        "Library",
        0,
        0,
        "attestkit",
        "SBOM and attestation documents: a neutral BOM model, CycloneDX 1.7 and SPDX 3.0.1 readers and writers, SPDX 2.3, in-toto Statements, SLSA provenance",
        "PORTFOLIO.md s9 item 15 (SBOMs and attestations); support library",
        # pkgformats.vocab for purl; sigwire's DSSE parse layer, and sigwire[verify] for the verify extra
        ("pkgformats", "sigwire"),
    ),
    "advisoryformats": (
        "Library",
        0,
        0,
        "advisoryformats",
        "vulnerability advisory formats (OSV, CVE record, Alpine secdb, Arch tracker, GLSA, CSAF, OpenVEX) with primary and secondary evidence labels",
        "PORTFOLIO.md s4 support libraries; s9 item 15",
        # pkgformats.vocab for purl and versions; devicespy.xmlsafe for GLSA
        ("pkgformats", "devicespy"),
    ),
    "distbuild": (
        "Tooling",
        0,
        0,
        "distbuild",
        "distribution build reproduction, registered untiered 2026-11-13",
        "PORTFOLIO.md s9 item 15 (registration)",
        ("pkgformats",),
    ),
    "primarysource": (
        "Reference",
        0,
        0,
        "primarysource",
        "one package: a core plus collections (rfc, itu, nist, usb, zim, cve, ...) behind extras",
        "reference library: PORTFOLIO.md s3 and s4, s9 items 13 and 15 (rfclib, itulib, nistlib, usblib and zimlore moved in and were archived 2026-11-13; cvelib moved in as `cve` and was never registered)",
    ),
    # sdk-forge until 2026-11-12, when it was retired into this repository (PORTFOLIO.md s9 item 12).
    "projecthub": (
        "Tooling",
        0,
        0,
        "projecthub",
        "the planning system, the shell generator, its gates and the git hook",
        "PORTFOLIO.md s3 'Tooling', s9 item 12",
    ),
    # Decided 2026-11-12: a tool for the router firmware build, not a library the portfolio depends on.
    "kconfigpy": (
        "Tooling",
        3,
        0,
        "kconfigpy",
        "explainable Kconfig analysis: a minimal kernel config from hardware, with a reason for every option",
        "decided 2026-11-12: tier 3, unsequenced, not active",
    ),
}


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    tier: int
    wave: int
    directory: str
    what: str
    source: str
    depends: tuple[str, ...] = ()


@dataclass(frozen=True)
class Wave:
    number: int
    title: str
    body: str
    exit: str


@dataclass(frozen=True)
class Decision:
    number: int
    title: str
    body: str
    decided: bool
    projects: tuple[str, ...] = ()


@dataclass(frozen=True)
class State:
    label: str  # "Implemented" | "Shell" | "Plan only" | "Absent"
    rows: int
    done: int
    matrix: str  # the file the counts came from, or ""
    repo: str  # "owner/name" on GitHub, or ""
    blurb: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def entries() -> list[Entry]:
    # Imported by name at call time: the generator is large, untyped, and only its position table is read.
    position = POSITION
    out = [
        Entry(
            name,
            "Library",
            tier,
            wave,
            name,
            "",
            "shells POSITION (PORTFOLIO.md s4, s6)",
            tuple(deps),
        )
        for name, (tier, wave, deps) in position.items()
    ]
    for name, spec in EXTRA.items():
        out.append(
            Entry(name, *spec[:6], depends=tuple(spec[6]) if len(spec) > 6 else ())
        )
    return sorted(out, key=lambda e: (e.tier or 9, e.wave or 9, e.name))


def waves(text: str) -> list[Wave]:
    """`**Wave N - title.** prose ... *Exit:* test.` from PORTFOLIO.md section 7."""
    section = _section(text, "7")
    found = []
    pattern = re.compile(
        r"^\*\*Wave (\d+) - (.+?)\.\*\*(.*?)(?=^\*\*Wave \d|^\*\*Hard rule|\Z)",
        re.M | re.S,
    )
    for match in pattern.finditer(section):
        body = match.group(3).strip()
        first = body.split("\n\n")[0]
        exit_test = first.split("*Exit:*", 1)[1].strip() if "*Exit:*" in first else ""
        found.append(
            Wave(
                int(match.group(1)),
                match.group(2).strip(),
                body,
                " ".join(exit_test.split()),
            )
        )
    return found


def decisions(text: str, names: set[str]) -> list[Decision]:
    """The numbered items of PORTFOLIO.md section 9. Only `**Decided` (capitalised, bold) marks one closed:
    item 9 records one decided sub-choice in lower case while its other choices are still open."""
    section = _section(text, "9")
    found = []
    pattern = re.compile(r"^(\d+)\. \*\*(.+?)\*\*(.*?)(?=^\d+\. \*\*|\Z)", re.M | re.S)
    for match in pattern.finditer(section):
        title = match.group(2).strip().rstrip(".")
        body = (match.group(2) + match.group(3)).strip()
        mentioned = tuple(n for n in re.findall(r"`([\w-]+)`", title) if n in names)
        found.append(
            Decision(
                int(match.group(1)),
                title,
                " ".join(body.split()),
                "**Decided" in body,
                mentioned,
            )
        )
    return found


def _section(text: str, number: str) -> str:
    match = re.search(rf"^## {number}\. .*?$(.*?)(?=^## \d+\. |\Z)", text, re.M | re.S)
    return match.group(1) if match else ""


def github_repo(root: pathlib.Path, directory: str) -> str:
    """`owner/name` from the checkout's own remote, or "" for a local-only project.

    A project directory nested in another repository (the workspace root) has no remote of its own and
    must not inherit the parent's, so the remote is read only when the directory is its own repository.
    """
    folder = root / directory
    remote = "origin"
    if not (folder / ".git").exists():
        return ""
    done = subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(folder), "remote", "get-url", remote],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    found = re.search(
        r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", done.stdout.strip()
    )
    return found.group(1) if found else ""


def state(root: pathlib.Path, entry: Entry) -> State:
    from projecthub import deps
    from projecthub.github import rowdoc
    from projecthub.github.sync import STATUSES, rows_of

    folder = root / entry.directory
    matrix = next(
        (
            f
            for f in ("ROADMAP.md", "PLAN.md")
            if (folder / f).is_file()
            and rows_of((folder / f).read_text(encoding="utf-8"))
        ),
        "",
    )
    rows = rows_of((folder / matrix).read_text(encoding="utf-8")) if matrix else []
    done = sum(1 for r in rows if STATUSES.get(r["status"], ("", "open"))[1] == "close")
    disk = deps.dependency_state(root, entry.directory).state
    if not folder.is_dir():
        label = "Absent"
    elif disk == "code" and (done or not rows):
        label = "Implemented"
    elif disk == "shell":
        label = "Shell"
    else:
        label = "Plan only"
    if entry.kind == "Tooling" and folder.is_dir():
        label = "Implemented"
    return State(
        label,
        len(rows),
        done,
        matrix,
        github_repo(root, entry.directory),
        rowdoc.project_blurb(root, entry.directory) or _readme_blurb(folder),
    )


def _readme_blurb(folder: pathlib.Path) -> str:
    for name in ("README.md", "PLAN.md"):
        path = folder / name
        if path.is_file():
            for block in path.read_text(encoding="utf-8").split("\n\n"):
                block = block.strip()
                if block and not block.startswith(("#", "|", "```", "!", "[!", "<")):
                    return " ".join(block.split())
    return ""
