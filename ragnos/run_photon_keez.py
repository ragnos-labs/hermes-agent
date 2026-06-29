#!/usr/bin/env python3
"""RAGnos sibling-service entrypoint: official Photon adapter -> Keez daemon.

This is the ONLY RAGnos-authored runtime file in the fork. It imports the
UNMODIFIED upstream Photon adapter and injects our Keez bridge handler (from the
RAGnos Hermes Hub package ``home_agent.photon_inbound``) via the adapter's public
``set_message_handler`` seam. Keeping our additions in ``ragnos/`` (never editing
upstream files) keeps ``git fetch upstream && merge`` clean.

Inbound:  iMessage -> Photon -> sidecar -> official adapter -> this handler -> Keez.
Outbound replies are auto-sent by the adapter. Outbound *push* (Morning Brief /
Hermes Hub alerts) is handled separately by the Hub via
``home_agent.photon_delivery`` against the same sidecar.

Env (rendered from Infisical in production; see ragnos/README.md):
  PHOTON_PROJECT_ID, PHOTON_PROJECT_SECRET   from ``hermes photon setup``
  PHOTON_SIDECAR_TOKEN                        shared secret (outbound uses it too)
  PHOTON_ALLOWED_USERS                        E.164 allowlist (comma/newline list)
  HOME_AGENT_SRC                              path to RAGnos tools/home-agent/src
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys


def _allowlist() -> list[str]:
    raw = os.environ.get("PHOTON_ALLOWED_USERS", "")
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


async def _run() -> int:
    logging.basicConfig(
        level=os.environ.get("PHOTON_KEEZ_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("photon-keez")

    # The Hub package (home_agent) lives in the RAGnos monorepo, not this fork.
    home_src = os.environ.get("HOME_AGENT_SRC")
    if home_src and home_src not in sys.path:
        sys.path.insert(0, home_src)

    from gateway.config import PlatformConfig
    from plugins.platforms.photon.adapter import PhotonAdapter
    from home_agent.photon_inbound import build_keez_handler

    adapter = PhotonAdapter(PlatformConfig(extra={}))
    adapter.set_message_handler(build_keez_handler(allowlist=_allowlist()))

    if not await adapter.connect():
        log.error("adapter.connect() failed (missing creds or sidecar). See logs above.")
        return 1
    log.info("connected; routing iMessage <-> Keez (allowlist=%d). Ctrl-C to stop.", len(_allowlist()))
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutting down")
    finally:
        await adapter.disconnect()
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
