"""`leases audit` queues an evidence check for a row already [ok] at a commit, with no lease and no Status."""

from __future__ import annotations

import pytest
import test_evidence_check as T

from projecthub.mcp import work as L


def _make(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    T._git(root, "init", "-q")
    base = T._commit(
        root,
        {
            "pyproject.toml": "[project]\nname = 'demo'\n",
            "demo/core.py": T.STUB,
            "ROADMAP.md": T.MATRIX.format(status="[ ]"),
        },
        "base",
    )
    return root, base


def test_an_ok_row_is_audited_without_a_lease(tmp_path):
    root, _ = _make(tmp_path)
    T._commit(root, {"ROADMAP.md": T.MATRIX.format(status="[ok]")}, "flip")
    sha = T._git(root, "rev-parse", "HEAD")
    store = L.Store(tmp_path / "leases.sqlite")
    got = store.audit("demo", "core.double", sha, root=tmp_path)
    assert got["state"] == "queued" and got["leased"] is False
    assert store.pending(10) == [], "an audit writes no board field"


def test_an_open_row_is_refused(tmp_path):
    root, base = _make(tmp_path)
    store = L.Store(tmp_path / "leases.sqlite")
    with pytest.raises(L.LeaseError, match="NOT_DONE"):
        store.audit("demo", "core.double", base, root=tmp_path)


def test_an_unknown_row_and_a_short_sha_are_refused(tmp_path):
    root, base = _make(tmp_path)
    store = L.Store(tmp_path / "leases.sqlite")
    with pytest.raises(L.LeaseError, match="NO_ROW"):
        store.audit("demo", "core.triple", base, root=tmp_path)
    with pytest.raises(L.LeaseError, match="BAD_SHA"):
        store.audit("demo", "core.double", base[:7], root=tmp_path)


def test_a_row_id_written_with_parentheses_is_accepted(tmp_path):
    root, _ = _make(tmp_path)
    T._commit(root, {"ROADMAP.md": T.MATRIX.format(status="[ok]")}, "flip")
    sha = T._git(root, "rev-parse", "HEAD")
    got = L.Store(tmp_path / "leases.sqlite").audit(
        "demo", "core.double()", sha, root=tmp_path
    )
    assert got["row_key"] == "demo:core.double"
