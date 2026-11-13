"""PORTFOLIO.md's waves and decisions are quoted, not paraphrased, so the parse is what is pinned here."""

from __future__ import annotations

from projecthub import registry as p

TEXT = """# Plan

## 7. Waves and sequencing

**Wave 0 - hygiene, no features.** Give every project a history. *Exit:* thirty directories
with `git log`.

*Wave 0 status:* later prose that is not the exit test.

**Wave 1 - the chokepoint.** Build it. *Exit:* the corpus passes.

**Wave 5 - the rest, in tier order, one at a time.**

**Hard rule:** two at once.

## 8. Next

## 9. Decisions that need a human

1. **`asn1core`, and how.** Long story. **Decided 2026-11-05 and carried out.**
2. **`certledger` as a flagship service.** Costs money.
   Second line.
9. **`trustatlas`, and what it may publish.** *Name:* **decided 2026-11-05.** Posture open.

## 10. Log
"""


def test_waves_carry_their_exit_test_and_only_that():
    waves = {w.number: w for w in p.waves(TEXT)}
    assert sorted(waves) == [0, 1, 5]
    assert waves[0].title == "hygiene, no features"
    assert waves[0].exit == "thirty directories with `git log`."
    assert waves[1].exit == "the corpus passes."
    assert waves[5].exit == ""


def test_only_a_capitalised_bold_decided_closes_a_decision():
    found = {
        d.number: d for d in p.decisions(TEXT, {"asn1core", "certledger", "trustatlas"})
    }
    assert found[1].decided
    assert not found[2].decided
    assert not found[9].decided, (
        "one sub-choice decided in lower case does not close the decision"
    )
    assert found[2].projects == ("certledger",)
    assert "Second line." in found[2].body


def test_every_entry_has_a_known_kind_and_the_reference_libraries_are_in():
    entries = p.entries()
    assert all(e.kind in p.KINDS for e in entries)
    names = {e.name for e in entries}
    assert {
        "rfclib",
        "primarysource",
        "zimlore",
        "asn1core",
        "certledger",
        "pkix",
    } <= names
    assert {e.name: e.directory for e in entries}["rfclib"] == "rfclibrary"
