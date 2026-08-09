const CLIENT_MESSAGE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/;
const PROVIDER_MESSAGE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

function normalizedClientMessageId(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string" || !CLIENT_MESSAGE_ID_RE.test(value)) {
    throw new TypeError("clientMessageId is invalid");
  }
  return value;
}
export function withClientMessageId(builder, value) {
  const clientMessageId = normalizedClientMessageId(value);
  if (clientMessageId === null) return builder;
  if (!builder || typeof builder.build !== "function") {
    throw new TypeError("content builder is invalid");
  }
  return {
    ...builder,
    async build(...args) {
      const content = await builder.build(...args);
      if (!content || typeof content !== "object") {
        throw new TypeError("built content is invalid");
      }
      return { ...content, clientMessageId };
    },
  };
}

export function confirmedSendResponse(result, value) {
  const clientMessageId = normalizedClientMessageId(value);
  const messageId = typeof result?.id === "string" ? result.id.trim() : "";
  if (!PROVIDER_MESSAGE_ID_RE.test(messageId)) {
    throw new TypeError("provider message id is invalid");
  }
  if (clientMessageId === null) return { messageId };
  const timestamp = result?.timestamp;
  if (!(timestamp instanceof Date) || Number.isNaN(timestamp.valueOf())) {
    throw new TypeError("provider timestamp is invalid");
  }
  return {
    messageId,
    clientMessageId,
    confirmed: true,
    providerStatus: "accepted",
    deliveredAt: timestamp.toISOString(),
  };
}
