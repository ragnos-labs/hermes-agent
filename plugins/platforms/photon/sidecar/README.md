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

An authenticated `POST /attachment/<handle>/lease` with exact body
`{"deliveryId":"<48 lowercase hex>"}` returns raw bytes plus an opaque lease
ID header. A crash or socket fault does not consume the handle: the same
delivery replays the same bytes and active lease. Another delivery is rejected.
`POST /attachment/<handle>/release` requires that delivery and lease ID and
makes the bytes available for a fresh lease. `POST
/attachment/<handle>/consume` requires the exact delivery and is allowed only
after the consumer has a durable upload receipt. The same consume is
idempotent; another delivery is rejected. Consumed receipts are bounded by the
same count and TTL limits.

Queued and leased transfers remain charged to the same count, byte, and TTL
limits until explicit consume or expiry zeroes the buffer. Completion, close,
or error only detaches the response. A stalled client is destroyed at lease or
item expiry. Shutdown also wipes every buffer. Plaintext never touches disk.

Handle-bearing events add a random 48-character `deliveryId`. A successful
NDJSON socket write is not acceptance. The sidecar retains exactly one pending
event (maximum 2 MiB, five-minute TTL), stops pulling later provider events,
and replays the identical event and delivery ID after an inbound consumer
reconnect. Before the event becomes pending, every handle in the normalized
event is atomically pre-bound to that delivery ID. A missing, duplicate, or
already-bound handle rejects the entire bind without changing any other
handle. The authenticated consumer must send the exact request below only
after secure handle upload and durable job submission both succeed:

```http
POST /inbound-ack
X-Hermes-Sidecar-Token: <shared token>
Content-Type: application/json

{"deliveryId":"<48 lowercase hex>"}
```

The ACK is idempotent for a bounded recent-token window. Unknown tokens return
a content-free 404. Expiry fails closed and restarts the sidecar; it never lets
later text overtake a pending attachment. Queue state, byte accounting, recent
ACKs, and timers are bounded. Logs do not include message content, handles, or
delivery IDs.

This is the external half of the secure attachment flow. Generic Hermes has no
encrypted media store, so the Python Photon adapter intentionally does not
fetch a handle or create a media cache file. An embedding runtime must register
both `PhotonAdapter.set_attachment_sender_authorizer(...)` and
`PhotonAdapter.set_attachment_handle_consumer(...)`. Sender authorization and
group mention acceptance run before the consumer. The callback receives the
raw event and must return `True` only when the downstream secure handler owns
handle redemption. It does not redeem the one-shot handle itself. The
production runner derives that readiness callback only from the exact handler
marker `keez_attachment_handles_owned is True`, then waits for the handler's
background processing-success hook before ACK. Without either callback,
attachment dispatch raises the retryable
`ATTACHMENT_CONSUMER_UNAVAILABLE` state, rolls back local dedup, and does not
tell the generic model that media is ready.

Keez's direct home-agent `/inbound` consumer follows the same wire contract; it
does not use the Python callback seam. It must authorize the sender, enforce
group mention policy, lease each `/attachment/<handle>`, securely upload the
bytes, consume only after the durable Box upload receipt, durably submit the
job keyed by `deliveryId`, and only then call
`/inbound-ack`. Reading or parsing the NDJSON line must never auto-ACK it. A
retry with the same delivery ID must resolve to the existing durable submit,
not create a second job. The secure upload and Box submission must each use
`deliveryId` as their idempotency key. Failed or unknown Box commits release
the lease and never consume or ACK. A crash before Box commit replays identical
bytes. A crash after consume or submit replays the durable receipt without a
second upload, and exact consume remains idempotent.

The remaining limitation is process crash replay. The pending event is held in
memory, and Spectrum exposes no public restart cursor API. A sidecar process
crash can therefore lose that local pending event before ACK. The protocol
closes consumer disconnect and application failure windows, but not the
provider-to-sidecar process-crash window.

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
