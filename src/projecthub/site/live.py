"""The site's data (schema 2), built live from the product repositories and the organization Project.

labs.b1tninja.com used to show a snapshot committed by hand. This builds the same files on every Pages run:

- the cards come from the registry and GitHub (the existing export);
- each project's obligations come from its ROADMAP, with spec, named test and state;
- Project 1 adds what only it knows: a row someone claimed shows In progress or Blocked, and each claim is an
  activity entry. Activity is public, so it carries its kind and time only (the owner's decision, 2026-11-08).

    projecthub site --out data            # FORGE_ROOT points at a workspace, such as projecthub checkout's
    projecthub site --out data --no-project

The contract is DATA.md in the site repository. A file that would break it is refused before anything is written.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from projecthub import config, paths, registry
from projecthub.db import parser as roadmap
from projecthub.mcp import loop
from projecthub.site import export

SCHEMA_VERSION = 2
STATE = {"done": "done", "open": "open", "part": "open"}
LIVE = {"In progress": "in_progress", "Blocked": "blocked"}
CARD_FIELDS = (
    "name",
    "display_name",
    "what",
    "kind",
    "section",
    "status",
    "tier",
    "wave",
    "blocked_by",
    "progress",
    "language",
    "last_push",
    "is_fork",
    "upstream",
    "archived",
    "repo_url",
    "homepage_url",
    "order",
    "updated",
)
NAME = re.compile(r"^[A-Za-z0-9._-]+$")
URLS = ("repo_url", "roadmap_url", "homepage_url", "board_url")


def _row_key(row: str) -> str:
    return row.replace("`", "").strip().removesuffix("()")


def obligations(root: pathlib.Path, project: str) -> list[dict[str, Any]]:
    """The project's ROADMAP rows as the site shows them. Non-goals are decisions, not work, so they are left out."""
    path = roadmap.matrix_path(root, project)
    if path is None:
        return []
    out = []
    for row in roadmap.rows_of(path.read_text(encoding="utf-8")):
        state = STATE.get(row["state"])
        if state is None:
            continue
        test, _ = roadmap.named_test(root, project, row["where"], row["evidence"])
        out.append(
            {
                "id": row["name"],
                "title": row["title"],
                "spec": row["spec"],
                "state": state,
                "test": test if "::" in test else "",
                "blocked_by": [],
            }
        )
    return out


def overlay(
    project: str, rows: list[dict[str, Any]], items: Sequence[Any]
) -> list[dict[str, str]]:
    """Apply Project 1's live status to `rows` in place, and return the activity it records, newest first.

    A done row stays done whatever the card says: done is decided by the named test, not by a field.
    """
    cards = {}
    for item in items:
        match = loop.CARD.match(str(item.title).strip())
        if match and match.group("project") == project:
            cards[_row_key(match.group("row"))] = item
    activity = []
    for row in rows:
        item = cards.get(_row_key(row["id"]))
        if item is None:
            continue
        live = LIVE.get(str(item.values.get("Status", "")))
        if live and row["state"] != "done":
            row["state"] = live
        claimed = item.values.get("Claimed")
        if claimed:
            activity.append({"at": str(claimed), "kind": "claim"})
    return sorted(activity, key=lambda a: a["at"], reverse=True)


def card(project: dict[str, Any], generated: str) -> dict[str, Any]:
    if project.get("obligations_total"):
        progress: dict[str, Any] | None = {
            "done": project["obligations_done"],
            "total": project["obligations_total"],
            "unit": "obligations",
        }
    elif project.get("milestones_total"):
        progress = {
            "done": project["milestones_done"],
            "total": project["milestones_total"],
            "unit": "milestones",
        }
    else:
        progress = None
    out = {k: project.get(k) for k in CARD_FIELDS}
    out["progress"] = progress
    out["updated"] = generated
    out["blocked_by"] = list(project.get("blocked_by") or [])
    return out


def board_url(public: bool) -> str:
    """The Project when anyone can open it; the planning repository's issues while it is private."""
    return (
        config.BOARD_URL
        if public
        else f"https://github.com/{config.PORTFOLIO_REPO}/issues"
    )


def project_is_public() -> bool:
    query = '{organization(login:"%s"){projectV2(number:%d){public}}}' % (
        config.PROJECT_OWNER,
        config.PROJECT_NUMBER,
    )
    done = subprocess.run(  # noqa: S603, S607
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "--jq",
            ".data.organization.projectV2.public",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return done.returncode == 0 and done.stdout.strip() == "true"


def build(
    root: pathlib.Path, record: dict[str, Any], items: Sequence[Any], public: bool
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """(index.json, {name: p/<name>.json}) from the export's record and the Project's items."""
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    registered = {e.name for e in registry.entries()}
    url = board_url(public)
    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "source": record["source"],
        "board_url": url,
        "sections": record["sections"],
        "projects": [],
    }
    files = {}
    for project in record["projects"]:
        rows = (
            obligations(root, project["name"]) if project["name"] in registered else []
        )
        activity = overlay(project["name"], rows, items)
        entry = card(project, generated)
        index["projects"].append(entry)
        files[project["name"]] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated,
            "project": {
                **entry,
                "roadmap_url": project.get("roadmap_url"),
                "board_url": url,
            },
            "obligations": rows,
            "activity": activity,
        }
    return index, files


def problems(index: dict[str, Any], files: dict[str, dict[str, Any]]) -> list[str]:
    """The rules DATA.md says the publisher enforces."""
    found = []
    sections = {s["id"] for s in index["sections"]}
    names = [p["name"] for p in index["projects"]]
    if len(set(names)) != len(names):
        found.append("duplicate project names")
    for p in index["projects"]:
        if not NAME.match(str(p["name"])):
            found.append(f"{p['name']!r} is not a valid name")
        if p["section"] not in sections:
            found.append(f"{p['name']}: section {p['section']!r} is not in sections")
    if set(names) != set(files):
        found.append("every card needs its file and every file its card")
    for doc in [index, *[f["project"] for f in files.values()], *index["projects"]]:
        for key in URLS:
            url = doc.get(key)
            if url is not None and (
                not str(url).startswith("https://")
                or any(c in str(url) for c in " \t\"'<>")
            ):
                found.append(
                    f"{doc.get('name', 'index')}: {key} {url!r} is not a plain https:// URL"
                )
    for name, doc in files.items():
        for entry in doc["activity"]:
            if set(entry) - {"at", "kind"}:
                found.append(f"{name}: activity carries more than kind and time")
    return found


def write(
    out: pathlib.Path, index: dict[str, Any], files: dict[str, dict[str, Any]]
) -> None:
    (out / "p").mkdir(parents=True, exist_ok=True)
    for stale in (out / "p").glob("*.json"):
        if stale.stem not in files:
            stale.unlink()
    for name, doc in files.items():
        (out / "p" / f"{name}.json").write_text(
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def project_items() -> list[Any]:
    from projecthub.github.client import Projects

    return list(Projects().items(config.PROJECT_OWNER, config.PROJECT_NUMBER))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub site", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument(
        "--no-project", action="store_true", help="skip Project 1: files only"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    root = paths.ROOT
    items = [] if args.no_project else project_items()
    active = None if args.no_project else set(loop.active_projects(items)) or None
    curation = (
        json.loads(export.CURATION.read_text(encoding="utf-8"))
        if export.CURATION.is_file()
        else {"repos": {}}
    )
    record = export.build(root, export.github_repos(), curation, active)
    index, files = build(
        root, record, items, public=not args.no_project and project_is_public()
    )
    found = problems(index, files)
    if found:
        print(
            "error: the site data breaks its contract:\n  " + "\n  ".join(found),
            file=sys.stderr,
        )
        return 1
    write(args.out, index, files)
    rows = sum(len(f["obligations"]) for f in files.values())
    claims = sum(len(f["activity"]) for f in files.values())
    print(
        f"wrote {args.out}: {len(files)} projects, {rows} obligations, {claims} activity entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
