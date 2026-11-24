import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { VirtuosoMockContext } from 'react-virtuoso';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from './api/messages';
import type { RoomSummary } from './api/rooms';
import { ChatWindow } from './chat/ChatWindow';
import { useMessages } from './chat/messagesStore';
import { createQueryClient } from './queryClient';
import { totalUnread, useUnread } from './stores/unread';
import { apiError, json, ME, mockApi, renderApp } from './test/utils';
import { socket } from './ws/socket';

// Like chat.test.tsx: jsdom has no layout, so the initial scroll position is left to the real list.
vi.mock('react-virtuoso', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-virtuoso')>();
  const { forwardRef } = await import('react');
  const Virtuoso = forwardRef<unknown, ComponentProps<typeof mod.Virtuoso>>((props, ref) => (
    <mod.Virtuoso {...props} ref={ref as never} initialTopMostItemIndex={undefined} />
  ));
  return { ...mod, Virtuoso };
});

const ALICE = { id: 1, username: 'alice', display_name: null, presence: 'online' as const };
const BOB = { id: 2, username: 'bob', display_name: null, presence: 'online' as const };

function room(id: number, name: string, extra: Partial<RoomSummary> = {}): RoomSummary {
  return {
    id, name, description: '', visibility: 'public', member_count: 2, owner: ALICE, my_role: 'member', unread: 0,
    created_at: '2026-11-23T10:00:00Z', ...extra,
  };
}

function msg(id: number, author = BOB): Message {
  return {
    id, chat: { type: 'room', id: 1 }, author, content: `message ${id}`,
    created_at: new Date(Date.UTC(2026, 10, 23, 10, id)).toISOString(), edited_at: null, deleted: false,
    reply_to: null, attachments: [], client_msg_id: null,
  };
}

const emit = (event: Record<string, unknown>) =>
  act(() => {
    const ws = (socket as unknown as { ws: { onmessage: (e: { data: string }) => void } | null }).ws;
    ws?.onmessage({ data: JSON.stringify(event) });
  });

beforeEach(() => {
  useUnread.getState().reset();
  useMessages.getState().reset();
  localStorage.clear();
  document.title = 'Chat';
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('unread store', () => {
  it('loads, bumps up to the cap, skips the chat being read and only moves the cursor forward', () => {
    const s = useUnread.getState();
    s.load([
      { chat: { type: 'room', id: 1 }, unread: 99, last_read_message_id: 10, last_message_id: 109 },
      { chat: { type: 'dm', id: 5 }, unread: 0, last_read_message_id: 3, last_message_id: 3 },
    ]);
    s.bump('room:1');
    s.bump('room:1');
    expect(useUnread.getState().byChat['room:1']).toBe(100);
    s.setReading('dm:5');
    s.bump('dm:5');
    expect(useUnread.getState().byChat['dm:5']).toBe(0);
    s.readUpTo('room:1', 50);
    s.set('room:1', 0, 20); // a late unread_update must not move the cursor back
    expect(useUnread.getState().lastRead['room:1']).toBe(50);
    expect(totalUnread({ a: 3, b: 4 })).toBe(7);
  });
});

describe('badges', () => {
  function setup() {
    const general = room(1, 'general', { unread: 2 });
    const calls = mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/rooms/mine': () => json({ public: [general, room(2, 'random')], private: [] }),
      'GET /api/contacts': () =>
        json({ friends: [{ ...BOB, dm_id: 5, unread: 1 }], incoming: [], outgoing: [], past_dms: [] }),
    });
    renderApp('/');
    return calls;
  }

  it('shows REST counters, then live ones: others bump, mine do not, unread_update resets', async () => {
    setup();
    const link = await screen.findByRole('link', { name: /#general/ });
    expect(within(link).getByText('2')).toBeInTheDocument();
    expect(within(await screen.findByRole('link', { name: /bob/ })).getByText('1')).toBeInTheDocument();

    act(() => useUnread.getState().set('room:2', 120, 0));
    expect(within(await screen.findByRole('link', { name: /#random/ })).getByText('99+')).toBeInTheDocument();

    act(() => useUnread.getState().set('room:1', 2, 10));
    emit({ type: 'new_message', message: msg(11) });
    expect(within(link).getByText('3')).toBeInTheDocument();
    emit({ type: 'new_message', message: { ...msg(12), author: { ...ALICE } } });
    expect(within(link).getByText('3')).toBeInTheDocument();
    emit({ type: 'unread_update', chat: { type: 'room', id: 1 }, unread: 0, last_read_message_id: 12 });
    expect(within(link).queryByText(/\d/)).not.toBeInTheDocument();
  });

  it('puts the total into the title and a dot on the favicon', async () => {
    const icon = document.createElement('link');
    icon.rel = 'icon';
    icon.href = '/favicon.svg';
    document.head.appendChild(icon);
    setup();
    await screen.findByRole('link', { name: /#general/ });
    act(() => useUnread.getState().set('room:1', 3, 0));
    act(() => useUnread.getState().set('dm:5', 2, 0));
    expect(document.title).toBe('(5) Chat');
    expect(icon.href).toMatch(/^data:image\/svg\+xml/);
    act(() => useUnread.getState().load([]));
    expect(document.title).toBe('Chat');
    expect(icon.getAttribute('href')).toBe('/favicon.svg');
    icon.remove();
  });
});

describe('chat window', () => {
  function renderChat() {
    return render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <VirtuosoMockContext.Provider value={{ viewportHeight: 2000, itemHeight: 60 }}>
            <ChatWindow chat={{ type: 'room', id: 1 }} me={{ id: 1, username: 'alice' }} canModerate={false}
              searchOpen={false} onCloseSearch={() => {}} />
          </VirtuosoMockContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('marks the first unread with "New messages" and marks the chat read when its end is visible', async () => {
    useUnread.getState().set('room:1', 2, 3);
    const calls = mockApi({
      'GET /api/rooms/1/messages?limit=50': () =>
        json({ items: [msg(1), msg(2), msg(3), msg(4), msg(5)], has_more: false, next_cursor: null }),
      'POST /api/rooms/1/read': () => json(undefined, 204),
    });
    renderChat();
    // the separator sits right before the first unread message (4), not before what was read (1–3)
    await waitFor(() => {
      const seps = screen.getAllByRole('separator', { name: 'New messages' });
      expect(seps).toHaveLength(1);
      expect(seps[0]!.nextElementSibling).toHaveTextContent('message 4');
    });

    // the whole list fits the viewport → bottom visible → read at once locally, on the server after 500 ms
    await waitFor(() => expect(useUnread.getState().byChat['room:1']).toBe(0));
    await waitFor(() => expect(calls.some((c) => c.key === 'POST /api/rooms/1/read')).toBe(true), { timeout: 2000 });
    const post = calls.find((c) => c.key === 'POST /api/rooms/1/read');
    expect(JSON.parse(String(post?.init?.body))).toEqual({ last_read_message_id: 5 });
    expect(useUnread.getState().lastRead['room:1']).toBe(5);
  });

  it('does not mark a hidden tab as read', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    useUnread.getState().set('room:1', 1, 1);
    mockApi({
      'GET /api/rooms/1/messages?limit=50': () => json({ items: [msg(1), msg(2)], has_more: false, next_cursor: null }),
    });
    renderChat();
    await screen.findByText('message 2');
    await new Promise((r) => setTimeout(r, 50));
    expect(useUnread.getState().byChat['room:1']).toBe(1);
  });
});

describe('delete account', () => {
  it('lists the consequences with owned rooms, checks the password and signs out', async () => {
    let deleted = false;
    mockApi({
      'GET /api/auth/me': () => (deleted ? apiError(401, 'not_authenticated') : json({ user: ME })),
      'GET /api/rooms/mine': () =>
        json({ public: [room(1, 'general', { my_role: 'owner' }), room(2, 'other')], private: [room(3, 'vault', { my_role: 'owner' })] }),
      'DELETE /api/me': (init) => {
        if (JSON.parse(String(init?.body)).password !== 'right-password') {
          return apiError(401, 'invalid_current_password', 'Password is incorrect');
        }
        deleted = true;
        return json(undefined, 204);
      },
    });
    renderApp('/profile');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete account' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete your account?' });
    expect(await within(dialog).findByText(/Rooms you own \(2\)/)).toHaveTextContent('#general, #vault');
    expect(dialog).toHaveTextContent('Deleted user');

    const password = within(dialog).getByLabelText('Password');
    fireEvent.change(password, { target: { value: 'nope' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete my account' }));
    expect(await within(dialog).findByText('Password is incorrect')).toBeInTheDocument();

    fireEvent.change(password, { target: { value: 'right-password' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete my account' }));
    expect(await screen.findByText('Your account was deleted.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });
});

describe('deleted authors', () => {
  it('re-signs cached messages of a deleted account as "Deleted user"', () => {
    const quote = { id: 1, author: BOB, excerpt: 'hi', deleted: false };
    useMessages.getState().setLatest('room:1', [msg(1), { ...msg(2, ALICE), reply_to: quote }], false);
    useMessages.getState().setLatest('room:2', [msg(3, ALICE)], false);
    useMessages.getState().anonymize(2);
    const [m1, m2] = useMessages.getState().get('room:1').messages;
    expect(m1!.author).toEqual({ id: null, username: 'Deleted user', display_name: null, presence: 'offline' });
    expect(m2!.author.username).toBe('alice');
    expect(m2!.reply_to?.author.username).toBe('Deleted user');
    expect(useMessages.getState().get('room:2').messages[0]!.author.username).toBe('alice');
  });
});
