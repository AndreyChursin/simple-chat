import { act, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RoomMember } from './api/rooms';
import { sortByPresence } from './layout/ContextPanel';
import { usePresenceStore } from './stores/presence';
import { json, ME, mockApi, renderApp } from './test/utils';

beforeEach(() => usePresenceStore.getState().reset());
afterEach(() => vi.unstubAllGlobals());

const member = (id: number, username: string, presence: RoomMember['presence'], role: RoomMember['role'] = 'member'): RoomMember => ({
  id, username, display_name: null, presence, role, joined_at: '2026-11-23T10:00:00Z',
});

describe('presence store', () => {
  it('snapshot marks previously known users offline unless listed', () => {
    const s = usePresenceStore.getState();
    s.update(5, 'online');
    s.update(6, 'AFK');
    s.snapshot([{ user_id: 6, presence: 'online' }, { user_id: 7, presence: 'AFK' }]);
    expect(usePresenceStore.getState().byUser).toEqual({ 5: 'offline', 6: 'online', 7: 'AFK' });
  });

  it('orders members online → AFK → offline, then by name, using live state over REST state', () => {
    const list = [member(2, 'bob', 'online'), member(3, 'Carol', 'offline'), member(4, 'amy', 'offline'), member(5, 'dan', 'AFK')];
    expect(sortByPresence(list, {}).map((m) => m.username)).toEqual(['bob', 'dan', 'amy', 'Carol']);
    expect(sortByPresence(list, { 3: 'online', 2: 'offline' }).map((m) => m.username)).toEqual(['Carol', 'dan', 'amy', 'bob']);
  });
});

describe('context panel', () => {
  it('updates dots and order when presence changes', async () => {
    const room = {
      id: 1, name: 'general', description: '', visibility: 'public', member_count: 3, my_role: 'owner', unread: 0,
      created_at: '2026-11-23T10:00:00Z', owner: { id: 1, username: 'alice', display_name: null, presence: 'online' },
      admins: [],
    };
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/mine': () => json({ public: [room], private: [] }),
      'GET /api/rooms/1': () => json(room),
      'GET /api/rooms/1/members?limit=1000': () =>
        json({ items: [member(1, 'alice', 'online', 'owner'), member(2, 'bob', 'offline'), member(3, 'carol', 'offline')], has_more: false, next_cursor: null }),
      'GET /api/rooms/1/messages?limit=50': () => json({ items: [], has_more: false, next_cursor: null }),
    });
    renderApp('/rooms/1');
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    await within(panel).findByText('carol');
    const members = () => within(panel).getByRole('heading', { name: /Members/ }).parentElement!;
    expect(within(members()).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['○bob', '○carol']);

    act(() => usePresenceStore.getState().update(3, 'AFK'));
    const items = within(members()).getAllByRole('listitem');
    expect(items.map((li) => li.textContent)).toEqual(['◐carol', '○bob']);
    expect(within(items[0]!).getByLabelText('AFK')).toBeInTheDocument();
  });
});
