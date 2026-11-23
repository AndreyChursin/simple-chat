import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RoomDetail, RoomMember, RoomSummary } from './api/rooms';
import { isSectionOpen } from './layout/navAccordion';
import { apiError, json, ME, mockApi, renderApp } from './test/utils';

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

const ALICE = { id: 1, username: 'alice', display_name: null, presence: 'offline' as const };
const BOB = { id: 2, username: 'bob', display_name: 'Bob B.', presence: 'offline' as const };

function room(id: number, name: string, extra: Partial<RoomSummary> = {}): RoomSummary {
  return {
    id, name, description: '', visibility: 'public', member_count: 1, owner: ALICE, my_role: 'owner', unread: 0,
    created_at: '2026-11-23T10:00:00Z', ...extra,
  };
}

const detail = (r: RoomSummary, admins = [ALICE]): RoomDetail => ({ ...r, admins });

const MEMBERS: RoomMember[] = [
  { ...ALICE, role: 'owner', joined_at: '2026-11-23T10:00:00Z' },
  { ...BOB, role: 'member', joined_at: '2026-11-23T10:05:00Z' },
];

const GENERAL = room(1, 'general', { description: 'Everything', member_count: 2 });
const VAULT = room(2, 'vault', { visibility: 'private' });
const MINE = { public: [GENERAL], private: [VAULT] };

function roomRoutes(r: RoomSummary) {
  return {
    [`GET /api/rooms/${r.id}`]: () => json(detail(r)),
    [`GET /api/rooms/${r.id}/members?limit=1000`]: () => json({ items: MEMBERS, has_more: false, next_cursor: null }),
  };
}

const sectionHeader = (name: string) => screen.getByRole('button', { name: new RegExp(name) });

describe('nav accordion', () => {
  it('opens every section when no chat is open and only the current one in compact mode', () => {
    expect(isSectionOpen('contacts', 'expanded', null, { contacts: false })).toBe(true);
    expect(isSectionOpen('private', 'compact', 'private', {})).toBe(true);
    expect(isSectionOpen('public', 'compact', 'private', {})).toBe(false);
    expect(isSectionOpen('public', 'compact', 'private', { public: true })).toBe(true);
  });

  it('is expanded on the empty screen, without a context panel', async () => {
    mockApi({ 'GET /api/auth/me': () => json({ user: ME }), 'GET /api/rooms/mine': () => json(MINE) });
    renderApp('/');
    expect(await screen.findByRole('link', { name: '#general' })).toBeInTheDocument();
    for (const name of ['Public Rooms', 'Private Rooms', 'Contacts']) {
      expect(sectionHeader(name)).toHaveAttribute('aria-expanded', 'true');
    }
    expect(screen.queryByRole('complementary', { name: 'Room details' })).not.toBeInTheDocument();
  });

  it('compacts to the current chat section inside a room and shows members on the right', async () => {
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/mine': () => json(MINE),
      ...roomRoutes(VAULT),
    });
    renderApp('/rooms/2');
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    expect(await within(panel).findByText('bob')).toBeInTheDocument();
    await waitFor(() => expect(sectionHeader('Private Rooms')).toHaveAttribute('aria-expanded', 'true'));
    expect(sectionHeader('Public Rooms')).toHaveAttribute('aria-expanded', 'false');
    expect(sectionHeader('Contacts')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('link', { name: '#general' })).not.toBeInTheDocument();

    fireEvent.click(sectionHeader('Public Rooms'));
    expect(sectionHeader('Public Rooms')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: '#general' })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('chat.nav.compactSections') ?? '{}')).toEqual({ public: true });
  });
});

describe('room context panel', () => {
  it('shows the owner hint instead of Leave and offers Manage room', async () => {
    mockApi({ 'GET /api/auth/me': () => json({ user: ME }), 'GET /api/rooms/mine': () => json(MINE), ...roomRoutes(GENERAL) });
    renderApp('/rooms/1');
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    expect(within(panel).getByText(/Owner can't leave/)).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: 'Leave room' })).not.toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: 'Manage room' })).toBeInTheDocument();
  });

  it('lets a member leave and returns to the empty screen', async () => {
    const asMember = room(1, 'general', { my_role: 'member', member_count: 2 });
    let mine = { public: [asMember], private: [] as RoomSummary[] };
    mockApi({
      'GET /api/auth/me': () => json({ user: { ...ME, id: 2, username: 'bob' } }),
      'GET /api/rooms/mine': () => json(mine),
      ...roomRoutes(asMember),
      'POST /api/rooms/1/leave': () => {
        mine = { public: [], private: [] };
        return json(undefined, 204);
      },
    });
    renderApp('/rooms/1');
    const panel = await screen.findByRole('complementary', { name: 'Room details' });
    expect(within(panel).queryByRole('button', { name: 'Manage room' })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('button', { name: 'Leave room' }));
    expect(await screen.findByText('You left #general')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /Welcome/ })).toBeInTheDocument();
  });

  it('offers to join a public room the user is not in', async () => {
    const outside = room(1, 'general', { my_role: null });
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/1': () => json(detail(outside)),
    });
    renderApp('/rooms/1');
    expect(await screen.findByRole('button', { name: 'Join room' })).toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: 'Room details' })).not.toBeInTheDocument();
  });

  it('shows "not found" for hidden rooms', async () => {
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/9': () => apiError(404, 'room_not_found', 'Room not found'),
    });
    renderApp('/rooms/9');
    expect(await screen.findByRole('heading', { name: 'Room not found' })).toBeInTheDocument();
  });
});

describe('create room', () => {
  it('shows a taken name under the field', async () => {
    const calls = mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'POST /api/rooms': () => apiError(409, 'room_name_taken', 'Room name is already taken'),
    });
    renderApp('/');
    fireEvent.click(await screen.findByRole('button', { name: '+ Create room' }));
    const dialog = screen.getByRole('dialog', { name: 'Create room' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: ' General ' } });
    fireEvent.click(within(dialog).getByLabelText(/Private/));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await within(dialog).findByText('Room name is already taken')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true');
    const body = JSON.parse(String(calls.find((c) => c.key === 'POST /api/rooms')?.init?.body));
    expect(body).toEqual({ name: 'General', description: '', visibility: 'private' });
  });

  it('validates the name length locally', async () => {
    mockApi({ 'GET /api/auth/me': () => json({ user: ME }) });
    renderApp('/');
    fireEvent.click(await screen.findByRole('button', { name: '+ Create room' }));
    const dialog = screen.getByRole('dialog', { name: 'Create room' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'ab' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(within(dialog).getByText('Name must be 3–64 characters')).toBeInTheDocument();
  });
});

describe('catalog', () => {
  const CATALOG = {
    items: [
      { ...room(1, 'general', { description: 'Everything', member_count: 12, my_role: null }), is_banned: false },
      { ...room(3, 'mine', { my_role: 'member' }), is_banned: false },
      { ...room(4, 'closed', { my_role: null }), is_banned: true },
    ],
    has_more: false,
    next_cursor: null,
  };

  it('lists public rooms with join / open / banned actions and joins', async () => {
    const joined = room(1, 'general', { my_role: 'member', member_count: 13 });
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/catalog?limit=30': () => json(CATALOG),
      'POST /api/rooms/1/join': () => json(joined),
      'GET /api/rooms/mine': () => json({ public: [joined], private: [] }),
      ...roomRoutes(joined),
    });
    renderApp('/rooms/catalog');
    expect(await screen.findByText('Everything')).toBeInTheDocument();
    expect(screen.getByText('12 members')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/rooms/3');
    expect(screen.getByRole('button', { name: 'Banned' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Join' }));
    expect(await screen.findByRole('complementary', { name: 'Room details' })).toBeInTheDocument();
  });

  it('searches with the query from the nav sidebar', async () => {
    const calls = mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/catalog?limit=30&search=rust': () => json({ items: [], has_more: false, next_cursor: null }),
    });
    renderApp('/');
    const search = await screen.findByRole('searchbox', { name: 'Search rooms' });
    fireEvent.change(search, { target: { value: 'rust' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(await screen.findByText('No public rooms match “rust”.')).toBeInTheDocument();
    expect(calls.some((c) => c.key === 'GET /api/rooms/catalog?limit=30&search=rust')).toBe(true);
  });

  it('tells a banned user why joining failed', async () => {
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/catalog?limit=30': () => json(CATALOG),
      'POST /api/rooms/1/join': () => apiError(403, 'room_banned', 'You are banned from this room'),
    });
    renderApp('/rooms/catalog');
    fireEvent.click(await screen.findByRole('button', { name: 'Join' }));
    expect(await screen.findByText('You are banned from this room')).toBeInTheDocument();
  });
});

describe('private rooms and invitations', () => {
  it('shows pending invitations in the menu badge and accepts one', async () => {
    let invitations = [
      {
        id: 5, room: { id: 2, name: 'vault', description: 'Secrets', visibility: 'private', member_count: 1 },
        inviter: ALICE, created_at: '2026-11-23T10:00:00Z',
      },
    ];
    const asMember = room(2, 'vault', { visibility: 'private', my_role: 'member', member_count: 2 });
    let mine = { public: [] as RoomSummary[], private: [] as RoomSummary[] };
    mockApi({
      'GET /api/auth/me': () => json({ user: { ...ME, id: 2, username: 'bob' } }),
      'GET /api/invitations': () => json({ items: invitations }),
      'GET /api/rooms/mine': () => json(mine),
      'POST /api/invitations/5/accept': () => {
        invitations = [];
        mine = { public: [], private: [asMember] };
        return json(asMember);
      },
      ...roomRoutes(asMember),
    });
    renderApp('/rooms/private');
    expect(await screen.findByLabelText('1 pending invitations')).toBeInTheDocument();
    expect(await screen.findByText('Invited by alice · ' + new Date('2026-11-23T10:00:00Z').toLocaleDateString()))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    expect(await screen.findByRole('complementary', { name: 'Room details' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText(/pending invitations/)).not.toBeInTheDocument());
  });
});

describe('websocket lifetime', () => {
  it('keeps one socket per tab across navigation', async () => {
    const opened: string[] = [];
    const Base = globalThis.WebSocket;
    vi.stubGlobal(
      'WebSocket',
      class extends (Base as unknown as new (url: string) => object) {
        static readonly OPEN = 1;
        constructor(url: string) {
          super(url);
          opened.push(url);
        }
      },
    );
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/mine': () => json(MINE),
      'GET /api/rooms/catalog?limit=30': () => json({ items: [], has_more: false, next_cursor: null }),
    });
    renderApp('/');
    await screen.findByRole('link', { name: '#general' });
    fireEvent.click(screen.getByRole('link', { name: 'Public Rooms' }));
    await screen.findByRole('heading', { name: 'Public rooms' });
    fireEvent.click(screen.getByRole('link', { name: 'Private Rooms' }));
    await screen.findByRole('heading', { name: 'Private rooms' });
    expect(opened).toHaveLength(1);
    expect(opened[0]).toMatch(/\/ws\?tab_id=/);
  });
});
