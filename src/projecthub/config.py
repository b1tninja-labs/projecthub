"""Which GitHub Project this installation mirrors to: one place, so it cannot disagree with itself.

An environment variable overrides each value, for tests and for trying a second Project.
"""

from __future__ import annotations

import os

PROJECT_OWNER = os.environ.get("PROJECTHUB_OWNER") or "b1tninja-labs"
PROJECT_NUMBER = int(os.environ.get("PROJECTHUB_NUMBER") or 1)
# Every entry's issue lives in this one public repository, with a project field: 35 repos are too many to check.
PORTFOLIO_REPO = (
    os.environ.get("PROJECTHUB_PORTFOLIO_REPO") or f"{PROJECT_OWNER}/portfolio"
)
BOARD_URL = f"https://github.com/orgs/{PROJECT_OWNER}/projects/{PROJECT_NUMBER}"
