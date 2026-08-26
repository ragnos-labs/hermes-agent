from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PICKER = REPO_ROOT / "scripts" / "sandbox" / "pick-release-tags.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_remote_filter_excludes_release_shaped_fork_only_tags(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "init", "--bare", "--quiet", str(upstream)],
        check=True,
    )
    fork = tmp_path / "fork"
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(fork)],
        check=True,
    )
    _git(fork, "config", "user.name", "Hermes Test")
    _git(fork, "config", "user.email", "test@example.invalid")
    _git(fork, "remote", "add", "upstream", str(upstream))
    _git(fork, "commit", "--allow-empty", "--quiet", "-m", "first")
    _git(fork, "tag", "v2026.1.1")
    _git(fork, "commit", "--allow-empty", "--quiet", "-m", "second")
    _git(fork, "tag", "v2026.1.2")
    _git(fork, "push", "--quiet", "upstream", "v2026.1.1", "v2026.1.2")
    # The remote is authoritative: this upstream tag is deliberately absent
    # from the fork's local tag set, as happens before a fork mirrors a release.
    _git(fork, "tag", "--delete", "v2026.1.2")
    _git(fork, "commit", "--allow-empty", "--quiet", "-m", "fork only")
    _git(fork, "tag", "v2026.1.3")

    result = subprocess.run(
        [
            str(PICKER),
            "--repo",
            str(fork),
            "--remote",
            "upstream",
            "--count",
            "5",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == ["v2026.1.1", "v2026.1.2"]


def test_remote_filter_fails_closed_when_remote_is_unavailable(tmp_path: Path) -> None:
    fork = tmp_path / "fork"
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(fork)],
        check=True,
    )
    _git(fork, "config", "user.name", "Hermes Test")
    _git(fork, "config", "user.email", "test@example.invalid")
    _git(fork, "commit", "--allow-empty", "--quiet", "-m", "first")
    _git(fork, "tag", "v2026.1.1")

    result = subprocess.run(
        [
            str(PICKER),
            "--repo",
            str(fork),
            "--remote",
            str(tmp_path / "missing.git"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "could not list release tags" in result.stderr
