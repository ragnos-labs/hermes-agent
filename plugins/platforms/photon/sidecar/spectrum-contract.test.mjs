import assert from "node:assert/strict";
import test from "node:test";

import {
  Spectrum,
  attachment,
  definePlatform,
  markdown,
  text,
  typing,
  voice,
} from "spectrum-ts";
import { asAttachment, asGroup, asText } from "spectrum-ts/authoring";
import { imessage } from "spectrum-ts/providers/imessage";
import z from "zod";

import { patchSpectrumTs } from "./patch-spectrum-mixed-attachments.mjs";

test("pinned Spectrum exposes every sidecar import", () => {
  for (const exported of [
    Spectrum,
    attachment,
    markdown,
    text,
    typing,
    voice,
  ]) {
    assert.equal(typeof exported, "function");
  }
  assert.equal(typeof imessage, "function");
  assert.equal(typeof imessage.config, "function");
});

test("mixed attachment compatibility hook accepts the pinned SDK", () => {
  const result = patchSpectrumTs();

  assert.equal(result.patched, false);
  assert.equal(result.reason, "upstream native mixed parts");
});

test("native mixed parts stay one parent with ordered child identity", async () => {
  const timestamp = new Date("2026-01-01T00:00:00.000Z");
  const base = {
    sender: { id: "sender" },
    space: { id: "space" },
    timestamp,
  };
  const provider = definePlatform("contract_test", {
    config: z.object({}),
    lifecycle: { createClient: async () => ({}) },
    user: { resolve: async ({ input }) => ({ id: input.userID }) },
    space: {
      create: async () => ({ id: "space" }),
      get: async ({ input }) => ({ id: input.id }),
    },
    async *messages() {
      yield {
        ...base,
        id: "parent",
        content: asGroup({
          items: [
            {
              ...base,
              id: "p:0/parent",
              content: asText("caption"),
              partIndex: 0,
              parentId: "parent",
            },
            {
              ...base,
              id: "p:1/parent",
              content: asAttachment({
                name: "photo.jpg",
                mimeType: "image/jpeg",
                size: 3,
                read: async () => Buffer.from([1, 2, 3]),
              }),
              partIndex: 1,
              parentId: "parent",
            },
          ],
        }),
      };
    },
    send: async ({ content, space }) => ({
      content,
      id: "outbound",
      space,
      timestamp,
    }),
  });
  const app = await Spectrum({
    providers: [provider.config({})],
    options: { flattenGroups: false, logLevel: "error" },
  });

  try {
    const iterator = app.messages[Symbol.asyncIterator]();
    const parent = (await iterator.next()).value[1];
    const [first, second] = parent.content.items;

    assert.equal(parent.id, "parent");
    assert.equal(parent.content.type, "group");
    assert.equal(parent.content.items.length, 2);
    assert.deepEqual(
      [first.id, first.partIndex, first.parentId, first.content.type],
      ["p:0/parent", 0, "parent", "text"]
    );
    assert.deepEqual(
      [second.id, second.partIndex, second.parentId, second.content.type],
      ["p:1/parent", 1, "parent", "attachment"]
    );
    assert.deepEqual([...await second.content.read()], [1, 2, 3]);
  } finally {
    await app.stop();
  }
});
