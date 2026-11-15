"""Scan tracked and staged repository files for common credential prefixes."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "Meko token": re.compile(rb"mko" + rb"_tkn_"),
    "Google API key": re.compile(rb"AI" + rb"za"),
    "GitHub token": re.compile(rb"ghp" + rb"_"),
}
SECRET_FILE = ".env"


def git_bytes(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def tracked_paths() -> set[Path]:
    paths = git_bytes("ls-files", "-z").split(b"\0")
    staged = git_bytes("diff", "--cached", "--name-only", "-z").split(b"\0")
    return {
        Path(os.fsdecode(path))
        for path in (*paths, *staged)
        if path
    }


def find_matches(content: bytes) -> list[str]:
    return [label for label, pattern in PATTERNS.items() if pattern.search(content)]


def main() -> int:
    findings: list[tuple[str, str]] = []
    for relative_path in sorted(tracked_paths(), key=lambda path: str(path).casefold()):
        if relative_path.name.casefold() == SECRET_FILE.casefold():
            findings.append((relative_path.as_posix(), "credential file must not be tracked"))
            continue

        worktree_path = ROOT / relative_path
        if worktree_path.is_file():
            matches = find_matches(worktree_path.read_bytes())
            findings.extend((relative_path.as_posix(), match) for match in matches)

        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", relative_path.as_posix()],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if staged.returncode == 1:
            blob = subprocess.run(
                ["git", "show", f":{relative_path.as_posix()}"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if blob.returncode == 0:
                matches = find_matches(blob.stdout)
                findings.extend((relative_path.as_posix(), match) for match in matches)
        elif staged.returncode > 1:
            print("Unable to inspect the Git index.", file=sys.stderr)
            return 2

    if findings:
        print("Potential credentials found; no matching content was printed:")
        for path, match in sorted(set(findings)):
            print(f"  {path}: {match}")
        return 1

    print("No common credential prefixes found in tracked or staged files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
