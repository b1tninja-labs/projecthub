# projecthub

A local planning database for coding agents. Agents read and change the plan through tools, never through a
markdown file: typed entries (epics, obligations, tasks, bugs, questions, decisions, findings, rules), links
between them, and citations that are checked against the source. An obligation is done when an evidence record
says so: its named test exists, passes, fails on the old code, asserts something, and catches a planted bug.
GitHub Projects is the display and the off-machine copy.

```bash
pip install -e ".[dev]"
projecthub --help
python -m pytest -q
```

## For agents

Run `projecthub pickup --who <your name>`. Its output is your assignment: one row, already claimed for you, with
the commands that prove it and the ways to end it. `NO WORK` is a real answer: ask the person what to work on.

To reach the planning tools over MCP, register `projecthub mcp`; [docs/agents.md](docs/agents.md) has the config
for Claude Code, Continue and Cline.

## Shells

`projecthub shells` generates the protocol SDK shells in the workspace, runs the five gates across them, and
is the pre-commit hook the workspace uses (`projecthub shells generate --dry-run`, `projecthub shells verify`).
It is the generator that used to be the separate sdk-forge tool; see [docs/shells.md](docs/shells.md).

Status: early. The design and the decisions behind it are recorded in the portfolio's PORTFOLIO.md, section 9,
items 10 and 11.
