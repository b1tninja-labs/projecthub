"""checkout: each project lands where the registry puts it, from wherever GitHub now keeps it."""

from __future__ import annotations

import json

from projecthub import checkout as C


def runner(listing: dict[str, list[str]]):
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        if argv[:3] == ["gh", "repo", "list"]:
            return 0, json.dumps(
                [{"nameWithOwner": f"{argv[3]}/{n}"} for n in listing.get(argv[3], [])]
            )
        return 0, ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_a_moved_repo_is_cloned_from_its_new_owner_into_the_registry_directory(
    tmp_path,
):
    run = runner(
        {
            "b1tninja": ["pkix", "registrata", "namespy"],
            "b1tninja-labs": ["pkix", "registrata"],
        }
    )
    assert C.main(["--dest", str(tmp_path)], run) == 0
    clones = {c[-1]: c[-2] for c in run.calls if c[:2] == ["git", "clone"]}  # type: ignore[attr-defined]
    assert clones[str(tmp_path / "pkix")] == "https://github.com/b1tninja-labs/pkix.git"
    assert (
        clones[str(tmp_path / "pyrwhois")]
        == "https://github.com/b1tninja-labs/registrata.git"
    ), "the directory the registry names"
    assert (
        clones[str(tmp_path / "namespy")] == "https://github.com/b1tninja/namespy.git"
    ), "one left on the personal account"


def test_projects_without_a_public_repository_are_reported_not_guessed(
    tmp_path, capsys
):
    run = runner({"b1tninja-labs": ["pkix"]})
    assert C.main(["--dest", str(tmp_path)], run) == 0
    out = capsys.readouterr().out
    assert (
        "checked out 1 of 1" in out
        and "no public repository:" in out
        and "registrata" in out
    )


def test_an_existing_clone_is_left_alone(tmp_path):
    (tmp_path / "pkix" / ".git").mkdir(parents=True)
    run = runner({"b1tninja-labs": ["pkix"]})
    assert C.main(["--dest", str(tmp_path)], run) == 0
    assert not [c for c in run.calls if c[:2] == ["git", "clone"]]  # type: ignore[attr-defined]
