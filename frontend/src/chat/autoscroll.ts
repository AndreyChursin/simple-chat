/** Smart autoscroll rules (FRONTEND_SPEC §4.3). */
export const AT_BOTTOM_THRESHOLD_PX = 80;

/** Follow a new message only when the reader is at the bottom or it is their own message. */
export function shouldFollow(atBottom: boolean, ownMessage: boolean): boolean {
  return atBottom || ownMessage;
}

/** How many messages arrived at the tail (after `prevTailId`) that the reader has not seen. */
export function countNewAtTail(
  prevTailId: number,
  messages: { id: number; author: { id: number | null } }[],
  myId: number,
): number {
  let n = 0;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!;
    if (m.id < 0) continue; // own optimistic message
    if (m.id <= prevTailId) break;
    if (m.author.id !== myId) n++;
  }
  return n;
}

/** Did a newer message arrive at the end? Prepending history, jumping to an older window or an
 * optimistic message being confirmed (same client_msg_id) does not count. */
export function newAtTail(
  prev: { id: number; client_msg_id: string | null } | undefined,
  last: { id: number; client_msg_id: string | null } | undefined,
): boolean {
  if (!prev || !last) return false;
  if (last.client_msg_id !== null && last.client_msg_id === prev.client_msg_id) return false;
  if (last.id < 0) return true; // a new optimistic (own) message
  if (prev.id < 0) return false; // our pending one is still last; something older was confirmed
  return last.id > prev.id;
}
