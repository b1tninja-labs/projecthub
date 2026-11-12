"""projecthub.paths: one reader for every setting, the old variable names as fallbacks, and the state directory."""

from __future__ import annotations

import pytest

from projecthub import paths

NAMES = (
    "ROOT",
    "LEASES_DB",
    "BOARD",
    "ORG",
    "ISSUE_REPO",
    "EVIDENCE",
    "MANIFEST",
    "ALLOW_HEADING_LOSS",
)


@pytest.fixture
def clean(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(f"PROJECTHUB_{name}", raising=False)
        monkeypatch.delenv(f"FORGE_{name}", raising=False)
    return monkeypatch


@pytest.mark.parametrize("name", NAMES)
def test_the_projecthub_name_wins_over_the_old_one(clean, name):
    clean.setenv(f"FORGE_{name}", "old")
    clean.setenv(f"PROJECTHUB_{name}", "new")
    assert paths.env(name, "default") == "new"


@pytest.mark.parametrize("name", NAMES)
def test_the_old_name_alone_still_works(clean, name):
    clean.setenv(f"FORGE_{name}", "old")
    assert paths.env(name, "default") == "old"


def test_neither_name_gives_the_default(clean):
    assert paths.env("ROOT", "D:/code") == "D:/code"
    assert paths.env("EVIDENCE") == ""


def test_an_empty_variable_falls_through_unless_empty_means_something(clean):
    clean.setenv("PROJECTHUB_ISSUE_REPO", "")
    clean.setenv("FORGE_ISSUE_REPO", "o/old")
    assert paths.env("ISSUE_REPO", "o/default") == "o/old"
    assert paths.env("ISSUE_REPO", "o/default", keep_empty=True) == ""
    clean.delenv("PROJECTHUB_ISSUE_REPO")
    clean.setenv("FORGE_ISSUE_REPO", "")
    assert paths.env("ISSUE_REPO", "o/default", keep_empty=True) == "", (
        "an old name set empty still means one repository per project"
    )
    clean.delenv("FORGE_ISSUE_REPO")
    assert paths.env("ISSUE_REPO", "o/default", keep_empty=True) == "o/default"


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


def test_a_new_install_uses_the_projecthub_state_directory(base):
    assert paths.state_dir() == base / "projecthub"


def test_an_existing_forge_state_directory_is_kept_where_it_is(base):
    (base / "forge").mkdir()
    (base / "forge" / "leases.sqlite").write_bytes(b"live")
    assert paths.state_dir() == base / "forge"
    assert (base / "forge" / "leases.sqlite").read_bytes() == b"live"
    assert not (base / "projecthub").exists(), "nothing is moved or created"


def test_the_projecthub_directory_wins_once_it_exists(base):
    (base / "forge").mkdir()
    (base / "projecthub").mkdir()
    assert paths.state_dir() == base / "projecthub"


def test_only_paths_knows_the_old_prefix():
    """Every setting is read through paths.env, so no other module spells an old variable name."""
    import pathlib

    src = pathlib.Path(paths.__file__).resolve().parent
    spelled = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if p != pathlib.Path(paths.__file__).resolve()
        and "FORGE_" in p.read_text(encoding="utf-8")
    ]
    assert spelled == []
