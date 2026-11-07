"""What a person or agent needs to know to pick up one ROADMAP row, assembled from files that already exist.

The first issues this forge filed were a title, a spec name and a boilerplate paragraph. That told a reader
neither which repository the work was in, nor what the function is for, nor which milestone it serves, nor
whether the test it names exists. Every one of those facts is already on disk; nothing here invents text.

Sources, each named in the output so a reader can check it:

- the project's one-line description, from its AGENTS.md;
- the stub: module docstring, signature and whether it still raises NotImplementedError, read with `ast`
  and never imported, so a broken module cannot stop the sync;
- the milestone ladder in ROADMAP.md, and the notes in ROADMAP.md that mention the row by name;
- the errata lines ROADMAP.md records for the document the row cites;
- whether the named test file and symbol exist;
- the state of each dependency the project's plan lists, read from the workspace.

It never says a dependency is "ready" or a row is "unblocked": see deps.py for why.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass

import roadmap

# The grammar lives in roadmap.py; the name stays importable from here for callers that already use it.
TEST_REF = roadmap.TEST_REF


@dataclass(frozen=True)
class Stub:
    path: str
    signature: str
    module_summary: str
    still_stub: bool


def project_blurb(root: pathlib.Path, name: str) -> str:
    """The first paragraph under the heading in AGENTS.md: what the library is for."""
    path = root / name / "AGENTS.md"
    if not path.is_file():
        return ""
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        block = block.strip()
        if block and not block.startswith("#"):
            return " ".join(block.split())
    return ""


def stub_of(root: pathlib.Path, name: str, row_name: str) -> Stub | None:
    """Locate `module.function` in the project's package and describe it, without importing it."""
    module, _, function = row_name.partition(".")
    path = root / name / name.replace("-", "_") / f"{module}.py"
    if not (function and path.is_file()):
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    summary = " ".join((ast.get_docstring(tree) or "").split("\n\n")[0].split())
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == function
        ):
            returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            signature = f"{function}({ast.unparse(node.args)}){returns}"
            raises = any(
                isinstance(n, ast.Raise)
                and "NotImplementedError" in ast.unparse(n.exc or ast.Constant(""))
                for n in ast.walk(node)
            )
            relative = path.relative_to(root / name).as_posix()
            return Stub(relative, signature, summary, raises)
    return None


def short_summary(stub: Stub | None, limit: int = 64) -> str:
    """A title-sized phrase: the module docstring's first clause, without its trailing citation.

    Cut at the first colon, semicolon or dash rather (a comma would leave a list's first item) than at a character count, so a title never
    ends mid-phrase; a clause still too long is cut at a word and marked.
    """
    if stub is None or not stub.module_summary:
        return ""
    text = re.sub(r"\s*\([^()]*\)\.?$", "", stub.module_summary).rstrip(".")
    text = re.split(r"[:;]|\s[-–—]\s", text, maxsplit=1)[0].strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;: ") + " ..."


def ladder_of(text: str, form: str = "M") -> dict[str, str]:
    """Milestone id -> its full description, continuation lines included. See roadmap.ladder_of for forms."""
    return roadmap.ladder_of(text, form)


def milestone_title(milestone: str, ladder: dict[str, str], limit: int = 120) -> str:
    """`M1 - anchors and chain discovery ...`: a milestone that names itself on a board."""
    if not milestone:
        return ""
    summary = ladder.get(milestone, "").split(":")[0].strip().rstrip(".")
    if not summary:
        return milestone
    if len(summary) > limit:
        summary = summary[:limit].rsplit(" ", 1)[0].rstrip(",;: ")
    return f"{milestone} - {summary}"


def notes_for(text: str, row_title: str, limit: int = 3, width: int = 900) -> list[str]:
    """Prose paragraphs outside the matrix that mention the row by name."""
    needle = f"`{row_title}`"
    notes: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if needle in block and not block.startswith("|") and not block.startswith("#"):
            flat = " ".join(block.split())
            notes.append(
                flat if len(flat) <= width else flat[:width].rsplit(" ", 1)[0] + " ..."
            )
    return notes[:limit]


def errata_lines(text: str, spec: str) -> list[str]:
    """The errata-section lines that mention any document the spec cell cites."""
    return roadmap.errata_lines(text, spec)


def test_evidence(
    root: pathlib.Path, project: str, where: str, evidence: str = ""
) -> tuple[bool, str]:
    """Do the row's named tests exist, by exact, collectable name? One implementation: roadmap.evidence.

    A floor, not the rule: only CI can show a test would fail if the behaviour regressed. What this
    refuses is the cheaper lie, a row pointing at a file or a symbol nobody wrote. A reference marked
    `(to be written)` never counts.
    """
    return roadmap.evidence(root, project, where, evidence)


def dependency_lines(root: pathlib.Path, name: str) -> list[str]:
    """One line per dependency in the project's plan, with its state read from the workspace."""
    import deps  # lazy: deps imports sync_roadmap, which imports this module

    lines = []
    for edge in deps.edges(root, name):
        state = deps.dependency_state(root, edge.dependency).state
        lines.append(f"`{edge.dependency}`: {state}")
    return lines


def siblings(rows: list[dict[str, str]], milestone: str, own: str) -> list[str]:
    return [
        r["title"]
        for r in rows
        if milestone and r["milestone"] == milestone and r["name"] != own
    ]
