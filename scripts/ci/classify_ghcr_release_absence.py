#!/usr/bin/env python3
"""Classify authenticated GHCR inspection errors without guessing."""

from __future__ import annotations

import re
import sys
from pathlib import Path


FORK_RELEASE_TAG_PATTERN = (
    r"v[0-9]{4}\.[0-9]+\.[0-9]+(?:\.[0-9]+)?-ragnos\.[1-9][0-9]*"
)
IMMUTABLE_IMAGE_TAG_PATTERN = rf"(?:sha-[0-9a-f]{{40}}|{FORK_RELEASE_TAG_PATTERN})"

_AMBIGUOUS_ERROR = re.compile(
    r"(?:unauthorized|authentication required|denied|forbidden|"
    r"context deadline exceeded|TLS handshake timeout|unexpected EOF|"
    r"i/o timeout|connection refused|connection reset|temporary failure|"
    r"malformed registry response)",
    re.IGNORECASE,
)
_EXPLICIT_ABSENCE = re.compile(
    rf"(?:MANIFEST_UNKNOWN|manifest unknown|"
    rf"ghcr\.io/v2/ragnos-labs/hermes-agent/manifests/{IMMUTABLE_IMAGE_TAG_PATTERN}: not found|"
    rf"ghcr\.io/ragnos-labs/hermes-agent:{IMMUTABLE_IMAGE_TAG_PATTERN}: not found|"
    rf"unexpected status from HEAD request to "
    rf"https://ghcr\.io/v2/ragnos-labs/hermes-agent/manifests/"
    rf"{IMMUTABLE_IMAGE_TAG_PATTERN}: 404 Not Found)"
)


def is_explicit_absence(stderr: str) -> bool:
    """Return true only for an unambiguous absence of a fork-owned tag."""
    if _AMBIGUOUS_ERROR.search(stderr):
        return False
    return _EXPLICIT_ABSENCE.search(stderr) is not None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: classify_ghcr_release_absence.py <stderr-path>", file=sys.stderr)
        return 2
    try:
        stderr = Path(argv[0]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 1
    return 0 if is_explicit_absence(stderr) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
