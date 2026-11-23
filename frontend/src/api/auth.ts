import { ApiError, apiRequest } from './client';

export type Presence = 'online' | 'AFK' | 'offline';

export interface Me {
  id: number;
  email: string;
  username: string;
  display_name: string | null;
  created_at: string;
}

export interface UserPublic {
  id: number | null;
  username: string;
  display_name: string | null;
  presence: Presence;
}

export interface SessionInfo {
  id: number;
  browser: string;
  os: string;
  device: 'Desktop' | 'Mobile' | 'Tablet' | 'Bot' | 'Other';
  user_agent: string;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  persistent: boolean;
  is_current: boolean;
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

/** Current user, or null when not signed in. */
export async function fetchMe(): Promise<Me | null> {
  try {
    return (await apiRequest<{ user: Me }>('/auth/me')).user;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}

export const register = async (email: string, username: string, password: string): Promise<Me> =>
  (await apiRequest<{ user: Me }>('/auth/register', json({ email, username, password }))).user;

export const login = async (email: string, password: string, keepSignedIn: boolean): Promise<Me> =>
  (await apiRequest<{ user: Me }>('/auth/login', json({ email, password, keep_signed_in: keepSignedIn }))).user;

export const logout = (): Promise<void> => apiRequest<void>('/auth/logout', { method: 'POST' });

export const forgotPassword = (email: string): Promise<void> =>
  apiRequest<void>('/auth/password/forgot', json({ email }));

export const resetPassword = (token: string, newPassword: string): Promise<void> =>
  apiRequest<void>('/auth/password/reset', json({ token, new_password: newPassword }));

export const changePassword = (currentPassword: string, newPassword: string): Promise<void> =>
  apiRequest<void>(
    '/auth/password/change',
    json({ current_password: currentPassword, new_password: newPassword }),
  );

export const updateProfile = async (displayName: string | null): Promise<Me> =>
  (
    await apiRequest<{ user: Me }>('/me', {
      method: 'PATCH',
      body: JSON.stringify({ display_name: displayName }),
    })
  ).user;

export const listSessions = async (): Promise<SessionInfo[]> =>
  (await apiRequest<{ items: SessionInfo[] }>('/sessions')).items;

export const revokeSession = (id: number): Promise<void> =>
  apiRequest<void>(`/sessions/${id}`, { method: 'DELETE' });

export const revokeOtherSessions = (): Promise<void> =>
  apiRequest<void>('/sessions?scope=others', { method: 'DELETE' });
