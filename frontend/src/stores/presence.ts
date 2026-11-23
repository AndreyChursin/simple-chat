import { create } from 'zustand';
import type { Presence, UserPublic } from '../api/auth';

/** Live presence from the WebSocket (`presence_snapshot` / `presence_update`). It overrides the
 * `presence` field that REST responses carry, which is only true at fetch time. */
interface PresenceStore {
  byUser: Record<number, Presence>;
  snapshot: (users: { user_id: number; presence: Presence }[]) => void;
  update: (userId: number, presence: Presence) => void;
  reset: () => void;
}

export const usePresenceStore = create<PresenceStore>((set) => ({
  byUser: {},
  // Everyone visible but not listed is offline; known entries that are absent are stale.
  snapshot: (users) =>
    set((s) => ({
      byUser: {
        ...Object.fromEntries(Object.keys(s.byUser).map((id) => [id, 'offline' as const])),
        ...Object.fromEntries(users.map((u) => [u.user_id, u.presence])),
      },
    })),
  update: (userId, presence) => set((s) => ({ byUser: { ...s.byUser, [userId]: presence } })),
  reset: () => set({ byUser: {} }),
}));

export function usePresence(user: Pick<UserPublic, 'id' | 'presence'>): Presence {
  const live = usePresenceStore((s) => (user.id === null ? undefined : s.byUser[user.id]));
  return live ?? user.presence;
}

export const PRESENCE_RANK: Record<Presence, number> = { online: 0, AFK: 1, offline: 2 };
