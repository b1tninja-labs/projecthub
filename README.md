# projecthub

Planning for coding agents, kept on GitHub Project 1: the Project holds every issue, its Status and milestone,
who is working on it and when they last touched it, and agents claim, track and finish work there the way a team
uses a Jira board. The local SQLite store is only a write queue for the Project and a short-lived read cache, and
note text stays in it, never published. Agents change the plan through tools, as typed entries with links and
citations checked against the source, and an obligation is done only when an evidence record says its named test
exists, passes, fails on the old code, asserts something and catches a planted bug.

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
