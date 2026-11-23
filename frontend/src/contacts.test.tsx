import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Contacts, Dm } from './api/contacts';
import type { Message } from './api/messages';
import { useMessages } from './chat/messagesStore';
import { usePresenceStore } from './stores/presence';
import { apiError, json, ME, mockApi, renderApp } from './test/utils';

beforeEach(() => {
  localStorage.clear();
  usePresenceStore.getState().reset();
  useMessages.getState().reset();
});
afterEach(() => vi.unstubAllGlobals());

const BOB = { id: 2, username: 'bob', display_name: null, presence: 'online' as const };
const CAROL = { id: 3, username: 'carol', display_name: null, presence: 'offline' as const };
const DAVE = { id: 4, username: 'dave', display_name: null, presence: 'offline' as const };

const CONTACTS: Contacts = {
  friends: [{ ...BOB, dm_id: 10, unread: 0 }],
  incoming: [{ id: 7, from: CAROL, message: 'Hi from carol', created_at: '2026-11-23T10:00:00Z' }],
  outgoing: [],
  past_dms: [{ dm_id: 11, peer: DAVE, frozen: true, unread: 0 }],
};

const message = (id: number, extra: Partial<Message> = {}): Message => ({
  id, chat: { type: 'dm', id: 11 }, author: DAVE, content: `old ${id}`, created_at: '2026-11-20T10:00:00Z',
  edited_at: null, deleted: false, reply_to: null, attachments: [], client_msg_id: null, ...extra,
});

describe('contacts in the nav sidebar', () => {
  it('lists friends with presence and keeps past conversations collapsed', async () => {
    mockApi({ 'GET /api/auth/me': () => json({ user: ME }), 'GET /api/contacts': () => json(CONTACTS) });
    renderApp('/');
    const friends = await screen.findByRole('list', { name: 'Friends' });
    const bob = await within(friends).findByRole('link', { name: /bob/ });
    expect(bob).toHaveAttribute('href', '/dm/10');
    expect(within(bob).getByLabelText('online')).toBeInTheDocument();
    act(() => usePresenceStore.getState().update(2, 'AFK'));
    expect(within(bob).getByLabelText('AFK')).toBeInTheDocument();

    expect(screen.queryByRole('list', { name: 'Past conversations' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Past conversations/ }));
    expect(within(screen.getByRole('list', { name: 'Past conversations' })).getByRole('link', { name: /dave/ }))
      .toHaveAttribute('href', '/dm/11');
  });

  it('shows the number of incoming friend requests in the top menu', async () => {
    mockApi({ 'GET /api/auth/me': () => json({ user: ME }), 'GET /api/contacts': () => json(CONTACTS) });
    renderApp('/');
    expect(await screen.findByLabelText('1 friend requests')).toBeInTheDocument();
  });
});

describe('direct messages', () => {
  it('a frozen DM is read-only with the reason and only "Copy text" on messages', async () => {
    const dm: Dm = { dm_id: 11, peer: DAVE, frozen: true, frozen_reason: 'user_banned' };
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/contacts': () => json(CONTACTS),
      'GET /api/dms/11': () => json(dm),
      'GET /api/users/dave': () => json({ ...DAVE, friendship: 'none', banned_by_me: true, has_banned_me: false }),
      'GET /api/dms/11/messages?limit=50': () =>
        json({ items: [message(1, { author: { ...ME, presence: 'online' } as Message['author'] })], has_more: false, next_cursor: null }),
    });
    renderApp('/dm/11');
    expect(await screen.findByText(/This conversation is read-only/)).toHaveTextContent('One of you has blocked the other');
    expect(screen.queryByLabelText('Message')).not.toBeInTheDocument();
    const panel = screen.getByRole('complementary', { name: 'Conversation details' });
    expect(within(panel).getByText(/Read-only/)).toBeInTheDocument();
    expect(await within(panel).findByRole('button', { name: 'Unban user' })).toBeInTheDocument();
  });

  it('the peer card bans with the confirmation text from the spec', async () => {
    const dm: Dm = { dm_id: 10, peer: BOB, frozen: false, frozen_reason: null };
    const calls = mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/contacts': () => json(CONTACTS),
      'GET /api/dms/10': () => json(dm),
      'GET /api/users/bob': () => json({ ...BOB, friendship: 'friends', banned_by_me: false, has_banned_me: false }),
      'GET /api/dms/10/messages?limit=50': () => json({ items: [], has_more: false, next_cursor: null }),
      'GET /api/user-bans': () => json({ items: [] }),
      'POST /api/user-bans': () => json(undefined, 201),
    });
    renderApp('/dm/10');
    const panel = await screen.findByRole('complementary', { name: 'Conversation details' });
    expect(await screen.findByLabelText('Message')).toBeInTheDocument(); // writable
    fireEvent.click(await within(panel).findByRole('button', { name: 'Ban user' }));
    const dialog = screen.getByRole('dialog', { name: 'Ban bob?' });
    expect(dialog).toHaveTextContent("They won't be able to contact you. Your friendship will end and your conversation will become read-only.");
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ban' }));
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/user-bans')).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.key === 'POST /api/user-bans')?.init?.body));
    expect(body).toEqual({ user_id: 2 });
  });
});

describe('contacts page', () => {
  it('accepts an incoming request and opens the DM', async () => {
    let contacts = CONTACTS;
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/contacts': () => json(contacts),
      'POST /api/friend-requests/7/accept': () => {
        contacts = { ...CONTACTS, incoming: [], friends: [...CONTACTS.friends, { ...CAROL, dm_id: 12, unread: 0 }] };
        return json({ friend: CAROL, dm_id: 12 });
      },
      'GET /api/dms/12': () => json({ dm_id: 12, peer: CAROL, frozen: false, frozen_reason: null }),
      'GET /api/users/carol': () => json({ ...CAROL, friendship: 'friends', banned_by_me: false, has_banned_me: false }),
      'GET /api/dms/12/messages?limit=50': () => json({ items: [], has_more: false, next_cursor: null }),
    });
    renderApp('/contacts?tab=requests');
    expect(await screen.findByText('“Hi from carol”')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    expect(await screen.findByText('You and carol are now friends')).toBeInTheDocument();
    expect(await screen.findByRole('complementary', { name: 'Conversation details' })).toHaveTextContent('carol');
  });

  it('sends a friend request with a message and shows errors', async () => {
    let answer = json({ request: { id: 9, to: DAVE, message: 'hey', created_at: '2026-11-23T10:00:00Z' } }, 201);
    const calls = mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'POST /api/friend-requests': () => answer,
    });
    renderApp('/contacts?tab=add');
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'dave' } });
    fireEvent.change(screen.getByLabelText('Message (optional)'), { target: { value: 'hey' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send friend request' }));
    expect(await screen.findByText('Friend request sent to dave')).toBeInTheDocument();
    expect(JSON.parse(String(calls.find((c) => c.key === 'POST /api/friend-requests')?.init?.body))).toEqual({
      username: 'dave',
      message: 'hey',
    });

    answer = apiError(403, 'user_banned', 'One of you has blocked the other');
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'dave' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send friend request' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('One of you has blocked the other');
  });
});

describe('room members', () => {
  it('offers a friend request from the member list', async () => {
    const room = {
      id: 1, name: 'general', description: '', visibility: 'public', member_count: 2, my_role: 'owner', unread: 0,
      created_at: '2026-11-23T10:00:00Z', owner: { ...ME, presence: 'online' }, admins: [],
    };
    const calls = mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/1': () => json(room),
      'GET /api/rooms/1/messages?limit=50': () => json({ items: [], has_more: false, next_cursor: null }),
      'GET /api/rooms/1/members?limit=1000': () =>
        json({
          items: [
            { ...ME, presence: 'online', role: 'owner', joined_at: '2026-11-23T10:00:00Z' },
            { ...CAROL, role: 'member', joined_at: '2026-11-23T10:00:00Z' },
          ],
          has_more: false,
          next_cursor: null,
        }),
      'GET /api/users/carol': () => json({ ...CAROL, friendship: 'none', banned_by_me: false, has_banned_me: false }),
      'POST /api/friend-requests': () => json({ request: { id: 3, to: CAROL, message: null, created_at: '2026-11-23T10:00:00Z' } }, 201),
    });
    renderApp('/rooms/1');
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    expect(within(panel).queryByRole('button', { name: 'alice' })).not.toBeInTheDocument(); // not for myself
    fireEvent.click(await within(panel).findByRole('button', { name: 'carol' }));
    const card = within(panel).getByRole('dialog', { name: 'Actions for carol' });
    fireEvent.click(await within(card).findByRole('button', { name: 'Send friend request' }));
    expect(await within(card).findByText('Friend request sent to carol')).toBeInTheDocument();
    expect(JSON.parse(String(calls.find((c) => c.key === 'POST /api/friend-requests')?.init?.body))).toEqual({
      username: 'carol',
      message: null,
    });
  });
});
