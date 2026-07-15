import os
import subprocess
from pathlib import Path


def changed_files_from_diff(diff_text: str) -> list[str]:
    files = []
    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            parts = line.split(" b/")
            if len(parts) == 2:
                files.append(parts[1])
    return files


def clone_with_token(token: str, url: str, sha: str, dest: Path) -> None:
    clone_url = f"https://x-access-token:{token}@github.com/{url}"
    proc = subprocess.run(
        ["git", "clone", "--depth", "100", clone_url, str(dest)],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Clone failed: {proc.stderr}")

    checkout = subprocess.run(
        ["git", "-C", str(dest), "checkout", sha],
        capture_output=True,
        text=True,
    )
    if checkout.returncode != 0:
        fetch = subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth", "100", "origin", sha],
            capture_output=True,
            text=True,
        )
        if fetch.returncode != 0:
            raise RuntimeError(f"Fetch failed: {fetch.stderr}")
        checkout = subprocess.run(
            ["git", "-C", str(dest), "checkout", sha],
            capture_output=True,
            text=True,
        )
        if checkout.returncode != 0:
            raise RuntimeError(f"Checkout failed: {checkout.stderr}")
