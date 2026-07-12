#!/usr/bin/env python3
"""Register your iMessage number on an EXISTING Photon project (keys in hand).

Use this when you ALREADY have PHOTON_PROJECT_ID (the spectrumProjectId) and
PHOTON_PROJECT_SECRET and do NOT want the device-login flow in photon_setup.py
(which rotates your project secret and would invalidate keys you already hold).

It registers your number as a Spectrum user (Basic auth with your project creds,
no dashboard login) and prints the agent's iMessage number to text. Idempotent.

    PHOTON_PROJECT_ID=... PHOTON_PROJECT_SECRET=... PYTHONPATH=$(pwd) \
        python ragnos/photon_register.py --phone +1XXXXXXXXXX
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_FORK_ROOT = Path(__file__).resolve().parent.parent
if str(_FORK_ROOT) not in sys.path:
    sys.path.insert(0, str(_FORK_ROOT))

from plugins.platforms.photon import auth as photon_auth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Register a number on an existing Photon project.")
    ap.add_argument("--phone", required=True, help="Your iMessage number, E.164 (e.g. +15551234567)")
    args = ap.parse_args()

    project_id = os.environ.get("PHOTON_PROJECT_ID", "").strip()
    project_secret = os.environ.get("PHOTON_PROJECT_SECRET", "").strip()
    if not project_id or not project_secret:
        print(
            "Set PHOTON_PROJECT_ID (spectrumProjectId) and PHOTON_PROJECT_SECRET in the "
            "environment first (e.g. `source ~/.hermes/ragnos-photon.env`).",
            file=sys.stderr,
        )
        return 2

    try:
        user, created = photon_auth.register_user_if_absent(
            project_id, project_secret, phone_number=args.phone
        )
    except Exception as e:  # noqa: BLE001
        print(f"registration failed: {e}", file=sys.stderr)
        return 1

    print("registered your number" if created else "your number is already registered")
    agent_number = photon_auth.user_assigned_line(user)
    print()
    if agent_number:
        print("=" * 60)
        print(f"  Agent iMessage number: {agent_number}")
        print("  Text THIS number from your phone to reach Keez.")
        print("=" * 60)
    else:
        print("  No iMessage line assigned yet; the free shared tier usually assigns")
        print("  one within a moment. Re-run, or check the Photon dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
