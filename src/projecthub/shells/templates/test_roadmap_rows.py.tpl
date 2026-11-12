"""The obligation matrix and the code cannot drift apart silently.

Every row of ROADMAP.md names a callable. A row that names nothing is a wish, and wishes
belong in the milestone list rather than in the matrix, because a matrix is what someone
trusts when deciding whether a behaviour exists.

A row marked [ok], [x] or [part] must also name a test that exists: a `file.py::test_name`
reference whose file parses and defines that exact, collectable name. A longer name does
not count (`test_load_anchor_helper` is not `test_load_anchor`), and a reference marked
"(to be written)" is a plan, never evidence. Only CI running the test can show it would
fail if the behaviour regressed; this refuses the cheaper lie of naming a test nobody wrote.

The grammar is projecthub's roadmap parser, copied small: columns are found by the table's
header, never by position. Failures use pytest.fail rather than assert, so `python -O`
cannot turn the check into a pass.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re

import pytest

import @PKG@ as package

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATUSES = {"[ok]", "[x]", "[part]", "[ ]", "[no]"}
NEEDS_TEST = {"[ok]", "[x]", "[part]"}
TO_BE_WRITTEN = "(to be written)"
TEST_REF = re.compile(r"([\w./-]+\.py)((?:::\w+)+)")


def cells_of(line):
    bars = [i for i, ch in enumerate(line) if ch == "|" and (i == 0 or line[i - 1] != "\\")]
    return [line[bars[k] + 1 : bars[k + 1]].strip() for k in range(len(bars) - 1)]


def matrix_rows():
    """(line number, cells, header) for every data line of every obligation table."""
    lines = (ROOT / "ROADMAP.md").read_text(encoding="utf-8").splitlines()
    found = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("|"):
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].startswith("|"):
            index += 1
        table = lines[start:index]
        if len(table) < 2 or not all(set(c) <= set("-: ") and c for c in cells_of(table[1])):
            continue
        header = [h.lower() for h in cells_of(table[0])]
        if not header or header[0] not in ("obligation", "id") or "status" not in header:
            continue
        for offset, line in enumerate(table[2:], start=start + 3):
            found.append((offset, cells_of(line), header))
    return found


def status_of(cells, header):
    at = header.index("status")
    return cells[at].strip().strip("`").lower() if at < len(cells) else ""


def callable_of(cells):
    """(module, function) for a `module.function()` first cell, else None."""
    match = re.fullmatch(r"`([a-z0-9_]+)\.([a-z0-9_]+)\(\)`", cells[0]) if cells else None
    return (match.group(1), match.group(2)) if match else None


def defines(path, parts):
    """Return "" when the file defines the collectable test `parts` names, else why not."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as error:
        return f"cannot be read ({error.__class__.__name__})"
    *classes, function = parts
    if not function.startswith("test"):
        return f"names {function}, which pytest does not collect"
    scope = tree.body
    for name in classes:
        hit = next((n for n in scope if isinstance(n, ast.ClassDef) and n.name == name), None)
        if hit is None or not name.startswith("Test"):
            return f"defines no collectable class {name}"
        scope = hit.body
    if any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == function for n in scope
    ):
        return ""
    return f"defines no {function}"


def evidence_failures(cells, header):
    """Why this row's named tests do not hold, or [] when at least one holds and none is missing."""
    text = " ".join(
        cells[header.index(k)]
        for k in ("where", "evidence")
        if k in header and header.index(k) < len(cells)
    )
    held, failures = 0, []
    for found in TEST_REF.finditer(text):
        window = re.split(r"[,;]", text[found.end() :], maxsplit=1)[0]
        if TO_BE_WRITTEN in window:
            continue
        relative, parts = found.group(1), found.group(2).split("::")[1:]
        path = ROOT / relative
        why = defines(path, parts) if path.is_file() else "does not exist"
        if why:
            failures.append(f"{relative} {why}")
        else:
            held += 1
    if not held and not failures:
        failures.append(
            "names no file.py::test_name that exists (a reference marked (to be written) does not count)"
        )
    return failures


def test_matrix_has_rows():
    if not matrix_rows():
        pytest.fail("ROADMAP.md lost its obligation rows")


def test_every_status_is_one_the_portfolio_recognises():
    bad = [
        f"line {n}: {status_of(c, h)!r}"
        for n, c, h in matrix_rows()
        if status_of(c, h) not in STATUSES
    ]
    if bad:
        pytest.fail("rows with a status nobody can act on: " + ", ".join(bad))


def test_every_row_names_a_real_callable():
    missing = []
    for _, cells, _header in matrix_rows():
        target = callable_of(cells)
        if target is None:
            continue  # an id such as `cl.ambient-trust` names an obligation, not a callable
        module = importlib.import_module(package.__name__ + "." + target[0])
        if not hasattr(module, target[1]):
            missing.append(target[0] + "." + target[1])
    if missing:
        pytest.fail("matrix rows naming nothing: " + ", ".join(missing))


def test_done_and_partial_rows_name_a_test_that_exists():
    broken = []
    for number, cells, header in matrix_rows():
        if status_of(cells, header) in NEEDS_TEST:
            broken += [
                f"line {number} {cells[0]}: {why}" for why in evidence_failures(cells, header)
            ]
    if broken:
        pytest.fail("rows claiming progress without a test:\n" + "\n".join(broken))
