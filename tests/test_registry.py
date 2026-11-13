"""The registry, as data: what it holds and who is allowed to be in it.

`data/registry.json` is the one list the board, the site export, the issue sync and the five gates all read,
so a name missing from it is a project nothing counts, and a name in it that nothing else knows is a project
whose directory, tier and wave nobody decided. Both directions of that drift happened during the port out of
sdk-forge: `nistlib` and `usblib` were in the old tree's `verify.PROJECTS` and in its `portfolio.EXTRA`, and
neither survived into the registry, so two gated projects were registered nowhere. These tests are what keeps
the third occurrence from being silent.

Nothing here touches the network or a git repository: `entries()` is POSITION plus EXTRA, both in-process.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from projecthub import registry as p
from projecthub.data import POSITION, PROJECTS
from projecthub.shells import generate

REGISTRY = pathlib.Path(p.__file__).parent / "data" / "registry.json"


def test_the_registry_file_holds_projects_and_position_and_nothing_else():
    """The shape `projecthub.data` unpacks without a KeyError, pinned on the bytes rather than the loader."""
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert set(raw) >= {"projects", "position"}
    assert all(isinstance(name, str) and name for name in raw["projects"])
    assert len(raw["projects"]) == len(set(raw["projects"])), (
        "a duplicated name would be gated twice and listed once"
    )
    for name, value in raw["position"].items():
        assert isinstance(value, list) and len(value) == 3, name
        tier, wave, deps = value
        assert isinstance(tier, int) and isinstance(wave, int), name
        assert isinstance(deps, list) and all(isinstance(d, str) for d in deps), name


def test_every_registry_name_is_a_generated_shell_or_is_in_extra():
    """The drift this closes: a name the registry carries that nothing positions.

    POSITION is what `shells generate` writes docs/plan.md from, and EXTRA is where the rest of the portfolio
    records its kind, tier, wave, directory and source. A name in neither has no tier, so the board files it
    under nothing and the site export cannot place it - which is how nistlib and usblib stayed invisible after
    the move.
    """
    positioned = set(POSITION) | set(p.EXTRA)
    orphans = sorted(set(PROJECTS) - positioned)
    assert orphans == [], f"registered but positioned nowhere: {orphans}"


def test_archived_reference_libraries_are_gone_and_primarysource_holds_them():
    """itulib, nistlib and usblib moved into primarysource's `itu`, `nist` and `usb` collections and their
    repositories were archived on 2026-11-13 (PORTFOLIO.md s9 items 13 and 15): nothing gates, syncs or
    resolves them any more, and an edge that pointed at one points at primarysource."""
    archived = {"itulib", "nistlib", "usblib"}
    assert not archived & set(PROJECTS)
    entries = {e.name: e for e in p.entries()}
    assert not archived & set(entries)
    assert not archived & {d for e in entries.values() for d in e.depends}
    assert not archived & set(p.directories())
    core = entries["primarysource"]
    assert core.kind == "Reference"
    assert (core.tier, core.wave) == (0, 0)
    assert "collections (rfc, itu, nist, usb" in core.what
    assert "archived 2026-11-13" in core.source
    assert "primarysource" in entries["miblang"].depends


def test_every_name_a_dependency_arrow_names_is_registered():
    """An arrow to an unregistered project renders as absent forever, because nothing resolves it."""
    registered = {e.name for e in p.entries()}
    dangling = sorted(
        {d for e in p.entries() for d in e.depends} - registered,
    )
    assert dangling == [], f"dependencies outside the registry: {dangling}"


def test_a_project_directory_is_unique_and_non_empty():
    folders = [e.directory for e in p.entries()]
    assert all(folders), "an empty directory would resolve to the workspace root itself"
    seen: dict[str, str] = {}
    for entry in p.entries():
        seen.setdefault(entry.directory, []).append(entry.name)
    shared = {d: names for d, names in seen.items() if len(names) > 1}
    assert shared == {}, f"two projects share one checkout directory: {shared}"


def test_directories_names_the_projects_whose_folder_is_named_otherwise():
    """registry.json has no directory field, so this is the only place the mapping exists.

    Without it `registrata` resolves to `D:/code/registrata`, which does not exist, and the name reads as absent
    even though `pyrwhois/` holds code and a matrix on disk.
    """
    got = p.directories()
    assert got["registrata"] == "pyrwhois"
    assert all(name != folder for name, folder in got.items()), (
        "a project named after its own directory needs no entry"
    )


@pytest.mark.parametrize("name", ["registrata"])
def test_a_registered_project_resolves_to_the_directory_on_disk(
    name, monkeypatch, tmp_path
):
    """state() and github_repo() go through Entry.directory, so that field is what the disk is read at."""
    (tmp_path / p.directories()[name]).mkdir()
    monkeypatch.setattr(p, "github_repo", lambda root, directory: "")
    entry = next(e for e in p.entries() if e.name == name)
    assert p.state(tmp_path, entry).label != "Absent"


def test_an_absent_project_is_reported_absent_rather_than_crashing():
    """A registered project with no directory yet (localns, keyproof) is a plan, not an error."""
    monkeypatched = p.Entry("nowhere", "Plan", 5, 4, "nowhere", "", "s4")
    state = p.state(pathlib.Path(__file__), monkeypatched)
    assert state.label == "Absent"
    assert (state.rows, state.done, state.matrix) == (0, 0, "")


def test_the_generated_shells_are_all_registered():
    """generate.PROJECT_ORDER is what a run writes; anything there must be in the registry it is gated from."""
    assert set(generate.PROJECT_ORDER) <= set(PROJECTS)


def test_portfolio_item_15_edges_are_in_the_registry():
    """PORTFOLIO.md section 9 item 15: the edges the generated plans and `graph` must show."""
    deps = {e.name: set(e.depends) for e in p.entries()}
    for consumer in ("asnkit", "bmpkit", "bgpfeed", "routevalid"):
        assert "pathattr" in deps[consumer], consumer
    assert {"pkix", "signeddata", "sigwire"} <= deps["rpkit"]
    assert "devicespy" in deps["cwmp"]
    assert "signeddata" in deps["enrollkit"]
    assert "pkix" in deps["shaken"]
    assert "asn1core" not in deps["srtpkit"]


def test_extra_entries_carry_their_dependencies_into_the_graph():
    by_name = {e.name: e for e in p.entries()}
    assert by_name["gitobj"].depends == ("archivekit",)
    assert by_name["archivekit"].depends == ()
    for name in (
        "pkgtrust",
        "primarysource",
        "archivekit",
        "sigwire",
        "gitobj",
        "pkgformats",
        "distbuild",
        "attestkit",
        "advisoryformats",
    ):
        assert name in by_name, name


def test_round_4_support_libraries_carry_their_edges():
    """PORTFOLIO.md section 9 item 15: pkgtrust -> attestkit -> pkgformats.vocab and sigwire[verify];
    advisoryformats -> pkgformats and devicespy; pkgtrust -> advisoryformats. sigwire never imports attestkit."""
    deps = {e.name: set(e.depends) for e in p.entries()}
    assert deps["attestkit"] == {"pkgformats", "sigwire"}
    assert deps["advisoryformats"] == {"pkgformats", "devicespy"}
    assert {"attestkit", "advisoryformats"} <= deps["pkgtrust"]
    assert "attestkit" not in deps["sigwire"]
    for name in ("attestkit", "advisoryformats"):
        entry = next(e for e in p.entries() if e.name == name)
        assert (entry.kind, entry.tier, entry.wave, entry.directory) == (
            "Library",
            0,
            0,
            name,
        )
