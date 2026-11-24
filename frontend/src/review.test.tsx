import { fireEvent, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from './api/messages';
import type { RoomMember, RoomSummary } from './api/rooms';
import { MAX_IN_MEMORY, useMessages } from './chat/messagesStore';
import { useUnread } from './stores/unread';
import { json, ME, mockApi, renderApp } from './test/utils';
import { bumpRoom } from './ws/SocketBridge';

const ALICE = { id: 1, username: 'alice', display_name: null, presence: 'offline' as const };

function room(id: number, name: string, extra: Partial<RoomSummary> = {}): RoomSummary {
  return {
    id, name, description: '', visibility: 'public', member_count: 1, owner: ALICE, my_role: 'owner', unread: 0,
    created_at: '2026-11-23T10:00:00Z', ...extra,
  };
}

function msg(id: number): Message {
  return {
    id, chat: { type: 'room', id: 1 }, author: ALICE, content: `m${id}`, created_at: '2026-11-23T10:00:00Z',
    edited_at: null, deleted: false, reply_to: null, attachments: [], client_msg_id: null,
  };
}

beforeEach(() => {
  localStorage.clear();
  useUnread.getState().reset();
  useMessages.getState().reset();
});
afterEach(() => vi.unstubAllGlobals());

describe('room order', () => {
  it('moves the room with a new message to the top of its section', () => {
    const mine = { public: [room(1, 'a'), room(2, 'b'), room(3, 'c')], private: [room(9, 'v', { visibility: 'private' })] };
    expect(bumpRoom(mine, 3).public.map((r) => r.id)).toEqual([3, 1, 2]);
    expect(bumpRoom(mine, 1)).toBe(mine); // already first: same object, no re-render
    expect(bumpRoom(mine, 42)).toBe(mine);
  });
});

describe('message memory cap', () => {
  it('drops the newest messages when scrolling far up, and marks them to be fetched again', () => {
    const s = useMessages.getState();
    s.setLatest('room:1', Array.from({ length: MAX_IN_MEMORY }, (_, i) => msg(1000 + i)), true);
    s.prependOlder('room:1', Array.from({ length: 50 }, (_, i) => msg(950 + i)), true);
    const c = useMessages.getState().get('room:1');
    expect(c.messages).toHaveLength(MAX_IN_MEMORY);
    expect(c.messages[0]!.id).toBe(950);
    expect(c.hasMoreAfter).toBe(true);
  });
});

describe('collapsed navigation', () => {
  it('keeps section icons with unread badges', async () => {
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/mine': () => json({ public: [room(1, 'general', { unread: 2 })], private: [room(2, 'vault', { visibility: 'private', unread: 5 })] }),
    });
    renderApp('/');
    await screen.findByRole('link', { name: /#general/ });
    fireEvent.click(screen.getByRole('button', { name: 'Collapse navigation' }));
    expect(screen.getByRole('button', { name: 'Public Rooms, 2 unread' })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: 'Private Rooms, 5 unread' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Contacts' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Private Rooms, 5 unread' }));
    expect(await screen.findByRole('link', { name: /#vault/ })).toBeInTheDocument();
  });
});

describe('private rooms page', () => {
  it('shows unread badges on my private rooms', async () => {
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/mine': () => json({ public: [], private: [room(2, 'vault', { visibility: 'private', unread: 120 })] }),
    });
    renderApp('/rooms/private');
    const card = (await screen.findByRole('heading', { name: /vault/ })).closest('li')!;
    expect(within(card).getByText('99+')).toBeInTheDocument();
  });
});

describe('large member lists', () => {
  it('virtualizes a group of 100+ members instead of rendering every row', async () => {
    const members: RoomMember[] = [
      { ...ALICE, role: 'owner', joined_at: '2026-11-23T10:00:00Z' },
      ...Array.from({ length: 150 }, (_, i) => ({
        id: 10 + i, username: `user${i}`, display_name: null, presence: 'offline' as const, role: 'member' as const,
        joined_at: '2026-11-23T10:00:00Z',
      })),
    ];
    const big = room(1, 'big', { member_count: 151 });
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/mine': () => json({ public: [big], private: [] }),
      'GET /api/rooms/1': () => json({ ...big, admins: [ALICE] }),
      'GET /api/rooms/1/members?limit=1000': () => json({ items: members, has_more: false, next_cursor: null }),
      'GET /api/rooms/1/messages?limit=50': () => json({ items: [], has_more: false, next_cursor: null }),
    });
    renderApp('/rooms/1');
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    const heading = await within(panel).findByRole('heading', { name: /Members/ });
    expect(heading).toHaveTextContent('(150)');
    // jsdom has no layout, so the virtual list renders (almost) nothing — far fewer than 150 rows
    expect(within(heading.parentElement!).queryAllByRole('listitem').length).toBeLessThan(150);
  });
});
