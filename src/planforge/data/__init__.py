"""Data planforge owns: the project registry, and the curated list of other public repositories."""

from __future__ import annotations

import json
import pathlib

_RAW = json.loads(
    (pathlib.Path(__file__).parent / "registry.json").read_text(encoding="utf-8")
)

PROJECTS: list[str] = list(_RAW["projects"])
POSITION: dict[str, tuple[int, int, tuple[str, ...]]] = {
    name: (value[0], value[1], tuple(value[2]))
    for name, value in _RAW["position"].items()
}
