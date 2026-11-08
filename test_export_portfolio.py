"""portfolio.json: facts only, a repo nobody chose stays off the site, and the export never lands in a tree."""

from __future__ import annotations

import json

import pytest

import export_portfolio as X


def _repo(
    name,
    fork=False,
    archived=False,
    pushed="2026-11-01T00:00:00Z",
    parent=None,
    lang="Python",
):
    return {
        "name": name,
        "description": f"{name} does things",
        "isFork": fork,
        "isArchived": archived,
        "pushedAt": pushed,
        "primaryLanguage": {"name": lang} if lang else None,
        "parent": {"name": parent[1], "owner": {"login": parent[0]}}
        if parent
        else None,
        "homepageUrl": "",
        "url": f"https://github.com/b1tninja/{name}",
    }


def test_a_milestone_is_done_only_when_every_row_in_it_is_done():
    rows = [
        {"milestone": "M1", "state": "done"},
        {"milestone": "M1", "state": "done"},
        {"milestone": "M2", "state": "done"},
        {"milestone": "M2", "state": "open"},
        {"milestone": "M3", "state": "nongoal"},
        {"milestone": "", "state": "done"},
    ]
    assert X.milestones(rows) == (1, 2), (
        "a non-goal row neither completes nor counts a milestone"
    )


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        (_repo("mirc-mcp"), {"include": True, "section": "tools", "kind": "mcp"}),
        (
            _repo("brother-firmware"),
            {"include": True, "section": "re", "kind": "hardware"},
        ),
        (_repo("snmpy"), {"include": True, "section": "other", "kind": "library"}),
        (
            _repo("malbot", pushed="2014-12-03T00:00:00Z"),
            {"include": True, "section": "archive", "kind": "app"},
        ),
        (
            _repo("linux", fork=True),
            {"include": False, "section": "forks", "kind": "tool"},
        ),
        (
            _repo("wordcraft", fork=True),
            {"include": True, "section": "forks", "kind": "app"},
        ),
        (
            _repo("continuous", fork=True),
            {"include": True, "section": "forks", "kind": "tool"},
        ),
    ],
)
def test_suggestions_are_a_first_guess_by_name_age_and_fork(repo, expected):
    assert X.suggest(repo) == expected


def test_seeding_keeps_what_the_person_wrote_and_skips_registered_repos():
    gh = {
        r["name"].lower(): r for r in (_repo("snmpy"), _repo("pkix"), _repo("basilisk"))
    }
    mine = {
        "repos": {
            "snmpy": {
                "include": False,
                "section": "other",
                "kind": "library",
                "reviewed": True,
            }
        }
    }
    seeded = X.seed(gh, registered={"pkix"}, existing=mine)["repos"]
    assert seeded["snmpy"] == mine["repos"]["snmpy"], (
        "a reviewed choice is never overwritten"
    )
    assert "pkix" not in seeded, (
        "registry projects come from the registry, not the curation file"
    )
    assert seeded["basilisk"]["reviewed"] is False


def test_only_included_repos_that_still_exist_are_exported_and_sections_are_checked():
    gh = {
        r["name"].lower(): r
        for r in (
            _repo("snmpy"),
            _repo("wordcraft", fork=True, parent=("ArtCraft", "wordcraft")),
        )
    }
    curation = {
        "repos": {
            "snmpy": {"include": False, "section": "other"},
            "wordcraft": {
                "include": True,
                "section": "forks",
                "kind": "app",
                "what": "Word in Rust",
            },
            "gone": {"include": True, "section": "other"},
        }
    }
    (only,) = X.curated_entries(curation, gh)
    assert only["name"] == "wordcraft" and only["what"] == "Word in Rust"
    assert only["is_fork"] is True and only["upstream"] == "ArtCraft/wordcraft"
    with pytest.raises(SystemExit, match="not one of"):
        X.curated_entries(
            {"repos": {"snmpy": {"include": True, "section": "misc"}}}, gh
        )


def test_the_record_has_the_fields_the_site_reads_in_section_order(monkeypatch):
    monkeypatch.setattr(X, "registry_entries", lambda root, gh, active: [])
    gh = {
        r["name"].lower(): r
        for r in (
            _repo("old", pushed="2019-01-01T00:00:00Z"),
            _repo("new"),
            _repo("newer", pushed="2026-11-07T00:00:00Z"),
        )
    }
    curation = {
        "repos": {
            n: {"include": True, "section": s}
            for n, s in (("old", "archive"), ("new", "other"), ("newer", "other"))
        }
    }
    record = X.build(X.ROOT, gh, curation, active=None)
    assert record["schema_version"] == X.SCHEMA_VERSION and record["source"]["commit"]
    assert [s["id"] for s in record["sections"]] == ["other", "archive"], (
        "empty sections are left out, order kept"
    )
    assert [p["name"] for p in record["projects"]] == ["newer", "new", "old"], (
        "latest push first within a section"
    )
    needed = {
        "name",
        "display_name",
        "what",
        "kind",
        "section",
        "tier",
        "wave",
        "status",
        "blocked_by",
        "milestones_done",
        "milestones_total",
        "obligations_done",
        "obligations_total",
        "roadmap_url",
        "board_url",
        "repo_url",
        "homepage_url",
        "language",
        "last_push",
        "is_fork",
        "archived",
        "upstream",
        "order",
    }
    assert all(needed <= set(p) for p in record["projects"])
    assert record["projects"][-1]["status"] == "archived"
    json.dumps(record)


def test_an_export_path_inside_a_work_tree_is_refused_before_github_is_called(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        X, "github_repos", lambda owner=X.OWNER: pytest.fail("called GitHub")
    )
    assert X.main(["--out", str(X.HERE / "portfolio.json")]) == 2
    assert "inside the workspace" in capsys.readouterr().err


def _record(**project):
    base = {
        "name": "x",
        "section": "other",
        "what": "x",
        "blocked_by": [],
        "milestones_done": 0,
        "milestones_total": 0,
        "obligations_done": 0,
        "obligations_total": 0,
        "repo_url": "https://github.com/b1tninja/x",
    }
    return {
        "board_url": X.BOARD_URL,
        "sections": [{"id": "other"}],
        "projects": [{**base, **project}],
    }


def test_a_clean_record_meets_the_sites_contract():
    assert X.contract_problems(_record()) == []
    assert X.contract_problems(_record(what=None)) == [], "what may be null"


@pytest.mark.parametrize(
    ("project", "fragment"),
    [
        ({"repo_url": "http://example.com"}, "not a plain https://"),
        ({"repo_url": "https://example.com/a b"}, "not a plain https://"),
        ({"homepage_url": 'https://x/"'}, "not a plain https://"),
        ({"section": "misc"}, "not in sections[]"),
        ({"blocked_by": "rpkit"}, "blocked_by must be a list"),
        ({"milestones_done": 1.0}, "must be an integer"),
        ({"milestones_total": True}, "must be an integer"),
        ({"obligations_done": 2.5}, "must be an integer"),
        ({"obligations_total": True}, "must be an integer"),
        ({"what": 3}, "string or null"),
    ],
)
def test_each_contract_breach_is_named(project, fragment):
    problems = X.contract_problems(_record(**project))
    assert any(fragment in p for p in problems), problems


def test_utf8_and_raw_angle_brackets_are_written_as_characters_not_escapes():
    record = _record(what="DER & X.509 <codec> \u2014 ok \ufffd")
    text = X.serialise(record)
    assert "\u2014" in text and "<codec>" in text and "\\u" not in text
    assert X.contract_problems(record) == []


def test_both_owners_are_listed_and_the_organizations_copy_wins(monkeypatch):
    listed = {
        "b1tninja": {
            "rfclib": _repo("rfclib"),
            "pkix": {**_repo("pkix"), "url": "https://github.com/b1tninja/pkix"},
        },
        "b1tninja-labs": {
            "pkix": {**_repo("pkix"), "url": "https://github.com/b1tninja-labs/pkix"}
        },
    }
    monkeypatch.setattr(X, "_list_owner", lambda owner: listed[owner])
    found = X.github_repos()
    assert set(found) == {"rfclib", "pkix"}
    assert found["pkix"]["url"] == "https://github.com/b1tninja-labs/pkix", (
        "a moved repo is linked at its new home"
    )
