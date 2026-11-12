"""Create a Rust project (a Cargo crate or workspace) from templates/rust/.

    projecthub shells rust new my-crate --dest D:/code              crate at D:/code/my-crate
    projecthub shells rust new my-crate --dest D:/code --bin        lib plus a src/main.rs binary
    projecthub shells rust new my-crate --dest D:/code --workspace  workspace with crates/my-crate

The name is checked first with shells/names.py: it must be well formed for Cargo/crates.io and not
already published, unless --skip-name-check is given (offline use). Existing files are never
overwritten. Nothing here runs cargo; `projecthub shells verify --rust PATH` does that.

Edition 2024 needs Rust 1.85 (the Edition Guide lists 1.85.0 as its release version), so MSRV is 1.85.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Callable

from projecthub.shells import names

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATES = HERE / "templates" / "rust"
LICENSE_SRC = HERE / "templates" / "LICENSE-2.0.txt"
EDITION = "2024"
MSRV = "1.85"

Fetcher = Callable[[str], tuple[int, bytes]]

# Files common to a crate and a workspace root: destination -> template, relative to templates/rust.
COMMON = {
    "rust-toolchain.toml": "rust-toolchain.toml",
    "rustfmt.toml": "rustfmt.toml.tpl",
    "clippy.toml": "clippy.toml.tpl",
    ".gitignore": "gitignore",
    "README.md": "README.md.tpl",
    "AGENTS.md": "AGENTS.md.tpl",
    "CONTRIBUTING.md": "CONTRIBUTING.md.tpl",
    "docs/layout.md": "docs/layout.md.tpl",
    "docs/msrv.md": "docs/msrv.md.tpl",
    ".github/workflows/ci.yml": "workflows/ci.yml.tpl",
}

LAYOUT_CRATE = "A single crate: `Cargo.toml`, `src/lib.rs`{bin}, `tests/`."
LAYOUT_WORKSPACE = (
    "A Cargo workspace. The root `Cargo.toml` holds shared package metadata and the lint table; each\n"
    "member lives in `crates/<name>/` and inherits both with `workspace = true`. Add a crate by adding a\n"
    "directory under `crates/`."
)


def ident(name: str) -> str:
    """The crate's identifier in Rust source: Cargo turns `-` into `_`."""
    return name.replace("-", "_")


def _fill(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace("@" + key + "@", value)
    return text


def render(
    name: str,
    *,
    workspace: bool = False,
    bin: bool = False,
    description: str | None = None,
) -> dict[str, bytes]:
    """Every file of the new project as relative path -> bytes. Raises ValueError for a bad name."""
    problems = names.crate_name_problems(name)
    if problems:
        raise ValueError(name + ": " + "; ".join(problems))
    mapping = {
        "NAME": name,
        "IDENT": ident(name),
        "EDITION": EDITION,
        "MSRV": MSRV,
        "DESCRIPTION": description or name + ": a Rust crate",
        "KIND": "workspace" if workspace else "crate",
        "LAYOUT": LAYOUT_WORKSPACE
        if workspace
        else LAYOUT_CRATE.format(bin=" and `src/main.rs`" if bin else ""),
    }
    plan = dict(COMMON)
    base = "crates/" + name + "/" if workspace else ""
    if workspace:
        plan["Cargo.toml"] = "workspace.Cargo.toml.tpl"
        plan[base + "Cargo.toml"] = "member.Cargo.toml.tpl"
    else:
        plan["Cargo.toml"] = "Cargo.toml.tpl"
    plan[base + "src/lib.rs"] = "src/lib.rs.tpl"
    plan[base + "tests/smoke.rs"] = "tests/smoke.rs.tpl"
    if bin:
        plan[base + "src/main.rs"] = "src/main.rs.tpl"
    files: dict[str, bytes] = {}
    for rel, template in plan.items():
        text = (TEMPLATES / template).read_text(encoding="utf-8")
        text = _fill(text, mapping)
        if workspace and rel == base + "Cargo.toml":
            text = text.replace('readme = "README.md"', 'readme = "../../README.md"')
        files[rel] = text.replace("\r\n", "\n").encode("utf-8")
    files["LICENSE"] = LICENSE_SRC.read_bytes().replace(b"\r\n", b"\n")
    return files


def write(files: dict[str, bytes], dest: pathlib.Path) -> list[str]:
    """Write `files` under `dest`, refusing before any write if one already exists."""
    clash = sorted(rel for rel in files if (dest / rel).exists())
    if clash:
        raise FileExistsError("refusing to overwrite: " + ", ".join(clash))
    for rel, data in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return sorted(files)


def check_name(name: str, fetcher: Fetcher = names.crate_fetch) -> str | None:
    """None when the name is usable; otherwise the reason, including 'could not check'."""
    status, rows = names.run_crates([name], fetcher, sleep=0.0)
    if not rows:
        return "crates.io canaries failed; could not check " + name
    verdict = rows[0]["verdict"]
    if verdict == "FREE":
        return None
    detail = "; ".join(rows[0]["problems"]) if verdict == "INVALID" else ""
    return name + " is " + verdict + (" (" + detail + ")" if detail else "")


def main(argv: list[str] | None = None, fetcher: Fetcher = names.crate_fetch) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub shells rust", description="Create a Rust crate or workspace."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="create a project")
    new.add_argument("name")
    new.add_argument(
        "--dest", type=pathlib.Path, required=True, help="parent directory"
    )
    new.add_argument(
        "--workspace", action="store_true", help="a workspace with crates/<name>"
    )
    new.add_argument("--bin", action="store_true", help="also add src/main.rs")
    new.add_argument("--description")
    new.add_argument(
        "--skip-name-check", action="store_true", help="do not ask crates.io"
    )
    args = parser.parse_args(argv)
    if not args.skip_name_check:
        reason = check_name(args.name, fetcher)
        if reason:
            print("shells rust: " + reason, file=sys.stderr)
            return 2
    try:
        files = render(
            args.name,
            workspace=args.workspace,
            bin=args.bin,
            description=args.description,
        )
        root = args.dest / args.name
        written = write(files, root)
    except (ValueError, FileExistsError) as error:
        print("shells rust: " + str(error), file=sys.stderr)
        return 2
    print("created " + str(root) + " (" + str(len(written)) + " files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
