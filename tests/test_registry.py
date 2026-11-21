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
        "idnawire",
        "tufverify",
    ):
        assert name in by_name, name


def test_round_4_support_libraries_carry_their_edges():
    """PORTFOLIO.md section 9 item 15: pkgtrust -> attestkit -> pkgformats.vocab and sigwire[verify];
    advisoryformats -> pkgformats and devicespy; pkgtrust -> advisoryformats. sigwire never imports attestkit."""
    deps = {e.name: set(e.depends) for e in p.entries()}
    # the `sigstore` extra's asn1core, pkix, signeddata (round 10) and ctspy (round 12) are declared too;
    # tufverify is not yet
    assert deps["attestkit"] == {
        "pkgformats",
        "sigwire",
        "asn1core",
        "pkix",
        "signeddata",
        "ctspy",
    }
    assert "tufverify" not in deps["attestkit"]
    assert deps["advisoryformats"] == {"pkgformats", "devicespy", "attestkit"}
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


def test_asn1wire_is_the_typescript_reader_and_depends_on_nothing():
    """PORTFOLIO.md sections 3 and 4 (2026-11-17): the TypeScript DER and X.509 reader is its own
    library. It does not replace asn1core or berkit, and it imports neither."""
    entry = next(e for e in p.entries() if e.name == "asn1wire")
    assert (entry.kind, entry.tier, entry.wave, entry.directory) == (
        "Library",
        0,
        0,
        "asn1wire",
    )
    assert entry.depends == ()
    assert "TypeScript" in entry.what
    from projecthub.db import parser

    assert "asn1wire" in parser.HAND_OWNED


def test_the_bluetooth_mx_path_is_registered():
    """The Bluetooth MX map (2026-11-21): the emulator reaches fakeobd through obdhub and
    gattmirror. voltkit's lab and bench commands start fakeobd and obd-bridge. obd-bridge does
    not depend on gattmirror. techlinekit and spskit still depend on nothing. The matrix is
    `obd-bridge/ROADMAP.md`, so the name is on the Rust sync list. fakegatt's roadmap still says
    the Core Specification is absent, so fakegatt stays off that list. obdlink-re has no matrix."""
    entries = {e.name: e for e in p.entries()}
    kinds = {
        "voltkit": "Tooling",
        "techlinekit": "Library",
        "spskit": "Library",
        "gattmirror": "Library",
        "fakeobd": "Tooling",
        "obd-bridge": "Tooling",
        "obdhub": "Tooling",
        "fakegatt": "Library",
        "obdlink-re": "Reference",
    }
    for name, kind in kinds.items():
        entry = entries[name]
        assert (entry.kind, entry.tier, entry.wave, entry.directory) == (
            kind,
            0,
            0,
            name,
        )
    assert entries["voltkit"].depends == ("fakeobd", "obd-bridge")
    assert entries["obdhub"].depends == ("gattmirror", "fakeobd")
    assert entries["fakegatt"].depends == ("gattmirror", "fakeobd")
    assert entries["gattmirror"].depends == ()
    assert entries["fakeobd"].depends == ()
    assert entries["obd-bridge"].depends == ()
    assert entries["obdlink-re"].depends == ()
    assert entries["techlinekit"].depends == ()
    assert entries["spskit"].depends == ()
    from projecthub.db import parser

    assert {"spskit", "techlinekit", "projecthub"} <= set(parser.HAND_OWNED)
    assert {"gattmirror", "fakeobd", "voltkit", "obdhub", "obd-bridge"} <= set(
        parser.RUST_OWNED
    )
    assert "fakegatt" not in parser.RUST_OWNED
    assert "obdlink-re" not in parser.RUST_OWNED
    text = (pathlib.Path(__file__).parents[1] / "ROADMAP.md").read_text(
        encoding="utf-8"
    )
    rows = {row["name"]: row["status"] for row in parser.rows_of(text)}
    assert rows["bt.flags-unread"] == "[no]"
    assert rows["bt.obd-bridge-matrix"] == "[ok]"
    from projecthub import paths

    matrix = parser.matrix_path(paths.ROOT, "obd-bridge")
    assert matrix is not None and matrix.name == "ROADMAP.md" and matrix.is_file()
    parsed = parser.parse(matrix.read_text(encoding="utf-8"))
    assert parsed.problems == []
    assert parsed.rows
    assert rows["bt.fakegatt-sync"] == "[ ]"


def test_the_vehicle_readers_are_registered_and_depend_on_nothing():
    """PORTFOLIO.md section 3 (2026-11-21): the offline vehicle format and diagnostics readers are
    registry rows, so import and query see them. None imports another, so each depends on nothing.
    spswire stays a standard-library-only Python reader in HAND_OWNED (Python-gated); the four
    diagnostics crates - udskit (renamed from udsdiag), isotpkit, mode06kit and gmdtcdb - were ported
    to Rust and moved to RUST_OWNED (synced, not Python-gated)."""
    entries = {e.name: e for e in p.entries()}
    for name in ("spswire", "isotpkit", "udskit", "mode06kit", "gmdtcdb"):
        entry = entries[name]
        assert (entry.kind, entry.tier, entry.wave, entry.directory, entry.depends) == (
            "Library",
            0,
            0,
            name,
            (),
        )
    from projecthub.db import parser

    assert "spswire" in parser.HAND_OWNED
    assert {"udskit", "isotpkit", "mode06kit", "gmdtcdb"} <= set(parser.RUST_OWNED)
    assert not (
        {"udskit", "udsdiag", "isotpkit", "mode06kit", "gmdtcdb"} & set(parser.HAND_OWNED)
    )


def test_rust_crates_are_registered_with_their_path_dependencies():
    """PORTFOLIO.md sections 3 and 4 (2026-11-13): berkit and cardkit depend on nothing; piv-card depends on
    cardkit and berkit by path."""
    entries = {e.name: e for e in p.entries()}
    assert entries["berkit"].depends == ()
    assert entries["cardkit"].depends == ()
    assert entries["piv-card"].depends == ("cardkit", "berkit")
    for name in ("berkit", "cardkit", "piv-card"):
        entry = entries[name]
        assert (entry.tier, entry.wave, entry.directory) == (0, 0, name)


def test_round_8_support_libraries_are_registered_with_their_edges():
    """PORTFOLIO.md section 4: idnawire has no dependency (standard library only); tufverify depends on
    sigwire. The consumer edges are added when the consumer declares the dependency: see the round 10 test below
    (attestkit to tufverify is still absent, because attestkit's pyproject does not declare it)."""
    entries = {e.name: e for e in p.entries()}
    assert entries["idnawire"].depends == ()
    assert entries["tufverify"].depends == ("sigwire",)
    for name, phrase in (("idnawire", "IDNA 2008"), ("tufverify", "TUF client")):
        entry = entries[name]
        assert (entry.kind, entry.tier, entry.wave, entry.directory) == (
            "Library",
            0,
            0,
            name,
        )
        assert phrase in entry.what


def test_round_10_edges_follow_the_pyprojects_that_declare_them():
    """pkix and registrata declare idnawire; certledger declares pkix (for cl.dane-topmost-signed-by) and
    asn1core; signeddata declares pkix in its extras; tlswire reaches pkix, sigwire, asn1core and idnawire
    through its `verify` extra; quicmux imports no sibling."""
    entries = {e.name: e for e in p.entries()}
    assert "idnawire" in entries["pkix"].depends
    assert entries["registrata"].depends == ("idnawire",)
    assert {"asn1core", "pkix"} <= set(entries["certledger"].depends)
    assert "pkix" in entries["signeddata"].depends
    assert set(entries["tlswire"].depends) == {
        "pkix",
        "sigwire",
        "asn1core",
        "idnawire",
    }
    assert entries["quicmux"].depends == ()
    for name in ("tlswire", "quicmux"):
        entry = entries[name]
        assert (entry.kind, entry.tier, entry.wave, entry.directory) == (
            "Library",
            0,
            0,
            name,
        )
    assert "TLS 1.3" in entries["tlswire"].what
    assert "QUIC" in entries["quicmux"].what


def test_the_registry_json_position_and_the_generator_position_agree():
    """Two copies of the same table: `data/registry.json` feeds the board and `graph`, and
    `shells/data/position.py` feeds the generated docs/plan.md. An edge in one only is drawn by half the tools."""
    from projecthub.shells.data import position

    assert dict(POSITION) == dict(position.POSITION)


# (consumer, dependency, where its pyproject declares it); each was missing from the registry until round 12.
DECLARED_EDGES = [
    ("ctspy", "sigwire", "dependencies"),
    ("pkix", "sigwire", "dependencies"),
    ("revokekit", "sigwire", "dependencies"),
    ("pgpenv", "sigwire", "dependencies"),
    ("enrollkit", "sigwire", "dependencies"),
    ("signeddata", "sigwire", "the verify extra"),
    ("certledger", "ctspy", "dependencies"),
    ("attestkit", "ctspy", "the sigstore extra"),
    ("h3tunnel", "sfields", "dependencies"),
    ("advisoryformats", "attestkit", "dependencies"),
    ("lgc", "pathattr", "the codec extra"),
    ("ohttp", "namespy", "the dns extra"),
    ("routevalid", "bgpfeed", "the feeds extra"),
    ("routevalid", "bmpkit", "the feeds extra"),
]


@pytest.mark.parametrize(("consumer", "dependency", "where"), DECLARED_EDGES)
def test_an_edge_a_pyproject_declares_is_in_the_registry(consumer, dependency, where):
    deps = {e.name: set(e.depends) for e in p.entries()}
    assert dependency in deps[consumer], f"{consumer} declares {dependency} in {where}"


def test_a_generated_edge_has_a_stated_reason():
    """`dep_line` falls back to a generic sentence when DEPENDS_WHY has no entry, so an edge added to POSITION
    without one reads as a bare assertion in the generated plan."""
    from projecthub.shells.data.position import DEPENDS_WHY

    missing = sorted(
        (name, dep)
        for name, (_tier, _wave, deps) in POSITION.items()
        for dep in deps
        if (name, dep) not in DEPENDS_WHY
    )
    assert missing == [], f"edges with no DEPENDS_WHY: {missing}"


def test_the_stale_edges_removed_in_round_12_stay_removed():
    """sigwire never imported pgpenv (it names it in a docstring) and pgpenv declares sigwire[verify], so the
    old arrow was stale and, with the new one, a cycle; pkgformats and pkgtrust do not use pgpenv or kconfigpy
    as pkgformats' rows and pkgtrust's R165 say."""
    deps = {e.name: set(e.depends) for e in p.entries()}
    assert "pgpenv" not in deps["sigwire"]
    assert "pgpenv" not in deps["pkgformats"]
    assert "kconfigpy" not in deps["pkgtrust"]


def test_the_dependency_graph_has_no_cycle():
    deps = {e.name: set(e.depends) for e in p.entries()}
    done: set[str] = set()
    visiting: list[str] = []

    def visit(name: str) -> None:
        assert name not in visiting, f"cycle: {' -> '.join([*visiting, name])}"
        if name in done:
            return
        visiting.append(name)
        for dep in sorted(deps.get(name, ())):
            visit(dep)
        visiting.pop()
        done.add(name)

    for name in sorted(deps):
        visit(name)
