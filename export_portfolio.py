"""portfolio.json: the portfolio as data, for the b1tninja.com site to render.

The site (OpenTofu `templatefile` over this file, in the private aws-infra repo) owns presentation; this file
owns facts. Two sources, both read and never written:

- the registry (portfolio.entries(): PORTFOLIO.md tiers and waves, forge.POSITION) plus each project's
  obligation matrix and dependency state on disk, for the planned and generated work;
- `forge_data/public_repos.json`, the hand-owned list of the other public repositories: which are shown,
  in which section, as what kind. Seeded by `--seed`; the person edits it. A repository absent from it is
  left out, so a new public repo never appears on the site until someone decides it should.

GitHub metadata (language, last push, fork parent, archived) comes from one `gh repo list` call.

    python export_portfolio.py --out D:/temp/portfolio.json     # outside every work tree
    python export_portfolio.py --seed                           # (re)write public_repos.json suggestions

Exit 2 when --out is inside the workspace or a git work tree, as verify.py --json refuses.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
OWNER = "b1tninja"
BOARD_URL = f"https://github.com/users/{OWNER}/projects/4"
CURATION = HERE / "forge_data" / "public_repos.json"
SCHEMA_VERSION = 1

SECTIONS = [
    {
        "id": "active",
        "title": "Active work",
        "blurb": "Implemented and in progress: code, named tests, and a ROADMAP whose done rows are each pinned by a test.",
    },
    {
        "id": "reference",
        "title": "Reference libraries: the context a task needs",
        "blurb": "The core idea: give an agent the source text instead of its memory of it. Each library serves a local corpus over MCP as precise, paged, token-budgeted extracts, every one with a citation naming its source and edition, and a missing or stale corpus is an error that names the fix, never an empty result. rfclib covers the RFC series, itulib the ITU-T ASN.1 and X.500 text the RFCs cannot reach, and zimlore language and framework documentation from Kiwix ZIM files, with the edition date and age of what it read.",
    },
    {
        "id": "planned",
        "title": "Planned SDKs",
        "blurb": "Generated shells and plans, by tier and wave. Each has an obligation matrix tied to the RFCs it implements.",
    },
    {
        "id": "tools",
        "title": "Tools and MCP servers",
        "blurb": "Launchers, infrastructure modules, and MCP servers that give agents a narrow, audited interface to a program.",
    },
    {
        "id": "re",
        "title": "Hardware and firmware",
        "blurb": "Reverse engineering and test doubles for printers, scanners and network gear.",
    },
    {
        "id": "other",
        "title": "Other projects",
        "blurb": "Libraries and applications outside the planned portfolio.",
    },
    {
        "id": "forks",
        "title": "Forks with work on top",
        "blurb": "Upstream projects carried with changes of our own.",
    },
    {
        "id": "archive",
        "title": "Earlier work",
        "blurb": "Older projects, kept for the record.",
    },
]
SECTION_IDS = [s["id"] for s in SECTIONS]


# ---------------------------------------------------------------------------------------------- GitHub


def github_repos(owner: str = OWNER) -> dict[str, dict[str, Any]]:
    """Public repositories by lower-cased name, from one `gh repo list` call."""
    done = subprocess.run(  # noqa: S603, S607
        [
            "gh",
            "repo",
            "list",
            owner,
            "--visibility",
            "public",
            "--limit",
            "500",
            "--json",
            "name,description,isFork,isArchived,pushedAt,primaryLanguage,parent,homepageUrl,url",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"error: gh repo list failed: {done.stderr.strip()}")
    return {r["name"].lower(): r for r in json.loads(done.stdout)}


def _meta(repo: dict[str, Any] | None) -> dict[str, Any]:
    if not repo:
        return {
            "repo_url": None,
            "homepage_url": None,
            "language": None,
            "last_push": None,
            "is_fork": False,
            "archived": False,
            "upstream": None,
        }
    parent = repo.get("parent") or {}
    upstream = (
        f"{parent['owner']['login']}/{parent['name']}" if parent.get("owner") else None
    )
    return {
        "repo_url": repo.get("url"),
        "homepage_url": repo.get("homepageUrl") or None,
        "language": (repo.get("primaryLanguage") or {}).get("name"),
        "last_push": repo.get("pushedAt"),
        "is_fork": bool(repo.get("isFork")),
        "archived": bool(repo.get("isArchived")),
        "upstream": upstream,
    }


# ---------------------------------------------------------------------------------------------- registry


KINDS = {
    "Library": "sdk",
    "Service": "service",
    "Reference": "reference",
    "Tooling": "tool",
    "Plan": "plan",
}


def milestones(rows: list[dict[str, str]]) -> tuple[int, int]:
    """(done, total) milestones: a milestone is done when every row in it that is not a non-goal is done."""
    by: dict[str, list[str]] = {}
    for row in rows:
        if row.get("milestone") and row.get("state") != "nongoal":
            by.setdefault(row["milestone"], []).append(row.get("state", ""))
    return sum(
        1 for states in by.values() if states and all(s == "done" for s in states)
    ), len(by)


def registry_entries(
    root: pathlib.Path, gh: dict[str, dict[str, Any]], active: set[str] | None
) -> list[dict[str, Any]]:
    import deps
    import portfolio
    import roadmap

    out = []
    for e in portfolio.entries():
        st = portfolio.state(root, e)
        path = roadmap.matrix_path(root, e.name)
        rows = roadmap.rows_of(path.read_text(encoding="utf-8")) if path else []
        done_m, total_m = milestones(rows)
        repo_name = st.repo.split("/")[-1].lower() if st.repo else e.directory.lower()
        meta = _meta(gh.get(repo_name))
        blocked_by = [
            d
            for d in e.depends
            if deps.dependency_state(root, roadmap.DIRECTORIES.get(d, d)).state
            != "code"
        ]
        kind = KINDS.get(e.kind, e.kind.lower())
        if e.name == "asn1core":
            kind = "library"
        if e.kind == "Reference":
            section = "reference"
        elif e.kind == "Tooling":
            section = "tools"
        elif st.label == "Implemented" or (active is not None and e.name in active):
            section = "active"
        else:
            section = "planned"
        if st.label == "Implemented":
            status = "shipped" if st.rows and st.done == st.rows else "active"
        elif blocked_by:
            status = "blocked"
        else:
            status = "planned"
        if active is not None and e.name in active and status in ("planned", "blocked"):
            status = "active"
        matrix_rel = (
            path.relative_to(roadmap.project_dir(root, e.name)).as_posix()
            if path
            else None
        )
        out.append(
            {
                "name": e.name,
                "display_name": e.name,
                "what": e.what
                or st.blurb
                or (gh.get(repo_name) or {}).get("description")
                or "",
                "kind": kind,
                "section": section,
                "tier": e.tier or None,
                "wave": e.wave if e.kind not in ("Reference", "Tooling") else None,
                "status": status,
                "blocked_by": blocked_by,
                "milestones_done": done_m,
                "milestones_total": total_m,
                # Obligation rows of the project's ROADMAP/PLAN matrix: done = rows marked [ok] (non-goals are
                # not counted). Projects almost never use `- M<n>` ladders, so these are the progress that exists.
                "obligations_done": sum(1 for r in rows if r.get("state") == "done"),
                "obligations_total": sum(1 for r in rows if r.get("state") != "nongoal"),
                "roadmap_url": f"{meta['repo_url']}/blob/HEAD/{matrix_rel}"
                if meta["repo_url"] and matrix_rel
                else None,
                "board_url": BOARD_URL,
                **meta,
                "order": (e.wave or 9) * 100 + (e.tier or 9),
            }
        )
    return out


# ---------------------------------------------------------------------------------------------- curation


RE_HINTS = (
    "firmware",
    "mfc",
    "brother",
    "5268ac",
    "fakeprint",
    "fakescan",
    "printscan",
    "plextor",
    "ath10k",
)
TOOL_HINTS = (
    "mcp",
    "launcher",
    "terraform",
    "server",
    "masscan",
    "wheelhouse",
    "tr-plugin",
)
CURATED_FORKS = ("continuous",)


def suggest(repo: dict[str, Any]) -> dict[str, Any]:
    """A first guess for one public repository that is not in the registry; the person corrects the file."""
    name = repo["name"].lower()
    pushed = (repo.get("pushedAt") or "")[:4]
    if repo.get("isFork"):
        keep = name in CURATED_FORKS or name.endswith("craft")
        return {
            "include": keep,
            "section": "forks",
            "kind": "app" if name.endswith("craft") else "tool",
        }
    if repo.get("isArchived") or pushed < "2024":
        return {"include": True, "section": "archive", "kind": "app"}
    if any(h in name for h in RE_HINTS):
        return {"include": True, "section": "re", "kind": "hardware"}
    kind = (
        "mcp"
        if "mcp" in name
        else "tool"
        if any(h in name for h in TOOL_HINTS)
        else "library"
    )
    return {
        "include": True,
        "section": "tools" if kind != "library" else "other",
        "kind": kind,
    }


def seed(
    gh: dict[str, dict[str, Any]], registered: set[str], existing: dict[str, Any]
) -> dict[str, Any]:
    """Suggestions for every public repo not in the registry, keeping any entry the person already wrote."""
    repos = dict(existing.get("repos", {}))
    for key, repo in sorted(gh.items()):
        if key not in registered and repo["name"] not in repos:
            repos[repo["name"]] = {**suggest(repo), "reviewed": False}
    return {
        "_note": "Hand-owned. include/section/kind/what/display_name per public repo not in the registry. "
        "Set reviewed true once checked. A repo missing here is not shown.",
        "repos": dict(sorted(repos.items(), key=lambda kv: kv[0].lower())),
    }


def curated_entries(
    curation: dict[str, Any], gh: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for name, choice in curation.get("repos", {}).items():
        repo = gh.get(name.lower())
        if not choice.get("include") or repo is None:
            continue
        section = choice.get("section", "archive")
        if section not in SECTION_IDS:
            raise SystemExit(
                f"error: {name}: section {section!r} is not one of {SECTION_IDS}"
            )
        meta = _meta(repo)
        out.append(
            {
                "name": repo["name"],
                "display_name": choice.get("display_name") or repo["name"],
                "what": choice.get("what") or repo.get("description") or "",
                "kind": choice.get("kind", "app"),
                "section": section,
                "tier": None,
                "wave": None,
                "status": "archived"
                if section == "archive" or meta["archived"]
                else "active",
                "blocked_by": [],
                "milestones_done": 0,
                "milestones_total": 0,
                "obligations_done": 0,
                "obligations_total": 0,
                "roadmap_url": None,
                "board_url": None,
                **meta,
                "order": 0,
                "reviewed": bool(choice.get("reviewed")),
            }
        )
    return out


# ---------------------------------------------------------------------------------------------- export


def build(
    root: pathlib.Path,
    gh: dict[str, dict[str, Any]],
    curation: dict[str, Any],
    active: set[str] | None,
) -> dict[str, Any]:
    projects = registry_entries(root, gh, active) + curated_entries(curation, gh)
    for p in projects:
        if p["section"] not in ("planned", "reference") and not p["order"]:
            # most recent first inside a section: later push, smaller key
            p["order"] = (
                -int(
                    datetime.fromisoformat(
                        p["last_push"].replace("Z", "+00:00")
                    ).timestamp()
                )
                if p["last_push"]
                else 0
            )
    projects.sort(
        key=lambda p: (SECTION_IDS.index(p["section"]), p["order"], p["name"].lower())
    )
    sha = subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {"repo": f"{OWNER}/sdk-forge (workspace)", "commit": sha or None},
        "board_url": BOARD_URL,
        "sections": [
            s for s in SECTIONS if any(p["section"] == s["id"] for p in projects)
        ],
        "projects": projects,
    }


URL_FIELDS = ("roadmap_url", "board_url", "repo_url", "homepage_url")
FORBIDDEN_ESCAPES = ("\\u003c", "\\u003e", "\\u0026", "\\u0022")
URL_BAD_CHARS = "\"' \t\r\n<>\\"


def serialise(record: dict[str, Any]) -> str:
    """The exact text written. ensure_ascii=False keeps UTF-8 as characters, so no \\u escapes appear."""
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"


def contract_problems(record: dict[str, Any]) -> list[str]:
    """What the site's `tofu plan` would reject, checked here first so a bad export fails in sdk-forge.

    From the site's contract (aws-infra sites/b1tninja): every URL is plain https:// with no quotes or
    whitespace; every project's section is in sections[]; the JSON holds no escapes for <, >, & or ";
    `what` is a string or null, `blocked_by` a list, and the milestone counts integers.
    """
    problems = []
    sections = {s["id"] for s in record.get("sections", [])}
    urls: list[tuple[str, str, object]] = [
        ("top", "board_url", record.get("board_url"))
    ]
    for p in record.get("projects", []):
        name = p.get("name", "?")
        if p.get("section") not in sections:
            problems.append(
                f"{name}: section {p.get('section')!r} is not in sections[]"
            )
        if not isinstance(p.get("what"), str | None):
            problems.append(f"{name}: what must be a string or null")
        if not isinstance(p.get("blocked_by"), list):
            problems.append(f"{name}: blocked_by must be a list")
        for key in ("milestones_done", "milestones_total", "obligations_done", "obligations_total"):
            if type(p.get(key)) is not int:
                problems.append(f"{name}: {key} must be an integer")
        urls += [(name, key, p.get(key)) for key in URL_FIELDS]
    for name, key, url in urls:
        if url is None:
            continue
        if (
            not isinstance(url, str)
            or not url.startswith("https://")
            or any(c in url for c in URL_BAD_CHARS)
        ):
            problems.append(f"{name}: {key} {url!r} is not a plain https:// URL")
    text = serialise(record).lower()
    problems += [
        f"the JSON contains the escape {e}" for e in FORBIDDEN_ESCAPES if e in text
    ]
    return problems


def _active_projects() -> set[str] | None:
    """The board's Active projects, or None when the board cannot be read (offline, no token)."""
    try:
        import planner

        return set(planner.active_projects())
    except Exception:  # noqa: BLE001 - the board is an enrichment, never a reason to fail the export
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        help="write portfolio.json here (outside any work tree)",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="add suggestions for new public repos to public_repos.json",
    )
    parser.add_argument(
        "--no-board", action="store_true", help="do not read Active from the board"
    )
    args = parser.parse_args(argv)
    import portfolio

    if not args.seed:
        if args.out is None:
            parser.error("--out is required unless --seed")
        import verify

        refused = verify.refuse_path(args.out)
        if refused:
            print(f"error: {refused}", file=sys.stderr)
            return 2
    gh = github_repos()
    curation = (
        json.loads(CURATION.read_text(encoding="utf-8"))
        if CURATION.is_file()
        else {"repos": {}}
    )
    if args.seed:
        registered = set()
        for e in portfolio.entries():
            repo = portfolio.github_repo(ROOT, e.directory)
            registered.add((repo.split("/")[-1] if repo else e.directory).lower())
        CURATION.write_text(
            json.dumps(seed(gh, registered, curation), indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {CURATION}")
        return 0
    assert args.out is not None
    record = build(ROOT, gh, curation, None if args.no_board else _active_projects())
    problems = contract_problems(record)
    if problems:
        print(
            "error: the export breaks the site's contract:\n  " + "\n  ".join(problems),
            file=sys.stderr,
        )
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(serialise(record), encoding="utf-8")
    counts = {
        s["id"]: sum(p["section"] == s["id"] for p in record["projects"])
        for s in record["sections"]
    }
    print(f"wrote {args.out}: {len(record['projects'])} projects {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
