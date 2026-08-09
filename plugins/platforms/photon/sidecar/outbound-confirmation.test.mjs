import assert from "node:assert/strict";
import test from "node:test";

import {
  confirmedSendResponse,
  withClientMessageId,
} from "./outbound-confirmation.mjs";

test("stable client message id reaches the built provider content", async () => {
  const builder = { build: async () => ({ type: "text", text: "private" }) };
  const wrapped = withClientMessageId(builder, "kpx-lhll-123");

  assert.deepEqual(await wrapped.build(), {
    type: "text",
    text: "private",
    clientMessageId: "kpx-lhll-123",
  });
});
test("provider snapshot becomes an exact confirmation response", () => {
  assert.deepEqual(
    confirmedSendResponse(
      { id: "spc-msg-123", timestamp: new Date("2026-08-09T05:27:45.678Z") },
      "kpx-lhll-123"
    ),
    {
      messageId: "spc-msg-123",
      clientMessageId: "kpx-lhll-123",
      confirmed: true,
      providerStatus: "accepted",
      deliveredAt: "2026-08-09T05:27:45.678Z",
    }
  );
});

test("legacy sends stay compatible and invalid receipts fail closed", () => {
  const builder = { build: async () => ({ type: "text", text: "private" }) };
  assert.equal(withClientMessageId(builder, undefined), builder);
  assert.deepEqual(
    confirmedSendResponse(
      { id: "spc-msg-123", timestamp: new Date("2026-08-09T05:27:45.678Z") },
      undefined
    ),
    { messageId: "spc-msg-123" }
  );
  assert.throws(() => withClientMessageId(builder, "contains spaces"), /invalid/);
  assert.throws(
    () => confirmedSendResponse({ id: null, timestamp: new Date() }, "kpx-123"),
    /provider message id/
  );
  assert.throws(
    () => confirmedSendResponse({ id: "spc-msg-123" }, "kpx-123"),
    /provider timestamp/
  );
});
