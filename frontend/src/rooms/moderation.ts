import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import {
  banFromRoom,
  fetchAllMembers,
  fetchBans,
  grantAdmin,
  revokeAdmin,
  roomKeys,
  unbanFromRoom,
  type RoomBan,
  type RoomMember,
  type RoomRole,
} from '../api/rooms';

export const BAN_HINT = 'Removing a member bans them until unbanned.';

export function useRoomMembers(roomId: number) {
  return useQuery({ queryKey: roomKeys.members(roomId), queryFn: () => fetchAllMembers(roomId) });
}

export function useRoomBans(roomId: number) {
  return useQuery({ queryKey: roomKeys.bans(roomId), queryFn: () => fetchBans(roomId), staleTime: 0 });
}

/** Moderation actions (FRONTEND_SPEC §4.6): tables change at once, the server answer (and the WS events
 * `member_removed` / `member_role_changed`) settle them; a failure rolls the optimistic change back. */
export function useModeration(roomId: number) {
  const qc = useQueryClient();
  const members = roomKeys.members(roomId);
  const bans = roomKeys.bans(roomId);

  function optimistic<T>(key: QueryKey, change: (prev: T) => T) {
    const prev = qc.getQueryData<T>(key);
    if (prev !== undefined) qc.setQueryData<T>(key, change(prev));
    return () => qc.setQueryData(key, prev);
  }

  const settle = (...keys: QueryKey[]) => () => {
    for (const key of [...keys, roomKeys.detail(roomId)]) void qc.invalidateQueries({ queryKey: key });
  };

  const setRole = (userId: number, role: RoomRole) =>
    optimistic<RoomMember[]>(members, (list) => list.map((m) => (m.id === userId ? { ...m, role } : m)));

  const grant = useMutation({
    mutationFn: (userId: number) => grantAdmin(roomId, userId),
    onMutate: (userId) => ({ rollback: setRole(userId, 'admin') }),
    onError: (_e, _v, ctx) => ctx?.rollback(),
    onSettled: settle(members),
  });

  const revoke = useMutation({
    mutationFn: (userId: number) => revokeAdmin(roomId, userId),
    onMutate: (userId) => ({ rollback: setRole(userId, 'member') }),
    onError: (_e, _v, ctx) => ctx?.rollback(),
    onSettled: settle(members),
  });

  const ban = useMutation({
    mutationFn: ({ userId, reason }: { userId: number; reason?: string }) => banFromRoom(roomId, userId, reason),
    onMutate: ({ userId }) => ({
      rollback: optimistic<RoomMember[]>(members, (list) => list.filter((m) => m.id !== userId)),
    }),
    onError: (_e, _v, ctx) => ctx?.rollback(),
    onSettled: settle(members, bans, roomKeys.invitations(roomId)),
  });

  const unban = useMutation({
    mutationFn: (userId: number) => unbanFromRoom(roomId, userId),
    onMutate: (userId) => ({
      rollback: optimistic<RoomBan[]>(bans, (list) => list.filter((b) => b.user.id !== userId)),
    }),
    onError: (_e, _v, ctx) => ctx?.rollback(),
    onSettled: settle(bans),
  });

  return { grant, revoke, ban, unban };
}
