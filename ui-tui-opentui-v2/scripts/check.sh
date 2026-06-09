#!/usr/bin/env bash
# Phase gate for the native OpenTUI engine (spec v4 §5). Runs the full headless
# suite: type-check + lint + bun test (which includes the headless frame gate via
# captureCharFrame). The agentic smoke (docs/plans/opentui-smoke.md) is the live
# complement — run BOTH every phase.
#
# OpenTUI core is Bun/FFI-only — everything runs via bun, never node (gotcha §8 #8).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== [1/4] format (prettier --check) =="
bunx prettier --check src

echo "== [2/4] type-check =="
bun run type-check

echo "== [3/4] lint =="
bun run lint

echo "== [4/4] bun test (incl. headless frame gate) =="
bun test

echo "== check OK =="
