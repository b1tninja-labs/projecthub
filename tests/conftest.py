"""Every test runs offline: no default client may reach GitHub, so a claim a test forgot to fake is refused."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _never_reach_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECTHUB_OFFLINE", "1")
