import assert from "node:assert/strict";
import test from "node:test";

import { AttachmentHandleStore } from "./attachment-handles.mjs";
import { InboundDeliveryQueue } from "./inbound-deliveries.mjs";
import { normalizeInboundEvent } from "./inbound-normalization.mjs";

const noTimer = () => ({ unref() {} });

for (const order of ["text-first", "attachment-first"]) {
  test(`${order} mixed parts become one durable parent delivery`, async () => {
    const handles = new AttachmentHandleStore({
      maxItemBytes: 16,
      maxTotalBytes: 16,
      maxCount: 1,
      ttlMs: 1_000,
      randomBytes: () => Buffer.alloc(24, 0xaa),
      setTimer: noTimer,
    });
    const queue = new InboundDeliveryQueue({
      randomBytes: () => Buffer.alloc(24, 0xbb),
      setTimer: noTimer,
    });
    const base = {
      sender: { id: "sender" },
      space: { id: "space" },
      timestamp: new Date("2026-01-01T00:00:00.000Z"),
    };
    const textPart = {
      ...base,
      id: "p:0/parent",
      partIndex: 0,
      parentId: "parent",
      content: { type: "text", text: "caption" },
    };
    const attachmentPart = {
      ...base,
      id: "p:1/parent",
      partIndex: 1,
      parentId: "parent",
      content: {
        type: "attachment",
        name: "photo.jpg",
        mimeType: "image/jpeg",
        size: 3,
        read: async () => Buffer.from([1, 2, 3]),
      },
    };
    const items = order === "text-first"
      ? [textPart, attachmentPart]
      : [attachmentPart, textPart];
    const event = await normalizeInboundEvent(
      { id: "space", __platform: "iMessage" },
      { ...base, id: "parent", content: { type: "group", items } },
      handles
    );

    assert.equal(event.messageId, "parent");
    assert.equal(event.content.type, "group");
    assert.deepEqual(
      event.content.items.map(({ id, partIndex, parentId }) => ({
        id,
        partIndex,
        parentId,
      })),
      items.map(({ id, partIndex, parentId }) => ({ id, partIndex, parentId }))
    );

    const delivery = queue.begin(event, {
      bindDelivery: (deliveryId) => handles.bindEvent(event, deliveryId),
    });
    const delivered = JSON.parse(delivery.line);
    let handlerCalls = 0;
    handlerCalls += 1;

    assert.equal(handlerCalls, 1);
    assert.equal(delivered.messageId, "parent");
    assert.equal(delivered.deliveryId, "bb".repeat(24));
    assert.equal(delivered.content.type, "group");
    const attachment = delivered.content.items.find(
      (item) => item.content.type === "attachment"
    );
    assert.deepEqual(
      [...handles.lease(attachment.content.handle, delivered.deliveryId).entry.bytes],
      [1, 2, 3]
    );

    assert.equal(queue.ack(delivered.deliveryId), "acked");
    assert.deepEqual(queue.stats(), { count: 0, totalBytes: 0 });
  });
}
