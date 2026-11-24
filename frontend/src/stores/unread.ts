import { create } from 'zustand';
import { apiRequest } from '../api/client';
import type { ChatRef } from '../api/messages';

/** Unread counters per chat key (`room:12`, `dm:5`), FRONTEND_SPEC §10. The server is the source of
 * truth (`GET /api/me/unread`, `unread_update`); between those, new messages from others bump locally. */
export interface UnreadItem {
  chat: ChatRef;
  unread: number;
  last_read_message_id: number;
  last_message_id: number | null;
}

export const UNREAD_CAP = 100;

export const fetchUnread = async (): Promise<UnreadItem[]> =>
  (await apiRequest<{ items: UnreadItem[] }>('/me/unread')).items;

interface UnreadState {
  byChat: Record<string, number>;
  lastRead: Record<string, number>;
  /** The chat this tab is showing at its bottom while visible: new messages there are read at once. */
  reading: string | null;
  load: (items: UnreadItem[]) => void;
  set: (key: string, unread: number, lastRead: number) => void;
  bump: (key: string) => void;
  /** Read locally up to `messageId` (the server confirms with `unread_update`). */
  readUpTo: (key: string, messageId: number) => void;
  setReading: (key: string | null) => void;
  reset: () => void;
}

export const useUnread = create<UnreadState>((set) => ({
  byChat: {},
  lastRead: {},
  reading: null,
  load: (items) =>
    set({
      byChat: Object.fromEntries(items.map((i) => [`${i.chat.type}:${i.chat.id}`, i.unread])),
      lastRead: Object.fromEntries(items.map((i) => [`${i.chat.type}:${i.chat.id}`, i.last_read_message_id])),
    }),
  set: (key, unread, lastRead) =>
    set((s) => ({
      byChat: { ...s.byChat, [key]: unread },
      lastRead: { ...s.lastRead, [key]: Math.max(s.lastRead[key] ?? 0, lastRead) },
    })),
  bump: (key) =>
    set((s) => (s.reading === key ? s : { byChat: { ...s.byChat, [key]: Math.min((s.byChat[key] ?? 0) + 1, UNREAD_CAP) } })),
  readUpTo: (key, messageId) =>
    set((s) =>
      (s.byChat[key] ?? 0) === 0 && (s.lastRead[key] ?? 0) >= messageId
        ? s
        : { byChat: { ...s.byChat, [key]: 0 }, lastRead: { ...s.lastRead, [key]: Math.max(s.lastRead[key] ?? 0, messageId) } },
    ),
  setReading: (key) => set({ reading: key }),
  reset: () => set({ byChat: {}, lastRead: {}, reading: null }),
}));

/** Sum over every chat, for `document.title` and the favicon. */
export const totalUnread = (byChat: Record<string, number>): number =>
  Object.values(byChat).reduce((sum, n) => sum + n, 0);
