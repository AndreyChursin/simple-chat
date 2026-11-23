import { create } from 'zustand';

export const TYPING_VISIBLE_MS = 5000;

interface Typist {
  username: string;
  until: number;
}

interface TypingStore {
  byChat: Record<string, Record<number, Typist>>;
  typing: (key: string, userId: number, username: string, now?: number) => void;
  stopped: (key: string, userId: number) => void;
  prune: (now?: number) => void;
}

export const useTyping = create<TypingStore>((set, get) => ({
  byChat: {},
  typing: (key, userId, username, now = Date.now()) =>
    set((s) => ({
      byChat: { ...s.byChat, [key]: { ...s.byChat[key], [userId]: { username, until: now + TYPING_VISIBLE_MS } } },
    })),
  stopped: (key, userId) => {
    const chat = get().byChat[key];
    if (!chat?.[userId]) return;
    const next = { ...chat };
    delete next[userId];
    set((s) => ({ byChat: { ...s.byChat, [key]: next } }));
  },
  prune: (now = Date.now()) => {
    let changed = false;
    const byChat: TypingStore['byChat'] = {};
    for (const [key, typists] of Object.entries(get().byChat)) {
      const alive = Object.fromEntries(Object.entries(typists).filter(([, t]) => t.until > now));
      if (Object.keys(alive).length !== Object.keys(typists).length) changed = true;
      byChat[key] = alive;
    }
    if (changed) set({ byChat });
  },
}));

export function typingText(names: string[]): string | null {
  if (names.length === 0) return null;
  if (names.length === 1) return `${names[0]} is typing…`;
  if (names.length === 2) return `${names[0]} and ${names[1]} are typing…`;
  return 'Several people are typing…';
}
