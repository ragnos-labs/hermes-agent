import crypto from "node:crypto";

const CLIENT_MESSAGE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;

export class SendRequestError extends Error {}

export function resolveClientMessageId(value, randomUUID = crypto.randomUUID) {
  if (value === undefined || value === null) return randomUUID();
  if (typeof value !== "string" || !CLIENT_MESSAGE_ID_RE.test(value)) {
    throw new SendRequestError(
      "clientMessageId must be 1-200 URL-safe identifier characters"
    );
  }
  return value;
}

export async function sendWithClientMessageId(
  space,
  builder,
  clientMessageId,
  { sdkSupportsClientMessageId = false } = {}
) {
  if (sdkSupportsClientMessageId) {
    return space.send(builder, { clientMessageId });
  }
  return space.send(builder);
}

function deliveredAtFrom(result) {
  const timestamp = result?.timestamp;
  if (timestamp === undefined || timestamp === null) return null;
  const date = timestamp instanceof Date ? timestamp : new Date(timestamp);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function buildSendReceipt(clientMessageId, result) {
  const messageId =
    typeof result?.id === "string" && result.id ? result.id : null;
  return {
    clientMessageId,
    confirmed: messageId !== null,
    providerStatus: messageId === null ? "unconfirmed" : "accepted",
    messageId,
    deliveredAt: deliveredAtFrom(result),
  };
}

export async function sendTextMessage({
  body,
  resolveSpace,
  textBuilder,
  markdownBuilder,
  sdkSupportsClientMessageId = false,
}) {
  const {
    spaceId,
    text,
    format = "text",
    clientMessageId: requestedClientMessageId,
  } = body || {};
  if (typeof spaceId !== "string" || !spaceId || typeof text !== "string") {
    throw new SendRequestError("spaceId and text are required");
  }
  if (format !== "text" && format !== "markdown") {
    throw new SendRequestError("format must be text or markdown");
  }
  const clientMessageId = resolveClientMessageId(requestedClientMessageId);
  const space = await resolveSpace(spaceId);
  const builder = format === "markdown" ? markdownBuilder(text) : textBuilder(text);
  const result = await sendWithClientMessageId(space, builder, clientMessageId, {
    sdkSupportsClientMessageId,
  });
  return buildSendReceipt(clientMessageId, result);
}
