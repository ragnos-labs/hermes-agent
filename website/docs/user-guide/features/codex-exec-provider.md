---
sidebar_position: 26
title: Codex exec provider
description: Use the Codex CLI for inference while Hermes owns tools and memory.
---

# Codex exec provider

The `codex_exec` provider keeps Hermes's native conversation loop, tool
dispatch, memory, session search and auxiliary tasks. Each model request runs
`codex exec` and translates a schema-validated response into Hermes text or
tool calls. It requires a separately installed Codex CLI with its native login
already configured, plus the `hermes-agent[codex-exec]` dependency extra.

```yaml
model:
  provider: codex_exec
  default: YOUR_CODEX_MODEL
codex_exec:
  command: codex
  reasoning_effort: high
  timeout_seconds: 120
auxiliary:
  background_review:
    provider: codex_exec
    model: YOUR_CODEX_MODEL
```

Auxiliary tasks set to `auto` resolve the main provider and model. Explicit
per-task overrides and Hermes's existing fallback configuration still apply.
A host requiring exclusive CLI inference must control those settings and
enforce its own admission and network boundaries. The provider itself never
opens an API endpoint, extracts tokens, purchases usage or selects another
provider. Executable discovery does not prove an authenticated session.

Codex receives the conversation and function definitions through stdin. Its
own shell, web search, apps, plugins, memories, hooks and multi-agent execution
are disabled with managed configuration. Sessions are ephemeral. Hermes alone
executes the returned tools after validating their names, IDs and arguments.
The CLI's read-only sandbox applies to the inference subprocess; it does not
sandbox Hermes's tools. The caller must separately isolate those tools.

Requests have a bounded deadline and output size. Cancellation terminates and
reaps the subprocess. Malformed JSON, invalid tool requests, unexpected Codex
tool execution and incomplete usage records fail the request. Usage exhaustion
is reported as a typed error with a reset timestamp when the CLI supplies one;
the hosting application owns any persistent hold and later resumption.

This transport currently forwards text context and function tools. It does
not implement the Codex app-server runtime or its native tool execution.

## Live integration check

From the owning checkout with a native Codex login, run:

```sh
python scripts/qualify_codex_exec.py --live --model YOUR_CODEX_MODEL \
  --evidence-dir /private/path/to/new-proof
```

The opt-in check uses a new private Hermes home and synthetic material. It
requires a real memory write, session lookup, retained recall in a fresh
session and an auxiliary learning response. Its receipt reports source state
separately from runtime qualification. It does not qualify a fleet deployment.

## Built-in memory and Holographic corrections

When the Holographic provider is enabled, new built-in memory writes record
their source target, content and available session/task provenance. Successful
replacement and removal update the corresponding mirrored facts, including
full-text search, across restarts. Failed or staged writes do not update facts.
Independent facts and facts still referenced by another memory target remain.
Pre-existing facts without mirror provenance are preserved; this change does
not guess which historical facts are owned by built-in memory.
