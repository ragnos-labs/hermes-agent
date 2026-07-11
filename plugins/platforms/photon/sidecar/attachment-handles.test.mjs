import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  AttachmentHandleError,
  AttachmentHandleStore,
  normalizeInboundBinaryContent,
  parseAttachmentHandlePath,
  serveAttachmentHandle,
} from "./attachment-handles.mjs";

const noTimer = () => ({ unref() {} });

test("attachment bytes are represented by metadata and a one-shot opaque handle", async () => {
  const store = new AttachmentHandleStore({
    maxItemBytes: 32,
    maxTotalBytes: 64,
    maxCount: 2,
    ttlMs: 1_000,
    setTimer: noTimer,
  });
  const event = await normalizeInboundBinaryContent(
    {
      type: "attachment",
      id: "provider-1",
      name: "photo.png",
      mimeType: "image/png",
      size: 4,
      read: async () => Buffer.from([1, 2, 3, 4]),
    },
    store
  );

  assert.deepEqual(Object.keys(event).sort(), [
    "handle",
    "id",
    "mimeType",
    "name",
    "size",
    "type",
  ]);
  assert.match(event.handle, /^[a-f0-9]{48}$/);
  assert.equal("data" in event, false);
  assert.equal("encoding" in event, false);

  const consumed = store.consume(event.handle);
  assert.deepEqual([...consumed.bytes], [1, 2, 3, 4]);
  assert.equal(consumed.mimeType, "image/png");
  assert.throws(
    () => store.consume(event.handle),
    (error) =>
      error instanceof AttachmentHandleError && error.code === "not_found"
  );
});

test("store enforces item, count, and aggregate byte caps", () => {
  const store = new AttachmentHandleStore({
    maxItemBytes: 4,
    maxTotalBytes: 6,
    maxCount: 2,
    ttlMs: 1_000,
    setTimer: noTimer,
  });
  store.put(Buffer.from("123"), { mimeType: "text/plain" });
  store.put(Buffer.from("456"), { mimeType: "text/plain" });
  assert.throws(
    () => store.put(Buffer.from("7"), {}),
    (error) => error.code === "capacity_exceeded"
  );
  assert.throws(
    () => store.put(Buffer.from("12345"), {}),
    (error) => error.code === "item_too_large"
  );
  assert.deepEqual(store.stats(), { count: 2, totalBytes: 6 });
});

test("expired handles are wiped and cannot be replayed", () => {
  let now = 10_000;
  const store = new AttachmentHandleStore({
    maxItemBytes: 8,
    maxTotalBytes: 8,
    maxCount: 1,
    ttlMs: 50,
    now: () => now,
    setTimer: noTimer,
  });
  const { handle } = store.put(Buffer.from("secret"), {});
  now += 51;
  store.purgeExpired();
  assert.deepEqual(store.stats(), { count: 0, totalBytes: 0 });
  assert.throws(() => store.consume(handle), /not found/);
});

test("expiry timer purges bytes without a later request", () => {
  let now = 20_000;
  let expire = null;
  const store = new AttachmentHandleStore({
    maxItemBytes: 8,
    maxTotalBytes: 8,
    maxCount: 1,
    ttlMs: 25,
    now: () => now,
    setTimer: (callback) => {
      expire = callback;
      return { unref() {} };
    },
    clearTimer: () => {},
  });
  store.put(Buffer.from("secret"), {});
  assert.equal(typeof expire, "function");
  now += 26;
  expire();
  assert.deepEqual(store.stats(), { count: 0, totalBytes: 0 });
});

test("read failures and over-cap reads emit metadata without a handle", async () => {
  const errors = [];
  const store = new AttachmentHandleStore({
    maxItemBytes: 4,
    maxTotalBytes: 8,
    maxCount: 2,
    ttlMs: 1_000,
    setTimer: noTimer,
  });
  const failed = await normalizeInboundBinaryContent(
    {
      type: "attachment",
      name: "failed.bin",
      size: 4,
      read: async () => {
        throw new Error("private provider failure");
      },
    },
    store,
    (message) => errors.push(message)
  );
  const oversized = await normalizeInboundBinaryContent(
    {
      type: "attachment",
      name: "large.bin",
      size: 4,
      read: async () => Buffer.from("12345"),
    },
    store,
    (message) => errors.push(message)
  );

  assert.equal("handle" in failed, false);
  assert.equal("handle" in oversized, false);
  assert.equal(JSON.stringify(errors).includes("private provider failure"), false);
  assert.deepEqual(store.stats(), { count: 0, totalBytes: 0 });
});

test("unknown-size Spectrum streams are read incrementally within the hard cap", async () => {
  const store = new AttachmentHandleStore({
    maxItemBytes: 4,
    maxTotalBytes: 4,
    maxCount: 1,
    ttlMs: 1_000,
    setTimer: noTimer,
  });
  const content = {
    type: "attachment",
    name: "stream.bin",
    stream: async () =>
      new ReadableStream({
        start(controller) {
          controller.enqueue(Uint8Array.from([1, 2]));
          controller.enqueue(Uint8Array.from([3, 4]));
          controller.close();
        },
      }),
  };

  const event = await normalizeInboundBinaryContent(content, store);
  assert.equal(event.size, 4);
  assert.deepEqual([...store.consume(event.handle).bytes], [1, 2, 3, 4]);
});

test("unknown-size streams are cancelled when they cross the hard cap", async () => {
  let cancelled = false;
  const store = new AttachmentHandleStore({
    maxItemBytes: 3,
    maxTotalBytes: 3,
    maxCount: 1,
    ttlMs: 1_000,
    setTimer: noTimer,
  });
  const event = await normalizeInboundBinaryContent(
    {
      type: "attachment",
      name: "large-stream.bin",
      stream: async () =>
        new ReadableStream({
          start(controller) {
            controller.enqueue(Uint8Array.from([1, 2]));
            controller.enqueue(Uint8Array.from([3, 4]));
          },
          cancel() {
            cancelled = true;
          },
        }),
    },
    store,
    () => {}
  );

  assert.equal("handle" in event, false);
  assert.equal(cancelled, true);
  assert.deepEqual(store.stats(), { count: 0, totalBytes: 0 });
});

test("attachment GET path schema is exact", () => {
  const handle = "a".repeat(48);
  assert.equal(parseAttachmentHandlePath(`/attachment/${handle}`), handle);
  for (const path of [
    `/attachment/${handle}?again=1`,
    `/attachment/${handle}/extra`,
    `/attachment/${"a".repeat(47)}`,
    `/attachments/${handle}`,
    "/attachment/not-hex",
  ]) {
    assert.equal(parseAttachmentHandlePath(path), null);
  }
});

class FakeResponse extends EventEmitter {
  constructor() {
    super();
    this.headers = {};
    this.statusCode = null;
    this.body = null;
  }

  setHeader(name, value) {
    this.headers[name] = value;
  }

  end(body, callback) {
    this.body = Buffer.isBuffer(body) ? Buffer.from(body) : String(body ?? "");
    callback?.();
  }
}

test("raw attachment GET consumes once and replay is content-free", () => {
  const store = new AttachmentHandleStore({
    maxItemBytes: 16,
    maxTotalBytes: 16,
    maxCount: 1,
    ttlMs: 1_000,
    setTimer: noTimer,
  });
  const { handle } = store.put(Buffer.from("private bytes"), {
    mimeType: "text/plain",
  });
  const first = new FakeResponse();
  assert.equal(
    serveAttachmentHandle(`/attachment/${handle}`, first, store),
    true
  );
  assert.equal(first.statusCode, 200);
  assert.equal(first.headers["Content-Type"], "text/plain");
  assert.equal(first.headers["Cache-Control"], "no-store");
  assert.equal(first.body.toString(), "private bytes");

  const replay = new FakeResponse();
  assert.equal(
    serveAttachmentHandle(`/attachment/${handle}`, replay, store),
    true
  );
  assert.equal(replay.statusCode, 404);
  assert.equal(replay.body.includes("private bytes"), false);
});

test("expired raw attachment GET returns the same stable not-found envelope", () => {
  let now = 1_000;
  const store = new AttachmentHandleStore({
    maxItemBytes: 16,
    maxTotalBytes: 16,
    maxCount: 1,
    ttlMs: 10,
    now: () => now,
    setTimer: noTimer,
  });
  const { handle } = store.put(Buffer.from("private bytes"), {});
  now += 11;
  const expired = new FakeResponse();
  serveAttachmentHandle(`/attachment/${handle}`, expired, store);
  assert.equal(expired.statusCode, 404);
  assert.deepEqual(JSON.parse(expired.body), {
    ok: false,
    error: "attachment handle not found",
  });
});

test("response faults still wipe consumed bytes", () => {
  const store = new AttachmentHandleStore({
    maxItemBytes: 16,
    maxTotalBytes: 16,
    maxCount: 1,
    ttlMs: 1_000,
    setTimer: noTimer,
  });
  const { handle } = store.put(Buffer.from("private bytes"), {});
  class FailingResponse extends FakeResponse {
    end(body) {
      this.reference = body;
      throw new Error("socket failed");
    }
  }
  const response = new FailingResponse();
  assert.throws(
    () => serveAttachmentHandle(`/attachment/${handle}`, response, store),
    /socket failed/
  );
  assert.equal(response.reference.every((value) => value === 0), true);
  assert.deepEqual(store.stats(), { count: 0, totalBytes: 0 });
});

test("stalled responses remain charged and are wiped by the transfer TTL", () => {
  let now = 30_000;
  let expire = null;
  const store = new AttachmentHandleStore({
    maxItemBytes: 16,
    maxTotalBytes: 16,
    maxCount: 1,
    ttlMs: 50,
    now: () => now,
    setTimer: (callback) => {
      expire = callback;
      return { unref() {} };
    },
    clearTimer: () => {},
  });
  const { handle } = store.put(Buffer.from("private bytes"), {});
  class StalledResponse extends FakeResponse {
    end(body) {
      this.reference = body;
    }

    destroy() {
      this.destroyed = true;
      this.emit("close");
    }
  }
  const response = new StalledResponse();
  serveAttachmentHandle(`/attachment/${handle}`, response, store);

  assert.deepEqual(store.stats(), { count: 1, totalBytes: 13 });
  assert.throws(
    () => store.put(Buffer.from("x"), {}),
    (error) => error.code === "capacity_exceeded"
  );
  now += 51;
  expire();
  assert.deepEqual(store.stats(), { count: 0, totalBytes: 0 });
  assert.equal(response.reference.every((value) => value === 0), true);
  assert.equal(response.destroyed, true);

  const replay = new FakeResponse();
  serveAttachmentHandle(`/attachment/${handle}`, replay, store);
  assert.equal(replay.statusCode, 404);
});

test("completion close error and shutdown release each lease exactly once", () => {
  for (const releaseKind of ["completion", "close", "error", "shutdown"]) {
    const store = new AttachmentHandleStore({
      maxItemBytes: 16,
      maxTotalBytes: 16,
      maxCount: 1,
      ttlMs: 1_000,
      setTimer: noTimer,
    });
    const { handle } = store.put(Buffer.from("private bytes"), {});
    class ManualResponse extends FakeResponse {
      end(body, callback) {
        this.reference = body;
        this.completion = callback;
      }

      destroy() {
        this.destroyed = true;
        this.emit("close");
      }
    }
    const response = new ManualResponse();
    serveAttachmentHandle(`/attachment/${handle}`, response, store);
    if (releaseKind === "completion") response.completion();
    if (releaseKind === "close") response.emit("close");
    if (releaseKind === "error") response.emit("error", new Error("socket"));
    if (releaseKind === "shutdown") store.close();

    response.completion();
    response.emit("close");
    if (releaseKind !== "error") response.emit("error", new Error("late"));
    store.close();
    assert.deepEqual(store.stats(), { count: 0, totalBytes: 0 });
    assert.equal(response.reference.every((value) => value === 0), true);
    assert.equal(Boolean(response.destroyed), releaseKind === "shutdown");
  }
});
