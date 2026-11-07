"""One grammar for the portfolio's obligation matrices: rows, ladders, and the evidence a done row rests on.

Why one module. Before this, three readers disagreed: sync_roadmap took any line that began with a backtick
cell, rowdoc read only `- M<n>` ladders, and each project's tests/test_roadmap_rows.py had its own split.
namespy's emoji matrix parsed to zero rows, a `kp.verify.raw` id was dropped without a word, and a table
whose rows did not start with a backtick had its rows read as headers. Every caller now reads the same
grammar, and a line that looks like a row but does not parse is reported instead of skipped.

The grammar, all of it:

- A matrix is a markdown table whose header's first column is `Obligation` or `Id` and which has a
  `Status` column. Other columns are found by header name, never by position, so adding a Milestone column
  cannot turn its text into evidence. A bare row with no header (a snippet, a test fixture) falls back to
  the historical positions: id, spec, status, where, evidence.
- A row's id is its first cell when that cell is one code span (`anchors.load()`, `cl.ambient-trust`,
  `kp.verify.raw`). A first cell of prose (namespy) is a row with a title and no id: it is counted, never
  given an invented name.
- Statuses are `[ok]`, `[x]`, `[part]`, `[ ]`, `[no]`, with `partial` and namespy's emoji as aliases.
  A known status followed by a note, `✅ (storage)`, keeps its status.
- Ladders come in four forms (D18): `- M<n>` bullets, trustatlas's `- **P<n> - ...**` bullets,
  certledger's `| **<n>** |` phase table in TRUST-MODEL.md, and keyproof's numbered adoption-ladder rungs.
  The locator says which file and form each project uses. Nothing here converts one form to another or
  invents an M-number for a project that does not use them.
- Matrix files are found per project (D19): ROADMAP.md, then PLAN.md, then docs/roadmap.md, with a path
  override where a project keeps its matrix somewhere specific.

The evidence check is strict on purpose. A substring match let `def load_anchor_helper` satisfy
`::load_anchor` and `def parse_3164` satisfy `::parse`. Here the test file is parsed with `ast`, the name
must match exactly, must be one pytest collects (`test*`, inside a `Test*` class for `file::Class::test`),
and a reference marked `(to be written)` is never evidence.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------------------------- statuses

# Canonical tokens are the keys sync_roadmap.STATUSES already uses, so a caller's lookup table keeps
# working when it switches to this parser.
STATES = {
    "[ok]": "done",
    "[x]": "done",
    "[part]": "part",
    "[ ]": "open",
    "[no]": "nongoal",
}
ALIASES = {
    "partial": "[part]",
    "[partial]": "[part]",
    "✅": "[ok]",  # white heavy check mark
    "\U0001f7e1": "[part]",  # large yellow circle
    "⬜": "[ ]",  # white large square
    "▫️": "[no]",  # white small square, emoji presentation
    "▫": "[no]",
}
TO_BE_WRITTEN = "(to be written)"

# `anchors.load_anchor()`, `cl.ambient-trust`, `kp.verify.raw`: a prefix, a dot, then dotted segments.
ID_RE = re.compile(r"^([a-z0-9_]+)\.([a-z0-9_\-]+(?:\.[a-z0-9_\-]+)*)$")
MATRIX_FIRST = ("obligation", "id")
MILESTONE_HEADERS = ("milestone", "phase", "rung", "ladder")


def status_of(cell: str) -> tuple[str, str] | None:
    """(canonical status, trailing note), or None when the cell is not a status."""
    text = cell.strip()
    for wrap in ("**", "`"):
        if text.startswith(wrap) and text.endswith(wrap) and len(text) > 2 * len(wrap):
            text = text[len(wrap) : -len(wrap)].strip()
    lowered = text.lower()
    known = {**{k: k for k in STATES}, **ALIASES}
    if lowered in known:
        return known[lowered], ""
    # A recognised token followed by a note: `✅ (storage)`, `[x] see below`.
    for token in sorted(known, key=len, reverse=True):
        if lowered.startswith(token):
            rest = text[len(token) :]
            if rest[:1].isspace() or rest[:1] == "(":
                return known[token], rest.strip()
        if lowered.startswith("`" + token + "`"):
            rest = text[len(token) + 2 :]
            if not rest or rest[:1].isspace() or rest[:1] == "(":
                return known[token], rest.strip()
    return None


# ---------------------------------------------------------------------------------------------- cells


def cell_spans(line: str) -> list[tuple[int, int]]:
    """(start, end) of each cell's text between pipes. A backslash-escaped pipe does not split a cell."""
    bars = [
        i for i, ch in enumerate(line) if ch == "|" and (i == 0 or line[i - 1] != "\\")
    ]
    return [(bars[k] + 1, bars[k + 1]) for k in range(len(bars) - 1)]


def split_cells(line: str) -> list[str]:
    return [line[a:b].strip() for a, b in cell_spans(line)]


def _is_separator(line: str) -> bool:
    cells = split_cells(line)
    return bool(cells) and all(c and set(c) <= set("-: ") and "-" in c for c in cells)


def _id_of(cell: str) -> str | None:
    """The id when the cell is one code span, "" for prose, None for a malformed code span."""
    match = re.fullmatch(r"`([^`]+)`(\(\))?", cell)
    if not match:
        return ""
    found = ID_RE.match(match.group(1).removesuffix("()"))
    return f"{found.group(1)}.{found.group(2)}" if found else None


# ---------------------------------------------------------------------------------------------- milestones

LADDER_ID = re.compile(r"\b(M\d+|P\d+)\b|\b(phase|rung)\s+(\d+)\b", re.IGNORECASE)
_M_ONLY = re.compile(r"\bM\d+\b")


def ladder_key(text: str, column: str = "") -> str:
    """A ladder id in its normal form (`M3`, `P2`, `phase 3`, `rung 1`), or "".

    In a column headed Phase or Rung a bare number is that rung: `| 2 |` under Rung is `rung 2`.
    """
    text = text.strip().strip("`*").strip()
    if column in ("phase", "rung") and re.fullmatch(r"\d+", text):
        return f"{column} {text}"
    found = LADDER_ID.search(text)
    if not found:
        return ""
    if found.group(1):
        token = found.group(1)
        return token[0].upper() + token[1:]
    return f"{found.group(2).lower()} {found.group(3)}"


# ---------------------------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class Problem:
    line: int  # 1-based
    text: str
    reason: str


@dataclass(frozen=True)
class Matrix:
    rows: list[dict[str, str]]
    problems: list[Problem]


def _tables(lines: list[str]) -> list[list[int]]:
    """Runs of consecutive lines that begin with a pipe, as line indices."""
    runs: list[list[int]] = []
    current: list[int] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("|"):
            current.append(index)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _row(
    cells: list[str], columns: dict[str, int], ladder_header: str, index: int
) -> dict[str, str]:
    def cell(key: str) -> str:
        at = columns.get(key)
        return cells[at] if at is not None and at < len(cells) else ""

    status, note = status_of(cell("status")) or ("", "")
    if "milestone" in columns:
        milestone = ladder_key(cell("milestone"), ladder_header)
    else:
        found = next((m.group() for c in cells for m in [_M_ONLY.search(c)] if m), "")
        milestone = found
    first = cells[0]
    ident = _id_of(first) or ""
    return {
        "name": ident,
        "title": first.strip("`") if ident else first,
        "spec": cell("spec"),
        "status": status,
        "state": STATES.get(status, ""),
        "status_note": note,
        "where": cell("where"),
        "evidence": cell("evidence"),
        "milestone": milestone,
        "line": str(index + 1),
    }


def parse(text: str) -> Matrix:
    """Every matrix row in the text, and every row-shaped line that did not parse."""
    lines = text.splitlines()
    rows: list[dict[str, str]] = []
    problems: list[Problem] = []
    for run in _tables(lines):
        has_header = len(run) > 1 and _is_separator(lines[run[1]])
        if has_header:
            header = [h.lower() for h in split_cells(lines[run[0]])]
            if not header or header[0] not in MATRIX_FIRST or "status" not in header:
                continue
            columns: dict[str, int] = {"id": 0}
            ladder_header = ""
            for key in ("spec", "status", "where", "evidence"):
                if key in header:
                    columns[key] = header.index(key)
            for key in MILESTONE_HEADERS:
                if key in header:
                    columns["milestone"] = header.index(key)
                    ladder_header = key
                    break
            body = run[2:]
            needed = columns["status"] + 1
            candidates = body
        else:
            # A snippet with no header: the historical positions, and only lines led by a code span.
            columns = {"id": 0, "spec": 1, "status": 2, "where": 3, "evidence": 4}
            ladder_header = ""
            needed = 4
            candidates = [i for i in run if lines[i].lstrip().startswith("| `")]
        for index in candidates:
            line = lines[index]
            cells = split_cells(line)
            if len(cells) < needed:
                problems.append(
                    Problem(
                        index + 1,
                        line,
                        f"{len(cells)} cells, the Status column needs {needed}",
                    )
                )
                continue
            if not cells[0]:
                problems.append(Problem(index + 1, line, "the first cell is empty"))
                continue
            ident = _id_of(cells[0])
            if ident is None:
                problems.append(
                    Problem(index + 1, line, f"{cells[0]} is a code span but not an id")
                )
                continue
            if not has_header and not ident:
                continue
            if status_of(cells[columns["status"]]) is None:
                problems.append(
                    Problem(
                        index + 1,
                        line,
                        f"{cells[columns['status']]!r} is not a recognised status",
                    )
                )
                continue
            rows.append(_row(cells, columns, ladder_header, index))
    return Matrix(rows, problems)


def rows_of(text: str) -> list[dict[str, str]]:
    """Rows that carry an id: the contract sync_roadmap, planner and deps were written against."""
    return [row for row in parse(text).rows if row["name"]]


def unparsable(text: str) -> list[Problem]:
    return parse(text).problems


def replace_status(line: str, new_status: str, column: int = 2) -> str:
    """The same line with only the Status cell's token changed: spacing, other cells and backticks kept."""
    spans = cell_spans(line)
    start, end = spans[column]
    raw = line[start:end]
    stripped = raw.strip()
    lead = raw[: len(raw) - len(raw.lstrip())]
    trail = raw[len(raw.rstrip()) :]
    ticked = stripped.startswith("`")
    note = (status_of(stripped) or ("", ""))[1]
    token = f"`{new_status}`" if ticked else new_status
    replacement = f"{token} {note}" if note else token
    return line[:start] + lead + replacement + trail + line[end:]


def status_column(text: str, line_no: int) -> int:
    """The Status column index for the table holding a 1-based line, by header; 2 when headerless."""
    lines = text.splitlines()
    for run in _tables(lines):
        if line_no - 1 in run:
            if len(run) > 1 and _is_separator(lines[run[1]]):
                header = [h.lower() for h in split_cells(lines[run[0]])]
                if "status" in header:
                    return header.index("status")
            break
    return 2


# ---------------------------------------------------------------------------------------------- ladders


def ladder_of(text: str, form: str = "M") -> dict[str, str]:
    """Ladder id -> its description, continuation lines included. `form` is M, P, phase or rung."""
    found: dict[str, str] = {}
    lines = text.splitlines()
    if form == "phase":
        for run in _tables(lines):
            if len(run) < 2 or not _is_separator(lines[run[1]]):
                continue
            header = [h.lower() for h in split_cells(lines[run[0]])]
            if not header or header[0] != "phase":
                continue
            for index in run[2:]:
                cells = split_cells(lines[index])
                number = cells[0].strip("*` ") if cells else ""
                if re.fullmatch(r"\d+", number) and len(cells) > 1:
                    found[f"phase {number}"] = cells[1]
        return found
    current: str | None = None
    in_ladder = form != "rung"
    for line in lines:
        if form == "rung" and line.startswith("#"):
            in_ladder = "ladder" in line.lower()
            current = None
            continue
        entry: tuple[str, str] | None = None
        if form == "M":
            hit = re.match(r"^- (M\d+)\s+(.*)$", line)
            entry = (hit.group(1), hit.group(2)) if hit else None
        elif form == "P":
            hit = re.match(r"^- \*\*(P\d+)\s*[-–—]\s*(.*?)\*\*\s*(.*)$", line)
            entry = (hit.group(1), f"{hit.group(2)} {hit.group(3)}") if hit else None
        elif form == "rung" and in_ladder:
            hit = re.match(r"^(\d+)\. \*\*(.*?)\*\*\s*(.*)$", line)
            entry = (
                (f"rung {hit.group(1)}", f"{hit.group(2)} {hit.group(3)}")
                if hit
                else None
            )
        if entry:
            key, value = entry
            current = key
            found[current] = value.strip()
        elif current and line.startswith("  ") and line.strip():
            found[current] += " " + line.strip()
        else:
            current = None
    return found


@dataclass(frozen=True)
class LadderSpec:
    path: str  # relative to the project directory; "" means the project's matrix file
    form: str


# D18: the ladder each project already writes, read where it is. Every other project uses `- M<n>` in
# its matrix file.
LADDERS: dict[str, LadderSpec] = {
    "certledger": LadderSpec("TRUST-MODEL.md", "phase"),
    "trustatlas": LadderSpec("PLAN.md", "P"),
    "keyproof": LadderSpec("PLAN.md", "rung"),
}

# ---------------------------------------------------------------------------------------------- discovery

# Project name -> directory, where they differ.
DIRECTORIES = {"itulib": "itulibrary", "rfclib": "rfclibrary"}
MATRIX_FILES = ("ROADMAP.md", "PLAN.md", "docs/roadmap.md")
# D19: where a project keeps its matrix somewhere the search order would not pick first.
MATRIX_OVERRIDES = {"namespy": "docs/roadmap.md"}
# D19: the projects outside the generated shells whose matrices are synced and gated.
HAND_OWNED = (
    "certledger",
    "ctspy",
    "pkix",
    "asn1core",
    "itulib",
    "keyproof",
    "localns",
    "trustatlas",
)


def project_dir(root: pathlib.Path, project: str) -> pathlib.Path:
    return root / DIRECTORIES.get(project, project)


def matrix_path(root: pathlib.Path, project: str) -> pathlib.Path | None:
    """The project's obligation matrix: the override, else the first file in MATRIX_FILES holding rows."""
    folder = project_dir(root, project)
    if project in MATRIX_OVERRIDES:
        path = folder / MATRIX_OVERRIDES[project]
        return path if path.is_file() else None
    for name in MATRIX_FILES:
        path = folder / name
        if path.is_file() and parse(path.read_text(encoding="utf-8")).rows:
            return path
    return None


def ladder_for(root: pathlib.Path, project: str) -> dict[str, str]:
    """The project's ladder, from the file and in the form LADDERS names; `- M<n>` in its matrix otherwise."""
    spec = LADDERS.get(project, LadderSpec("", "M"))
    path = (
        project_dir(root, project) / spec.path
        if spec.path
        else matrix_path(root, project)
    )
    if path is None or not path.is_file():
        return {}
    return ladder_of(path.read_text(encoding="utf-8"), spec.form)


# ---------------------------------------------------------------------------------------------- errata


def errata_lines(text: str, spec: str) -> list[str]:
    """The bullets of the errata section that mention any RFC the spec cell cites, anywhere on the line.

    `- RFC 6962 and RFC 9162 carry ...` belongs to a row citing either; the earlier reader matched only
    the number the line began with. Only bullets under a heading that names errata count, so a prose
    bullet elsewhere that happens to cite the same RFC is not reported as an erratum.
    """
    numbers = list(dict.fromkeys(re.findall(r"RFC\s*(\d+)", spec)))
    out: list[str] = []
    in_errata = False
    for line in text.splitlines():
        if line.startswith("#"):
            in_errata = "errat" in line.lower()
            continue
        if (
            in_errata
            and line.startswith("- ")
            and any(re.search(rf"\bRFC\s*{n}\b", line) for n in numbers)
        ):
            out.append(line[2:].strip())
    return out


# ---------------------------------------------------------------------------------------------- evidence

TEST_REF = re.compile(r"([\w./\\-]+\.py)((?:::\w+)+)")
BARE_TEST = re.compile(r"`(test_\w+)`")


def _defines(tree: ast.Module, parts: list[str]) -> str:
    """Return "" when the module defines the collectable test `parts` names, else why not."""
    *classes, function = parts
    if not function.startswith("test"):
        return f"{function} is not collectable: pytest collects only test* functions"
    scope: list[ast.stmt] = tree.body
    for name in classes:
        if not name.startswith("Test"):
            return f"{name} is not collectable: pytest collects only Test* classes"
        found = next(
            (n for n in scope if isinstance(n, ast.ClassDef) and n.name == name), None
        )
        if found is None:
            return f"defines no class {name}"
        scope = found.body
    if any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == function
        for n in scope
    ):
        return ""
    return f"defines no {function}"


def _parse_file(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def _marked(cell: str, end: int) -> bool:
    """Is the reference ending at `end` followed by `(to be written)` before the next item in the cell?"""
    window = re.split(r"[,;]|`(?=[\w./\-]+\.py::|test_)", cell[end:], maxsplit=1)[0]
    return TO_BE_WRITTEN in window


def named_test(
    root: pathlib.Path, project: str, where: str, evidence_cell: str = ""
) -> tuple[str, str]:
    """(pytest node id, state) for the test a row names. state: exists, to be written, missing or none.

    The node comes from Where when Where names one, and from Evidence when Where names a module instead:
    certledger's cl.multi-path reads `certledger/fanout.py` (planned) in Where and carries its test in
    Evidence, and reporting the module as the "named test" told an agent to run a file that is not a test.
    """
    folder = project_dir(root, project)
    for cell in (where, evidence_cell):
        for found in TEST_REF.finditer(cell):
            relative, parts = found.group(1), found.group(2).split("::")[1:]
            node = f"{relative}::{'::'.join(parts)}"
            if _marked(cell, found.end()):
                return node, "to be written"
            tree = (
                _parse_file(folder / relative)
                if (folder / relative).is_file()
                else None
            )
            return node, "exists" if tree is not None and not _defines(
                tree, parts
            ) else "missing"
    for cell in (where, evidence_cell):
        for found in BARE_TEST.finditer(cell):
            name = found.group(1)
            if _marked(cell, found.end()):
                return name, "to be written"
            tests = folder / "tests"
            files = sorted(tests.rglob("test*.py")) if tests.is_dir() else []
            for path in files:
                tree = _parse_file(path)
                if tree is not None and not _defines(tree, [name]):
                    return f"{path.relative_to(folder).as_posix()}::{name}", "exists"
            return name, "missing"
    return "", "none"


def evidence(
    root: pathlib.Path, project: str, where: str, evidence_cell: str = ""
) -> tuple[bool, str]:
    """Do the tests a row names exist, by exact name, in files pytest would collect them from?

    A floor, not the rule: only CI running the test can show it would fail if the behaviour regressed.
    What this refuses is the cheaper lie, a row pointing at a file or a symbol nobody wrote. Every
    `file.py::test` reference in Where and Evidence must resolve, except one marked `(to be written)`,
    which is a plan and never counts; at least one must resolve. A bare `test_x` in either cell (projects
    with no Evidence column name tests in Where) is looked up, by exact name, among the project's tests/.
    """
    folder = project_dir(root, project)
    held: list[str] = []
    for cell in (where, evidence_cell):
        for found in TEST_REF.finditer(cell):
            relative, parts = found.group(1), found.group(2).split("::")[1:]
            if _marked(cell, found.end()):
                continue
            path = folder / relative
            if not path.is_file():
                return False, f"{relative} does not exist in {project}"
            tree = _parse_file(path)
            if tree is None:
                return False, f"{relative} does not parse"
            why = _defines(tree, parts)
            if why:
                return False, f"{relative} {why}" if why.startswith("defines") else why
            held.append(f"{relative} defines {'::'.join(parts)}")
    referenced = {
        m.group(2).split("::")[-1]
        for c in (where, evidence_cell)
        for m in TEST_REF.finditer(c)
    }
    bare = [(c, m) for c in (where, evidence_cell) for m in BARE_TEST.finditer(c)]
    for cell, found in bare:
        name = found.group(1)
        if name in referenced or _marked(cell, found.end()):
            continue
        files = (
            sorted((folder / "tests").rglob("test*.py"))
            if (folder / "tests").is_dir()
            else []
        )
        hit = next(
            (
                f
                for f in files
                if (t := _parse_file(f)) is not None and not _defines(t, [name])
            ),
            None,
        )
        if hit is None:
            return False, f"no file under {project}/tests defines {name}"
        held.append(f"{hit.relative_to(folder).as_posix()} defines {name}")
    if not held:
        if TO_BE_WRITTEN in where or TO_BE_WRITTEN in evidence_cell:
            return False, f"the named test is marked {TO_BE_WRITTEN}: {where!r}"
        return False, f"the Where cell names no file::symbol: {where!r}"
    return True, "; ".join(held)
