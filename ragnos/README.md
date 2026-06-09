RAGnos additions to the hermes-agent fork
=========================================

This directory holds the ONLY RAGnos-authored files in the
`ragnos-labs/hermes-agent` fork of `NousResearch/hermes-agent`. Everything else
is upstream, used UNMODIFIED, so we can pull updates cleanly.

Why a fork
----------
The official Photon (iMessage) plugin (`plugins/platforms/photon/`) is a finished,
maintained drop-in: a Python adapter + a Node `spectrum-ts` sidecar + tests. Our
custom Hermes Hub treats hermes-agent OSS as its baseline. Rather than rebuild the
iMessage transport, we fork, use the official adapter as-is, and bridge it to Keez.

Topology
--------
Sibling service (this checkout) runs the official Photon adapter with ONLY the
Photon platform active. It bridges to the RAGnos Hub at two seams that live in the
Hub package (`tools/home-agent/src/home_agent/`), NOT here:

* Inbound  (SEAM 1): `home_agent.photon_inbound.build_keez_handler` -> Keez daemon.
* Outbound (SEAM 2): `home_agent.photon_delivery` -> sidecar `/send` (Morning Brief,
  Hermes Hub alerts pushed to the phone).

`ragnos/run_photon_keez.py` is the entrypoint: it imports the unmodified official
adapter and injects the Keez handler via the adapter's public
`set_message_handler` seam. No upstream file is edited.

Pulling upstream updates
------------------------
    git fetch upstream
    git merge upstream/main        # or rebase ragnos/main onto upstream/main
    cd plugins/platforms/photon/sidecar && npm install   # if sidecar deps changed

Our changes are confined to `ragnos/`, so merges should not conflict with upstream.

Running it (live activation, human-gated)
-----------------------------------------
1. `hermes photon setup --phone +1XXXXXXXXXX`  (device-code login at
   app.photon.codes; creates a Spectrum project, links the number, installs the
   sidecar). This is the one step a human must do; it needs a Photon account + a
   phone.
2. Put `PHOTON_PROJECT_ID` / `PHOTON_PROJECT_SECRET` / `PHOTON_SIDECAR_TOKEN` in
   Infisical and render them into this service's env (do NOT use `~/.hermes/.env`
   in production).
3. Set `PHOTON_ALLOWED_USERS=+1XXXXXXXXXX` and
   `HOME_AGENT_SRC=/Users/huntercanning/dev/ragnos/workspace/tools/home-agent/src`.
4. `python ragnos/run_photon_keez.py`  (a launchd unit stages this; see the RAGnos
   repo `scripts/launchd/`).

Verification without live Photon
--------------------------------
The RAGnos repo ships a battle-test that runs THIS adapter against a fake sidecar
(no Photon account needed):
`tools/home-agent/scripts/photon_battle_test.py` (11 scenarios, fault + concurrency).
