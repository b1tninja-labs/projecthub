"""The single MCP entry point: every toolset builds, serves the tools it promises, and read stays read-only."""

from __future__ import annotations

import pytest

from projecthub import cli
from projecthub.mcp import serve

EXPECTED = {
    "read": [
        "forge_context",
        "forge_next",
        "forge_packet",
        "forge_propose_status",
        "forge_status",
    ],
    "work": [
        "forge_block",
        "forge_lease_acquire",
        "forge_lease_heartbeat",
        "forge_lease_release",
        "forge_leases",
    ],
    "projects": [
        "item_context",
        "portfolio_overview",
        "project_items",
        "project_schema",
        "projects_auth",
        "projects_list",
    ],
}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """The work toolset opens its lease store on build; keep it, and the projects toolsets, out of this machine."""
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(tmp_path / "leases.sqlite"))
    monkeypatch.setenv("FORGE_LEASES_DB", str(tmp_path / "leases.sqlite"))
    monkeypatch.setenv("PROJECTS_MCP_TOOLSETS", "projects,portfolio")
    return tmp_path


def _tools(toolset):
    pytest.importorskip("mcp")
    import anyio

    listed = anyio.run(serve.build(toolset).list_tools)
    return list(getattr(listed, "tools", listed))


def test_the_toolsets_are_the_three_servers():
    assert sorted(serve.TOOLSETS) == sorted(EXPECTED)
    assert serve.DEFAULT == "read"


@pytest.mark.parametrize("toolset", sorted(EXPECTED))
def test_every_toolset_builds_with_the_expected_tools(toolset, isolated):
    assert sorted(t.name for t in _tools(toolset)) == EXPECTED[toolset]


def test_the_read_toolset_has_no_tool_that_is_not_read_only(isolated):
    for tool in _tools("read"):
        assert (
            tool.annotations is not None and tool.annotations.read_only_hint is True
        ), tool.name


def test_only_the_work_toolset_writes(isolated):
    for toolset in ("read", "projects"):
        for tool in _tools(toolset):
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is True, (toolset, tool.name)
    assert any(
        tool.annotations is not None and tool.annotations.read_only_hint is False
        for tool in _tools("work")
    )


def test_building_the_read_toolset_writes_no_lease_store(isolated):
    _tools("read")
    assert not (isolated / "leases.sqlite").exists()


def test_an_unknown_toolset_is_refused():
    with pytest.raises(ValueError, match="unknown toolset"):
        serve.build("admin")
    with pytest.raises(SystemExit):
        serve.main(["--toolset", "admin"])


def test_the_cli_routes_mcp_to_this_module():
    assert cli.COMMANDS["mcp"][0] == "projecthub.mcp.serve"


def test_main_serves_the_chosen_toolset_on_stdio(monkeypatch, isolated):
    served = []

    class Fake:
        async def run_stdio_async(self):
            served.append(True)

    chosen = []

    def fake_build(toolset=serve.DEFAULT):
        chosen.append(toolset)
        return Fake()

    monkeypatch.setattr(serve, "build", fake_build)
    assert serve.main([]) == 0
    assert serve.main(["--toolset", "work"]) == 0
    assert chosen == ["read", "work"] and served == [True, True]


def test_projecthub_variables_win_over_the_forge_names():
    env = {"PROJECTHUB_ROOT": "/new", "FORGE_ROOT": "/old", "FORGE_LEASES_DB": "/kept"}
    serve.bridge_env(env)
    assert env["FORGE_ROOT"] == "/new"
    assert env["FORGE_LEASES_DB"] == "/kept"


def test_the_work_store_follows_projecthub_leases_db(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    target = tmp_path / "projecthub-leases.sqlite"
    monkeypatch.setenv("FORGE_LEASES_DB", str(tmp_path / "old.sqlite"))
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(target))
    serve.build("work")
    assert target.exists() and not (tmp_path / "old.sqlite").exists()


def test_a_missing_mcp_extra_names_the_install(monkeypatch, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "mcp", None)
    assert serve.main([]) == 2
    assert "projecthub[mcp]" in capsys.readouterr().err
