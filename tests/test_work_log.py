"""The work log: notes, parking, proposals and submissions, and the outbox that carries them to the board.

The outbox is written in the transaction of the event that causes it, coalesces per item, and never carries
note text. Nothing here writes a ROADMAP or marks a row done.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from projecthub.mcp import work as L

SECRET = "the private resume note 7f3a"
SHA = "0123456789abcdef0123456789abcdef01234567"
BOARD_FIELDS = {"Status", "Claimed", "Agent", "Last activity"}


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(tmp_path):
    clock = Clock()
    return L.Store(tmp_path / "leases.sqlite", now=clock), clock


def _rows(s: L.Store, table: str) -> list[sqlite3.Row]:
    db = sqlite3.connect(s.path)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()  # noqa: S608
    finally:
        db.close()


def _board(s: L.Store, key: str) -> dict[str, object]:
    found = [x for x in s.pending() if x["sink"] == "board" and x["key"] == key]
    assert len(found) == 1, found
    return found[0]


def test_acquire_queues_the_claim_for_the_board_in_one_write(store):
    s, clock = store
    s.acquire("pkix", "a.b", "agent-1")
    item = _board(s, "pkix:a.b")
    assert item["payload"] == {
        "row_key": "pkix:a.b",
        "fields": {
            "Status": "In progress",
            "Claimed": "1970-01-12",
            "Agent": "agent-1",
            "Last activity": f"acquire {L._iso(clock.t)}",
        },
    }
    assert item["attempts"] == 0


def test_the_outbox_row_rolls_back_with_its_event(store, monkeypatch):
    s, _ = store
    real = L.Store.enqueue

    def enqueue_then_fail(self, db, sink, key, payload):  # type: ignore[no-untyped-def]
        real(self, db, sink, key, payload)
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
        raise RuntimeError("crash after the outbox write, before COMMIT")

    monkeypatch.setattr(L.Store, "enqueue", enqueue_then_fail)
    with pytest.raises(RuntimeError, match="crash"):
        s.acquire("pkix", "a.b")
    assert _rows(s, "outbox") == []
    assert _rows(s, "events") == []
    assert s.live_keys() == set(), "the lease went with them: one transaction"


def test_repeated_updates_to_one_row_stay_one_pending_write(store):
    s, clock = store
    lease = s.acquire("pkix", "a.b", "agent-1")
    for n in range(5):
        clock.t += 10
        s.note(lease.lease_id, "progress", f"step {n}")
    clock.t += 10
    s.submit(lease.lease_id, SHA)
    assert len(_rows(s, "outbox")) == 1
    item = _board(s, "pkix:a.b")
    assert item["payload"]["fields"] == {
        "Status": "In review",
        "Claimed": "1970-01-12",
        "Agent": "agent-1",
        "Last activity": f"submit {L._iso(clock.t)}",
    }, "a later update keeps the fields an earlier one set"

    stale = item["payload"]
    clock.t += 10
    s.note(lease.lease_id, "finding", "one more")
    assert s.mark_done(int(item["id"]), stale) is False, (
        "a write coalesced after it was read is not lost by marking the old one done"
    )
    fresh = _board(s, "pkix:a.b")
    assert s.mark_done(int(fresh["id"]), fresh["payload"]) is True
    assert s.pending() == []

    clock.t += 10
    s.note(lease.lease_id, "progress", "after the drain")
    again = _board(s, "pkix:a.b")
    assert again["id"] == fresh["id"], "the item keeps its outbox row"
    assert set(again["payload"]["fields"]) == {"Last activity"}, (
        "a drained write is replaced, not merged"
    )
    s.retry_later(int(again["id"]), 60)
    assert s.pending() == [], "not offered again before its retry time"
    clock.t += 61
    assert _board(s, "pkix:a.b")["attempts"] == 1
    assert s.mark_done(int(again["id"])) is True


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        ("handoff", "Ready"),
        ("abandoned", "Ready"),
        ("parked", "Ready"),
        ("pr_opened", "In review"),
    ],
)
def test_each_release_outcome_sets_its_board_status(store, outcome, status):
    s, clock = store
    lease = s.acquire("pkix", "a.b")
    clock.t += 5
    s.release(lease.lease_id, outcome, note="resume at x", pr_url="https://x/pull/1")
    fields = _board(s, "pkix:a.b")["payload"]["fields"]
    assert fields["Status"] == status
    assert fields["Last activity"] == f"release:{outcome} {L._iso(clock.t)}"


def test_block_expire_and_steal_set_the_board_status(store):
    s, clock = store
    s.block(s.acquire("pkix", "a.b").lease_id, "env", "no network")
    assert _board(s, "pkix:a.b")["payload"]["fields"]["Status"] == "Blocked"
    s.acquire("pkix", "c.d", ttl=60)
    clock.t += 61
    assert s.live_keys() == set()
    assert _board(s, "pkix:c.d")["payload"]["fields"]["Status"] == "Ready"
    s.steal(s.acquire("pkix", "e.f").lease_id, "a person ended it")
    assert _board(s, "pkix:e.f")["payload"]["fields"]["Status"] == "Ready"


def test_no_payload_carries_note_block_or_release_text(store):
    s, _ = store
    lease = s.acquire("pkix", "a.b", "agent-1")
    s.note(lease.lease_id, "finding", SECRET, refs=[f"file {SECRET}"])
    s.submit(lease.lease_id, SHA)
    s.park(lease.lease_id, SECRET)
    s.release(s.acquire("pkix", "c.d", "agent-1").lease_id, "handoff", note=SECRET)
    s.block(s.acquire("pkix", "e.f", "agent-1").lease_id, "spec_question", SECRET)
    s.propose("pkix", "a title", SECRET, "bug", spec=SECRET, found_in=SECRET)
    outbox = _rows(s, "outbox")
    assert len(outbox) == 4
    for row in outbox:
        assert SECRET not in row["payload"], row["payload"]
        payload = json.loads(row["payload"])
        if "row_key" in payload:
            assert set(payload) == {"row_key", "fields"}
            assert set(payload["fields"]) <= BOARD_FIELDS
    assert SECRET in json.dumps(s.log("pkix:a.b")), "the text stays in the local log"


def test_a_note_is_lease_scoped_and_extends_the_lease(store):
    s, clock = store
    lease = s.acquire("pkix", "a.b", ttl=60)
    with pytest.raises(L.LeaseError, match="BAD_KIND"):
        s.note(lease.lease_id, "done", "x")
    with pytest.raises(L.LeaseError, match="TEXT_REQUIRED"):
        s.note(lease.lease_id, "progress", "  ")
    clock.t += 50
    got = s.note(lease.lease_id, "test_written", "covers the edge", refs=["tests/x.py"])
    assert got["expires_at"] == L._iso(clock.t + 60)
    clock.t += 50
    assert s.live_keys() == {"pkix:a.b"}, "the note renewed it"
    clock.t += 11
    with pytest.raises(L.LeaseError, match="LEASE_EXPIRED"):
        s.note(lease.lease_id, "progress", "too late")
    with pytest.raises(L.LeaseError, match="LEASE_UNKNOWN"):
        s.note("not-a-lease", "progress", "x")
    entry = s.log("pkix:a.b")[0]
    assert entry["kind"] == "note:test_written"
    assert entry["text"] == "covers the edge" and entry["refs"] == ["tests/x.py"]


def test_parking_needs_a_note_and_pins_it_for_the_next_holder(store):
    s, clock = store
    lease = s.acquire("pkix", "a.b")
    with pytest.raises(L.LeaseError, match="NOTE_REQUIRED"):
        s.release(lease.lease_id, "parked")
    with pytest.raises(L.LeaseError, match="NOTE_REQUIRED"):
        s.park(lease.lease_id, " ")
    s.note(lease.lease_id, "progress", "halfway")
    assert s.last_handoff("pkix:a.b")["text"] == "halfway"
    clock.t += 1
    assert s.park(lease.lease_id, "resume at the DER decoder") == "released"
    assert s.live_keys() == set()
    pinned = s.last_handoff("pkix:a.b")
    assert pinned is not None
    assert pinned["kind"] == "release:parked"
    assert pinned["text"] == "resume at the DER decoder"
    again = s.acquire("pkix", "a.b")
    s.release(again.lease_id, "abandoned")
    assert s.last_handoff("pkix:a.b")["text"] == "resume at the DER decoder", (
        "a release without a note does not hide the last one"
    )
    assert s.last_handoff("pkix:none") is None


def test_one_live_lease_per_holder_by_default(store):
    s, _ = store
    first = s.acquire("pkix", "a.b", "agent-1")
    with pytest.raises(
        L.LeaseError, match="HOLDER_BUSY: agent-1 already holds pkix:a.b"
    ):
        s.acquire("pkix", "c.d", "agent-1")
    s.acquire("pkix", "c.d", "agent-2")
    s.acquire("pkix", "e.f", "agent-1", max_live=2)
    s.release(first.lease_id, "handoff")
    s.acquire("pkix", "g.h", "agent-1", max_live=2)


def test_a_proposal_waits_in_triage_for_the_owner(store):
    s, _ = store
    with pytest.raises(L.LeaseError, match="BAD_KIND"):
        s.propose("pkix", "t", "w", "feature")
    with pytest.raises(L.LeaseError, match="WHY_REQUIRED"):
        s.propose("pkix", "t", " ", "bug")
    with pytest.raises(L.LeaseError, match="TITLE_REQUIRED"):
        s.propose("pkix", "", "w", "bug")
    got = s.propose(
        "pkix", "Reject a negative serial", "RFC 5280 says so", "obligation", "§4.1.2.2"
    )
    assert got == {"proposal_id": "pkix:1", "state": "open", "status": "Triage"}
    s.propose(
        "pkix",
        "Which clause?",
        "unclear",
        "question",
        found_in="pkix:a.b",
        private=True,
    )
    stored = _rows(s, "proposals")
    assert [(r["kind"], r["state"]) for r in stored] == [
        ("obligation", "open"),
        ("question", "open"),
    ]
    assert stored[0]["spec"] == "§4.1.2.2" and stored[1]["found_in"] == "pkix:a.b"
    assert [r["private"] for r in stored] == [0, 1]
    item = _board(s, "proposal:pkix:1")
    assert item["payload"] == {
        "proposal_id": "pkix:1",
        "project": "pkix",
        "fields": {"Status": "Triage"},
        "title": "Reject a negative serial",
        "kind": "obligation",
    }
    assert _board(s, "proposal:pkix:2")["payload"]["private"] is True


@pytest.mark.parametrize(
    "sha", ["", "main", SHA[:7], SHA + "0", SHA[:-1] + "g", "v1.0.0", f" {SHA[:39]} "]
)
def test_submit_refuses_anything_but_a_full_commit_id(store, sha):
    s, _ = store
    lease = s.acquire("pkix", "a.b")
    with pytest.raises(L.LeaseError, match="BAD_SHA"):
        s.submit(lease.lease_id, sha)
    assert _rows(s, "submissions") == []


def test_submit_queues_a_check_and_runs_nothing(store):
    s, _ = store
    lease = s.acquire("pkix", "a.b")
    got = s.submit(lease.lease_id, SHA.upper())
    assert got == {
        "submission_id": 1,
        "row_key": "pkix:a.b",
        "sha": SHA,
        "state": "queued",
    }
    assert [x["sha"] for x in s.queued_submissions()] == [SHA]
    assert s.log("pkix:a.b")[0]["kind"] == "submit"
    assert s.live_keys() == {"pkix:a.b"}, "submitting does not end the lease"
    s.release(lease.lease_id, "handoff")
    with pytest.raises(L.LeaseError, match="LEASE_ENDED"):
        s.submit(lease.lease_id, SHA)


def test_a_store_from_before_refs_gains_the_column(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, lease_id TEXT, "
        "row_key TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);"
        "INSERT INTO events(at, lease_id, row_key, kind, detail) VALUES (1, 'x', 'pkix:a.b', 'acquire', '');"
    )
    db.close()
    s = L.Store(path)
    assert s.log("pkix:a.b")[0]["refs"] == []
    L.Store(path)  # a second open does not add it twice


def test_the_projecthub_variable_names_the_store_first(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_LEASES_DB", str(tmp_path / "old.sqlite"))
    assert L.Store().path == tmp_path / "old.sqlite"
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(tmp_path / "new.sqlite"))
    assert L.Store().path == tmp_path / "new.sqlite"


def test_the_new_tools_have_schemas_never_destroy_and_never_write_a_roadmap(
    store, tmp_path, monkeypatch
):
    pytest.importorskip("mcp")
    import anyio
    from mcp.server.mcpserver.exceptions import ToolError

    from projecthub import paths

    workspace = tmp_path / "workspace"
    (workspace / "pkix").mkdir(parents=True)
    roadmap = workspace / "pkix" / "ROADMAP.md"
    roadmap.write_text("| `a.b` | [ ] | [ ] | w |\n", encoding="utf-8")
    monkeypatch.setattr(paths, "ROOT", workspace)
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}

    s, _ = store
    app = L.build(s, lambda project, row: "")
    listed = anyio.run(app.list_tools)
    tools = {t.name: t for t in getattr(listed, "tools", listed)}
    for name in ("note", "park", "propose", "submit", "log"):
        assert tools[name].output_schema, name
        assert tools[name].annotations.destructive_hint is False, name
        assert "write" not in tools[name].input_schema.get("properties", {}), name

    def call(name, args):  # type: ignore[no-untyped-def]
        return anyio.run(app.call_tool, name, args).structured_content

    lease = call("forge_lease_acquire", {"project": "pkix", "row_id": "a.b"})[
        "lease_id"
    ]
    got = call(
        "note", {"lease_id": lease, "kind": "finding", "text": "x", "refs": ["y"]}
    )
    assert got["row_key"] == "pkix:a.b"
    with pytest.raises(ToolError, match="BAD_SHA"):
        call("submit", {"lease_id": lease, "sha": "main"})
    assert call("submit", {"lease_id": lease, "sha": SHA})["state"] == "queued"
    with pytest.raises(ToolError, match="NOTE_REQUIRED"):
        call("park", {"lease_id": lease, "note": ""})
    assert (
        call("park", {"lease_id": lease, "note": "resume here"})["state"] == "released"
    )
    proposed = call(
        "propose", {"project": "pkix", "title": "t", "why": "w", "kind": "task"}
    )
    assert proposed["status"] == "Triage"
    log = call("log", {"row_key": "pkix:a.b"})
    assert log["last_handoff"]["text"] == "resume here"
    assert [e["kind"] for e in log["events"]][:3] == [
        "release:parked",
        "submit",
        "note:finding",
    ]

    after = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert after == before, (
        "no work tool writes a ROADMAP or anything else in the workspace"
    )


def test_the_command_line_offers_every_new_call(tmp_path, monkeypatch, capsys):
    path = tmp_path / "leases.sqlite"
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(path))
    lease = L.Store(path).acquire("pkix", "a.b", "agent-1").lease_id

    assert (
        L.main(["note", lease, "progress", "wrote", "the", "parser", "--ref", "a.py"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["kind"] == "progress"
    assert L.main(["note", lease, "progress", " "]) == 1
    assert "TEXT_REQUIRED" in capsys.readouterr().err

    assert L.main(["submit", lease, "abc123"]) == 1
    assert "BAD_SHA" in capsys.readouterr().err
    assert L.main(["submit", lease, SHA]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "queued"

    assert L.main(["outbox"]) == 0
    pending = json.loads(capsys.readouterr().out)
    assert [x["key"] for x in pending] == ["pkix:a.b"]

    assert L.main(["park", lease, "resume", "at", "step", "3"]) == 0
    assert capsys.readouterr().out.strip() == "released"

    assert (
        L.main(
            ["propose", "pkix", "bug", "Crash", "on", "empty", "--why", "seen in a.b"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "Triage"
    with pytest.raises(SystemExit):
        L.main(["propose", "pkix", "bug", "No why"])
    capsys.readouterr()

    assert L.main(["log", "pkix:a.b", "--limit", "2"]) == 0
    log = json.loads(capsys.readouterr().out)
    assert log["last_handoff"]["text"] == "resume at step 3"
    assert len(log["events"]) == 2

    other = L.Store(path).acquire("pkix", "c.d", "agent-1").lease_id
    assert L.main(["release", other, "parked"]) == 1
    assert "NOTE_REQUIRED" in capsys.readouterr().err
    assert L.main(["release", other, "parked", "--note", "later"]) == 0


def test_pending_offers_only_due_unfinished_writes_oldest_first(store):
    """The drain's contract: due (next_at <= now), not done, oldest first, payload already a dict."""
    s, clock = store
    for row in ("a.b", "c.d", "e.f"):
        s.release(s.acquire("pkix", row).lease_id, "handoff")
    first, second, third = s.pending()
    assert [x["key"] for x in (first, second, third)] == [
        "pkix:a.b",
        "pkix:c.d",
        "pkix:e.f",
    ]
    for item in (first, second, third):
        assert {"id", "sink", "key", "payload", "attempts"} <= set(item)
        assert isinstance(item["payload"], dict) and item["sink"] == "board"
    assert s.mark_done(int(first["id"]))
    s.retry_later(int(second["id"]), 30)
    assert [x["key"] for x in s.pending()] == ["pkix:e.f"]
    assert [x["key"] for x in s.pending(limit=1)] == ["pkix:e.f"]
    clock.t += 30
    assert [x["key"] for x in s.pending()] == ["pkix:c.d", "pkix:e.f"]


def test_retry_later_counts_the_attempt_and_waits_the_delay(store):
    s, clock = store
    s.acquire("pkix", "a.b")
    item = s.pending()[0]
    s.retry_later(int(item["id"]), 45)
    row = _rows(s, "outbox")[0]
    assert row["attempts"] == 1 and row["next_at"] == clock.t + 45
    clock.t += 45
    s.retry_later(int(item["id"]), 90)
    row = _rows(s, "outbox")[0]
    assert row["attempts"] == 2 and row["next_at"] == clock.t + 90


def test_the_store_path_is_public_so_the_drain_can_share_the_file(tmp_path):
    path = tmp_path / "leases.sqlite"
    s = L.Store(path)
    assert s.path == path
    db = sqlite3.connect(s.path)
    db.execute("CREATE TABLE IF NOT EXISTS drain_state (k TEXT PRIMARY KEY, v TEXT)")
    db.commit()
    db.close()
    s.acquire("pkix", "a.b")
    assert L.Store(path).pending()[0]["key"] == "pkix:a.b"


def test_a_lease_names_its_branch_and_an_old_store_gains_the_column(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE leases (lease_id TEXT PRIMARY KEY, row_key TEXT NOT NULL, holder TEXT NOT NULL, "
        "scope_files TEXT NOT NULL, ttl REAL NOT NULL, acquired_at REAL NOT NULL, expires_at REAL NOT NULL, "
        "state TEXT NOT NULL, outcome TEXT, note TEXT, pr_url TEXT);"
        "INSERT INTO leases VALUES ('old', 'pkix:z.z', 'a', '[]', 60, 1e12, 1e12, 'live', NULL, NULL, NULL);"
    )
    db.close()
    s = L.Store(path)
    assert {x.row_key: x.branch for x in s.live()} == {"pkix:z.z": ""}
    lease = s.acquire("pkix", "a.b", "agent-1", branch="agents/a-b")
    assert lease.branch == "agents/a-b"
    stored = {r["lease_id"]: r["branch"] for r in _rows_by(s, "leases")}
    assert stored == {"old": None, lease.lease_id: "agents/a-b"}
    assert s.acquire("pkix", "c.d", "agent-2").branch == ""


def _rows_by(s: L.Store, table: str) -> list[sqlite3.Row]:
    db = sqlite3.connect(s.path)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
    finally:
        db.close()


def test_the_submissions_table_is_the_evidence_workers_shape(store):
    s, clock = store
    db = sqlite3.connect(s.path)
    try:
        columns = [
            (r[1], r[2], bool(r[3]), r[5])
            for r in db.execute("PRAGMA table_info(submissions)")
        ]
        sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='submissions'"
        ).fetchone()[0]
    finally:
        db.close()
    assert columns == [
        ("id", "INTEGER", False, 1),
        ("row_key", "TEXT", True, 0),
        ("sha", "TEXT", True, 0),
        ("lease_id", "TEXT", False, 0),
        ("at", "REAL", True, 0),
        ("state", "TEXT", True, 0),
    ]
    assert "AUTOINCREMENT" in sql
    s.submit(s.acquire("pkix", "a.b").lease_id, SHA)
    row = _rows(s, "submissions")[0]
    assert (row["state"], row["at"]) == ("queued", clock.t)


def test_acquire_takes_a_branch_over_mcp_and_the_command_line(
    store, tmp_path, monkeypatch, capsys
):
    pytest.importorskip("mcp")
    import anyio

    s, _ = store
    app = L.build(s, lambda project, row: "")
    got = anyio.run(
        app.call_tool,
        "forge_lease_acquire",
        {"project": "pkix", "row_id": "a.b", "branch": "agents/a-b"},
    ).structured_content
    assert got["branch"] == "agents/a-b"

    path = tmp_path / "cli.sqlite"
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(path))
    monkeypatch.setattr(
        L, "_ready_from_planner", lambda p, r: "" if r == "a.b" else "not ready"
    )
    assert L.main(["acquire", "pkix", "x.y"]) == 1
    assert "NOT_READY: not ready" in capsys.readouterr().err
    assert (
        L.main(["acquire", "pkix", "a.b", "--holder", "h", "--branch", "agents/w"]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["branch"] == "agents/w" and out["row_key"] == "pkix:a.b"
    assert L.Store(path).held("pkix:a.b").branch == "agents/w"
