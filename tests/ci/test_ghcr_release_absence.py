import subprocess
import sys
from pathlib import Path

from scripts.ci.classify_ghcr_release_absence import is_explicit_absence


def test_exact_fork_release_404_is_explicit_absence():
    assert is_explicit_absence(
        "ghcr.io/ragnos-labs/hermes-agent:v2026.8.19-ragnos.1: not found"
    )


def test_fork_release_head_404_is_explicit_absence():
    assert is_explicit_absence(
        "unexpected status from HEAD request to "
        "https://ghcr.io/v2/ragnos-labs/hermes-agent/manifests/"
        "v2026.8.19.2-ragnos.3: 404 Not Found"
    )


def test_exact_sha_404_is_explicit_absence():
    assert is_explicit_absence(
        f"ghcr.io/ragnos-labs/hermes-agent:sha-{'a' * 40}: not found"
    )


def test_malformed_or_unowned_404_stays_unknown():
    errors = (
        "ghcr.io/ragnos-labs/hermes-agent:v2026.8.19: not found",
        "ghcr.io/ragnos-labs/hermes-agent:v2026.8-ragnos.1: not found",
        "ghcr.io/ragnos-labs/hermes-agent:v2026.8.19-ragnos.0: not found",
        "ghcr.io/ragnos-labs/hermes-agent:v2026.8.19-ragnos.1-extra: not found",
        "ghcr.io/another-owner/hermes-agent:v2026.8.19-ragnos.1: not found",
    )
    assert all(not is_explicit_absence(error) for error in errors)


def test_mixed_absence_and_registry_failure_stays_unknown():
    absence = "ghcr.io/ragnos-labs/hermes-agent:v2026.8.19-ragnos.1: not found"
    ambiguous = (
        "unauthorized: authentication required",
        "context deadline exceeded",
        "TLS handshake timeout",
        "unexpected EOF",
        "malformed registry response",
    )
    assert all(not is_explicit_absence(f"{absence}\n{error}") for error in ambiguous)


def test_audited_classifier_runs_outside_an_old_source_checkout(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    source_sha = "6df4078de6c6b606d22b16725d603b7960f98b26"
    workflow_sha = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    classifier_spec = (
        f"{workflow_sha}:scripts/ci/classify_ghcr_release_absence.py"
    )
    expected_blob = subprocess.run(
        ["git", "rev-parse", "--verify", classifier_spec],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    old_source_checkout = tmp_path / "old-source-checkout"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            str(repo),
            str(old_source_checkout),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "--detach", source_sha],
        cwd=old_source_checkout,
        check=True,
    )
    checked_out_sha = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=old_source_checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert checked_out_sha == source_sha
    assert not (
        old_source_checkout / "scripts/ci/classify_ghcr_release_absence.py"
    ).exists()

    classifier_bytes = subprocess.run(
        ["git", "show", classifier_spec],
        cwd=old_source_checkout,
        check=True,
        capture_output=True,
    ).stdout

    audited_policy = tmp_path / "audited-workflow-policy"
    audited_policy.mkdir()
    classifier_path = audited_policy / "classify_ghcr_release_absence.py"
    classifier_path.write_bytes(classifier_bytes)
    actual_blob = subprocess.run(
        ["git", "hash-object", str(classifier_path)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual_blob == expected_blob

    error_path = tmp_path / "registry.stderr"
    error_path.write_text(
        "ghcr.io/ragnos-labs/hermes-agent:v2026.8.19-ragnos.1: not found",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(classifier_path), str(error_path)],
        cwd=old_source_checkout,
        check=False,
    )
    assert result.returncode == 0
