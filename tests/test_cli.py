"""The command line: every subcommand names a real module with a main, and the registry loads."""

from __future__ import annotations

import importlib

import pytest

from planforge import cli
from planforge.data import POSITION, PROJECTS


@pytest.mark.parametrize("name", sorted(cli.COMMANDS))
def test_every_command_names_a_module_with_a_main(name):
    module = importlib.import_module(cli.COMMANDS[name][0])
    assert callable(module.main)


def test_help_lists_every_command(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["planforge", "--help"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert all(name in out for name in cli.COMMANDS)


def test_an_unknown_command_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["planforge", "nope"])
    assert cli.main() == 2
    assert "usage" in capsys.readouterr().out


def test_the_registry_loads_with_tuples_as_the_code_expects():
    assert "pkix" in PROJECTS
    tier, wave, depends = POSITION["pkix"]
    assert (
        isinstance(tier, int) and isinstance(wave, int) and isinstance(depends, tuple)
    )
