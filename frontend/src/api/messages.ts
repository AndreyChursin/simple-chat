/** Messages (docs/API_AND_WS_SPEC.md §0.3, §9). */
import type { UserPublic } from './auth';
import { apiRequest } from './client';
import type { Page } from './rooms';

export type ChatType = 'room' | 'dm';

export interface ChatRef {
  type: ChatType;
  id: number;
}

export interface ReplyRef {
  id: number;
  author: UserPublic;
  excerpt: string;
  deleted: boolean;
}

export interface Message {
  id: number;
  chat: ChatRef;
  author: UserPublic;
  content: string;
  created_at: string;
  edited_at: string | null;
  deleted: boolean;
  reply_to: ReplyRef | null;
  attachments: unknown[];
  client_msg_id: string | null;
}

export interface MessagesAround {
  items: Message[];
  has_more_before: boolean;
  has_more_after: boolean;
}

export const MESSAGE_MAX_BYTES = 3072;
export const PAGE_SIZE = 50;

export const chatKey = (chat: ChatRef): string => `${chat.type}:${chat.id}`;
const chatPath = (chat: ChatRef): string => `/${chat.type === 'room' ? 'rooms' : 'dms'}/${chat.id}`;

export function fetchMessages(chat: ChatRef, cursor: { before_id?: number; after_id?: number } = {}, limit = PAGE_SIZE) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor.before_id !== undefined) params.set('before_id', String(cursor.before_id));
  if (cursor.after_id !== undefined) params.set('after_id', String(cursor.after_id));
  return apiRequest<Page<Message>>(`${chatPath(chat)}/messages?${params}`);
}

export const fetchAround = (chat: ChatRef, messageId: number, limit = PAGE_SIZE) =>
  apiRequest<MessagesAround>(`${chatPath(chat)}/messages/around/${messageId}?limit=${limit}`);

export function searchMessages(chat: ChatRef, q: string, beforeId?: number) {
  const params = new URLSearchParams({ q, limit: '30' });
  if (beforeId !== undefined) params.set('before_id', String(beforeId));
  return apiRequest<Page<Message>>(`${chatPath(chat)}/messages/search?${params}`);
}

export const editMessage = (id: number, content: string) =>
  apiRequest<Message>(`/messages/${id}`, { method: 'PATCH', body: JSON.stringify({ content }) });

export const deleteMessage = (id: number): Promise<void> => apiRequest<void>(`/messages/${id}`, { method: 'DELETE' });

/** UTF-8 byte length, the unit of the server-side limit. */
export const byteLength = (text: string): number => new TextEncoder().encode(text.replace(/\r\n/g, '\n')).length;
