"""The heading check is the one that has to be right, because its job is to catch a mistake that
looks like an ordinary documentation edit. These cover the cases that actually happened.
"""

from __future__ import annotations

from projecthub.shells import hygiene as h


def test_reports_only_the_lost_heading():
    before = "# Title\n\n## One\n\n## Two\n"
    after = "# Title\n\n## One\n"
    assert h.heading_regression(before, after) == ["## Two"]


def test_duplicate_headings_are_counted_not_deduplicated():
    # The most common accident: two sections with the same name, one replaced away. A set difference
    # would report nothing here.
    assert h.heading_regression("## Notes\n\n## Notes\n", "## Notes\n") == ["## Notes"]


def test_reordered_sections_are_not_a_regression():
    assert h.heading_regression("## A\n## B\n", "## B\n## A\n") == []


def test_unspaced_hashes_are_prose():
    assert h.heading_regression("# Title\n\n#fixme later\n", "# Title\n") == []


def test_total_deletion_is_reported():
    assert h.heading_regression("# Title\n", "") == ["# Title"]


def test_scratch_shapes_are_recognised():
    for name in (
        "probe_ber.py",
        "projecthub/probe_x.py",
        "overlap.log",
        "run.out",
        "stderr.err",
        "a.diff",
    ):
        assert h.SCRATCH_RE.search(name), name


def test_real_sources_are_not_scratch():
    for name in (
        "generate.py",
        "verify.py",
        "sync_roadmap.py",
        "docs/rfc-map.md",
        "parts/part1.py",
    ):
        assert not h.SCRATCH_RE.search(name), name
