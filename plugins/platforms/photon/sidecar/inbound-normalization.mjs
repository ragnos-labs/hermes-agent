import { normalizeInboundBinaryContent } from "./attachment-handles.mjs";
import { normalizeEventPosition } from "./message-actions.mjs";

const REACTION_TARGET_TEXT_CAP = 2000;
const MAX_PARENT_ID_CHARS = 512;

function reactionTargetText(target) {
  const content = target && typeof target === "object" ? target.content : null;
  if (!content || typeof content !== "object") return null;
  let text = null;
  if (content.type === "text") {
    text = content.text;
  } else if (content.type === "group") {
    for (const item of Array.isArray(content.items) ? content.items : []) {
      const child = item && typeof item === "object" ? item.content : null;
      if (child && child.type === "text" && child.text) {
        text = child.text;
        break;
      }
    }
  }
  if (typeof text !== "string" || !text) return null;
  return text.length > REACTION_TARGET_TEXT_CAP
    ? text.slice(0, REACTION_TARGET_TEXT_CAP)
    : text;
}

function normalizeChildPosition(item) {
  const position = {};
  if (Number.isSafeInteger(item?.partIndex) && item.partIndex >= 0) {
    position.partIndex = item.partIndex;
  }
  if (
    typeof item?.parentId === "string" &&
    item.parentId.length > 0 &&
    item.parentId.length <= MAX_PARENT_ID_CHARS
  ) {
    position.parentId = item.parentId;
  }
  return position;
}

export async function normalizeInboundContent(content, attachmentHandles) {
  if (!content || typeof content !== "object") {
    return { type: "unknown" };
  }
  if (content.type === "text") {
    return { type: "text", text: content.text || "" };
  }
  if (content.type === "attachment" || content.type === "voice") {
    return await normalizeInboundBinaryContent(content, attachmentHandles);
  }
  if (content.type === "group") {
    const items = [];
    for (const item of Array.isArray(content.items) ? content.items : []) {
      items.push({
        id: item && typeof item === "object" ? item.id ?? null : null,
        ...normalizeChildPosition(item),
        content: await normalizeInboundContent(item?.content, attachmentHandles),
      });
    }
    return { type: "group", items };
  }
  if (content.type === "reaction") {
    const target = content.target;
    return {
      type: "reaction",
      emoji: content.emoji || "",
      targetMessageId: target?.id ?? null,
      targetDirection: target?.direction ?? null,
      targetText: reactionTargetText(target),
    };
  }
  return { type: content.type || "unknown" };
}

export async function normalizeInboundEvent(space, message, attachmentHandles) {
  try {
    const messageSpace = message.space || {};
    const timestamp = message.timestamp;
    return {
      messageId: message.id ?? null,
      ...normalizeEventPosition(message),
      platform: message.platform || space.__platform || "iMessage",
      space: {
        id: space.id ?? messageSpace.id ?? null,
        type: space.type ?? messageSpace.type ?? "dm",
        phone: space.phone ?? messageSpace.phone ?? null,
      },
      sender: { id: message.sender ? message.sender.id : null },
      content: await normalizeInboundContent(message.content, attachmentHandles),
      timestamp:
        timestamp instanceof Date
          ? timestamp.toISOString()
          : timestamp
            ? String(timestamp)
            : null,
    };
  } catch (error) {
    console.error(
      "photon-sidecar: failed to normalize inbound message: " + String(error)
    );
    return null;
  }
}
