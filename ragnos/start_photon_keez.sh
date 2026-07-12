#!/usr/bin/env bash
# Start the Photon -> Keez runner with creds pulled live from Infisical.
#
# No plaintext env file: the 4 PHOTON_* secrets live in Infisical (workspace
# 3820769e-...). This reads them with the read-only daemon token at launch,
# exports them into the runner's process env, and execs run_photon_keez.py.
# Set PHOTON_KEEZ_PYTHON to a python that has httpx + the home_agent package.
set -euo pipefail

FORK="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT=3820769e-6d2a-4c15-ac4a-212382efa34e
READ_TOKEN="$(cat "$HOME/.infisical/agent-token.json")"

fetch() {
  curl -s "https://app.infisical.com/api/v3/secrets/raw/$1?workspaceId=$PROJECT&environment=prod&secretPath=/&type=shared" \
    -H "Authorization: Bearer $READ_TOKEN" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('secret',{}).get('secretValue',''))"
}

export PHOTON_PROJECT_ID="$(fetch PHOTON_PROJECT_ID)"
export PHOTON_PROJECT_SECRET="$(fetch PHOTON_PROJECT_SECRET)"
export PHOTON_SIDECAR_TOKEN="$(fetch PHOTON_SIDECAR_TOKEN)"
export PHOTON_ALLOWED_USERS="$(fetch PHOTON_ALLOWED_USERS)"
export PYTHONPATH="$FORK"

if [ -z "$PHOTON_PROJECT_ID" ] || [ -z "$PHOTON_PROJECT_SECRET" ]; then
  echo "[start] missing PHOTON creds from Infisical (read token stale?)" >&2
  exit 1
fi

# Defensive port reclaim before launch. A hard-crashed (kill -9 / OOM) runner
# skips its finally-block cleanup, orphaning its child spectrum-ts sidecar, which
# keeps LISTENing on 127.0.0.1:8789. The fresh sidecar then cannot bind and the
# runner crash-loops under launchd KeepAlive. Kill any stale sidecar and wait for
# the port to clear. Idempotent: a no-op on the normal-exit path (finally already
# cleans up) and when nothing is stale.
pkill -f "plugins/platforms/photon/sidecar/index.mjs" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do
  lsof -ti tcp:8789 >/dev/null 2>&1 || break
  sleep 1
done

PY_BIN="${PHOTON_KEEZ_PYTHON:-python3}"
echo "[start] launching Photon->Keez runner (allowlist set, creds from Infisical)"

# Supervise the runner as a CHILD (do not exec). When it dies -- even via a hard
# kill -9 / OOM that skips the runner's own finally-block cleanup -- this wrapper
# regains control and reaps the orphaned spectrum-ts sidecar. That matters for
# two reasons: the orphaned sidecar keeps LISTENing on 8789 (blocking the next
# bind) AND, being a surviving child of this launchd job, it keeps the job
# "alive" so KeepAlive never fires. Reaping it lets this wrapper exit cleanly so
# launchd respawns a fresh instance. The TERM/INT trap forwards launchd's stop
# signal to the runner so a graceful stop still runs the runner's own cleanup.
_reap_sidecar() { pkill -f "plugins/platforms/photon/sidecar/index.mjs" 2>/dev/null || true; }
"$PY_BIN" "$FORK/ragnos/run_photon_keez.py" &
RUNNER_PID=$!
trap 'kill -TERM "$RUNNER_PID" 2>/dev/null || true' TERM INT
wait "$RUNNER_PID"
STATUS=$?
_reap_sidecar
exit "$STATUS"
