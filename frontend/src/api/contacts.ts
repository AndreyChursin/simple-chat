/** Contacts, friend requests, personal bans and DMs (docs/API_AND_WS_SPEC.md §3, §4, §8). */
import type { UserPublic } from './auth';
import { apiRequest } from './client';

export type FriendshipState = 'none' | 'pending_out' | 'pending_in' | 'friends';
export type FrozenReason = 'not_friends' | 'user_banned' | 'user_deleted';

export interface Friend extends UserPublic {
  dm_id: number | null;
  unread: number;
}

export interface IncomingRequest {
  id: number;
  from: UserPublic;
  message: string | null;
  created_at: string;
}

export interface OutgoingRequest {
  id: number;
  to: UserPublic;
  message: string | null;
  created_at: string;
}

export interface PastDm {
  dm_id: number;
  peer: UserPublic;
  frozen: true;
  unread: number;
}

export interface Contacts {
  friends: Friend[];
  incoming: IncomingRequest[];
  outgoing: OutgoingRequest[];
  past_dms: PastDm[];
}

export interface UserProfile extends UserPublic {
  friendship: FriendshipState;
  banned_by_me: boolean;
  has_banned_me: boolean;
}

export interface Dm {
  dm_id: number;
  peer: UserPublic;
  frozen: boolean;
  frozen_reason: FrozenReason | null;
}

export interface Ban {
  user: UserPublic;
  created_at: string;
}

export const contactKeys = {
  all: ['contacts'] as const,
  bans: ['user-bans'] as const,
  dm: (id: number) => ['dm', id] as const,
  profile: (username: string) => ['user', username.toLowerCase()] as const,
};

export const FROZEN_TEXT: Record<FrozenReason, string> = {
  not_friends: 'You are no longer friends',
  user_banned: 'One of you has blocked the other',
  user_deleted: 'User deleted their account',
};

const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const fetchContacts = (): Promise<Contacts> => apiRequest<Contacts>('/contacts');

/** 201 → a new request; 200 → their pending request was accepted instead. */
export const sendFriendRequest = (username: string, message: string) =>
  apiRequest<{ request: OutgoingRequest } | { friendship: 'accepted'; friend: UserPublic; dm_id: number }>(
    '/friend-requests',
    post({ username, message: message.trim() || null }),
  );

export const acceptFriendRequest = (id: number) =>
  apiRequest<{ friend: UserPublic; dm_id: number }>(`/friend-requests/${id}/accept`, post());

export const declineFriendRequest = (id: number): Promise<void> =>
  apiRequest<void>(`/friend-requests/${id}/decline`, post());

export const cancelFriendRequest = (id: number): Promise<void> =>
  apiRequest<void>(`/friend-requests/${id}`, { method: 'DELETE' });

export const removeFriend = (userId: number): Promise<void> => apiRequest<void>(`/friends/${userId}`, { method: 'DELETE' });

export const fetchBans = async (): Promise<Ban[]> => (await apiRequest<{ items: Ban[] }>('/user-bans')).items;

export const banUser = (userId: number): Promise<void> => apiRequest<void>('/user-bans', post({ user_id: userId }));

export const unbanUser = (userId: number): Promise<void> =>
  apiRequest<void>(`/user-bans/${userId}`, { method: 'DELETE' });

export const openDm = (userId: number): Promise<Dm> => apiRequest<Dm>('/dms', post({ user_id: userId }));

export const fetchDm = (id: number): Promise<Dm> => apiRequest<Dm>(`/dms/${id}`);

export const fetchProfile = (username: string): Promise<UserProfile> =>
  apiRequest<UserProfile>(`/users/${encodeURIComponent(username)}`);
