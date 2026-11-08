"""forge.db, migration step 1: import every obligation matrix losslessly, export it back byte for byte.

PORTFOLIO.md section 9 item 10 (decided 2026-11-08) makes a local database the working authority for planning
state and turns each ROADMAP matrix into a generated region. Nothing may depend on that until the round trip is
proven lossless, so this module does only three things:

- **Import.** A matrix file is split into prose segments and matrix regions (each contiguous table run that
  holds at least one obligation row). Every line is stored exactly as read, line ending included, next to the
  fields roadmap.parse found in it. Prose is stored too, but only so the file can be rebuilt; it stays
  hand-owned and nothing here edits it.
- **Export.** The file is rebuilt from the store. An unchanged row is emitted verbatim. A changed row has only
  the changed cells rewritten, through roadmap.replace_status for the Status token and a span replacement for
  any other cell, so its spacing, backticks and the notes after a status survive. That is stricter than
  re-rendering the row canonically, and it means a one-cell change is a one-cell diff.
- **Markers.** With `mark=True` each region is wrapped in
  `<!-- forge:matrix begin rev=N sha256=H -->` ... `<!-- forge:matrix end -->`, where H is the sha256 of the
  region's exact bytes. `check_markers` needs no database: a region whose bytes do not hash to its own marker
  was edited by hand, which the pre-commit hook and CI can refuse in any worktree.

    python forgedb.py roundtrip                      # import and export every matrix in memory; exit 1 on a diff
    python forgedb.py check-markers FILE [FILE ...]  # exit 1 on a hand-edited region
"""

from __future__ import annotations

from projecthub import paths

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from projecthub.db import parser as roadmap

ROOT = paths.ROOT
BEGIN = re.compile(r"^<!-- forge:matrix begin rev=(\d+) sha256=([0-9a-f]{64}) -->\s*$")
END = re.compile(r"^<!-- forge:matrix end -->\s*$")
HEADERLESS = {"spec": 1, "status": 2, "where": 3, "evidence": 4}
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  project TEXT PRIMARY KEY, path TEXT NOT NULL, imported_from TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
  project TEXT NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('prose', 'matrix')),
  rev INTEGER, marked INTEGER NOT NULL DEFAULT 0, columns TEXT,
  PRIMARY KEY (project, ordinal)
);
CREATE TABLE IF NOT EXISTS lines (
  project TEXT NOT NULL, segment INTEGER NOT NULL, ordinal INTEGER NOT NULL, raw TEXT NOT NULL,
  row_id TEXT, parsed TEXT, override TEXT,
  PRIMARY KEY (project, segment, ordinal)
);
CREATE INDEX IF NOT EXISTS lines_row ON lines (project, row_id);
"""


# ---------------------------------------------------------------------------------------------- the file


@dataclass
class Segment:
    kind: str  # "prose" | "matrix"
    lines: list[str]  # with their line endings
    rev: int | None = None
    marked: bool = False
    columns: dict[str, int] = field(default_factory=dict)


def _eol(line: str) -> str:
    return line[len(line.rstrip("\r\n")) :]


def region_hash(lines: Sequence[str]) -> str:
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _columns(lines: Sequence[str]) -> dict[str, int]:
    """Column positions by header name, or the historical positions for a header-less snippet."""
    bare = [line.rstrip("\r\n") for line in lines]
    if len(bare) > 1 and roadmap._is_separator(bare[1]):
        header = [h.lower() for h in roadmap.split_cells(bare[0])]
        found = {
            name: header.index(name)
            for name in ("spec", "status", "where", "evidence")
            if name in header
        }
        for name in roadmap.MILESTONE_HEADERS:
            if name in header:
                found["milestone"] = header.index(name)
                break
        return found
    return dict(HEADERLESS)


def split(text: str) -> list[Segment]:
    """Prose and matrix segments, losslessly: joining every segment's lines gives `text` back."""
    lines = text.splitlines(keepends=True)
    segments: list[Segment] = []
    prose: list[str] = []

    def flush() -> None:
        if prose:
            segments.append(Segment("prose", prose.copy()))
            prose.clear()

    i = 0
    # A marked region is taken as the markers say, so a hand edit inside it stays inside it.
    while i < len(lines):
        begin = BEGIN.match(lines[i].rstrip("\r\n"))
        if begin:
            j = i + 1
            while j < len(lines) and not END.match(lines[j].rstrip("\r\n")):
                j += 1
            if j == len(lines):
                raise ValueError(f"line {i + 1}: forge:matrix begin with no end marker")
            flush()
            body = lines[i + 1 : j]
            segments.append(
                Segment("matrix", body, int(begin.group(1)), True, _columns(body))
            )
            i = j + 1
            continue
        prose.append(lines[i])
        i += 1
    flush()
    # Unmarked prose: cut out each table run that holds an obligation row.
    out: list[Segment] = []
    for seg in segments:
        if seg.kind == "matrix":
            out.append(seg)
            continue
        out.extend(_cut_tables(seg.lines))
    return out


def _cut_tables(lines: list[str]) -> list[Segment]:
    text = "".join(lines)
    row_lines = {int(r["line"]) - 1 for r in roadmap.parse(text).rows}
    bare = text.splitlines()
    out: list[Segment] = []
    cursor = 0
    for run in roadmap._tables(bare):
        if not row_lines & set(run):
            continue
        start, end = run[0], run[-1] + 1
        if start > cursor:
            out.append(Segment("prose", lines[cursor:start]))
        body = lines[start:end]
        out.append(Segment("matrix", body, 0, False, _columns(body)))
        cursor = end
    if cursor < len(lines):
        out.append(Segment("prose", lines[cursor:]))
    return out


def marker_lines(seg: Segment, eol: str) -> tuple[str, str]:
    return (
        f"<!-- forge:matrix begin rev={seg.rev or 0} sha256={region_hash(seg.lines)} -->{eol}",
        f"<!-- forge:matrix end -->{eol}",
    )


def join(segments: Sequence[Segment], mark: bool | None = None) -> str:
    """The file text. mark=None keeps each region as it was found; True or False forces markers on or off."""
    parts: list[str] = []
    for seg in segments:
        if seg.kind == "matrix" and (seg.marked if mark is None else mark):
            eol = _eol(seg.lines[0]) or "\n"
            begin, end = marker_lines(seg, eol)
            parts += [begin, *seg.lines, end]
        else:
            parts += seg.lines
    return "".join(parts)


def check_markers(text: str) -> list[str]:
    """Hand edits inside marked regions, found from the file alone: no database, so any worktree or CI can run it."""
    problems = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        begin = BEGIN.match(lines[i].rstrip("\r\n"))
        if END.match(lines[i].rstrip("\r\n")):
            problems.append(f"line {i + 1}: forge:matrix end with no begin marker")
        if begin:
            j = i + 1
            while j < len(lines) and not END.match(lines[j].rstrip("\r\n")):
                j += 1
            if j == len(lines):
                problems.append(f"line {i + 1}: forge:matrix begin with no end marker")
                break
            actual = region_hash(lines[i + 1 : j])
            if actual != begin.group(2):
                problems.append(
                    f"lines {i + 2}-{j}: the generated matrix was edited by hand (sha256 {actual[:12]}, "
                    f"marker says {begin.group(2)[:12]}); change it with the forge tools and re-export"
                )
            i = j
        i += 1
    return problems


# ---------------------------------------------------------------------------------------------- cell edits


def set_cell(line: str, column: int, value: str, status_column: bool = False) -> str:
    """`line` with one cell replaced, keeping the line ending and the cell's own padding."""
    body, eol = line.rstrip("\r\n"), _eol(line)
    if status_column:
        return roadmap.replace_status(body, value, column) + eol
    spans = roadmap.cell_spans(body)
    start, end = spans[column]
    raw = body[start:end]
    lead = raw[: len(raw) - len(raw.lstrip())] or " "
    trail = raw[len(raw.rstrip()) :] or " "
    return body[:start] + lead + value + trail + body[end:] + eol


# ---------------------------------------------------------------------------------------------- the store


class Store:
    """The raw-row store. Step 1 only: import, edit a cell, export. Live state (leases, logs) comes in step 4."""

    def __init__(self, path: str | pathlib.Path = ":memory:") -> None:
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def import_text(self, project: str, path: str, text: str, source: str = "") -> int:
        """Replace the project's document with `text`. Returns the number of obligation rows stored."""
        segments = split(text)
        with self.db:
            for table in ("documents", "segments", "lines"):
                self.db.execute(f"DELETE FROM {table} WHERE project = ?", (project,))  # noqa: S608
            self.db.execute(
                "INSERT INTO documents VALUES (?, ?, ?)",
                (project, path, source or path),
            )
            rows = 0
            for s_index, seg in enumerate(segments):
                self.db.execute(
                    "INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        project,
                        s_index,
                        seg.kind,
                        seg.rev,
                        int(seg.marked),
                        json.dumps(seg.columns),
                    ),
                )
                parsed = self._parse_region(seg) if seg.kind == "matrix" else {}
                for l_index, raw in enumerate(seg.lines):
                    row = parsed.get(l_index)
                    rows += row is not None
                    self.db.execute(
                        "INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, NULL)",
                        (
                            project,
                            s_index,
                            l_index,
                            raw,
                            row["name"] if row else None,
                            json.dumps(row) if row else None,
                        ),
                    )
        return rows

    @staticmethod
    def _parse_region(seg: Segment) -> dict[int, dict[str, str]]:
        text = "".join(seg.lines)
        return {int(r["line"]) - 1: r for r in roadmap.parse(text).rows if r["name"]}

    def import_project(self, root: pathlib.Path, project: str) -> int:
        path = roadmap.matrix_path(root, project)
        if path is None:
            return 0
        rel = path.relative_to(root).as_posix()
        return self.import_text(project, rel, path.read_bytes().decode("utf-8"), rel)

    def projects(self) -> list[str]:
        return [
            r[0]
            for r in self.db.execute("SELECT project FROM documents ORDER BY project")
        ]

    def rows(self, project: str) -> list[dict[str, str]]:
        return [
            {**json.loads(parsed), **json.loads(override or "{}")}
            for parsed, override in self.db.execute(
                "SELECT parsed, override FROM lines WHERE project = ? AND row_id IS NOT NULL ORDER BY segment, ordinal",
                (project,),
            )
        ]

    def set_field(self, project: str, row_id: str, name: str, value: str) -> None:
        """Change one field of one row: status, spec, where, evidence or milestone."""
        found = self.db.execute(
            "SELECT segment, ordinal, override FROM lines WHERE project = ? AND row_id = ?",
            (project, row_id),
        ).fetchall()
        if len(found) != 1:
            raise LookupError(f"{project}: {len(found)} rows with id {row_id!r}")
        segment, ordinal, override = found[0]
        columns = json.loads(
            self.db.execute(
                "SELECT columns FROM segments WHERE project = ? AND ordinal = ?",
                (project, segment),
            ).fetchone()[0]
        )
        if name not in columns:
            raise LookupError(
                f"{project}: the matrix holding {row_id} has no {name} column"
            )
        merged = {**json.loads(override or "{}"), name: value}
        with self.db:
            self.db.execute(
                "UPDATE lines SET override = ? WHERE project = ? AND segment = ? AND ordinal = ?",
                (json.dumps(merged), project, segment, ordinal),
            )

    def segments(self, project: str) -> list[Segment]:
        out: list[Segment] = []
        for s_index, kind, rev, marked, columns in self.db.execute(
            "SELECT ordinal, kind, rev, marked, columns FROM segments WHERE project = ? ORDER BY ordinal",
            (project,),
        ).fetchall():
            cols = json.loads(columns or "{}")
            lines = []
            changed = False
            for raw, override in self.db.execute(
                "SELECT raw, override FROM lines WHERE project = ? AND segment = ? ORDER BY ordinal",
                (project, s_index),
            ):
                for name, value in json.loads(override or "{}").items():
                    raw = set_cell(
                        raw, cols[name], value, status_column=name == "status"
                    )
                    changed = True
                lines.append(raw)
            out.append(
                Segment(
                    kind, lines, (rev or 0) + (1 if changed else 0), bool(marked), cols
                )
            )
        return out

    def export_text(self, project: str, mark: bool | None = None) -> str:
        return join(self.segments(project), mark)


# ---------------------------------------------------------------------------------------------- the proof


def matrices(root: pathlib.Path) -> Iterator[tuple[str, pathlib.Path]]:
    from projecthub import registry as portfolio

    for entry in portfolio.entries():
        path = roadmap.matrix_path(root, entry.name)
        if path is not None:
            yield entry.name, path


def roundtrip(root: pathlib.Path) -> list[dict[str, object]]:
    """Import then export every matrix with no change, unmarked and marked. Both must give the original bytes."""
    results = []
    store = Store()
    for project, path in matrices(root):
        original = path.read_bytes().decode("utf-8")
        rows = store.import_project(root, project)
        plain = store.export_text(project) == original
        marked_text = store.export_text(project, mark=True)
        relabeled = Store()
        relabeled.import_text(project, "x", marked_text)
        marked = relabeled.export_text(
            project, mark=False
        ) == original and not check_markers(marked_text)
        regions = sum(1 for s in store.segments(project) if s.kind == "matrix")
        results.append(
            {
                "project": project,
                "path": path.relative_to(root).as_posix(),
                "rows": rows,
                "regions": regions,
                "identical": plain,
                "marked_roundtrip": marked,
            }
        )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "roundtrip", help="prove import and export are lossless on every matrix"
    )
    check = sub.add_parser("check-markers", help="refuse hand-edited generated regions")
    check.add_argument("files", nargs="+", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.command == "roundtrip":
        results = roundtrip(ROOT)
        for r in results:
            ok = r["identical"] and r["marked_roundtrip"]
            print(
                f"{'OK  ' if ok else 'DIFF'} {r['project']:<12} {r['path']:<28} rows={r['rows']:<3} regions={r['regions']}"
            )
        bad = [r for r in results if not (r["identical"] and r["marked_roundtrip"])]
        print(
            f"{len(results) - len(bad)}/{len(results)} matrices round-trip byte for byte"
        )
        return 1 if bad else 0
    failed = False
    for path in args.files:
        for problem in check_markers(path.read_bytes().decode("utf-8")):
            print(f"{path}: {problem}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
