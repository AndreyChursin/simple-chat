/** Per-chat message cache (FRONTEND_SPEC §10): sorted by id, deduplicated by id / client_msg_id, with
 * optimistic "sending" entries. REST is the source of truth; WS events patch the cache. */
import { create } from 'zustand';
import type { Message } from '../api/messages';

export type PendingState = 'sending' | 'failed';

export interface ChatMessage extends Message {
  /** Set only for optimistic messages that have no server id yet (their `id` is negative). */
  pending?: PendingState;
  error?: string;
  reply_to_message_id?: number | null;
}

export interface ChatState {
  messages: ChatMessage[];
  loaded: boolean;
  hasMoreBefore: boolean;
  /** True after jumping to an old message: the newest part of the history is not in memory. */
  hasMoreAfter: boolean;
  /** react-virtuoso `firstItemIndex`: decreases by the number of prepended items. */
  firstItemIndex: number;
  loadingOlder: boolean;
}

export const FIRST_ITEM_INDEX = 1_000_000;
export const MAX_IN_MEMORY = 2000;
const REPLY_EXCERPT_CHARS = 200;

export const emptyChat = (): ChatState => ({
  messages: [],
  loaded: false,
  hasMoreBefore: false,
  hasMoreAfter: false,
  firstItemIndex: FIRST_ITEM_INDEX,
  loadingOlder: false,
});

/** Real messages ascending by id, optimistic ones (negative ids) after them in creation order. */
export function sortMessages(list: ChatMessage[]): ChatMessage[] {
  return [...list].sort((a, b) => {
    const ap = a.id < 0;
    const bp = b.id < 0;
    if (ap !== bp) return ap ? 1 : -1;
    return ap ? b.id - a.id : a.id - b.id;
  });
}

/** Insert or replace by id; a server message replaces the optimistic entry with its client_msg_id. */
export function mergeMessage(list: ChatMessage[], msg: ChatMessage): ChatMessage[] {
  const rest = list.filter(
    (m) => m.id !== msg.id && !(m.id < 0 && msg.client_msg_id !== null && m.client_msg_id === msg.client_msg_id),
  );
  return sortMessages([...rest, msg]);
}

interface MessagesStore {
  chats: Record<string, ChatState>;
  get: (key: string) => ChatState;
  setLatest: (key: string, items: Message[], hasMoreBefore: boolean) => void;
  setWindow: (key: string, items: Message[], hasMoreBefore: boolean, hasMoreAfter: boolean) => void;
  prependOlder: (key: string, items: Message[], hasMoreBefore: boolean) => void;
  appendNewer: (key: string, items: Message[], hasMoreAfter: boolean) => void;
  setLoadingOlder: (key: string, loading: boolean) => void;
  /** new_message / ack / message_edited / REST result. Returns false if it was not stored (gap). */
  upsert: (key: string, msg: Message) => boolean;
  addPending: (key: string, msg: ChatMessage) => void;
  failPending: (key: string, clientMsgId: string, error: string) => void;
  retryPending: (key: string, clientMsgId: string) => void;
  removePending: (key: string, clientMsgId: string) => void;
  markDeleted: (key: string, messageId: number) => void;
  /** The author deleted their account: their messages stay, signed "Deleted user". */
  anonymize: (userId: number) => void;
  drop: (key: string) => void;
  reset: () => void;
}

export const useMessages = create<MessagesStore>((set, get) => {
  const update = (key: string, fn: (c: ChatState) => ChatState) =>
    set((s) => ({ chats: { ...s.chats, [key]: fn(s.chats[key] ?? emptyChat()) } }));
  const pendingOf = (c: ChatState) => c.messages.filter((m) => m.id < 0);

  return {
    chats: {},
    get: (key) => get().chats[key] ?? emptyChat(),

    setLatest: (key, items, hasMoreBefore) =>
      update(key, (c) => ({
        ...emptyChat(),
        loaded: true,
        hasMoreBefore,
        messages: sortMessages([...items, ...pendingOf(c)]),
      })),

    setWindow: (key, items, hasMoreBefore, hasMoreAfter) =>
      update(key, () => ({ ...emptyChat(), loaded: true, hasMoreBefore, hasMoreAfter, messages: sortMessages(items) })),

    prependOlder: (key, items, hasMoreBefore) =>
      update(key, (c) => {
        const known = new Set(c.messages.map((m) => m.id));
        const fresh = items.filter((m) => !known.has(m.id));
        return {
          ...c,
          hasMoreBefore,
          loadingOlder: false,
          firstItemIndex: c.firstItemIndex - fresh.length,
          messages: sortMessages([...fresh, ...c.messages]),
        };
      }),

    appendNewer: (key, items, hasMoreAfter) =>
      update(key, (c) => {
        let messages = c.messages;
        for (const m of items) messages = mergeMessage(messages, m);
        return { ...c, hasMoreAfter, messages };
      }),

    setLoadingOlder: (key, loading) => update(key, (c) => ({ ...c, loadingOlder: loading })),

    upsert: (key, msg) => {
      const c = get().chats[key];
      if (!c || !c.loaded) return false;
      const exists = c.messages.some((m) => m.id === msg.id || (m.id < 0 && m.client_msg_id === msg.client_msg_id));
      const newest = c.messages.filter((m) => m.id > 0).at(-1);
      // Newer than what we hold while the tail is not loaded: it belongs to the gap.
      if (!exists && c.hasMoreAfter && newest && msg.id > newest.id) return false;
      update(key, (cur) => {
        let messages = mergeMessage(cur.messages, msg).map((m) =>
          m.reply_to?.id === msg.id && !msg.deleted
            ? { ...m, reply_to: { ...m.reply_to, excerpt: msg.content.slice(0, REPLY_EXCERPT_CHARS) } }
            : m,
        );
        let { hasMoreBefore, firstItemIndex } = cur;
        if (messages.length > MAX_IN_MEMORY) {
          const drop = messages.length - MAX_IN_MEMORY;
          messages = messages.slice(drop);
          hasMoreBefore = true;
          firstItemIndex += drop;
        }
        return { ...cur, messages, hasMoreBefore, firstItemIndex };
      });
      return true;
    },

    addPending: (key, msg) => update(key, (c) => ({ ...c, messages: sortMessages([...c.messages, msg]) })),

    failPending: (key, clientMsgId, error) =>
      update(key, (c) => ({
        ...c,
        messages: c.messages.map((m) =>
          m.id < 0 && m.client_msg_id === clientMsgId ? { ...m, pending: 'failed' as const, error } : m,
        ),
      })),

    retryPending: (key, clientMsgId) =>
      update(key, (c) => ({
        ...c,
        messages: c.messages.map((m) =>
          m.id < 0 && m.client_msg_id === clientMsgId ? { ...m, pending: 'sending' as const, error: undefined } : m,
        ),
      })),

    removePending: (key, clientMsgId) =>
      update(key, (c) => ({ ...c, messages: c.messages.filter((m) => !(m.id < 0 && m.client_msg_id === clientMsgId)) })),

    markDeleted: (key, messageId) =>
      update(key, (c) => ({
        ...c,
        messages: c.messages.map((m) => {
          if (m.id === messageId) return { ...m, deleted: true, content: '', attachments: [] };
          if (m.reply_to?.id === messageId) return { ...m, reply_to: { ...m.reply_to, deleted: true, excerpt: '' } };
          return m;
        }),
      })),

    anonymize: (userId) =>
      set((s) => {
        const gone = { id: null, username: 'Deleted user', display_name: null, presence: 'offline' as const };
        const chats: Record<string, ChatState> = {};
        for (const [key, c] of Object.entries(s.chats)) {
          chats[key] = {
            ...c,
            messages: c.messages.map((m) => {
              const author = m.author.id === userId ? gone : m.author;
              const reply = m.reply_to && m.reply_to.author.id === userId ? { ...m.reply_to, author: gone } : m.reply_to;
              return author === m.author && reply === m.reply_to ? m : { ...m, author, reply_to: reply };
            }),
          };
        }
        return { chats };
      }),

    drop: (key) =>
      set((s) => {
        const chats = { ...s.chats };
        delete chats[key];
        return { chats };
      }),

    reset: () => set({ chats: {} }),
  };
});
