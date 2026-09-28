# ragnos-governance

A `pre_tool_call` gate plus a JSONL ledger of tool calls. It observes by
default and blocks nothing until enforcement is turned on.

## Settings

| Key | Meaning |
| --- | --- |
| `RAGNOS_GOVERNANCE_ENFORCE` | `1`, `true`, `yes` or `on` turns blocking on. |
| `RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS` | Comma or newline separated tool names and toolset names to block. |
| `RAGNOS_GOVERNANCE_REQUIRED` | Truthy value makes the gate fail closed when enforcement is not configured (see below). |
| `RAGNOS_GOVERNANCE_LEDGER` | Ledger path. Default `$HERMES_HOME/ragnos-governance/governance-ledger.jsonl`. |

### Where settings come from

1. The process environment.
2. An optional `$HERMES_HOME/governance.env` file.

The process environment wins when both set a key. The file uses `KEY=VALUE`
lines, `#` comments, an optional `export ` prefix and optional matching quotes.
Only `RAGNOS_GOVERNANCE_*` keys are read from it; anything else is ignored.
The file is re-read when its size or modification time changes.

Use the file for services that start Hermes without your shell environment,
such as a gateway service, cron or the desktop app:

```sh
# $HERMES_HOME/governance.env
RAGNOS_GOVERNANCE_ENFORCE=1
RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS=terminal,write_file,mcp-github
RAGNOS_GOVERNANCE_REQUIRED=1
```

File tools refuse to write `governance.env`, so the agent cannot relax its own
policy through `write_file` or `patch`.

### Tool and toolset names

Each forbidden entry is matched as a tool name and, when it names a toolset,
expanded to every tool in that toolset at call time. `terminal` blocks
`terminal` and `process`; `mcp-github` blocks every tool from that MCP server,
including tools registered after startup; `all` blocks every tool.

## Fail-open and fail-closed

Fail-open is the default. With no settings, or with `RAGNOS_GOVERNANCE_ENFORCE`
unset, every tool call is allowed and only recorded.

With `RAGNOS_GOVERNANCE_REQUIRED=1`, every tool call is blocked while any of
these holds:

- `RAGNOS_GOVERNANCE_ENFORCE` is not enabled,
- `RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS` is empty,
- `governance.env` exists but cannot be read.

The block message names what is missing, and the ledger row carries
`required_config_missing: true`. Set `RAGNOS_GOVERNANCE_REQUIRED` in the
process environment when the point is to catch a missing or deleted
`governance.env`; set in the file itself, it only catches an incomplete file.

This switch only works while the plugin is loaded. It does not detect a
Hermes process that runs without the plugin enabled.
