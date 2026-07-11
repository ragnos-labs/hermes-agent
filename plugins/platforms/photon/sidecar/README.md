# Photon sidecar

Small Node helper that bridges Hermes Agent to Photon's Spectrum SDK
(`spectrum-ts`).  Hermes is Python; Photon has no public HTTP
send-message endpoint today; replies therefore go through this sidecar.

The sidecar:

- runs `Spectrum({ projectId, projectSecret, providers: [imessage.config()] })`
- exposes a loopback-only HTTP control channel for the Python adapter
  to push send, reply, edit, and typing requests (auth via
  `X-Hermes-Sidecar-Token`)
- drains the inbound message stream so `spectrum-ts` keeps its
  reconnect/heartbeat machinery alive and forwards normalized events over the
  authenticated loopback NDJSON stream (Hermes does not use a Photon webhook)

## Install

```bash
cd plugins/platforms/photon/sidecar
npm install
```

The Hermes plugin's `hermes photon setup` command runs `npm install`
here automatically.

The SDK is pinned to `spectrum-ts` 9.3.1. That release natively preserves
ordered mixed text and attachment parts. The postinstall compatibility hook
still recognizes and patches the older 8.x mapper, but leaves the 9.3.1
implementation unchanged after verifying the native ordered-parts behavior.

## Message action contract

Every route requires the sidecar token header. `/reply` accepts exactly
`spaceId`, `text`, `replyToMessageId`, and `clientMessageId`. `/edit` accepts
exactly `spaceId`, `messageId`, `text`, and `clientMessageId`. Unknown fields,
missing fields, identifiers over 512 characters, text over 100,000 characters,
and invalid client message IDs are rejected before an SDK call. Successful
responses contain only `clientMessageId`, `confirmed`, `providerStatus`,
`messageId`, and `deliveredAt`; message content is never echoed.

Normalized inbound events may also carry `sequence` when the provider exposes
a nonnegative safe integer and `cursor` when it exposes a bounded opaque
string. Both fields are omitted when unavailable or malformed.

## Catch-up capability audit

Spectrum 9.3.1 does not expose application-level history or cursor replay.
The public `Space` contract supports only one-message lookup through
`getMessage(id)`, and the root `spectrum-ts` package exports no `history`,
`catchUp`, `fetchMissed`, or `resumableOrderedStream` API. The iMessage
provider does use cursor catch-up internally while its live iterator remains
running, but the cursor and raw provider client are not part of the public
application contract. A sidecar restart therefore cannot request a bounded
page from a persisted cursor without reaching through `app.__internal` or
binding directly to `@photon-ai/advanced-imessage` internals. This sidecar
does neither, so it intentionally exposes no `/history` or `/catch-up` route.

Executable audit from this directory after `npm ci`:

```bash
node --input-type=module -e '
  import fs from "node:fs";
  const d = fs.readFileSync("node_modules/@spectrum-ts/core/dist/attachment-ChqzKngn.d.ts", "utf8");
  const s = d.slice(d.indexOf("interface Space<_Def = unknown>"), d.indexOf("//#endregion", d.indexOf("interface Space<_Def = unknown>")));
  console.log({getMessage: /getMessage\\(id: string\\)/.test(s), history: /\\bhistory\\b/i.test(s), catchUp: /\\bcatchUp\\b/.test(s), cursor: /\\bcursor\\b/.test(s)});
'
node --input-type=module -e '
  const s = await import("spectrum-ts");
  console.log(Object.fromEntries(["history", "catchUp", "fetchMissed", "resumableOrderedStream"].map(k => [k, k in s])));
'
rg -n 'fetchMissed: \\(cursor\\)|catchUp\\(since' \
  node_modules/@spectrum-ts/imessage/dist/index.js \
  node_modules/@photon-ai/advanced-imessage/dist/index.d.ts
```

Expected first two outputs are `{ getMessage: true, history: false, catchUp:
false, cursor: false }` and four `false` export checks. The final command shows
that catch-up exists only beneath Spectrum's provider implementation.

## Inbound attachment handles

Inbound attachment bytes never enter NDJSON and are never cached to plaintext
disk by the Photon adapter. The normalized event contains the existing
metadata plus a random 48-character opaque `handle`. The sidecar holds a copy
of the bytes in memory with fixed limits: 20 MiB per item, 64 MiB total, 64
items, and a five-minute TTL. Capacity and read failures emit metadata without
a handle and never include provider error text.

An authenticated `GET /attachment/<handle>` returns the raw bytes once with
`Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. The exact path
accepts no query string or suffix. Consumption is atomic: concurrent or replay
requests receive the same content-free 404 as expired and unknown handles.
Queued and in-flight transfers remain charged to the same count, byte, and TTL
limits until their buffer is zeroed. Buffers are released exactly once after
response completion, close, error, TTL expiry, or shutdown. A stalled client
is destroyed at TTL expiry, so it cannot move plaintext outside the bounded
store accounting or retain a queued response after its lease ends.

This is the external half of the secure attachment flow. Generic Hermes has no
encrypted media store, so the Python Photon adapter intentionally does not
fetch a handle or create a media cache file. An embedding runtime must register
`PhotonAdapter.set_attachment_handle_consumer(...)`; the callback receives the
raw event and must return `True` only after accepting ownership of every
handle. Without that consumer, attachment dispatch raises the retryable
`ATTACHMENT_CONSUMER_UNAVAILABLE` fatal state, rolls back local dedup, and does
not tell the generic model that media is ready. Keez can install this seam and
redeem the raw handle inside its governed attachment boundary.

The remaining limitation is upstream replay: Spectrum exposes no public
restart cursor API, as documented above. Reconnect is requested after a missing
consumer, but replay of that exact event is not guaranteed. A complete generic
consumer and durable replay queue require a larger approved design.

## Reply and edit crash boundary

Spectrum 9.3.1 does not accept `clientMessageId` or other caller reference
metadata in `Space.send`, `Message.reply`, or `Message.edit`. Reply returns a
provider `Message`, edit returns `void`, and the only public lookup is
`getMessage(id)`. There is no lookup by caller reference. Therefore a process
crash after Photon accepts a reply but before an external receipt CAS can
duplicate that reply on retry. A local post-send ledger cannot close this
window and this sidecar does not claim otherwise.

Executable evidence after `npm ci`:

```bash
rg -n 'clientMessageId' node_modules/@spectrum-ts node_modules/spectrum-ts
rg -n 'reply\\(content:|edit\\(newContent:|getMessage\\(id:' \
  node_modules/@spectrum-ts/core/dist/*.d.ts
```

The first command returns no matches. The second shows reply returning a
message, edit returning `Promise<void>`, and lookup requiring a provider
message ID.

## Run standalone

For debugging:

```bash
PHOTON_PROJECT_ID=... PHOTON_PROJECT_SECRET=... \
PHOTON_SIDECAR_PORT=8789 PHOTON_SIDECAR_TOKEN=$(openssl rand -hex 16) \
node index.mjs
```

In normal use, the Python adapter supervises this process — start,
restart on crash, kill on shutdown — and never asks the user to run
it by hand.

## Why a sidecar at all?

Photon publishes webhooks (inbound) but their docs state explicitly:

> Pass `space.id` to `Space.send(...)` from a separate `spectrum-ts`
> SDK instance to reply.  No public HTTP send endpoint exists today.

— https://photon.codes/docs/webhooks/events

When Photon ships an HTTP send endpoint, the plan is to retire this
sidecar entirely and call it directly from Python.  The plugin's
outbound code path is already isolated behind a single helper
(`_sidecar_send` in `adapter.py`) to make that swap a one-file change.
