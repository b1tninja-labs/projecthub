"""The site data (schema 3): one paginated read of Project 1, the active set, and only kind and time public."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from projecthub import config
from projecthub.site import live as L


def node(
    title: str,
    labels: tuple[str, ...] = (),
    url: str = "",
    **values: str,
) -> dict[str, Any]:
    fields = [
        {"__typename": "Text", "text": v, "field": {"name": k.replace("_", " ")}}
        for k, v in values.items()
    ]
    return {
        "fieldValues": {"nodes": [*fields, {}]},
        "content": {
            "__typename": "Issue",
            "title": title,
            "url": url or f"https://github.com/{config.PORTFOLIO_REPO}/issues/1",
            "state": "OPEN",
            "updatedAt": "2026-11-01T00:00:00Z",
            "labels": {"nodes": [{"name": n} for n in labels]},
        },
    }


class Fake:
    """Answers the site's query from `nodes`, `size` per page, at `cost` points a page."""

    def __init__(
        self, nodes: list[dict[str, Any]], size: int = 100, cost: int = 2, public=False
    ) -> None:
        self.nodes, self.size, self.cost, self.public = nodes, size, cost, public
        self.calls: list[dict[str, object]] = []

    def graphql(
        self, query: str, variables: dict[str, object] | None = None
    ) -> dict[str, Any]:
        assert "rateLimit { cost remaining" in query and variables is not None
        self.calls.append(variables)
        start = int(str(variables["after"] or 0))
        page = self.nodes[start : start + self.size]
        more = start + self.size < len(self.nodes)
        return {
            "rateLimit": {"cost": self.cost, "remaining": 5000 - len(self.calls)},
            "organization": {
                "projectV2": {
                    "public": self.public,
                    "items": {
                        # The real page size is PAGE_SIZE; the estimate scales the fake's total to match.
                        "totalCount": len(self.nodes) * L.PAGE_SIZE // self.size,
                        "pageInfo": {
                            "hasNextPage": more,
                            "endCursor": str(start + self.size),
                        },
                        "nodes": page,
                    },
                }
            },
        }


def board() -> list[dict[str, Any]]:
    return [
        node("alpha: a codec", Status="In progress", Level="Project"),
        node("beta: a client", Status="In progress"),
        node("gamma: a server", Status="In progress"),
        node("delta: a plan", Status="Backlog"),
        node("Wave 1: the first wave", Status="In progress"),
        node("Decision 3: pick one", Status="In progress"),
        node(
            "[alpha] core.parse(): RFC 1 sec. 2",
            Status="Done",
            Last_activity="release:pr_opened 2026-11-10T09:00:00Z",
        ),
        node(
            "[alpha] core.verify(): RFC 1 sec. 3",
            Status="In progress",
            Agent="agent-7",
            Claimed="2026-11-11",
            Last_activity="note:progress 2026-11-11T12:00:00Z",
        ),
        node("[alpha] core.limits(): x", Status="Backlog"),
        node("[alpha] core.never(): not ours", ("type:non-goal",), Status="Backlog"),
        node("[alpha] M1 - a milestone", Status="Backlog"),
        node(
            "[beta] net.dial(): y",
            Status="Blocked",
            Last_activity="block:dependency 2026-11-12T08:00:00Z",
        ),
        node("[gamma] srv.listen(): z", Status="Ready", Claimed="2026-11-01"),
        node("[delta] plan.write(): w", Status="Backlog"),
        node("[epsilon] lone.row(): no card"),
    ]


def index(limit: int = 6, **kw: Any) -> dict[str, Any]:
    found = L.read(Fake(board(), **kw))
    return L.build(found, limit, now=datetime(2026, 11, 12, tzinfo=UTC))


def test_one_paginated_read_counts_requests_and_cost():
    fake = Fake(board(), size=4, cost=3)
    found = L.read(fake, budget=1000)
    assert found.requests == len(fake.calls) == 4 and found.cost == 12
    assert len(found.cards) == len(board()) and found.remaining == 4996
    assert [c["after"] for c in fake.calls] == [None, "4", "8", "12"]
    assert L.build(found)["build"] == {
        "items": len(board()) * L.PAGE_SIZE // 4,
        "requests": 4,
        "cost": 12,
        "budget": 1000,
        "remaining": 4996,
    }


def test_a_run_over_budget_is_refused_before_the_second_request():
    fake = Fake(board(), size=4, cost=30)
    with pytest.raises(L.OverBudget, match="budget is 100"):
        L.read(fake, budget=100)
    assert len(fake.calls) == 1


class Rising(Fake):
    """A page that costs more than the first one did, as a larger page of field values can."""

    def graphql(
        self, query: str, variables: dict[str, object] | None = None
    ) -> dict[str, Any]:
        self.cost = 2 if not self.calls else 50
        return super().graphql(query, variables)


def test_the_budget_is_also_checked_as_pages_are_spent():
    fake = Rising(board(), size=4)
    with pytest.raises(L.OverBudget, match="after 2 requests"):
        L.read(fake, budget=40)
    assert len(fake.calls) == 2


def test_main_writes_nothing_when_over_budget(tmp_path, capsys):
    out = tmp_path / "data"
    code = L.main(["--out", str(out), "--budget", "1"], client=Fake(board(), cost=2))
    assert code == 1 and not out.exists()
    assert "over budget" in capsys.readouterr().err


def test_the_active_set_is_in_progress_cards_by_latest_activity_and_capped():
    data = index()
    assert [p["name"] for p in data["active"]] == ["beta", "alpha", "gamma"]
    assert [p["last_activity"] for p in data["active"]] == [
        "2026-11-12T08:00:00Z",
        "2026-11-11T12:00:00Z",
        "2026-11-01",
    ]
    capped = index(limit=2)
    assert [p["name"] for p in capped["active"]] == ["beta", "alpha"]
    assert "gamma" in [p["name"] for p in capped["others"]]


def test_waves_decisions_and_milestones_are_not_projects_or_rows():
    data = index()
    names = [p["name"] for p in [*data["active"], *data["others"]]]
    assert sorted(names) == ["alpha", "beta", "delta", "epsilon", "gamma"]
    alpha = data["active"][1]
    ids = [r["id"] for g in alpha["groups"] for r in g["rows"]]
    assert "M1" not in ids and "core.never" not in ids


def test_rows_are_grouped_by_status_with_counts_and_progress():
    alpha = index()["active"][1]
    assert [g["status"] for g in alpha["groups"]] == ["Backlog", "In progress", "Done"]
    assert alpha["counts"] == {"Backlog": 1, "In progress": 1, "Done": 1}
    assert alpha["progress"] == {"done": 1, "total": 3} and alpha["nongoals"] == 1
    assert alpha["claims"] == [
        {"row": "core.verify", "holder": "agent-7", "since": "2026-11-11"}
    ]
    assert alpha["activity"] == [
        {"kind": "note:progress", "at": "2026-11-11T12:00:00Z"},
        {"kind": "release:pr_opened", "at": "2026-11-10T09:00:00Z"},
    ]
    assert alpha["groups"][1]["rows"][0] == {
        "id": "core.verify",
        "title": "RFC 1 sec. 3",
        "url": f"https://github.com/{config.PORTFOLIO_REPO}/issues/1",
        "holder": "agent-7",
        "since": "2026-11-11",
    }


def test_everything_else_is_one_line_with_counts_and_no_rows():
    others = {p["name"]: p for p in index()["others"]}
    assert others["delta"] == {
        "name": "delta",
        "status": "Backlog",
        "issues_url": L.issues_url("delta"),
        "counts": {"Backlog": 1},
        "progress": {"done": 0, "total": 1},
    }
    assert others["epsilon"]["status"] is None
    assert others["epsilon"]["counts"] == {"No status": 1}
    assert [p["name"] for p in index()["others"]] == ["delta", "epsilon"]


def test_activity_never_carries_note_text():
    nodes = board()
    nodes.append(
        node(
            "[alpha] core.leak(): t",
            Status="In progress",
            Last_activity="the private note says the key is under the mat",
            Agent='x"><script>',
        )
    )
    found = L.read(Fake(nodes))
    data = L.build(found)
    alpha = next(p for p in data["active"] if p["name"] == "alpha")
    text = json.dumps(data)
    assert "under the mat" not in text and "<script>" not in text
    assert all(set(e) == {"kind", "at"} for e in alpha["activity"])
    assert {"row": "core.leak", "holder": "xscript", "since": None} in alpha["claims"]
    assert L.problems(data, found.public) == []


def test_a_private_project_is_not_linked_and_a_public_one_is():
    private = index()
    assert private["board_url"] == f"https://github.com/{config.PORTFOLIO_REPO}/issues"
    assert config.BOARD_URL not in json.dumps(private)
    public = index(public=True)
    assert public["board_url"] == config.BOARD_URL
    assert L.problems(public, True) == []
    leaked = {**private, "board_url": config.BOARD_URL}
    assert any("private" in p for p in L.problems(leaked, False))


def test_the_contract_refuses_extra_fields_and_bad_urls():
    data = index()
    assert L.problems(data, False) == []
    bad = json.loads(json.dumps(data))
    bad["active"][0]["activity"].append({"kind": "note", "at": "t", "text": "x"})
    bad["active"][0]["groups"][0]["rows"][0]["url"] = "http://x"
    bad["others"][0]["rows"] = []
    found = L.problems(bad, False)
    assert any("more than kind and time" in p for p in found)
    assert any("not a plain https" in p for p in found)
    assert any("keys differ" in p for p in found)


def test_schema_3_shape(tmp_path):
    data = index()
    assert set(data) == L.INDEX_KEYS and data["schema_version"] == 3
    assert data["generated_at"] == "2026-11-12T00:00:00+00:00"
    assert all(set(p) == L.ACTIVE_KEYS for p in data["active"])
    assert all(set(p) == L.OTHER_KEYS for p in data["others"])
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "old.json").write_text("{}", encoding="utf-8")
    L.write(tmp_path, data)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index.json"]
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8")) == data


def test_main_writes_the_index(tmp_path, capsys):
    out = tmp_path / "data"
    assert L.main(["--out", str(out), "--active", "1"], client=Fake(board())) == 0
    data = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in data["active"]] == ["beta"]
    assert "1 requests costing 2 points" in capsys.readouterr().out
