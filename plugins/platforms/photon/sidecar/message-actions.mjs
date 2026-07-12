import { buildSendReceipt, resolveClientMessageId, SendRequestError } from "./send-contract.mjs";

const MAX_TEXT_CHARS = 100_000;
const MAX_ID_CHARS = 512;
const MAX_CURSOR_CHARS = 1_024;

const REPLY_FIELDS = new Set([
  "spaceId",
  "text",
  "replyToMessageId",
  "clientMessageId",
]);
const EDIT_FIELDS = new Set([
  "spaceId",
  "messageId",
  "text",
  "clientMessageId",
]);

export class MessageActionRequestError extends SendRequestError {}

function exactObject(body, fields, label) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new MessageActionRequestError(`${label} request must be an object`);
  }
  const keys = Object.keys(body);
  if (keys.length !== fields.size || keys.some((key) => !fields.has(key))) {
    throw new MessageActionRequestError(`${label} request fields are invalid`);
  }
}

function requiredId(value, field, label) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_ID_CHARS
  ) {
    throw new MessageActionRequestError(
      `${label} request ${field} must be 1-${MAX_ID_CHARS} characters`
    );
  }
  return value;
}

function requiredText(value, label) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_TEXT_CHARS
  ) {
    throw new MessageActionRequestError(
      `${label} request text must be 1-${MAX_TEXT_CHARS} characters`
    );
  }
  return value;
}

function requiredClientMessageId(value, label) {
  if (value === undefined || value === null) {
    throw new MessageActionRequestError(
      `${label} request clientMessageId is required`
    );
  }
  try {
    return resolveClientMessageId(value);
  } catch {
    throw new MessageActionRequestError(
      `${label} request clientMessageId is invalid`
    );
  }
}

export function normalizeEventPosition(message) {
  const position = {};
  if (Number.isSafeInteger(message?.sequence) && message.sequence >= 0) {
    position.sequence = message.sequence;
  }
  if (
    typeof message?.cursor === "string" &&
    message.cursor.length > 0 &&
    message.cursor.length <= MAX_CURSOR_CHARS
  ) {
    position.cursor = message.cursor;
  }
  return position;
}

export async function replyToMessage({
  body,
  resolveTarget,
  textBuilder,
}) {
  exactObject(body, REPLY_FIELDS, "reply");
  const spaceId = requiredId(body.spaceId, "spaceId", "reply");
  const replyToMessageId = requiredId(
    body.replyToMessageId,
    "replyToMessageId",
    "reply"
  );
  const text = requiredText(body.text, "reply");
  const clientMessageId = requiredClientMessageId(
    body.clientMessageId,
    "reply"
  );
  const target = await resolveTarget(spaceId, replyToMessageId);
  if (!target || typeof target.reply !== "function") {
    throw new MessageActionRequestError("reply request target was not found");
  }
  const result = await target.reply(textBuilder(text));
  return buildSendReceipt(clientMessageId, result);
}

export async function editMessage({
  body,
  resolveTarget,
  textBuilder,
}) {
  exactObject(body, EDIT_FIELDS, "edit");
  const spaceId = requiredId(body.spaceId, "spaceId", "edit");
  const messageId = requiredId(body.messageId, "messageId", "edit");
  const text = requiredText(body.text, "edit");
  const clientMessageId = requiredClientMessageId(
    body.clientMessageId,
    "edit"
  );
  const target = await resolveTarget(spaceId, messageId);
  if (!target || typeof target.edit !== "function") {
    throw new MessageActionRequestError("edit request target was not found");
  }
  await target.edit(textBuilder(text));
  return {
    clientMessageId,
    confirmed: true,
    providerStatus: "edited",
    messageId,
    deliveredAt: null,
  };
}
