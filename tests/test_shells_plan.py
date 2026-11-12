"""The generated plan states a dependency's status from the workspace, not from a table.

The bug these pin: nine plans said asn1core "does not exist yet" for as long as it took the claim to
become false, because the status was a hand-written table nothing re-read.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from projecthub.shells import generate


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "ROOT", tmp_path)
    return tmp_path


def make_code(root: pathlib.Path, name: str) -> None:
    (root / name / name).mkdir(parents=True)
    (root / name / name / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (root / name / "tests").mkdir()
    (root / name / "tests" / "test_a.py").write_text(
        "def test_a(): pass\n", encoding="utf-8"
    )


def test_an_absent_dependency_is_stated_as_missing_and_blocking(workspace):
    text = " ".join(generate.render_plan({"name": "pkix"}).split())
    assert "does not exist yet" in text
    assert "**Blocked, not in progress.**" in text


def test_a_plan_only_dependency_names_its_plan_file(workspace):
    (workspace / "asn1core").mkdir()
    text = " ".join(generate.render_plan({"name": "pkix"}).split())
    assert "exists only as a plan" in text
    assert "../../asn1core/PLAN.md" in text
    assert "**Blocked, not in progress.**" in text


def test_a_dependency_with_code_is_no_longer_called_missing_or_blocked(workspace):
    make_code(workspace, "asn1core")
    text = " ".join(generate.render_plan({"name": "pkix"}).split())
    assert "does not exist yet" not in text
    assert "Blocked, not in progress" not in text
    assert "Gated by coverage, not by absence" in text
    assert "does not say it provides what a row here needs" in text


def test_the_gated_text_never_claims_the_dependency_is_ready(workspace):
    make_code(workspace, "asn1core")
    text = generate.render_plan({"name": "pkix"}).lower()
    assert not re.search(r"(unblocked|ready)", text)


def test_a_shell_dependency_still_gets_the_shell_note(workspace):
    make_code(workspace, "pkix")
    (workspace / "pkix" / "ROADMAP.md").write_text(
        "| `a.b()` | RFC 1 | `[ ]` | `tests/test_a.py::test_a` |\n", encoding="utf-8"
    )
    text = " ".join(generate.render_plan({"name": "revokekit"}).split())
    assert "exists only as a generated shell" in text
    assert "same gate in weaker form" in text


def test_namespy_keeps_its_recorded_wording_when_it_has_code(workspace):
    make_code(workspace, "namespy")
    text = " ".join(generate.render_plan({"name": "dnssd-srp"}).split())
    assert "**Status: exists.**" in text
    assert "proven end to end" in text
