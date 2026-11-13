"""`projecthub shells`: the command surface, the git hook it installs, and what the move renamed.

The hook is tested as a shell script, not only as the Python it calls, because its one invariant lives in
the script: when it cannot find projecthub or an interpreter it exits 1, never 0. A hook that cannot run
and exits 0 reports the same green as a clean tree.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

from projecthub import cli, paths
from projecthub.shells import cli as shells_cli
from projecthub.shells import generate, hygiene

PKG = pathlib.Path(generate.__file__).resolve().parent
REPO = PKG.parents[2]
HOOK = PKG / "githooks" / "pre-commit"
SH = shutil.which("sh")


def git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A repository with one committed markdown file, and its second heading deleted in the index."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "notes.md").write_text("# Title\n\n## Keep\n\n## Lose\n", encoding="utf-8")
    git(root, "add", "notes.md")
    git(root, "commit", "-q", "--no-verify", "-m", "start")
    (root / "notes.md").write_text("# Title\n\n## Keep\n", encoding="utf-8")
    git(root, "add", "notes.md")
    return root


# ---------------------------------------------------------------- the command surface


def test_projecthub_has_one_shells_entry():
    assert cli.COMMANDS["shells"][0] == "projecthub.shells.cli"
    assert not [name for name in cli.COMMANDS if "forge" in name]


def test_every_subcommand_names_a_module_with_an_entry_point():
    import importlib

    for name, (module, _) in shells_cli.COMMANDS.items():
        loaded = importlib.import_module(module)
        entry = "run" if name == "generate" else "main"
        assert callable(getattr(loaded, entry)), name
    assert {"generate", "verify", "names", "hygiene", "hook"} <= set(
        shells_cli.COMMANDS
    )


def test_help_and_unknown_commands(capsys):
    assert shells_cli.main([]) == 0
    assert "projecthub shells <command>" in capsys.readouterr().out
    assert shells_cli.main(["nosuch"]) == 2
    assert shells_cli.main(["hook"]) == 2
    assert shells_cli.main(["hook", "post-merge"]) == 2


def test_generate_plan_without_a_name_is_a_usage_error(capsys):
    assert shells_cli.main(["generate", "--plan"]) == 2
    assert "projecthub shells generate --plan NAME" in capsys.readouterr().err


# ---------------------------------------------------------------- hook pre-commit


def test_hook_refuses_a_lost_heading(repo, monkeypatch, capsys):
    monkeypatch.delenv("PROJECTHUB_ALLOW_HEADING_LOSS", raising=False)
    monkeypatch.delenv("FORGE_ALLOW_HEADING_LOSS", raising=False)
    monkeypatch.chdir(repo)
    assert shells_cli.main(["hook", "pre-commit"]) == 1
    err = capsys.readouterr().err
    assert "notes.md: heading vanished: ## Lose" in err
    assert "PROJECTHUB_ALLOW_HEADING_LOSS=1" in err


@pytest.mark.parametrize(
    "variable", ["PROJECTHUB_ALLOW_HEADING_LOSS", "FORGE_ALLOW_HEADING_LOSS"]
)
def test_heading_loss_override_reads_the_new_name_then_the_old(
    repo, monkeypatch, variable
):
    monkeypatch.delenv("PROJECTHUB_ALLOW_HEADING_LOSS", raising=False)
    monkeypatch.delenv("FORGE_ALLOW_HEADING_LOSS", raising=False)
    monkeypatch.setenv(variable, "1")
    monkeypatch.chdir(repo)
    assert shells_cli.main(["hook", "pre-commit"]) == 0


def test_the_shipped_hook_is_the_one_install_writes():
    assert HOOK.read_bytes() == hygiene.HOOK.encode("utf-8")
    text = hygiene.HOOK
    assert text.startswith("#!/bin/sh\n")
    assert "-m projecthub shells hook pre-commit" in text
    assert "exit 1" in text
    assert not re.search(r"\bexit 0\b", text), "a hook that can exit 0 unchecked"
    assert "\r" not in text


def _run_hook(hook: pathlib.Path, cwd: pathlib.Path, **env: str):
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHON", "PROJECTHUB_SRC", "PYTHONPATH")
    }
    return subprocess.run(
        [SH, hook.as_posix()],
        cwd=str(cwd),
        env={**clean, **env},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh")
def test_the_hook_exits_1_when_projecthub_cannot_be_imported(tmp_path):
    fake = tmp_path / "fakepython"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    copied = tmp_path / "hooks" / "pre-commit"
    copied.parent.mkdir()
    shutil.copyfile(HOOK, copied)
    done = _run_hook(copied, tmp_path, PYTHON=fake.as_posix())
    assert done.returncode == 1, done
    assert "cannot run the projecthub hygiene check" in done.stderr


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh")
def test_the_hook_resolves_its_own_checkout_and_runs_the_check(repo):
    python = pathlib.Path(sys.executable).as_posix()
    done = _run_hook(
        HOOK,
        repo,
        PYTHON=python,
        PROJECTHUB_ALLOW_HEADING_LOSS="",
        FORGE_ALLOW_HEADING_LOSS="",
    )
    assert done.returncode == 1, done
    assert "heading vanished: ## Lose" in done.stderr
    git(repo, "reset", "-q", "--hard")
    assert _run_hook(HOOK, repo, PYTHON=python).returncode == 0


# ---------------------------------------------------------------- pins and package data


def test_the_shipped_pins_match_the_dev_extras():
    def pins(lines):
        return {
            name: version
            for name, version in (
                line.split("==", 1)
                for line in lines
                if "==" in line and not line.startswith("#")
            )
        }

    shipped = pins(
        (PKG / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    )
    extras = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    dev = pins(extras["project"]["optional-dependencies"]["dev"])
    assert shipped == {k: v for k, v in dev.items() if k in shipped}
    assert {"ruff", "mypy", "pytest"} <= set(shipped)


def test_the_generator_finds_its_data_beside_it():
    assert generate.LICENSE_SRC.is_file()
    assert (PKG / "templates" / "index.md.tpl").is_file()
    assert (PKG / "templates" / "rust" / "Cargo.toml.tpl").is_file()
    if not paths.env("MANIFEST"):
        assert generate.MANIFEST == PKG / "data" / "generated.json"


# ---------------------------------------------------------------- what the move renamed


def test_generated_text_names_projecthub_not_the_old_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "ROOT", tmp_path)
    old = ("sdk-forge", "forge.py", ".forge/", "FORGE_")
    for project in generate.ALL_PROJECTS:
        for rel, data in generate.render_project(project).items():
            text = data.decode("utf-8", "replace")
            assert not [word for word in old if word in text], (project["name"], rel)
    files = generate.render_project(
        next(p for p in generate.ALL_PROJECTS if p["name"] == "rpkit")
    )
    assert (
        b"python -m projecthub pickup --who <your name> --project rpkit"
        in files["AGENTS.md"]
    )
    plan = " ".join(files["docs/plan.md"].decode("utf-8").split())
    assert generate.PLAN_MARKER in plan
    index = generate.render_index(generate.ALL_PROJECTS)
    assert "`projecthub shells generate`" in " ".join(index.split())


def test_a_plan_with_the_old_generated_header_is_still_recognised(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(generate, "ROOT", tmp_path)
    plan = tmp_path / "docs" / "plan.md"
    plan.parent.mkdir()
    old_header = (
        "This file is generated by "
        + "sdk-forge/"
        + "forge.py from the portfolio plan."
    )
    plan.write_text("# Portfolio position\n\n" + old_header + "\n", encoding="utf-8")
    found = generate.decide(
        "tacacs", tmp_path, {"docs/plan.md": b"new\n"}, {"docs/plan.md": "plan"}, {}
    )
    assert [(d.action, d.reason) for d in found] == [
        ("update", "unrecorded, but it carries the generated-file header")
    ]


def test_the_generators_position_table_agrees_with_the_registry():
    from projecthub.data import POSITION as registered

    assert {
        name: (tier, wave, tuple(deps))
        for name, (tier, wave, deps) in generate.POSITION.items()
    } == dict(registered)
