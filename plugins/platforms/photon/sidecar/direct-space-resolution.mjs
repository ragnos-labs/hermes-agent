export function canonicalDirectChatId(phoneTarget) {
  return `any;-;${phoneTarget}`;
}

export async function resolveDirectMessageSpace(im, phoneTarget, cached = null) {
  const canonicalId = canonicalDirectChatId(phoneTarget);
  if (cached?.id === canonicalId) return cached;

  // Photon can surface an inbound direct space as a raw E.164 id. Do not send
  // through that cached object: Spectrum 12 validates outbound chat ids.
  if (cached) return await im.space.get(canonicalId);

  let created = null;
  let createError = null;

  try {
    created = await im.space.create(phoneTarget);
  } catch (error) {
    createError = error;
  }

  if (created?.id === canonicalId) return created;

  try {
    return await im.space.get(canonicalId);
  } catch (getError) {
    if (createError) {
      throw new AggregateError(
        [createError, getError],
        `unable to resolve direct iMessage space ${canonicalId}`
      );
    }
    throw getError;
  }
}
