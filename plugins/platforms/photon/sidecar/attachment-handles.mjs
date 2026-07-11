import crypto from "node:crypto";

const HANDLE_RE = /^[a-f0-9]{48}$/;

export class AttachmentHandleError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export class AttachmentHandleStore {
  constructor({
    maxItemBytes,
    maxTotalBytes,
    maxCount,
    ttlMs,
    now = Date.now,
    randomBytes = crypto.randomBytes,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    for (const [name, value] of Object.entries({
      maxItemBytes,
      maxTotalBytes,
      maxCount,
      ttlMs,
    })) {
      if (!Number.isSafeInteger(value) || value <= 0) {
        throw new TypeError(`${name} must be a positive safe integer`);
      }
    }
    if (maxItemBytes > maxTotalBytes) {
      throw new TypeError("maxItemBytes cannot exceed maxTotalBytes");
    }
    this.maxItemBytes = maxItemBytes;
    this.maxTotalBytes = maxTotalBytes;
    this.maxCount = maxCount;
    this.ttlMs = ttlMs;
    this._now = now;
    this._randomBytes = randomBytes;
    this._setTimer = setTimer;
    this._clearTimer = clearTimer;
    this._entries = new Map();
    this._totalBytes = 0;
    this._expiryTimer = null;
  }

  _wipe(entry) {
    entry.bytes.fill(0);
    this._totalBytes -= entry.bytes.length;
  }

  _scheduleExpiry() {
    if (this._expiryTimer !== null) {
      this._clearTimer(this._expiryTimer);
      this._expiryTimer = null;
    }
    let earliest = Infinity;
    for (const entry of this._entries.values()) {
      earliest = Math.min(earliest, entry.expiresAt);
    }
    if (earliest === Infinity) return;
    this._expiryTimer = this._setTimer(
      () => {
        this._expiryTimer = null;
        this.purgeExpired();
      },
      Math.max(1, earliest - this._now())
    );
    this._expiryTimer?.unref?.();
  }

  purgeExpired() {
    const now = this._now();
    for (const [handle, entry] of this._entries) {
      if (entry.expiresAt <= now) {
        this._entries.delete(handle);
        this._wipe(entry);
      }
    }
    this._scheduleExpiry();
  }

  assertCapacityFor(size) {
    if (!Number.isSafeInteger(size) || size <= 0) {
      throw new AttachmentHandleError(
        "size_unavailable",
        "attachment size is unavailable"
      );
    }
    if (size > this.maxItemBytes) {
      throw new AttachmentHandleError(
        "item_too_large",
        "attachment exceeds the per-item limit"
      );
    }
    this.purgeExpired();
    if (
      this._entries.size >= this.maxCount ||
      this._totalBytes + size > this.maxTotalBytes
    ) {
      throw new AttachmentHandleError(
        "capacity_exceeded",
        "attachment handle capacity is full"
      );
    }
  }

  availableBytes() {
    this.purgeExpired();
    if (this._entries.size >= this.maxCount) return 0;
    return Math.min(
      this.maxItemBytes,
      this.maxTotalBytes - this._totalBytes
    );
  }

  put(bytes, { mimeType = null } = {}) {
    if (!Buffer.isBuffer(bytes)) {
      throw new TypeError("attachment bytes must be a Buffer");
    }
    this.assertCapacityFor(bytes.length);
    let handle;
    do {
      handle = this._randomBytes(24).toString("hex");
    } while (this._entries.has(handle));
    const owned = Buffer.from(bytes);
    this._entries.set(handle, {
      bytes: owned,
      mimeType:
        typeof mimeType === "string" && mimeType.length <= 255
          ? mimeType
          : null,
      expiresAt: this._now() + this.ttlMs,
    });
    this._totalBytes += owned.length;
    this._scheduleExpiry();
    return { handle };
  }

  consume(handle) {
    if (typeof handle !== "string" || !HANDLE_RE.test(handle)) {
      throw new AttachmentHandleError(
        "not_found",
        "attachment handle not found"
      );
    }
    this.purgeExpired();
    const entry = this._entries.get(handle);
    if (!entry) {
      throw new AttachmentHandleError(
        "not_found",
        "attachment handle not found"
      );
    }
    this._entries.delete(handle);
    this._totalBytes -= entry.bytes.length;
    this._scheduleExpiry();
    return entry;
  }

  stats() {
    this.purgeExpired();
    return { count: this._entries.size, totalBytes: this._totalBytes };
  }

  close() {
    if (this._expiryTimer !== null) {
      this._clearTimer(this._expiryTimer);
      this._expiryTimer = null;
    }
    for (const entry of this._entries.values()) this._wipe(entry);
    this._entries.clear();
  }
}

export async function normalizeInboundBinaryContent(
  content,
  store,
  logError = (message) => console.error(message)
) {
  const meta = {
    type: content.type,
    id: content.id ?? null,
    name: content.name ?? null,
    mimeType: content.mimeType ?? null,
    size: typeof content.size === "number" ? content.size : null,
  };
  if (content.type === "voice" && typeof content.duration === "number") {
    meta.duration = content.duration;
  }
  try {
    if (meta.size !== null) store.assertCapacityFor(meta.size);
    const maxBytes = store.availableBytes();
    if (maxBytes <= 0) {
      throw new AttachmentHandleError(
        "capacity_exceeded",
        "attachment handle capacity is full"
      );
    }
    const bytes = await readContentBytes(content, maxBytes);
    const { handle } = store.put(bytes, { mimeType: meta.mimeType });
    meta.size = bytes.length;
    meta.handle = handle;
  } catch (error) {
    const code =
      error instanceof AttachmentHandleError ? error.code : "read_failed";
    logError(`photon-sidecar: attachment bytes unavailable (${code})`);
  }
  return meta;
}

async function readContentBytes(content, maxBytes) {
  if (typeof content.stream === "function") {
    const stream = await content.stream();
    if (!stream || typeof stream.getReader !== "function") {
      throw new AttachmentHandleError(
        "stream_unavailable",
        "attachment stream is unavailable"
      );
    }
    const reader = stream.getReader();
    const chunks = [];
    let total = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = Buffer.from(value);
        total += chunk.length;
        if (total > maxBytes) {
          try {
            await reader.cancel();
          } catch {
            // The hard cap is authoritative even if provider cancellation
            // itself reports a transport failure.
          }
          throw new AttachmentHandleError(
            "item_too_large",
            "attachment exceeds the available memory limit"
          );
        }
        chunks.push(chunk);
      }
    } finally {
      reader.releaseLock?.();
    }
    return Buffer.concat(chunks, total);
  }
  if (
    typeof content.read !== "function" ||
    !Number.isSafeInteger(content.size) ||
    content.size <= 0
  ) {
    throw new AttachmentHandleError(
      "size_unavailable",
      "attachment has no bounded byte source"
    );
  }
  const bytes = Buffer.from(await content.read());
  if (bytes.length > maxBytes) {
    throw new AttachmentHandleError(
      "item_too_large",
      "attachment exceeds the available memory limit"
    );
  }
  return bytes;
}

export function parseAttachmentHandlePath(path) {
  if (typeof path !== "string") return null;
  const match = path.match(/^\/attachment\/([a-f0-9]{48})$/);
  return match ? match[1] : null;
}

function safeMimeType(value) {
  if (
    typeof value === "string" &&
    /^[A-Za-z0-9!#$&^_.+-]+\/[A-Za-z0-9!#$&^_.+-]+$/.test(value)
  ) {
    return value;
  }
  return "application/octet-stream";
}

export function serveAttachmentHandle(path, res, store) {
  const handle = parseAttachmentHandlePath(path);
  if (handle === null) return false;
  let entry;
  try {
    entry = store.consume(handle);
  } catch (error) {
    if (!(error instanceof AttachmentHandleError)) throw error;
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", "no-store");
    res.end(JSON.stringify({ ok: false, error: "attachment handle not found" }));
    return true;
  }
  res.statusCode = 200;
  res.setHeader("Content-Type", safeMimeType(entry.mimeType));
  res.setHeader("Content-Length", String(entry.bytes.length));
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  let wiped = false;
  const wipe = () => {
    if (wiped) return;
    wiped = true;
    entry.bytes.fill(0);
  };
  res.once?.("close", wipe);
  res.once?.("error", wipe);
  try {
    res.end(entry.bytes, wipe);
  } catch (error) {
    wipe();
    throw error;
  }
  return true;
}
