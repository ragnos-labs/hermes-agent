#!/usr/bin/env python3
"""Standalone Photon provisioning for the RAGnos Hermes Hub iMessage surface.

Does exactly what `hermes photon setup --phone ...` does, WITHOUT installing the
full hermes CLI. It drives the fork's self-contained
`plugins/platforms/photon/auth.py` (httpx only): device-code login, find/create
the "Hermes Agent" project, enable Spectrum, rotate the project secret, register
your number, and surface the agent's iMessage line.

Credentials persist to ~/.hermes/auth.json (the official adapter reads them from
there). Your number is also written into ~/.hermes/ragnos-photon.env as
PHOTON_ALLOWED_USERS so the runner authorizes your own inbound messages.

Prereq: a Photon account (sign up at https://app.photon.codes/ ; the free tier is
enough for personal use). Then:

    PYTHONPATH=$(pwd) python ragnos/photon_setup.py --phone +1XXXXXXXXXX

Approve the device login in your browser, then text the printed agent number.
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


def _print_code(code) -> None:
    target = code.verification_uri_complete or code.verification_uri
    print()
    print("  Open this URL:  " + str(target))
    print("  Enter the code: " + str(code.user_code))
    print("  (waiting for browser approval; Ctrl-C to cancel)")
    print()


def _write_allowlist(phone: str) -> None:
    """Fill PHOTON_ALLOWED_USERS in ~/.hermes/ragnos-photon.env (default-deny
    means the runner must allowlist your own number to accept your texts)."""
    envf = Path(os.path.expanduser("~/.hermes/ragnos-photon.env"))
    if not envf.exists():
        return
    out = []
    found = False
    for ln in envf.read_text().splitlines():
        if ln.startswith("export PHOTON_ALLOWED_USERS="):
            out.append(f"export PHOTON_ALLOWED_USERS={phone}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"export PHOTON_ALLOWED_USERS={phone}")
    envf.write_text("\n".join(out) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision Photon iMessage for the Hermes Hub.")
    ap.add_argument("--phone", required=True, help="Your iMessage number, E.164 (e.g. +15551234567)")
    ap.add_argument("--project-name", default=None, help="Project name (default: 'Hermes Agent')")
    ap.add_argument("--no-browser", action="store_true", help="Print the login URL instead of opening a browser")
    args = ap.parse_args()

    # 1. Device-code login (interactive: approve in the browser).
    token = photon_auth.load_photon_token()
    if not token:
        print("[1/4] Photon device login...")
        token = photon_auth.login_device_flow(open_browser=not args.no_browser, on_user_code=_print_code)
        print("  logged in (token saved to %s)" % photon_auth._auth_json_path())
    else:
        print("[1/4] Reusing existing Photon token")

    # 2. Find or create the project.
    name = args.project_name or photon_auth.DEFAULT_PROJECT_NAME
    dashboard_id = photon_auth.load_dashboard_project_id()
    if dashboard_id:
        print("[2/4] Reusing configured project")
    else:
        existing = photon_auth.find_project_by_name(token, name)
        if existing and existing.get("id"):
            dashboard_id = existing["id"]
            print(f"[2/4] Found project '{name}'")
        else:
            print(f"[2/4] Creating project '{name}'...")
            dashboard_id = photon_auth.create_project(token, name=name).get("id")
    if not dashboard_id:
        print("could not resolve a Photon project id", file=sys.stderr)
        return 1

    # 3. Enable Spectrum, rotate the secret, persist creds.
    print("[3/4] Enabling Spectrum and provisioning credentials...")
    proj = photon_auth.ensure_spectrum_enabled(token, dashboard_id)
    spectrum_id = str(proj.get("spectrumProjectId") or "")
    if not spectrum_id:
        print("spectrum provisioning failed: no spectrum project id", file=sys.stderr)
        return 1
    secret = photon_auth.regenerate_project_secret(token, dashboard_id)
    photon_auth.store_project_credentials(
        spectrum_project_id=spectrum_id, project_secret=secret,
        dashboard_project_id=dashboard_id, name=name,
    )
    print(f"  Spectrum enabled (project {spectrum_id}); secret saved to auth.json")

    # 4. Register your number + surface the agent's iMessage line.
    print("[4/4] Registering your number...")
    user, created = photon_auth.register_user_if_absent(spectrum_id, secret, phone_number=args.phone)
    print("  phone registered" if created else "  phone already registered")
    _write_allowlist(args.phone)
    agent_number = photon_auth.user_assigned_line(user)
    if not agent_number:
        try:
            line = photon_auth.get_imessage_line(token, dashboard_id)
            agent_number = line.get("phoneNumber") if line else None
        except Exception as e:  # noqa: BLE001
            print(f"  (could not fetch the assigned line: {e})", file=sys.stderr)
    photon_auth.store_user_numbers(
        phone_number=args.phone, assigned_phone_number=agent_number,
        user_id=str(user.get("id")) if user.get("id") else None,
        dashboard_project_id=dashboard_id,
    )
    print()
    if agent_number:
        print("=" * 64)
        print(f"  Your agent's iMessage number: {agent_number}")
        print("  Text THIS number from your phone to reach Keez.")
        print("=" * 64)
    else:
        print("  No iMessage line assigned yet; check the Photon dashboard.")
    print("\nDone. Tell me it is provisioned and I will start the runner.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
