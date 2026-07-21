import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalDirectChatId,
  resolveDirectMessageSpace,
} from "./direct-space-resolution.mjs";

test("direct space resolution rehydrates a raw create result through Spectrum's canonical DM GUID", async () => {
  const calls = [];
  const im = {
    space: {
      create: async (address) => {
        calls.push(["create", address]);
        return { id: address };
      },
      get: async (chatId) => {
        calls.push(["get", chatId]);
        return { id: chatId, type: "dm" };
      },
    },
  };

  const space = await resolveDirectMessageSpace(im, "+15555550123");

  assert.equal(space.id, "any;-;+15555550123");
  assert.deepEqual(calls, [
    ["create", "+15555550123"],
    ["get", "any;-;+15555550123"],
  ]);
});

test("direct space resolution keeps an already canonical create result", async () => {
  const canonicalId = canonicalDirectChatId("+15555550123");
  const im = {
    space: {
      create: async () => ({ id: canonicalId, type: "dm" }),
      get: async () => {
        throw new Error("canonical create result must not be rehydrated");
      },
    },
  };

  const space = await resolveDirectMessageSpace(im, "+15555550123");

  assert.equal(space.id, canonicalId);
});

test("direct space resolution replaces a raw inbound cache entry before outbound use", async () => {
  const calls = [];
  const im = {
    space: {
      create: async () => {
        throw new Error("a raw cache entry must rehydrate without recreating");
      },
      get: async (chatId) => {
        calls.push(chatId);
        return { id: chatId, type: "dm" };
      },
    },
  };

  const space = await resolveDirectMessageSpace(
    im,
    "+15555550123",
    { id: "+15555550123", type: "dm" }
  );

  assert.equal(space.id, "any;-;+15555550123");
  assert.deepEqual(calls, ["any;-;+15555550123"]);
});
