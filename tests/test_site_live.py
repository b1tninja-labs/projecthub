"""The live site data: obligations from the ROADMAP, live status from the Project, and only kind and time public."""

from __future__ import annotations

import pathlib
import types

from projecthub.site import live as L

MATRIX = """# demo

| Obligation | Spec | Status | Where |
|---|---|---|---|
| `core.parse()` | RFC 1 sec. 2 | `[ok]` | `tests/test_core.py::test_parse` |
| `core.verify()` | RFC 1 sec. 3 | `[ ]` | `tests/test_core.py::test_verify` |
| `core.limits()` | RFC 1 sec. 4 | `[ ]` | - |
| `core.never()` | - | `[no]` | - |
"""


def item(title: str, **values: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(title=title, values=values)


def workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "demo" / "tests").mkdir(parents=True)
    (tmp_path / "demo" / "ROADMAP.md").write_text(MATRIX, encoding="utf-8")
    (tmp_path / "demo" / "tests" / "test_core.py").write_text(
        "def test_parse():\n    assert 1\n", encoding="utf-8"
    )
    return tmp_path


def test_obligations_come_from_the_roadmap_and_leave_out_non_goals(tmp_path):
    rows = L.obligations(workspace(tmp_path), "demo")
    assert [(r["id"], r["state"]) for r in rows] == [
        ("core.parse", "done"),
        ("core.verify", "open"),
        ("core.limits", "open"),
    ]
    assert rows[0]["test"] == "tests/test_core.py::test_parse" and rows[2]["test"] == ""


def test_the_project_adds_live_status_and_claims_but_never_undoes_done(tmp_path):
    rows = L.obligations(workspace(tmp_path), "demo")
    items = [
        item(
            "[demo] core.verify(): RFC 1 sec. 3",
            Status="In progress",
            Claimed="2026-11-11",
        ),
        item(
            "[demo] core.parse(): RFC 1 sec. 2", Status="Blocked", Claimed="2026-11-09"
        ),
        item("[other] core.limits(): x", Status="Blocked"),
    ]
    activity = L.overlay("demo", rows, items)
    assert {r["id"]: r["state"] for r in rows} == {
        "core.parse": "done",
        "core.verify": "in_progress",
        "core.limits": "open",
    }
    assert activity == [
        {"at": "2026-11-11", "kind": "claim"},
        {"at": "2026-11-09", "kind": "claim"},
    ]


def test_cards_prefer_obligations_and_fall_back_to_milestones():
    obl = L.card(
        {
            "name": "a",
            "obligations_done": 2,
            "obligations_total": 5,
            "milestones_total": 3,
        },
        "t",
    )
    mil = L.card(
        {
            "name": "b",
            "obligations_total": 0,
            "milestones_done": 1,
            "milestones_total": 4,
        },
        "t",
    )
    none = L.card({"name": "c"}, "t")
    assert obl["progress"] == {"done": 2, "total": 5, "unit": "obligations"}
    assert mil["progress"] == {"done": 1, "total": 4, "unit": "milestones"}
    assert (
        none["progress"] is None and none["updated"] == "t" and none["blocked_by"] == []
    )


def test_a_private_board_is_not_linked():
    assert L.board_url(False).endswith(
        "/portfolio/issues"
    ) and "projects" in L.board_url(True)


def test_the_contract_refuses_extra_activity_fields_and_bad_urls():
    index = {
        "sections": [{"id": "s"}],
        "projects": [{"name": "a", "section": "s", "repo_url": "https://x"}],
        "board_url": "https://b",
    }
    good = {"a": {"project": {"name": "a"}, "activity": [{"at": "t", "kind": "claim"}]}}
    assert L.problems(index, good) == []
    bad = {
        "a": {
            "project": {"name": "a", "roadmap_url": "http://x"},
            "activity": [{"at": "t", "kind": "log", "text": "x"}],
        }
    }
    found = L.problems(index, bad)
    assert any("not a plain https" in p for p in found) and any(
        "more than kind and time" in p for p in found
    )
    assert any(
        "section" in p
        for p in L.problems(
            {**index, "projects": [{"name": "a", "section": "z"}]}, good
        )
    )


def test_write_removes_files_for_projects_that_left(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "gone.json").write_text("{}", encoding="utf-8")
    L.write(tmp_path, {"projects": []}, {"a": {"x": 1}})
    assert sorted(p.name for p in (tmp_path / "p").iterdir()) == ["a.json"]
