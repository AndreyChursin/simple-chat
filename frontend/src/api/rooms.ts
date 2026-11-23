/** Rooms and invitations (docs/API_AND_WS_SPEC.md §5–6). */
import type { UserPublic } from './auth';
import { apiRequest } from './client';

export type Visibility = 'public' | 'private';
export type RoomRole = 'owner' | 'admin' | 'member';

export interface RoomSummary {
  id: number;
  name: string;
  description: string;
  visibility: Visibility;
  member_count: number;
  owner: UserPublic;
  my_role: RoomRole | null;
  unread: number;
  created_at: string;
}

export interface CatalogRoom extends RoomSummary {
  is_banned: boolean;
}

export interface RoomDetail extends RoomSummary {
  admins: UserPublic[];
}

export interface Page<T> {
  items: T[];
  has_more: boolean;
  next_cursor: number | null;
}

export interface RoomMember extends UserPublic {
  role: RoomRole;
  joined_at: string;
}

export interface RoomBrief {
  id: number;
  name: string;
  description: string;
  visibility: Visibility;
  member_count: number;
}

export interface MyInvitation {
  id: number;
  room: RoomBrief;
  inviter: UserPublic;
  created_at: string;
}

export interface RoomInvitation {
  id: number;
  invitee: UserPublic;
  inviter: UserPublic;
  created_at: string;
}

export interface MyRooms {
  public: RoomSummary[];
  private: RoomSummary[];
}

/** TanStack Query keys, shared so mutations can invalidate precisely. */
export const roomKeys = {
  mine: ['rooms', 'mine'] as const,
  catalog: (search: string) => ['rooms', 'catalog', search] as const,
  catalogAll: ['rooms', 'catalog'] as const,
  detail: (id: number) => ['room', id] as const,
  members: (id: number) => ['room', id, 'members'] as const,
  invitations: (id: number) => ['room', id, 'invitations'] as const,
  myInvitations: ['invitations'] as const,
};

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export function fetchCatalog(search: string, cursor: number | null): Promise<Page<CatalogRoom>> {
  const params = new URLSearchParams({ limit: '30' });
  if (search) params.set('search', search);
  if (cursor !== null) params.set('cursor', String(cursor));
  return apiRequest<Page<CatalogRoom>>(`/rooms/catalog?${params}`);
}

export const fetchMyRooms = (): Promise<MyRooms> => apiRequest<MyRooms>('/rooms/mine');

export const fetchRoom = (id: number): Promise<RoomDetail> => apiRequest<RoomDetail>(`/rooms/${id}`);

export const createRoom = (body: { name: string; description: string; visibility: Visibility }) =>
  apiRequest<RoomSummary>('/rooms', send('POST', body));

export const updateRoom = (id: number, body: Partial<{ name: string; description: string; visibility: Visibility }>) =>
  apiRequest<RoomSummary>(`/rooms/${id}`, send('PATCH', body));

export const deleteRoom = (id: number): Promise<void> => apiRequest<void>(`/rooms/${id}`, send('DELETE'));

export const joinRoom = (id: number): Promise<RoomSummary> => apiRequest<RoomSummary>(`/rooms/${id}/join`, send('POST'));

export const leaveRoom = (id: number): Promise<void> => apiRequest<void>(`/rooms/${id}/leave`, send('POST'));

export async function fetchAllMembers(id: number): Promise<RoomMember[]> {
  const items: RoomMember[] = [];
  let cursor: number | null = null;
  for (;;) {
    const params = new URLSearchParams({ limit: '1000' });
    if (cursor !== null) params.set('cursor', String(cursor));
    const page: Page<RoomMember> = await apiRequest<Page<RoomMember>>(`/rooms/${id}/members?${params}`);
    items.push(...page.items);
    if (!page.has_more || page.next_cursor === null) return items;
    cursor = page.next_cursor;
  }
}

export const inviteUser = (roomId: number, username: string) =>
  apiRequest<{ invitation: RoomInvitation }>(`/rooms/${roomId}/invitations`, send('POST', { username }));

export const fetchRoomInvitations = async (roomId: number): Promise<RoomInvitation[]> =>
  (await apiRequest<{ items: RoomInvitation[] }>(`/rooms/${roomId}/invitations`)).items;

export const revokeInvitation = (roomId: number, invitationId: number): Promise<void> =>
  apiRequest<void>(`/rooms/${roomId}/invitations/${invitationId}`, send('DELETE'));

export const fetchMyInvitations = async (): Promise<MyInvitation[]> =>
  (await apiRequest<{ items: MyInvitation[] }>('/invitations')).items;

export const acceptInvitation = (id: number): Promise<RoomSummary> =>
  apiRequest<RoomSummary>(`/invitations/${id}/accept`, send('POST'));

export const declineInvitation = (id: number): Promise<void> =>
  apiRequest<void>(`/invitations/${id}/decline`, send('POST'));

export const searchUsers = async (q: string): Promise<UserPublic[]> =>
  (await apiRequest<{ items: UserPublic[] }>(`/users?${new URLSearchParams({ search: q, limit: '8' })}`)).items;
