import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSendReceipt,
  resolveClientMessageId,
  sendTextMessage,
  sendWithClientMessageId,
} from "./send-contract.mjs";
import {
  normalizeEventPosition,
  replyToMessage,
  editMessage,
} from "./message-actions.mjs";

test("clientMessageId remains stable when supplied and is generated when absent", () => {
  assert.equal(resolveClientMessageId("delivery:job-123"), "delivery:job-123");
  assert.equal(
    resolveClientMessageId(undefined, () => "generated-123"),
    "generated-123"
  );
});

test("clientMessageId rejects malformed values", () => {
  for (const value of ["", "contains spaces", "x".repeat(201), 123]) {
    assert.throws(() => resolveClientMessageId(value), /clientMessageId/);
  }
});

test("send passes clientMessageId only when the SDK capability is enabled", async () => {
  const calls = [];
  const space = {
    async send(...args) {
      calls.push(args);
      return { id: "provider-1", timestamp: new Date("2026-07-11T01:02:03Z") };
    },
  };
  const builder = { type: "text" };

  await sendWithClientMessageId(space, builder, "client-1");
  await sendWithClientMessageId(space, builder, "client-2", {
    sdkSupportsClientMessageId: true,
  });

  assert.deepEqual(calls, [
    [builder],
    [builder, { clientMessageId: "client-2" }],
  ]);
});

test("text send accepts and echoes clientMessageId without returning raw content", async () => {
  const calls = [];
  const receipt = await sendTextMessage({
    body: {
      spaceId: "space-1",
      text: "private content",
      format: "markdown",
      clientMessageId: "delivery:job-123",
    },
    resolveSpace: async (spaceId) => {
      assert.equal(spaceId, "space-1");
      return {
        async send(...args) {
          calls.push(args);
          return {
            id: "provider-1",
            timestamp: "2026-07-11T01:02:03Z",
          };
        },
      };
    },
    textBuilder: (text) => ({ type: "text", text }),
    markdownBuilder: (text) => ({ type: "markdown", text }),
  });

  assert.deepEqual(calls, [[{ type: "markdown", text: "private content" }]]);
  assert.equal(receipt.clientMessageId, "delivery:job-123");
  assert.equal(receipt.confirmed, true);
  assert.equal(receipt.providerStatus, "accepted");
  assert.equal(receipt.messageId, "provider-1");
  assert.equal(receipt.deliveredAt, "2026-07-11T01:02:03.000Z");
  assert.equal(JSON.stringify(receipt).includes("private content"), false);
});

test("structured receipt confirms only a provider message and contains no content", () => {
  assert.deepEqual(
    buildSendReceipt("client-1", {
      id: "provider-1",
      timestamp: new Date("2026-07-11T01:02:03Z"),
      content: { type: "text", text: "private content" },
    }),
    {
      clientMessageId: "client-1",
      confirmed: true,
      providerStatus: "accepted",
      messageId: "provider-1",
      deliveredAt: "2026-07-11T01:02:03.000Z",
    }
  );
});

test("structured receipt is explicitly unconfirmed when the SDK returns no message", () => {
  assert.deepEqual(buildSendReceipt("client-1", undefined), {
    clientMessageId: "client-1",
    confirmed: false,
    providerStatus: "unconfirmed",
    messageId: null,
    deliveredAt: null,
  });
});

test("event position preserves only a safe sequence and opaque cursor", () => {
  assert.deepEqual(normalizeEventPosition({ sequence: 7, cursor: "opaque:7" }), {
    sequence: 7,
    cursor: "opaque:7",
  });
  assert.deepEqual(normalizeEventPosition({ sequence: -1, cursor: "" }), {});
  assert.deepEqual(
    normalizeEventPosition({ sequence: Number.MAX_SAFE_INTEGER + 1, cursor: 9 }),
    {}
  );
});

test("reply requires the exact authenticated sidecar schema and returns a content-free receipt", async () => {
  const target = {
    async reply(builder) {
      assert.deepEqual(builder, { type: "text", text: "answer" });
      return {
        id: "reply-1",
        timestamp: "2026-07-11T03:04:05Z",
        content: { type: "text", text: "answer" },
      };
    },
  };
  const receipt = await replyToMessage({
    body: {
      spaceId: "space-1",
      text: "answer",
      replyToMessageId: "inbound-1",
      clientMessageId: "reply:job-1",
    },
    resolveTarget: async (spaceId, messageId) => {
      assert.equal(spaceId, "space-1");
      assert.equal(messageId, "inbound-1");
      return target;
    },
    textBuilder: (text) => ({ type: "text", text }),
  });

  assert.deepEqual(receipt, {
    clientMessageId: "reply:job-1",
    confirmed: true,
    providerStatus: "accepted",
    messageId: "reply-1",
    deliveredAt: "2026-07-11T03:04:05.000Z",
  });
  assert.equal(JSON.stringify(receipt).includes("answer"), false);
});

test("reply rejects extra fields and oversized text before provider access", async () => {
  let resolved = false;
  for (const body of [
    {
      spaceId: "space-1",
      text: "answer",
      replyToMessageId: "inbound-1",
      clientMessageId: "reply:job-1",
      extra: true,
    },
    {
      spaceId: "space-1",
      text: "x".repeat(100_001),
      replyToMessageId: "inbound-1",
      clientMessageId: "reply:job-1",
    },
  ]) {
    await assert.rejects(
      replyToMessage({
        body,
        resolveTarget: async () => {
          resolved = true;
        },
        textBuilder: (text) => text,
      }),
      /reply request/
    );
  }
  assert.equal(resolved, false);
});

test("edit requires the exact schema and confirms the existing message id", async () => {
  const target = {
    id: "outbound-1",
    async edit(builder) {
      assert.deepEqual(builder, { type: "text", text: "corrected" });
    },
  };
  const receipt = await editMessage({
    body: {
      spaceId: "space-1",
      messageId: "outbound-1",
      text: "corrected",
      clientMessageId: "edit:job-1",
    },
    resolveTarget: async () => target,
    textBuilder: (text) => ({ type: "text", text }),
  });

  assert.deepEqual(receipt, {
    clientMessageId: "edit:job-1",
    confirmed: true,
    providerStatus: "edited",
    messageId: "outbound-1",
    deliveredAt: null,
  });
  assert.equal(JSON.stringify(receipt).includes("corrected"), false);
});

test("edit rejects missing and extra fields before provider access", async () => {
  let resolved = false;
  for (const body of [
    {
      spaceId: "space-1",
      messageId: "outbound-1",
      text: "corrected",
    },
    {
      spaceId: "space-1",
      messageId: "outbound-1",
      text: "corrected",
      clientMessageId: "edit:job-1",
      unexpected: "field",
    },
  ]) {
    await assert.rejects(
      editMessage({
        body,
        resolveTarget: async () => {
          resolved = true;
        },
        textBuilder: (text) => text,
      }),
      /edit request/
    );
  }
  assert.equal(resolved, false);
});
