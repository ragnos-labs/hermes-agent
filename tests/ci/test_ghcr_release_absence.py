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
