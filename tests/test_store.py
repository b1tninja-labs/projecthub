"""The planning database, step 1: lossless import and export of every matrix shape, one-cell edits, and self-checking markers."""

from __future__ import annotations

import pathlib

import pytest

from projecthub.db import store as F
from projecthub.db import parser as roadmap

MIXED = (
    "# demo\n"
    "\n"
    "Prose before, with a | pipe that is not a table.\n"
    "\n"
    "| Obligation | Spec | Status | Where | Milestone |\n"
    "|---|:--|---|---|---|\n"
    "| `core.parse` | RFC 1 §2 | `[ok]` | `tests/test_core.py::test_parse` | M1 |\n"
    "| `core.verify` | RFC 1 §3 | ✅ (storage) | `tests/test_core.py::test_verify` (to be written) | M2 |\n"
    "| `core.limits`|RFC 1 §4|[part] see below|  `pkg/core.py`  |M2|\n"
    "| `core.nope` | - | ▫️ | - | - |\n"
    "\n"
    "## Notes\n"
    "\n"
    "| Not | a matrix |\n"
    "|---|---|\n"
    "| x | y |\n"
    "\n"
    "Snippet with no header:\n"
    "| `snip.one` | RFC 2 | [ ] | `tests/test_s.py::test_one` | |\n"
    "trailing prose, no final newline"
)


def _store(text: str = MIXED, project: str = "demo") -> F.Store:
    s = F.Store()
    s.import_text(project, "ROADMAP.md", text)
    return s


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_every_shape_round_trips_byte_for_byte(eol):
    text = MIXED.replace("\n", eol)
    s = _store(text)
    assert s.export_text("demo") == text
    kinds = [seg.kind for seg in s.segments("demo")]
    assert kinds.count("matrix") == 2, (
        "the header matrix and the header-less snippet; the Notes table is prose"
    )
    assert [r["name"] for r in s.rows("demo")] == [
        r["name"] for r in roadmap.rows_of(text)
    ]


def test_a_status_change_is_a_one_cell_diff_that_keeps_ticks_notes_and_spacing():
    s = _store()
    s.set_field("demo", "core.parse", "status", "[part]")
    s.set_field("demo", "core.verify", "status", "[ok]")
    s.set_field("demo", "core.limits", "status", "[ok]")
    out = s.export_text("demo").splitlines()
    before = MIXED.splitlines()
    changed = [(a, b) for a, b in zip(before, out, strict=True) if a != b]
    assert [b for _, b in changed] == [
        "| `core.parse` | RFC 1 §2 | `[part]` | `tests/test_core.py::test_parse` | M1 |",
        "| `core.verify` | RFC 1 §3 | [ok] (storage) | `tests/test_core.py::test_verify` (to be written) | M2 |",
        "| `core.limits`|RFC 1 §4|[ok] see below|  `pkg/core.py`  |M2|",
    ]
    assert {r["name"]: r["status"] for r in roadmap.rows_of("\n".join(out))}[
        "core.limits"
    ] == "[ok]"


def test_any_named_column_can_change_and_a_missing_one_is_refused():
    s = _store()
    s.set_field("demo", "core.limits", "where", "`tests/test_core.py::test_limits`")
    s.set_field("demo", "snip.one", "evidence", "`tests/test_s.py::test_one`")
    rows = {r["name"]: r for r in roadmap.rows_of(s.export_text("demo"))}
    assert rows["core.limits"]["where"] == "`tests/test_core.py::test_limits`"
    assert rows["snip.one"]["evidence"] == "`tests/test_s.py::test_one`", (
        "header-less rows use the historical positions"
    )
    with pytest.raises(LookupError, match="no evidence column"):
        s.set_field("demo", "core.parse", "evidence", "x")
    with pytest.raises(LookupError, match="0 rows"):
        s.set_field("demo", "core.absent", "status", "[ok]")


def test_markers_wrap_each_region_and_import_back_to_the_same_bytes():
    s = _store()
    marked = s.export_text("demo", mark=True)
    assert marked.count("<!-- projecthub:matrix begin rev=0 sha256=") == 2
    assert "forge:matrix" not in marked, "a new export writes only the new marker"
    assert F.check_markers(marked) == []
    again = F.Store()
    again.import_text("demo", "ROADMAP.md", marked)
    assert again.export_text("demo") == marked, "a marked file keeps its markers"
    assert again.export_text("demo", mark=False) == MIXED, "and they come off cleanly"


def _legacy(text: str) -> str:
    """`text` as an export from before the rename marked it."""
    return text.replace("<!-- projecthub:matrix ", "<!-- forge:matrix ")


def test_a_file_with_the_old_markers_round_trips_byte_for_byte(tmp_path):
    old = _legacy(_store().export_text("demo", mark=True))
    assert old.count("<!-- forge:matrix begin rev=0 sha256=") == 2
    assert F.check_markers(old) == [], "the old markers still seal their regions"
    for store in (F.Store(), F.Store(tmp_path / "persisted.db")):
        store.import_text("demo", "ROADMAP.md", old)
        assert store.export_text("demo") == old, (
            "unchanged regions keep the old markers"
        )
        assert store.export_text("demo", mark=False) == MIXED
    assert [s.legacy for s in F.split(old) if s.kind == "matrix"] == [True, True]


def test_a_changed_region_from_an_old_file_is_remarked_with_the_new_tag():
    s = _store(_legacy(_store().export_text("demo", mark=True)))
    s.set_field("demo", "core.parse", "status", "[part]")
    out = s.export_text("demo")
    assert out.count("<!-- projecthub:matrix begin rev=1 sha256=") == 1
    assert out.count("<!-- forge:matrix begin rev=0 sha256=") == 1, (
        "the untouched region keeps its old marker"
    )
    assert F.check_markers(out) == []


def test_a_hand_edit_inside_an_old_region_is_still_caught():
    edited = _legacy(_store().export_text("demo", mark=True)).replace(
        "| `core.parse` | RFC 1 §2 | `[ok]`", "| `core.parse` | RFC 1 §2 | `[part]`"
    )
    (problem,) = F.check_markers(edited)
    assert "edited by hand" in problem


def test_a_hand_edit_inside_a_region_is_caught_from_the_file_alone():
    marked = _store().export_text("demo", mark=True)
    edited = marked.replace(
        "| `core.parse` | RFC 1 §2 | `[ok]`", "| `core.parse` | RFC 1 §2 | `[part]`"
    )
    (problem,) = F.check_markers(edited)
    assert "edited by hand" in problem and "projecthub tools" in problem
    prose_edit = marked.replace("Prose before", "Prose, edited, before")
    assert F.check_markers(prose_edit) == [], (
        "prose outside the markers stays hand-owned"
    )


def test_an_exported_change_bumps_rev_and_reseals_the_hash():
    s = _store(_store().export_text("demo", mark=True))
    s.set_field("demo", "core.parse", "status", "[part]")
    out = s.export_text("demo")
    assert "rev=1 sha256=" in out and "rev=0 sha256=" in out, (
        "only the changed region's rev moves"
    )
    assert F.check_markers(out) == []


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (
            "<!-- projecthub:matrix begin rev=0 sha256=" + "0" * 64 + " -->\n| a |\n",
            "no end marker",
        ),
        ("<!-- projecthub:matrix end -->\n", "no begin marker"),
        (
            "<!-- forge:matrix begin rev=0 sha256=" + "0" * 64 + " -->\n| a |\n",
            "no end marker",
        ),
        ("<!-- forge:matrix end -->\n", "no begin marker"),
    ],
)
def test_unbalanced_markers_are_reported(text, fragment):
    assert any(fragment in p for p in F.check_markers(text))


def test_the_cli_checks_marker_files(tmp_path, capsys):
    good, bad = tmp_path / "good.md", tmp_path / "bad.md"
    marked = _store().export_text("demo", mark=True)
    good.write_bytes(marked.encode("utf-8"))
    bad.write_bytes(marked.replace("RFC 1 §3", "RFC 1 §9").encode("utf-8"))
    assert F.main(["check-markers", str(good)]) == 0
    assert F.main(["check-markers", str(good), str(bad)]) == 1
    assert "bad.md" in capsys.readouterr().err


WORKSPACE = F.ROOT / "PORTFOLIO.md"


@pytest.mark.skipif(not WORKSPACE.is_file(), reason="needs the D:\\code workspace")
def test_every_real_matrix_in_the_workspace_round_trips_byte_for_byte():
    results = F.roundtrip(F.ROOT)
    assert len(results) >= 30, results
    bad = [r for r in results if not (r["identical"] and r["marked_roundtrip"])]
    assert bad == []


def test_the_store_persists_to_a_file(tmp_path: pathlib.Path):
    path = tmp_path / "projecthub.db"
    s = F.Store(path)
    s.import_text("demo", "ROADMAP.md", MIXED)
    s.set_field("demo", "core.parse", "status", "[part]")
    s.db.close()
    reopened = F.Store(path)
    assert reopened.projects() == ["demo"]
    assert {r["name"]: r["status"] for r in reopened.rows("demo")}[
        "core.parse"
    ] == "[part]"
