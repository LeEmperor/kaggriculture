"""Fetch the exact upstream source used as the behavioral oracle."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REFERENCE_DIR = Path(__file__).resolve().parent
LOCK_PATH = REFERENCE_DIR / "upstream.lock.json"
DEFAULT_DESTINATION = REFERENCE_DIR / "upstream" / "kaggle-environments"


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    destination = args.destination.resolve()
    repository = lock["repository"]
    commit = lock["commit"]

    if not (destination / ".git").is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            repository,
            str(destination),
        )

    run("git", "fetch", "--depth=1", "origin", commit, cwd=destination)
    run("git", "checkout", "--detach", commit, cwd=destination)
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=destination, text=True
    ).strip()
    if actual != commit:
        raise RuntimeError(f"expected {commit}, checked out {actual}")

    print(f"Pinned oracle ready at {destination}")
    print(f"Revision: {actual}")


if __name__ == "__main__":
    main()
