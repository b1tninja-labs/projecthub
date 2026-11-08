# planforge

A local planning database for coding agents. Agents read and change the plan through tools, never through a
markdown file: typed entries (epics, obligations, tasks, bugs, questions, decisions, findings, rules), links
between them, and citations that are checked against the source. An obligation is done when an evidence record
says so: its named test exists, passes, fails on the old code, asserts something, and catches a planted bug.
GitHub Projects is the display and the off-machine copy.

The repository is `b1tninja-labs/forge`; the package and the command are `planforge`, because `forge` is taken
on PyPI and is already a widely used command.

```bash
pip install -e ".[dev]"
planforge --help
python -m pytest -q
```

Status: early. The design and the decisions behind it are recorded in the portfolio's PORTFOLIO.md, section 9,
items 10 and 11.
