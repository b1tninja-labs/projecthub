"""Which GitHub Project this installation mirrors to: one place, so it cannot disagree with itself.

An environment variable overrides each value, for tests and for trying a second Project.
"""

from __future__ import annotations

import os

PROJECT_OWNER = os.environ.get("PROJECTHUB_OWNER") or "b1tninja-labs"
PROJECT_NUMBER = int(os.environ.get("PROJECTHUB_NUMBER") or 1)
# Wave epics and portfolio-level decisions have no project repository of their own (PORTFOLIO.md decision 11).
PORTFOLIO_REPO = (
    os.environ.get("PROJECTHUB_PORTFOLIO_REPO") or f"{PROJECT_OWNER}/projecthub"
)
BOARD_URL = f"https://github.com/orgs/{PROJECT_OWNER}/projects/{PROJECT_NUMBER}"
