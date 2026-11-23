import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RoomBan, RoomDetail, RoomMember, RoomRole } from './api/rooms';
import { apiError, json, ME, mockApi, renderApp } from './test/utils';
import { socket } from './ws/socket';

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

const user = (id: number, username: string) => ({ id, username, display_name: null, presence: 'offline' as const });
const ALICE = user(1, 'alice');
const BOB = user(2, 'bob');
const CAROL = user(3, 'carol');
const member = (u: ReturnType<typeof user>, role: RoomRole): RoomMember => ({ ...u, role, joined_at: '2026-11-23T10:00:00Z' });

function roomAs(myRole: RoomRole): RoomDetail {
  return {
    id: 1, name: 'general', description: '', visibility: 'public', member_count: 3, owner: ALICE, my_role: myRole,
    unread: 0, created_at: '2026-11-23T10:00:00Z', admins: [ALICE, BOB],
  };
}

const MEMBERS = [member(ALICE, 'owner'), member(BOB, 'admin'), member(CAROL, 'member')];
const BAN: RoomBan = { user: user(4, 'dave'), banned_by: BOB, banned_at: '2026-11-23T11:00:00Z', reason: 'spam' };

/** Signed in as `me`; the room has alice (owner), bob (admin), carol (member). */
function setup(me: { id: number; username: string }, myRole: RoomRole, extra: Record<string, (init?: RequestInit) => Response> = {}) {
  let members = [...MEMBERS];
  const room = roomAs(myRole);
  const calls = mockApi({
    'GET /api/auth/me': () => json({ user: { ...ME, ...me } }),
    'GET /api/rooms/mine': () => json({ public: [room], private: [] }),
    'GET /api/rooms/1': () => json(room),
    'GET /api/rooms/1/members?limit=1000': () => json({ items: members, has_more: false, next_cursor: null }),
    'GET /api/rooms/1/messages?limit=50': () => json({ items: [], has_more: false, next_cursor: null }),
    'GET /api/rooms/1/bans': () => json({ items: [BAN] }),
    'GET /api/rooms/1/invitations': () => json({ items: [] }),
    'GET /api/users/carol': () => json({ ...CAROL, friendship: 'none', banned_by_me: false, has_banned_me: false }),
    'GET /api/users/bob': () => json({ ...BOB, friendship: 'none', banned_by_me: false, has_banned_me: false }),
    'GET /api/users/alice': () => json({ ...ALICE, friendship: 'none', banned_by_me: false, has_banned_me: false }),
    'POST /api/rooms/1/bans': (init) => {
      const { user_id } = JSON.parse(String(init?.body)) as { user_id: number };
      members = members.filter((m) => m.id !== user_id);
      return json({ ...BAN, user: user(user_id, 'x') }, 201);
    },
    ...extra,
  });
  renderApp('/rooms/1');
  return { calls, setMembers: (m: RoomMember[]) => (members = m) };
}

async function openManage() {
  const panel = await screen.findByRole('complementary', { name: 'Room details' });
  fireEvent.click(within(panel).getByRole('button', { name: 'Manage room' }));
  return screen.findByRole('dialog', { name: 'Manage #general' });
}

describe('manage room modal', () => {
  it('has the five tabs and bans a member with a reason from Members', async () => {
    const { calls } = setup(ALICE, 'owner');
    const dialog = await openManage();
    expect(within(dialog).getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Members', 'Admins', 'Banned users', 'Invitations', 'Settings',
    ]);
    const rows = await within(dialog).findAllByRole('row');
    // header + 3 members; the owner row and my own row have no Remove button
    expect(rows).toHaveLength(4);
    expect(within(dialog).getAllByRole('button', { name: 'Remove (ban)' })).toHaveLength(2);

    const carolRow = within(dialog).getByRole('cell', { name: 'carol' }).closest('tr')!;
    fireEvent.click(within(carolRow).getByRole('button', { name: 'Remove (ban)' }));
    const confirm = within(dialog).getByRole('group', { name: 'Ban carol' });
    expect(confirm).toHaveTextContent('Removing a member bans them until unbanned.');
    fireEvent.change(within(confirm).getByLabelText('Reason (optional)'), { target: { value: 'flood' } });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Ban' }));

    await waitFor(() => expect(within(dialog).queryByRole('cell', { name: 'carol' })).not.toBeInTheDocument());
    const post = calls.find((c) => c.key === 'POST /api/rooms/1/bans');
    expect(JSON.parse(String(post?.init?.body))).toEqual({ user_id: 3, reason: 'flood' });
  });

  it('lets the owner add and remove admins; an admin only removes', async () => {
    const { calls } = setup(ALICE, 'owner', {
      'POST /api/rooms/1/admins/3': () => json(member(CAROL, 'admin')),
      'DELETE /api/rooms/1/admins/2': () => json(member(BOB, 'member')),
    });
    const dialog = await openManage();
    fireEvent.click(within(dialog).getByRole('tab', { name: 'Admins' }));
    await within(dialog).findByRole('cell', { name: /bob/ });
    const ownerRow = within(dialog).getByRole('cell', { name: /alice/ }).closest('tr')!;
    expect(ownerRow).toHaveTextContent('Owner');
    expect(within(ownerRow).queryByRole('button')).not.toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText('Add admin'), { target: { value: '3' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Make admin' }));
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/rooms/1/admins/3')).toBe(true));

    const bobRow = within(dialog).getByRole('cell', { name: /bob/ }).closest('tr')!;
    fireEvent.click(within(bobRow).getByRole('button', { name: 'Remove admin' }));
    await waitFor(() => expect(calls.some((c) => c.key === 'DELETE /api/rooms/1/admins/2')).toBe(true));
  });

  it('shows an admin the ban list without the owner-only controls', async () => {
    let bans = [BAN];
    const { calls } = setup(BOB, 'admin', {
      'GET /api/rooms/1/bans': () => json({ items: bans }),
      'DELETE /api/rooms/1/bans/4': () => {
        bans = [];
        return json(undefined, 204);
      },
    });
    const dialog = await openManage();
    fireEvent.click(within(dialog).getByRole('tab', { name: 'Admins' }));
    await within(dialog).findByRole('cell', { name: /bob/ });
    expect(within(dialog).queryByLabelText('Add admin')).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('tab', { name: 'Banned users' }));
    const row = (await within(dialog).findByRole('cell', { name: 'dave' })).closest('tr')!;
    expect(row).toHaveTextContent('bob');
    expect(row).toHaveTextContent('spam');
    expect(row).toHaveTextContent(new Date(BAN.banned_at).toLocaleString());
    fireEvent.click(within(row).getByRole('button', { name: 'Unban' }));
    await waitFor(() => expect(within(dialog).queryByRole('cell', { name: 'dave' })).not.toBeInTheDocument());
    expect(calls.some((c) => c.key === 'DELETE /api/rooms/1/bans/4')).toBe(true);

    fireEvent.click(within(dialog).getByRole('tab', { name: 'Settings' }));
    expect(within(dialog).getByText('Only the owner can change room settings.')).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Delete room' })).not.toBeInTheDocument();
  });

  it('bans by username and reports unknown users and server refusals', async () => {
    const { calls } = setup(ALICE, 'owner', {
      'GET /api/users/ghost': () => apiError(404, 'user_not_found', 'User not found'),
      'GET /api/users/alice': () => json({ ...ALICE, friendship: 'none', banned_by_me: false, has_banned_me: false }),
    });
    const dialog = await openManage();
    fireEvent.click(within(dialog).getByRole('tab', { name: 'Banned users' }));
    const username = within(dialog).getByLabelText('Username');

    fireEvent.change(username, { target: { value: 'ghost' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ban user' }));
    expect(await within(dialog).findByText('User not found')).toBeInTheDocument();

    fireEvent.change(username, { target: { value: 'carol' } });
    fireEvent.change(within(dialog).getByLabelText('Reason (optional)'), { target: { value: 'trolling' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ban user' }));
    expect(await within(dialog).findByText('carol is banned')).toBeInTheDocument();
    const post = calls.find((c) => c.key === 'POST /api/rooms/1/bans');
    expect(JSON.parse(String(post?.init?.body))).toEqual({ user_id: 3, reason: 'trolling' });
  });

  it('keeps the invite form in the Invitations tab', async () => {
    setup(ALICE, 'owner');
    const dialog = await openManage();
    fireEvent.click(within(dialog).getByRole('tab', { name: 'Invitations' }));
    expect(await within(dialog).findByText('No pending invitations.')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Send invitation' })).toBeInTheDocument();
  });
});

describe('member popover', () => {
  async function openCard(name: string) {
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    fireEvent.click(await within(panel).findByRole('button', { name }));
    return screen.findByRole('dialog', { name: `Actions for ${name}` });
  }

  it('offers the owner Make admin and Ban from room for a member', async () => {
    const { calls } = setup(ALICE, 'owner', { 'POST /api/rooms/1/admins/3': () => json(member(CAROL, 'admin')) });
    let card = await openCard('carol');
    expect(within(card).queryByRole('button', { name: 'Remove admin' })).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Make admin' }));
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/rooms/1/admins/3')).toBe(true));

    card = await openCard('bob');
    expect(within(card).queryByRole('button', { name: 'Make admin' })).not.toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Remove admin' })).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Ban from room' }));
    fireEvent.click(within(card).getByRole('button', { name: 'Ban' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'bob' })).not.toBeInTheDocument());
  });

  it('gives an admin no actions on the owner and no Make admin', async () => {
    setup(BOB, 'admin');
    const owner = await openCard('alice');
    expect(within(owner).queryByRole('button', { name: 'Ban from room' })).not.toBeInTheDocument();
    const card = await openCard('carol');
    expect(within(card).getByRole('button', { name: 'Ban from room' })).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Make admin' })).not.toBeInTheDocument();
  });

  it('gives a plain member no moderation actions', async () => {
    setup(CAROL, 'member');
    const card = await openCard('bob');
    await within(card).findByRole('button', { name: 'Send friend request' });
    expect(within(card).queryByRole('button', { name: 'Ban from room' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Manage room' })).not.toBeInTheDocument();
  });
});

describe('ws moderation events', () => {
  const emit = (event: Record<string, unknown>) =>
    act(() => {
      const ws = (socket as unknown as { ws: { onmessage: (e: { data: string }) => void } | null }).ws;
      ws?.onmessage({ data: JSON.stringify(event) });
    });

  it('a ban closes the room with a toast', async () => {
    setup(CAROL, 'member');
    await screen.findByRole('complementary', { name: 'Room details' });
    emit({ type: 'room_access_revoked', room_id: 1, reason: 'banned' });
    expect(await screen.findByText('You were banned from #general')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /Welcome/ })).toBeInTheDocument();
  });

  it('refetches members when someone is removed or changes role', async () => {
    const { calls, setMembers } = setup(ALICE, 'owner');
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    await within(panel).findByRole('button', { name: 'carol' });
    setMembers([member(ALICE, 'owner'), member(BOB, 'member')]);
    emit({ type: 'member_removed', room_id: 1, user_id: 3, reason: 'banned', member_count: 2 });
    await waitFor(() => expect(within(panel).queryByRole('button', { name: 'carol' })).not.toBeInTheDocument());
    expect(within(panel).queryByRole('heading', { name: /Admins/ })).not.toBeInTheDocument();
    const before = calls.filter((c) => c.key === 'GET /api/rooms/mine').length;
    emit({ type: 'member_role_changed', room_id: 1, user_id: 2, role: 'member' });
    await waitFor(() => expect(calls.filter((c) => c.key === 'GET /api/rooms/mine').length).toBeGreaterThan(before));
  });
});
