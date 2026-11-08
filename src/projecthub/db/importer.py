"""Import every ROADMAP/PLAN into typed entries, with a per-project report the maintainer signs off.

PORTFOLIO.md section 9 item 11 (decided 2026-11-08): ROADMAP.md goes, and its content becomes typed entries
(project, epic, obligation, task, bug, question, decision, finding, rule) with links and citations. A file is
removed only after its import report is signed off, so this importer has one hard property:

**Every source line is placed exactly once.** Each line of the file, as stored losslessly by forgedb's raw
store, is assigned to one entry or flagged for the maintainer. Nothing is guessed silently and nothing is
dropped. The report proves it: `lines = placed + flagged + structural`, and lists every flagged line.

Mapping, by section heading (the 29 generated shells share six sections, the hand-owned files add a few):

| Section heading (contains)                         | Entry type                                  |
|----------------------------------------------------|---------------------------------------------|
| a matrix row                                       | obligation (id = the row id)                |
| milestones, ladder, phase                          | epic per ladder key (M1, P2, phase 3)       |
| open question                                      | question per list item                      |
| non-goal, will not claim, not on this list         | decision (accepted) per list item           |
| publication checklist                              | task per list item                          |
| errata watch, dead end, unverified, retracted      | finding per list item                       |
| defect                                             | bug per list item                           |
| captured ideas                                     | task (state "proposed") per list item       |
| the "**The honesty rule:**" paragraph              | rule                                        |
| board projection                                   | flagged: describes the old projection, superseded by the sync |
| anything else                                      | the project's description, flagged for review |

Citations are extracted (RFC numbers with an optional section, ITU-T X.6xx clauses) and stored as
`unresolved`: resolving them through rfclib and itulib is the next sub-step, and a citation is never trusted
from text alone. No clause text is ever stored, only refs.

    python forge_import.py --report-dir D:/temp/forge-import [--project pkix] [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from projecthub.db import store as forgedb
from projecthub.db import parser as roadmap

TYPES = (
    "project",
    "epic",
    "obligation",
    "task",
    "bug",
    "question",
    "decision",
    "finding",
    "rule",
)
LINKS = (
    "parent",
    "blocks",
    "relates",
    "duplicates",
    "supersedes",
    "found_in",
    "answers",
    "cites",
)
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY, project TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN {TYPES!r}),
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
  fields TEXT NOT NULL DEFAULT '{{}}', flagged TEXT
);
CREATE TABLE IF NOT EXISTS links (
  src TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN {LINKS!r}), dst TEXT NOT NULL,
  PRIMARY KEY (src, kind, dst)
);
-- Refs and the library's short cite string only. There is deliberately no column for clause text: the ITU
-- corpus may not be redistributed, and the board and site are public.
CREATE TABLE IF NOT EXISTS citations (
  id INTEGER PRIMARY KEY, entry TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('rfc', 'itu', 'zim', 'url', 'commit', 'file')),
  ref TEXT NOT NULL, cite TEXT, edition TEXT,
  role TEXT NOT NULL CHECK (role IN ('normative', 'informative', 'evidence', 'prior_art')),
  status TEXT NOT NULL CHECK (status IN ('current', 'obsolete', 'errata', 'edition_changed', 'unresolved')),
  checked_at REAL, note TEXT,
  UNIQUE (entry, source, ref, role)
);
-- Provenance: every stored source line is owned by exactly one entry, or flagged, or is structure.
CREATE TABLE IF NOT EXISTS placements (
  project TEXT NOT NULL, segment INTEGER NOT NULL, ordinal INTEGER NOT NULL,
  entry TEXT, how TEXT NOT NULL CHECK (how IN ('placed', 'flagged', 'structural')), reason TEXT,
  PRIMARY KEY (project, segment, ordinal)
);
"""

SECTION_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("open question",), "question", "open"),
    (("non-goal", "will not claim", "not on this list"), "decision", "accepted"),
    (("publication checklist",), "task", "open"),
    (("errata watch", "dead end", "unverified", "retracted"), "finding", "current"),
    (("defect",), "bug", "open"),
    (("captured ideas",), "task", "proposed"),
]
LADDER_HEADINGS = ("milestone", "ladder", "phase")
SUPERSEDED_HEADINGS = ("board projection",)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
RFC = re.compile(
    r"\bRFC\s?(\d{3,5})(?:\s*(?:section|§|sections)\s*(\d+(?:\.\d+)*))?", re.IGNORECASE
)
ITU = re.compile(r"\bX\.(6[89]\d)(?:\s*(?:§|clause|section)\s*(\d+(?:\.\d+)*))?")
HONESTY = re.compile(r"^\*\*The honesty rule")


@dataclass
class Entry:
    id: str
    type: str
    title: str
    state: str
    lines: list[tuple[int, int]] = field(default_factory=list)  # (segment, ordinal)
    body: list[str] = field(default_factory=list)
    fields: dict[str, object] = field(default_factory=dict)
    parent: str = ""
    flagged: str = ""


def _strip(line: str) -> str:
    return line.rstrip("\r\n")


def _title(text: str, limit: int = 100) -> str:
    text = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", text.strip())
    text = re.sub(r"\*\*|`", "", text)
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "..."


def _rule_for(heading: str) -> tuple[str, str] | None:
    low = heading.lower()
    for needles, kind, state in SECTION_RULES:
        if any(n in low for n in needles):
            return kind, state
    return None


class Importer:
    def __init__(self, store: forgedb.Store) -> None:
        self.store = store
        self.db: sqlite3.Connection = store.db
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------------------------------------ one project

    def import_project(self, root: pathlib.Path, project: str) -> dict[str, object]:
        rows = self.store.import_project(root, project)
        segments = self.store.segments(project)
        ladder = roadmap.ladder_for(root, project)
        entries: dict[str, Entry] = {}
        placements: dict[tuple[int, int], tuple[str | None, str, str]] = {}
        counters: Counter[str] = Counter()

        def new(kind: str, title: str, state: str, key: str = "") -> Entry:
            counters[kind] += 1
            ident = f"{project}:{key or f'{kind}-{counters[kind]}'}"
            entry = Entry(ident, kind, title, state)
            entries[ident] = entry
            return entry

        def place(
            pos: tuple[int, int],
            entry: Entry | None,
            how: str,
            reason: str = "",
            text: str = "",
        ) -> None:
            placements[pos] = (entry.id if entry else None, how, reason)
            if entry is not None and how != "structural":
                entry.lines.append(pos)
                if text:
                    entry.body.append(text)

        proj = new("project", project, "active", key="project")
        epics = {
            key: new("epic", f"{key}: {_title(text)}", "open", key=key)
            for key, text in ladder.items()
        }
        for epic in epics.values():
            epic.parent = proj.id

        # Obligations from matrix rows, with their parsed fields.
        row_lines = {
            (seg, ordinal): row for seg, ordinal, row in self._row_positions(project)
        }
        section = ""
        current: Entry | None = None
        for s_index, seg in enumerate(segments):
            for o_index, raw in enumerate(seg.lines):
                pos = (s_index, o_index)
                text = _strip(raw)
                if seg.kind == "matrix":
                    row = row_lines.get(pos)
                    if row is not None:
                        ob = new(
                            "obligation",
                            row["title"],
                            row["state"] or "open",
                            key=row["name"] or "",
                        )
                        ob.fields = {
                            k: row[k]
                            for k in (
                                "spec",
                                "status",
                                "where",
                                "evidence",
                                "milestone",
                                "status_note",
                            )
                        }
                        if row.get("minted"):
                            ob.fields["minted"] = True
                            ob.flagged = "the row had no id; this one was minted from its position, name it"
                        ob.parent = (
                            epics[row["milestone"]].id
                            if row.get("milestone") in epics
                            else proj.id
                        )
                        place(pos, ob, "placed", text=text)
                        self._cite(ob, row["spec"], "normative")
                    elif (
                        not text.strip() or roadmap._is_separator(text) or o_index == 0
                    ):
                        place(pos, proj, "structural", "matrix header or separator")
                    else:
                        place(
                            pos,
                            None,
                            "flagged",
                            "a table line in the matrix that is not a parsable row",
                        )
                    continue
                heading = HEADING.match(text)
                if heading:
                    section = heading.group(2)
                    current = None
                    place(pos, proj, "structural", f"heading: {section}")
                    continue
                if not text.strip():
                    place(pos, proj, "structural", "blank line")
                    continue
                low = section.lower()
                if HONESTY.match(text.strip()):
                    current = new("rule", "The honesty rule", "active")
                    current.parent = proj.id
                    place(pos, current, "placed", text=text)
                    continue
                if (
                    current is not None
                    and current.type == "rule"
                    and not ITEM.match(text.strip())
                ):
                    place(pos, current, "placed", text=text)
                    continue
                if any(h in low for h in SUPERSEDED_HEADINGS):
                    place(
                        pos,
                        None,
                        "flagged",
                        "board projection text: superseded by the GitHub sync",
                    )
                    continue
                if any(h in low for h in LADDER_HEADINGS) and ladder:
                    key = roadmap.ladder_key(text)
                    if key in epics:
                        current = epics[key]
                    if current is not None and current.type == "epic":
                        place(pos, current, "placed", text=text)
                    else:
                        place(
                            pos,
                            proj,
                            "placed",
                            "milestones section prose, kept in the description",
                            text=text,
                        )
                        proj.flagged = proj.flagged or "has description prose to review"
                    continue
                rule = _rule_for(section)
                if rule is not None:
                    kind, state = rule
                    indent = len(text) - len(text.lstrip())
                    if text.lstrip().startswith("|"):
                        # A table in a typed section (the non-goals table): its header and separator are
                        # structure, and each body row is one entry, titled by its first cell.
                        nxt = (
                            _strip(seg.lines[o_index + 1])
                            if o_index + 1 < len(seg.lines)
                            else ""
                        )
                        if roadmap._is_separator(text) or roadmap._is_separator(nxt):
                            place(pos, proj, "structural", "table header or separator")
                            current = None
                        else:
                            cells = roadmap.split_cells(text)
                            current = new(
                                kind, _title(cells[0] if cells else text), state
                            )
                            current.parent = proj.id
                            current.fields = {"cells": cells}
                            place(pos, current, "placed", text=text)
                            self._cite(current, text, "informative")
                        continue
                    if (
                        current is not None
                        and current.type == kind
                        and current.fields.get("cells")
                    ):
                        # Prose after a table belongs to no row: a lead-in of its own.
                        current = None
                    if ITEM.match(text.strip()) and indent == 0:
                        current = new(kind, _title(text), state)
                        current.parent = proj.id
                        place(pos, current, "placed", text=text)
                        self._cite(current, text, "informative")
                    elif current is not None and current.type == kind:
                        place(pos, current, "placed", text=text)
                        self._cite(current, text, "informative")
                    else:
                        # Lead-in prose of a typed section: one entry of that type for the paragraph.
                        current = new(kind, _title(text), state)
                        current.parent = proj.id
                        current.flagged = (
                            f"section lead-in under '{section}', made one {kind}"
                        )
                        place(pos, current, "placed", text=text)
                    continue
                place(
                    pos,
                    proj,
                    "placed",
                    f"description: {section or 'preamble'}",
                    text=text,
                )
                proj.flagged = "has description prose to review"
        self._store(project, entries, placements)
        return self.report(project, rows)

    def _row_positions(self, project: str) -> list[tuple[int, int, dict[str, str]]]:
        out = []
        for seg, ordinal, parsed in self.db.execute(
            "SELECT segment, ordinal, parsed FROM lines WHERE project = ? AND parsed IS NOT NULL",
            (project,),
        ):
            out.append((seg, ordinal, json.loads(parsed)))
        # Rows with no id (namespy) are stored by forgedb without a row_id; mint ids from their position.
        for seg, ordinal, raw in self.db.execute(
            "SELECT l.segment, l.ordinal, l.raw FROM lines l JOIN segments s ON s.project = l.project "
            "AND s.ordinal = l.segment WHERE l.project = ? AND s.kind = 'matrix' AND l.parsed IS NULL",
            (project,),
        ):
            text = "".join(
                r[0]
                for r in self.db.execute(
                    "SELECT raw FROM lines WHERE project = ? AND segment = ? ORDER BY ordinal",
                    (project, seg),
                )
            )
            for row in roadmap.parse(text).rows:
                if int(row["line"]) - 1 == ordinal and not row["name"]:
                    minted = {**row, "name": f"row-{seg}-{ordinal}", "minted": "1"}
                    out.append((seg, ordinal, minted))
        return out

    # ------------------------------------------------------------------------------------------ citations

    def _cite(self, entry: Entry, text: str, role: str) -> None:
        refs = entry.fields.setdefault("_cites", [])
        assert isinstance(refs, list)
        for m in RFC.finditer(text or ""):
            refs.append(
                (
                    "rfc",
                    f"RFC{m.group(1)}" + (f"§{m.group(2)}" if m.group(2) else ""),
                    role,
                )
            )
        for m in ITU.finditer(text or ""):
            refs.append(
                (
                    "itu",
                    f"X.{m.group(1)}" + (f"§{m.group(2)}" if m.group(2) else ""),
                    role,
                )
            )

    # ------------------------------------------------------------------------------------------ storage

    def _store(
        self,
        project: str,
        entries: dict[str, Entry],
        placements: dict[tuple[int, int], tuple[str | None, str, str]],
    ) -> None:
        with self.db:
            self.db.execute("DELETE FROM placements WHERE project = ?", (project,))
            old = [
                r[0]
                for r in self.db.execute(
                    "SELECT id FROM entries WHERE project = ?", (project,)
                )
            ]
            self.db.executemany("DELETE FROM links WHERE src = ?", [(i,) for i in old])
            self.db.executemany(
                "DELETE FROM citations WHERE entry = ?", [(i,) for i in old]
            )
            self.db.execute("DELETE FROM entries WHERE project = ?", (project,))
            for e in entries.values():
                cites = e.fields.pop("_cites", [])
                self.db.execute(
                    "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        e.id,
                        project,
                        e.type,
                        e.title,
                        "\n".join(e.body),
                        e.state,
                        json.dumps(e.fields),
                        e.flagged or None,
                    ),
                )
                if e.parent:
                    self.db.execute(
                        "INSERT OR IGNORE INTO links VALUES (?, 'parent', ?)",
                        (e.id, e.parent),
                    )
                assert isinstance(cites, list)
                for source, ref, role in cites:
                    self.db.execute(
                        "INSERT OR IGNORE INTO citations (entry, source, ref, role, status) VALUES (?, ?, ?, ?, 'unresolved')",
                        (e.id, source, ref, role),
                    )
            self.db.executemany(
                "INSERT INTO placements VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (project, s, o, entry, how, reason)
                    for (s, o), (entry, how, reason) in placements.items()
                ],
            )

    # ------------------------------------------------------------------------------------------ report

    def report(self, project: str, rows: int) -> dict[str, object]:
        total = self.db.execute(
            "SELECT count(*) FROM lines WHERE project = ?", (project,)
        ).fetchone()[0]
        how = dict(
            self.db.execute(
                "SELECT how, count(*) FROM placements WHERE project = ? GROUP BY how",
                (project,),
            ).fetchall()
        )
        by_type = dict(
            self.db.execute(
                "SELECT type, count(*) FROM entries WHERE project = ? GROUP BY type ORDER BY type",
                (project,),
            ).fetchall()
        )
        flagged_lines = [
            (seg, ordinal, reason, raw)
            for seg, ordinal, reason, raw in self.db.execute(
                "SELECT p.segment, p.ordinal, p.reason, l.raw FROM placements p JOIN lines l USING (project, segment, ordinal) "
                "WHERE p.project = ? AND p.how = 'flagged' ORDER BY p.segment, p.ordinal",
                (project,),
            )
        ]
        flagged_entries = self.db.execute(
            "SELECT id, type, flagged FROM entries WHERE project = ? AND flagged IS NOT NULL ORDER BY id",
            (project,),
        ).fetchall()
        cites = self.db.execute(
            "SELECT c.source, count(*), count(DISTINCT c.ref) FROM citations c JOIN entries e ON e.id = c.entry "
            "WHERE e.project = ? GROUP BY c.source",
            (project,),
        ).fetchall()
        minted = sum(
            1
            for (f,) in self.db.execute(
                "SELECT fields FROM entries WHERE project = ? AND type = 'obligation'",
                (project,),
            )
            if json.loads(f).get("minted")
        )
        accounted = sum(how.values())
        return {
            "project": project,
            "lines": total,
            "placed": how.get("placed", 0),
            "structural": how.get("structural", 0),
            "flagged": how.get("flagged", 0),
            "every_line_once": accounted == total,
            "rows": rows,
            "entries": by_type,
            "minted_ids": minted,
            "citations": {source: {"refs": n, "distinct": d} for source, n, d in cites},
            "flagged_lines": [
                {"segment": s, "line": o, "reason": r, "text": _strip(raw)[:160]}
                for s, o, r, raw in flagged_lines
            ],
            "flagged_entries": [
                {"id": i, "type": t, "why": w} for i, t, w in flagged_entries
            ],
        }


def render(report: dict[str, object]) -> str:
    """The report the maintainer reads before signing off the project's ROADMAP removal."""
    r = report
    entries = r["entries"]
    assert isinstance(entries, dict)
    lines = [
        f"# Import report: {r['project']}",
        "",
        f"- Source lines: **{r['lines']}**: placed {r['placed']}, structural {r['structural']}, "
        f"flagged {r['flagged']}. Every line accounted for exactly once: **{'yes' if r['every_line_once'] else 'NO'}**.",
        "- Entries: " + ", ".join(f"{n} {t}" for t, n in entries.items()),
        f"- Obligations with ids minted because the row had none: {r['minted_ids']}",
        f"- Citations found (all unresolved until checked through rfclib/itulib): {json.dumps(r['citations'])}",
        "",
        "## Flagged lines (decide where each goes)",
        "",
    ]
    flagged = r["flagged_lines"]
    assert isinstance(flagged, list)
    lines += [
        f"- `{f['segment']}:{f['line']}` {f['reason']}: `{f['text']}`" for f in flagged
    ] or ["- none"]
    lines += ["", "## Flagged entries (check the type and split)", ""]
    fe = r["flagged_entries"]
    assert isinstance(fe, list)
    lines += [f"- `{f['id']}` ({f['type']}): {f['why']}" for f in fe] or ["- none"]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report-dir", type=pathlib.Path, required=True, help="outside every work tree"
    )
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args(argv)
    from projecthub import paths

    refused = paths.refuse_path(args.report_dir / "x")
    if refused:
        print(f"error: {refused}", file=sys.stderr)
        return 2
    importer = Importer(forgedb.Store(args.db))
    args.report_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for project, _ in forgedb.matrices(forgedb.ROOT):
        if args.project and project not in args.project:
            continue
        report = importer.import_project(forgedb.ROOT, project)
        (args.report_dir / f"{project}.md").write_text(render(report), encoding="utf-8")
        summary.append(report)
        ok = "ok " if report["every_line_once"] else "BAD"
        print(
            f"{ok} {project:<12} lines={report['lines']:<4} placed={report['placed']:<4} "
            f"flagged={report['flagged']:<3} entries={sum(report['entries'].values())}"  # type: ignore[attr-defined]
        )
    (args.report_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0 if all(r["every_line_once"] for r in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
