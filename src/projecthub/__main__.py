"""`python -m projecthub`, which the git hook in shells/githooks runs: the same commands as `projecthub`."""

from __future__ import annotations

from projecthub.cli import main

raise SystemExit(main())
