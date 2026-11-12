"""Tests for the tool that decides which obligations become work items.

The forge had no tests until now, which is the same defect it exists to prevent elsewhere: sync_roadmap
files issues into eighteen repositories, and a parser that agrees with itself would file a confident,
wrong board. These run without the network and without a token: gh is replaced by a fake that serves
scripted reads and records every write.

This file replaces an inline heredoc in .github/workflows/ci.yml that asserted the same things. Having
two copies of one assertion is not a joke in this portfolio: a fix landed in one copy and the other
went red in CI on the same commit.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from projecthub.db import parser as roadmap
from projecthub.github import sync as s

# The real pkix row, under the real pkix header: five columns, the last one a Milestone.
PKIX_ROW = (
    "| Obligation | Spec | Status | Where | Milestone |\n"
    "|---|---|---|---|---|\n"
    "| `anchors.load_anchor()` | RFC 5280 sec. 6.1 | `[ ]` | `tests/test_obligations.py::load_anchor` | M1 |"
)
CERTLEDGER_ROW = "| `cl.ambient-trust` | Model I1 | `[x]` | `anchors.py`, `context.py` | `test_environment_is_ignored` |"
NOT_A_ROW = "| Non-goal | Status | Revisit when |\n| a certificate parser | `[no]` | a named document requires it |"

ROADMAP = """# Roadmap

| Obligation | Spec | Status | Where | Milestone |
|---|---|---|---|---|
| `a.one()` | RFC 5280 | `[ ]` | `tests/test_a.py::test_one` | M1 |
| `a.two()` | RFC 5280 | `[ ]` | `tests/test_a.py::test_two` | M1 |
| `a.three()` | RFC 5280 | `[ ]` | `tests/test_a.py::test_three` | - |
| `a.done()` | RFC 5280 | `[ok]` | `tests/test_a.py::test_done` | M1 |

## Milestones

- M1 first slice
- M2 second slice
- M3 third slice
"""


# ---------------------------------------------------------------------------------------------- parsing


def test_pkix_shape_parses_with_its_milestone_column():
    rows = s.rows_of(PKIX_ROW)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["name"] == "anchors.load_anchor"
    assert row["title"] == "anchors.load_anchor()"
    assert row["status"] == "[ ]"
    assert s.STATUSES[row["status"]][1] == "open"
    # The Milestone column is read by header, so its text never becomes evidence.
    assert row["milestone"] == "M1"
    assert row["evidence"] == ""


def test_certledger_shape_parses_including_evidence():
    rows = s.rows_of(CERTLEDGER_ROW)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["name"] == "cl.ambient-trust"
    # Markdown cells keep their backticks; the issue body renders them, and stripping them here would
    # silently mangle evidence strings that contain punctuation.
    assert row["evidence"] == "`test_environment_is_ignored`"
    assert s.STATUSES[row["status"]][1] == "close", "a done row must not open an issue"


def test_headers_and_non_goals_are_not_rows():
    assert s.rows_of(NOT_A_ROW) == []
    assert s.rows_of("") == []


def test_the_grammar_is_roadmap_py_and_the_board_is_not_here():
    assert s.rows_of is roadmap.rows_of
    for gone in ("milestones_of", "milestone_of", "MILESTONE_RE", "sync_board"):
        assert not hasattr(s, gone), f"{gone} belongs to roadmap.py or board.py now"


def test_marker_round_trips_through_the_issue_body():
    row = s.rows_of(PKIX_ROW)[0]
    body = s.body(row, "M1")
    assert s.MARKER.format(row["name"]) in body
    assert "tests/test_obligations.py::load_anchor" in body
    # A done row's body is never used, but an open row's must carry the rule that closes it.
    assert "named test" in body.lower()


# ---------------------------------------------------------------------------------------------- fake gh


class Done:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class FakeGh:
    """Stands in for subprocess.run on gh: scripted reads, recorded writes."""

    def __init__(
        self,
        issues=(),
        labels=(),
        milestones=(),
        fail_issues=False,
        fail_milestones=False,
    ):
        self.issues, self.labels, self.milestones = (
            list(issues),
            list(labels),
            list(milestones),
        )
        self.fail_issues, self.fail_milestones = fail_issues, fail_milestones
        self.writes: list[list[str]] = []
        self.seen: dict[str, object] = {}

    def __call__(self, argv, **kwargs):
        self.seen.update(kwargs)
        args = list(argv[1:])
        if args[:2] == ["repo", "view"]:
            return Done('{"name": "r"}')
        if args[:2] == ["issue", "list"]:
            if self.fail_issues:
                return Done("", 1, "HTTP 502: Bad Gateway")
            return Done(json.dumps(self.issues))
        if args[:2] == ["label", "list"]:
            return Done(json.dumps([{"name": n} for n in self.labels]))
        if args[:1] == ["api"] and len(args) == 5 and "--paginate" in args:
            if self.fail_milestones:
                return Done("", 1, "HTTP 403: rate limited")
            return Done("\n".join(json.dumps(m) for m in self.milestones))
        self.writes.append(args)
        return Done("")

    def created(self, kind: str) -> list[list[str]]:
        return [w for w in self.writes if w[:2] == [kind, "create"]]


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    folder = tmp_path / "demo"
    (folder / "tests").mkdir(parents=True)
    (folder / "ROADMAP.md").write_text(ROADMAP, encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake(monkeypatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(s.subprocess, "run", fake)
    monkeypatch.setattr(s, "gh", lambda: "gh")
    monkeypatch.setattr(s, "PACER", s.Pacer(delay=0, sleep=lambda _: None))
    return fake


def issue(key, number, state="OPEN", milestone=None):
    return {
        "number": number,
        "title": "t",
        "body": f"{s.MARKER.format(key)}\n",
        "state": state,
        "milestone": {"title": milestone} if milestone else None,
    }


# ---------------------------------------------------------------------------------------------- reads


def test_gh_output_is_decoded_as_utf8_not_the_windows_code_page(fake):
    """Issue bodies hold dashes and curly quotes. Decoding them as cp1252 made three bodies look changed
    on every run, so the sync rewrote them forever."""
    s.existing("o/r", True)
    assert fake.seen["encoding"] == "utf-8"


def test_a_failed_issue_read_raises_instead_of_reading_as_no_issues(fake):
    fake.fail_issues = True
    with pytest.raises(s.ReadError, match="502"):
        s.existing("o/r", True)


def test_a_read_that_fills_the_limit_is_refused_as_possibly_truncated(fake):
    fake.issues = [issue(f"a.k{i}", i) for i in range(3)]
    with pytest.raises(s.ReadError, match="truncated"):
        s.existing("o/r", True, limit=3)
    assert len(s.existing("o/r", True, limit=4)) == 3


def test_an_unparsable_issue_read_raises(monkeypatch):
    monkeypatch.setattr(s, "gh", lambda: "gh")
    monkeypatch.setattr(s.subprocess, "run", lambda argv, **k: Done("<html>"))
    with pytest.raises(s.ReadError, match="JSON"):
        s.existing("o/r", True)


def test_a_failed_milestone_read_raises(fake):
    fake.fail_milestones = True
    with pytest.raises(s.ReadError, match="milestones"):
        s.read_milestones("o/r")


def test_a_duplicate_marker_resolves_to_the_open_issue(fake):
    fake.issues = [issue("a.one", 1, "CLOSED"), issue("a.one", 2, "OPEN")]
    assert s.existing("o/r", True)["a.one"]["number"] == 2


# ---------------------------------------------------------------------------------------------- apply


def test_a_failed_read_in_apply_mode_creates_0_issues(project, fake, capsys):
    fake.fail_issues = True
    assert s.sync_one("demo", "o/demo", True, False, project) == 1
    assert fake.writes == [], "a failed read must not be taken for an empty repository"
    assert "nothing written" in capsys.readouterr().err


def test_a_failed_milestone_read_in_apply_mode_writes_nothing(project, fake):
    fake.fail_milestones = True
    assert s.sync_one("demo", "o/demo", True, False, project) == 1
    assert fake.writes == []


def test_only_the_labels_and_milestones_rows_use_are_created(project, fake):
    fake.labels = ["bug"]
    assert s.sync_one("demo", "o/demo", True, False, project) == 0
    labels = [w[2] for w in fake.created("label")]
    assert labels == ["status:unimplemented"], (
        "no done, partial or non-goal row is filed"
    )
    milestones = [w for w in fake.writes if w[0] == "api"]
    assert len(milestones) == 1 and "title=M1 - first slice" in milestones[0]
    created = fake.created("issue")
    assert len(created) == 3
    with_milestone = [w for w in created if "--milestone" in w]
    assert len(with_milestone) == 2, "a row citing no rung gets no milestone"


def test_labels_and_milestones_that_exist_are_not_recreated(project, fake):
    fake.labels = ["status:unimplemented"]
    fake.milestones = [
        {"number": 4, "title": "M1 - first slice", "description": "first slice"}
    ]
    s.sync_one("demo", "o/demo", True, False, project)
    assert fake.created("label") == []
    assert [w for w in fake.writes if w[0] == "api"] == []


def test_existing_issues_are_not_filed_again_and_a_closed_one_is_not_reopened(
    project, fake, capsys
):
    fake.issues = [
        issue("a.one", 1, "OPEN", "M1 - first slice"),
        issue("a.two", 2, "CLOSED", "M1 - first slice"),
        issue("a.three", 3, "OPEN"),
        issue("a.done", 4, "OPEN", "M1 - first slice"),
    ]
    s.sync_one("demo", "o/demo", True, False, project)
    assert fake.created("issue") == []
    closed = [w for w in fake.writes if w[:2] == ["issue", "close"]]
    assert closed == [["issue", "close", "4", "--repo", "o/demo"]]
    assert not any(w[:3] == ["issue", "edit", "2"] for w in fake.writes)
    assert "#2 is closed but the row is [ ]" in capsys.readouterr().out


def test_a_plan_run_writes_nothing(project, fake, capsys):
    s.sync_one("demo", "o/demo", False, False, project)
    assert fake.writes == []
    assert "would run: gh issue create" in capsys.readouterr().out


def test_a_project_with_no_repository_is_skipped_by_plan_and_apply_but_not_by_check(
    tmp_path, monkeypatch, capsys
):
    project = tmp_path / "local-only"
    project.mkdir()
    (project / "ROADMAP.md").write_text(PKIX_ROW + "\n", encoding="utf-8")
    monkeypatch.setattr(s, "repo_exists", lambda repo: False)
    monkeypatch.setattr(s, "existing", lambda repo, network, **_: {})
    monkeypatch.setattr(
        s, "run", lambda *a, **k: pytest.fail("must not write to a missing repo")
    )
    assert s.sync_one("local-only", "o/local-only", True, False, tmp_path) == 0
    assert "no GitHub repository" in capsys.readouterr().out
    # --check still counts the open row as drift, so an unreachable repo cannot report green.
    assert s.sync_one("local-only", "o/local-only", False, True, tmp_path) == 1


# ---------------------------------------------------------------------------------------------- check


def test_check_flags_an_open_row_whose_issue_is_closed(project, fake, capsys):
    fake.issues = [
        issue("a.one", 1, "CLOSED", "M1 - first slice"),
        issue("a.two", 2, "OPEN", "M1 - first slice"),
        issue("a.three", 3, "OPEN"),
    ]
    assert s.sync_one("demo", "o/demo", False, True, project) == 1
    assert "a.one is [ ] but issue #1 is closed" in capsys.readouterr().err
    assert fake.writes == []


def test_check_flags_a_done_row_whose_issue_is_open(project, fake, capsys):
    fake.issues = [
        issue("a.one", 1, "OPEN", "M1 - first slice"),
        issue("a.two", 2, "OPEN", "M1 - first slice"),
        issue("a.three", 3, "OPEN"),
        issue("a.done", 4, "OPEN", "M1 - first slice"),
    ]
    assert s.sync_one("demo", "o/demo", False, True, project) == 1
    assert "a.done is [ok] but issue #4 is still open" in capsys.readouterr().err


def test_check_flags_a_milestone_mismatch_and_a_stale_marker(project, fake, capsys):
    fake.issues = [
        issue("a.one", 1, "OPEN", "M2 - second slice"),
        issue("a.two", 2, "OPEN", "M1 - first slice"),
        issue("a.three", 3, "OPEN"),
        issue("a.gone", 9, "OPEN"),
    ]
    assert s.sync_one("demo", "o/demo", False, True, project) == 2
    err = capsys.readouterr().err
    assert "issue #1 for a.one is on milestone 'M2 - second slice'" in err
    assert "issue #9 marks a.gone, which is no longer a row" in err


def test_check_counts_a_failed_read_as_drift(project, fake, capsys):
    fake.fail_issues = True
    assert s.sync_one("demo", "o/demo", False, True, project) == 1
    assert "could not read issues" in capsys.readouterr().err


def test_check_in_sync_reports_nothing(project, fake):
    fake.issues = [
        issue("a.one", 1, "OPEN", "M1 - first slice"),
        issue("a.two", 2, "OPEN", "M1 - first slice"),
        issue("a.three", 3, "OPEN"),
        issue("a.done", 4, "CLOSED", "M1 - first slice"),
    ]
    assert s.sync_one("demo", "o/demo", False, True, project) == 0


# ---------------------------------------------------------------------------------------------- pacing


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_the_pacer_spaces_writes_by_the_delay():
    clock = Clock()
    pacer = s.Pacer(delay=2.0, clock=clock, sleep=clock.sleep)
    pacer.wait()
    pacer.wait()
    assert clock.slept == [2.0] and pacer.count == 2


def test_the_pacer_waits_for_the_minute_to_free_a_slot():
    clock = Clock()
    pacer = s.Pacer(delay=0, per_minute=3, per_hour=100, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        pacer.wait()
        clock.now += 1
    pacer.wait()
    assert clock.slept == [57.0], (
        "the oldest write leaves the window 60 s after it was made"
    )
    assert pacer.count == 4


def test_the_pacer_stops_when_the_hour_is_spent():
    clock = Clock()
    pacer = s.Pacer(delay=0, per_minute=100, per_hour=2, clock=clock, sleep=clock.sleep)
    pacer.wait()
    pacer.wait()
    with pytest.raises(s.BudgetExhausted):
        pacer.wait()


def test_an_early_stop_reports_where_it_stopped(project, fake, monkeypatch, capsys):
    monkeypatch.setattr(
        s,
        "resolve",
        lambda root, name, org: s.Target(name, root / name, None, f"o/{name}"),
    )
    fake.labels = ["status:unimplemented"]
    fake.milestones = [
        {"number": 4, "title": "M1 - first slice", "description": "first slice"}
    ]
    code = s.main(
        [
            "--project",
            "demo",
            "--root",
            str(project),
            "--apply",
            "--per-hour",
            "2",
            "--delay",
            "0",
        ]
    )
    assert code == 3
    assert len(fake.created("issue")) == 2, "the budget stops the third create"
    err = capsys.readouterr().err
    assert "stopped early at demo a.three after 2 write(s)" in err


@pytest.mark.parametrize(
    "flags",
    [
        ["--per-minute", "81"],
        ["--per-hour", "501"],
        ["--per-hour", "0"],
        ["--delay", "-1"],
        ["--apply", "--check"],
        ["--board"],
        ["--board", "Protocol SDK portfolio"],
    ],
)
def test_unsafe_or_removed_options_are_refused(flags, monkeypatch):
    monkeypatch.setattr(s, "PACER", s.Pacer())
    with pytest.raises(SystemExit) as stop:
        s.main(flags)
    assert stop.value.code == 2


# ---------------------------------------------------------------------------------------------- targets


def test_itulib_resolves_to_itulibrary_its_plan_matrix_and_its_own_repo(tmp_path):
    folder = tmp_path / "itulibrary"
    folder.mkdir()
    (folder / "PLAN.md").write_text(PKIX_ROW + "\n", encoding="utf-8")
    seen: list[str] = []

    def remote(root, directory):
        seen.append(directory)
        return "b1tninja/itulib"

    target = s.resolve(tmp_path, "itulib", "someone", remote)
    assert target.directory == folder
    assert target.matrix == folder / "PLAN.md"
    assert target.repo == "b1tninja/itulib"
    assert seen == ["itulibrary"]


def test_a_project_with_no_remote_falls_back_to_the_org(tmp_path):
    (tmp_path / "demo").mkdir()
    target = s.resolve(tmp_path, "demo", "someone", lambda root, d: "")
    assert target.repo == "someone/demo" and target.matrix is None


def test_the_default_set_is_the_shells_plus_every_hand_owned_matrix_once():
    names = s.default_projects()
    assert len(names) == len(set(names))
    assert set(s.PROJECTS) <= set(names)
    assert set(roadmap.HAND_OWNED) <= set(names)


def test_in_the_shared_repository_a_project_reads_only_its_own_issues(monkeypatch):
    import projecthub.github.sync as sync

    monkeypatch.setattr(sync, "ISSUE_REPO", "o/portfolio")
    listed = [
        {
            "number": 1,
            "title": "[pkix] a.b: x",
            "body": "<!-- roadmap:a.b -->",
            "state": "OPEN",
        },
        {
            "number": 2,
            "title": "[ctspy] a.b: y",
            "body": "<!-- roadmap:a.b -->",
            "state": "OPEN",
        },
    ]
    monkeypatch.setattr(
        sync, "read", lambda argv, what: __import__("json").dumps(listed)
    )
    got = sync.existing("o/portfolio", True, project="pkix")
    assert [i["number"] for i in got.values()] == [1], (
        "the same row id in another project is not this one"
    )
    assert sync.issue_home("o/pkix") == "o/portfolio"
    assert sync.placement({"milestone": "M1"}, {"M1": "first"}) == "", (
        "milestones would collide in one repo"
    )
    monkeypatch.setattr(sync, "ISSUE_REPO", "")
    assert sync.issue_home("o/pkix") == "o/pkix" and sync.owned_by("[ctspy] x", "pkix")


@pytest.fixture(autouse=True)
def _one_repository_per_project(monkeypatch):
    """These tests describe the per-project layout. The shared repository has tests of its own."""
    import projecthub.github.sync as sync_module

    monkeypatch.setattr(sync_module, "ISSUE_REPO", "")
