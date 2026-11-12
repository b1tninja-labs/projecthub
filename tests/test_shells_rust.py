"""Rust support: crates.io name checks, project generation, and the Rust gates.

No test here touches the network: crates.io is replaced by a fake fetcher. Tests that run cargo skip with a
reason when cargo is absent; the generation tests do not need it.
"""

from __future__ import annotations

import json
import shutil
import tomllib

import pytest

from projecthub.shells import names
from projecthub.shells import rust
from projecthub.shells import verify

CANARY_TAKEN = names.CRATES_MUST_EXIST[0]


def fake(taken: dict[str, int] | None = None, default: int = 404):
    """A crates.io stand-in: a status per crate name (it folds case and -/_ as the real API does)."""
    table = {
        names.crate_canonical(k): v
        for k, v in ({CANARY_TAKEN: 200} if taken is None else taken).items()
    }
    seen: list[str] = []

    def fetcher(url: str) -> tuple[int, bytes]:
        seen.append(url)
        assert url.startswith(names.CRATES_API)
        key = names.crate_canonical(url.removeprefix(names.CRATES_API))
        status = table.get(key, default)
        body = b""
        if status == 200:
            body = json.dumps({"crate": {"name": key, "max_version": "1.2.3"}}).encode()
        return status, body

    fetcher.seen = seen  # type: ignore[attr-defined]
    return fetcher


# ------------------------------------------------------------------------------------ names


def test_a_404_is_free_and_a_200_is_taken_with_its_version():
    f = fake({CANARY_TAKEN: 200, "widget": 200})
    assert names.probe_crate("free-one", f)["verdict"] == "FREE"
    row = names.probe_crate("widget", f)
    assert (row["verdict"], row["version"]) == ("TAKEN", "1.2.3")


def test_case_and_dash_underscore_variants_are_one_name():
    f = fake({CANARY_TAKEN: 200, "serde_json": 200})
    for variant in ("serde-json", "SERDE_JSON", "Serde-Json"):
        assert names.probe_crate(variant, f)["verdict"] == "TAKEN"
    assert names.crate_canonical("My-Crate") == "my_crate"


def test_malformed_names_are_refused_before_any_request():
    f = fake()
    for bad in ("", "has space", "dot.name", "fn", "nul", "1abc", "café", "a" * 65):
        row = names.probe_crate(bad, f)
        assert row["verdict"] == "INVALID", bad
        assert row["problems"], bad
    assert f.seen == []
    assert names.crate_name_problems("a" * 64) == []


def test_the_request_carries_a_generic_user_agent_and_the_api_url():
    agent = names.CRATES_UA["User-Agent"]
    assert "@" not in agent
    assert agent.startswith("projecthub-names/")
    f = fake()
    names.probe_crate("my-crate", f)
    assert f.seen == ["https://crates.io/api/v1/crates/my-crate"]


def test_non_verdict_statuses_are_never_reported_free():
    assert names.crate_verdict(429) == "RATE-LIMITED"
    assert names.crate_verdict(-1) == "ERROR"
    assert names.crate_verdict(500) == "UNKNOWN"
    assert names.crate_verdict(403) == "UNKNOWN"
    status, rows = names.run_crates(["x-y"], fake(default=500), sleep=0)
    assert status == 2
    assert not rows or rows[0]["verdict"] != "FREE"


def test_a_lying_registry_is_refused_by_the_canaries():
    always_404 = fake({}, default=404)
    assert names.check_crate_canaries(always_404)
    assert names.run_crates(["anything"], always_404, sleep=0) == (2, [])
    assert names.check_crate_canaries(fake({}, default=200))


def test_exit_status_one_when_any_name_is_taken():
    f = fake({CANARY_TAKEN: 200, "busy": 200})
    assert names.run_crates(["free-a", "free-b"], f, sleep=0)[0] == 0
    assert names.run_crates(["free-a", "busy"], f, sleep=0)[0] == 1


# ---------------------------------------------------------------------------------- templates


def text(files: dict[str, bytes], rel: str) -> str:
    return files[rel].decode("utf-8")


def test_a_crate_has_every_expected_file_and_no_unfilled_placeholder():
    files = rust.render("my-crate", bin=True)
    expected = {
        "Cargo.toml",
        "src/lib.rs",
        "src/main.rs",
        "tests/smoke.rs",
        "README.md",
        "LICENSE",
        ".gitignore",
        "rust-toolchain.toml",
        "rustfmt.toml",
        "clippy.toml",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "docs/layout.md",
        "docs/msrv.md",
        ".github/workflows/ci.yml",
    }
    assert expected <= set(files)
    for rel, data in files.items():
        assert b"\r" not in data, rel
        if rel != "LICENSE":
            for key in (
                "NAME",
                "IDENT",
                "EDITION",
                "MSRV",
                "DESCRIPTION",
                "KIND",
                "LAYOUT",
            ):
                assert f"@{key}@".encode() not in data, (rel, key)


def test_manifest_edition_msrv_and_lints():
    files = rust.render("my-crate")
    package = tomllib.loads(text(files, "Cargo.toml"))
    assert package["package"]["name"] == "my-crate"
    assert (package["package"]["edition"], package["package"]["rust-version"]) == (
        "2024",
        "1.85",
    )
    assert package["lints"]["rust"]["unsafe_code"] == "forbid"
    assert package["lints"]["clippy"]["all"]["level"] == "warn"
    assert tomllib.loads(text(files, "clippy.toml"))["msrv"] == "1.85"


def test_toolchain_is_pinned_to_stable_with_fmt_and_clippy():
    files = rust.render("my-crate")
    tool = tomllib.loads(text(files, "rust-toolchain.toml"))["toolchain"]
    assert tool["channel"] == "stable"
    assert {"rustfmt", "clippy"} <= set(tool["components"])


def test_sources_use_the_underscored_identifier():
    files = rust.render("my-crate", bin=True)
    assert "my_crate::name()" in text(files, "src/main.rs")
    assert "my_crate::name()" in text(files, "tests/smoke.rs")
    assert "src/main.rs" not in rust.render("my-crate")


def test_agents_file_lists_the_four_commands_and_ci_runs_them():
    files = rust.render("my-crate")
    commands = (
        "cargo fmt --check",
        "cargo clippy --all-targets -- -D warnings",
        "cargo test",
        "cargo doc --no-deps",
    )
    for command in commands:
        assert command in text(files, "AGENTS.md"), command
        assert command in text(files, ".github/workflows/ci.yml"), command
    assert "rust-toolchain@1.85" in text(files, ".github/workflows/ci.yml")


def test_workspace_layout_inherits_from_the_root():
    files = rust.render("my-crate", workspace=True)
    root = tomllib.loads(text(files, "Cargo.toml"))
    member = tomllib.loads(text(files, "crates/my-crate/Cargo.toml"))
    assert root["workspace"]["members"] == ["crates/*"]
    assert root["workspace"]["package"]["edition"] == "2024"
    assert member["lints"] == {"workspace": True}
    assert member["package"]["edition"] == {"workspace": True}
    assert "crates/my-crate/src/lib.rs" in files
    assert "src/lib.rs" not in files


def test_write_refuses_to_overwrite_and_writes_nothing(tmp_path):
    files = rust.render("my-crate")
    (tmp_path / "README.md").write_text("mine", encoding="utf-8")
    with pytest.raises(FileExistsError):
        rust.write(files, tmp_path)
    assert not (tmp_path / "Cargo.toml").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "mine"


def test_new_refuses_a_taken_or_invalid_name_and_creates_a_free_one(tmp_path, capsys):
    f = fake({CANARY_TAKEN: 200, "busy": 200})
    assert rust.main(["new", "busy", "--dest", str(tmp_path)], f) == 2
    assert "TAKEN" in capsys.readouterr().err
    assert rust.main(["new", "fn", "--dest", str(tmp_path)], f) == 2
    assert not (tmp_path / "busy").exists()
    assert rust.main(["new", "fresh-one", "--dest", str(tmp_path)], f) == 0
    assert (tmp_path / "fresh-one" / "Cargo.toml").is_file()


def test_skip_name_check_makes_no_request(tmp_path):
    def boom(url: str) -> tuple[int, bytes]:
        raise AssertionError("network used")

    argv = ["new", "offline", "--dest", str(tmp_path), "--skip-name-check"]
    assert rust.main(argv, boom) == 0


# ------------------------------------------------------------------------------------- gates


def test_the_rust_gates_are_the_four_commands_plus_compile_and_doc():
    assert verify.RUST_GATES["format"] == ["fmt", "--check"]
    assert verify.RUST_GATES["ruff"] == [
        "clippy",
        "--all-targets",
        "--",
        "-D",
        "warnings",
    ]
    assert verify.RUST_GATES["pytest"] == ["test"]
    assert verify.RUST_GATES["doc"] == ["doc", "--no-deps"]
    assert set(verify.GATES) - set(verify.RUST_GATES) == {"mypy"}
    assert "mypy" in verify.RUST_NOT_APPLICABLE


def test_verify_rust_reports_each_gate_and_the_not_applicable_one(tmp_path, capsys):
    calls = []

    def runner(args, cwd):
        calls.append(args)
        return (1, "", "boom") if args[0] == "clippy" else (0, "", "")

    failures = verify.verify_rust(tmp_path, runner)
    line = capsys.readouterr().out
    assert [c[0] for c in calls] == ["check", "test", "clippy", "fmt", "doc"]
    assert "clippy=FAIL" in line
    assert "fmt=OK" in line
    assert "mypy=n/a" in line
    assert [(f[1], f[3]) for f in failures] == [("clippy", "boom")]


def test_main_rust_needs_cargo_and_a_manifest(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    assert verify.main(["--rust", str(tmp_path)]) == 2
    assert "needs cargo" in capsys.readouterr().err
    monkeypatch.setattr(verify.shutil, "which", lambda name: "cargo")
    assert verify.main(["--rust", str(tmp_path)]) == 2
    assert "no Cargo.toml" in capsys.readouterr().err


def test_main_rust_exit_status_follows_the_gates(monkeypatch, tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    monkeypatch.setattr(verify.shutil, "which", lambda name: "cargo")
    results = iter([(0, "", "")] * 5 + [(1, "o", "e")] * 5)
    monkeypatch.setattr(verify, "run_cargo", lambda a, c, timeout=None: next(results))
    original = verify.verify_rust
    monkeypatch.setattr(
        verify, "verify_rust", lambda cwd: original(cwd, verify.run_cargo)
    )
    assert verify.main(["--rust", str(tmp_path)]) == 0
    assert verify.main(["--rust", str(tmp_path)]) == 1


def test_a_missing_cargo_is_127_not_a_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    assert verify.run_cargo(["check"], tmp_path)[0] == 127


needs_cargo = pytest.mark.skipif(
    shutil.which("cargo") is None, reason="cargo is not installed"
)


@needs_cargo
@pytest.mark.parametrize("workspace", [False, True])
def test_a_generated_project_passes_its_own_gates(tmp_path, workspace):
    files = rust.render("gen-check", workspace=workspace, bin=True)
    rust.write(files, tmp_path)
    assert verify.verify_rust(tmp_path) == []


@needs_cargo
def test_the_gates_catch_a_badly_formatted_file(tmp_path):
    rust.write(rust.render("gen-check"), tmp_path)
    lib = tmp_path / "src" / "lib.rs"
    lib.write_text(
        lib.read_text(encoding="utf-8") + "\nfn   ugly( ) {}\n", encoding="utf-8"
    )
    assert "fmt" in {f[1] for f in verify.verify_rust(tmp_path)}
