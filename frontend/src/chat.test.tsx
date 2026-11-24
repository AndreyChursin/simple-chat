import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { forwardRef, useEffect, type ComponentProps } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { VirtuosoMockContext } from 'react-virtuoso';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from './api/messages';
import { countNewAtTail, newAtTail, shouldFollow } from './chat/autoscroll';
import { ChatWindow } from './chat/ChatWindow';
import { Composer, composerLimits } from './chat/Composer';
import { isGrouped, linkify } from './chat/format';
import { FIRST_ITEM_INDEX, useMessages, type ChatMessage } from './chat/messagesStore';
import { createQueryClient } from './queryClient';
import { json, mockApi } from './test/utils';
import { backoffDelay, socket } from './ws/socket';

const virtuosoMock = vi.hoisted(() => ({ scrolledUp: false }));

// "Start at the bottom" needs real layout; under jsdom's mock viewport it renders nothing.
vi.mock('react-virtuoso', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-virtuoso')>();
  const Virtuoso = forwardRef<unknown, ComponentProps<typeof mod.Virtuoso>>((props, ref) => {
    const { atBottomStateChange } = props;
    // Lets a test pretend the reader scrolled up (jsdom has no real scrolling).
    useEffect(() => {
      if (virtuosoMock.scrolledUp) atBottomStateChange?.(false);
    }, [atBottomStateChange]);
    return (
      <mod.Virtuoso
        {...props}
        ref={ref as any}
        initialTopMostItemIndex={undefined}
        // while "scrolled up", the real list (which fits the mock viewport) must not report the bottom
        atBottomStateChange={virtuosoMock.scrolledUp ? undefined : atBottomStateChange}
      />
    );
  });
  return { ...mod, Virtuoso };
});

const ALICE = { id: 1, username: 'alice', display_name: null, presence: 'online' as const };
const BOB = { id: 2, username: 'bob', display_name: null, presence: 'online' as const };

function msg(id: number, extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id,
    chat: { type: 'room', id: 1 },
    author: BOB,
    content: `message ${id}`,
    created_at: new Date(Date.UTC(2026, 10, 23, 10, id)).toISOString(),
    edited_at: null,
    deleted: false,
    reply_to: null,
    attachments: [],
    client_msg_id: null,
    ...extra,
  };
}

beforeEach(() => {
  useMessages.getState().reset();
  virtuosoMock.scrolledUp = false;
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('composer', () => {
  const noop = () => {};
  const renderComposer = (props: Partial<Parameters<typeof Composer>[0]> = {}) => {
    const onSend = vi.fn();
    const onCancel = vi.fn();
    const onEditLast = vi.fn();
    render(
      <Composer chat={{ type: 'room', id: 1 }} replyTo={null} editing={null} onSend={onSend} onSaveEdit={noop} onCancel={onCancel}
        onEditLast={onEditLast} onTyping={noop} {...props} />,
    );
    return { onSend, onCancel, onEditLast, input: screen.getByLabelText('Message') as HTMLTextAreaElement };
  };

  it('counts UTF-8 bytes, not characters', () => {
    expect(composerLimits('😀'.repeat(768))).toMatchObject({ bytes: 3072, tooLong: false, showCounter: true });
    expect(composerLimits('😀'.repeat(768) + 'x').tooLong).toBe(true);
    expect(composerLimits('a'.repeat(2800)).showCounter).toBe(false);
    expect(composerLimits(' \n ').empty).toBe(true);
  });

  it('shows the counter above 2800 bytes and blocks sending above 3072', () => {
    const { input } = renderComposer();
    fireEvent.change(input, { target: { value: 'a'.repeat(2801) } });
    expect(screen.getByText('2801 / 3072 bytes')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
    fireEvent.change(input, { target: { value: 'ж'.repeat(1537) } }); // 2 bytes each
    expect(screen.getByRole('alert')).toHaveTextContent('3074 / 3072 bytes — Message is too long (max 3 KB)');
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('sends on Enter, inserts a newline on Shift+Enter', () => {
    const { input, onSend } = renderComposer();
    fireEvent.change(input, { target: { value: 'line 1' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: 'line 1\nline 2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('line 1\nline 2', []);
    expect(input.value).toBe('');
    fireEvent.keyDown(input, { key: 'Enter' }); // empty: nothing
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('shows the reply target, cancels with Esc, edits the last message with ↑', () => {
    const { input, onCancel, onEditLast } = renderComposer({ replyTo: msg(5, { content: 'original text' }) });
    expect(screen.getByText(/Replying to/)).toHaveTextContent('Replying to: bob — original text');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(onEditLast).toHaveBeenCalled();
  });
});

describe('autoscroll rules', () => {
  it('follows only at the bottom or for own messages', () => {
    expect(shouldFollow(true, false)).toBe(true);
    expect(shouldFollow(false, true)).toBe(true);
    expect(shouldFollow(false, false)).toBe(false);
  });

  it('counts unseen messages of others at the tail', () => {
    const list = [msg(1), msg(2), msg(3, { author: ALICE }), msg(4), msg(-1, { author: ALICE })];
    expect(countNewAtTail(1, list, ALICE.id)).toBe(2); // 2 and 4; own 3 and the pending one skipped
    expect(countNewAtTail(4, list, ALICE.id)).toBe(0);
  });

  it('detects a newer message at the tail only', () => {
    expect(newAtTail(msg(5), msg(6))).toBe(true);
    expect(newAtTail(msg(5), msg(-1, { client_msg_id: 'c' }))).toBe(true); // own optimistic
    expect(newAtTail(msg(-1, { client_msg_id: 'c' }), msg(9, { client_msg_id: 'c' }))).toBe(false); // confirmed
    expect(newAtTail(msg(6), msg(6))).toBe(false); // history prepended above
    expect(newAtTail(msg(300), msg(40))).toBe(false); // jumped to an older window
    expect(newAtTail(undefined, msg(1))).toBe(false); // first load
  });

  it('groups messages of one author within 5 minutes', () => {
    expect(isGrouped(msg(1), msg(2))).toBe(true);
    expect(isGrouped(msg(1), msg(7))).toBe(false);
    expect(isGrouped(msg(1, { author: ALICE }), msg(2))).toBe(false);
  });
});

describe('messages store', () => {
  const key = 'room:1';

  it('replaces the optimistic message by client_msg_id and keeps order', () => {
    const s = useMessages.getState();
    s.setLatest(key, [msg(1), msg(2)], true);
    s.addPending(key, msg(-1, { client_msg_id: 'c-1', author: ALICE, pending: 'sending' }));
    s.upsert(key, msg(3)); // someone else's message arrives first
    expect(useMessages.getState().get(key).messages.map((m) => m.id)).toEqual([1, 2, 3, -1]);
    s.upsert(key, msg(4, { client_msg_id: 'c-1', author: ALICE }));
    s.upsert(key, msg(4, { client_msg_id: 'c-1', author: ALICE })); // ack + new_message: no duplicate
    expect(useMessages.getState().get(key).messages.map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it('prepends older pages and moves firstItemIndex', () => {
    const s = useMessages.getState();
    s.setLatest(key, [msg(5), msg(6)], true);
    s.prependOlder(key, [msg(3), msg(4)], false);
    const c = useMessages.getState().get(key);
    expect(c.messages.map((m) => m.id)).toEqual([3, 4, 5, 6]);
    expect(c.firstItemIndex).toBe(FIRST_ITEM_INDEX - 2);
    expect(c.hasMoreBefore).toBe(false);
  });

  it('marks deletions in messages and in quotes', () => {
    const s = useMessages.getState();
    s.setLatest(key, [msg(1), msg(2, { reply_to: { id: 1, author: BOB, excerpt: 'message 1', deleted: false } })], false);
    s.markDeleted(key, 1);
    const [first, second] = useMessages.getState().get(key).messages;
    expect(first).toMatchObject({ deleted: true, content: '' });
    expect(second?.reply_to).toMatchObject({ deleted: true, excerpt: '' });
  });

  it('ignores live messages while the newest history is not loaded (after a jump)', () => {
    const s = useMessages.getState();
    s.setWindow(key, [msg(10), msg(11)], true, true);
    expect(s.upsert(key, msg(500))).toBe(false);
    expect(useMessages.getState().get(key).messages.map((m) => m.id)).toEqual([10, 11]);
  });
});

describe('formatting and transport helpers', () => {
  it('links URLs and never renders HTML', () => {
    render(<p data-testid="t">{linkify('see https://example.com/a?b=1. <b>bold</b>')}</p>);
    const link = screen.getByRole('link', { name: 'https://example.com/a?b=1' });
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByTestId('t').innerHTML).toContain('&lt;b&gt;bold&lt;/b&gt;');
  });

  it('backs off exponentially from 0.5 s to 10 s with ±20 % jitter', () => {
    expect(backoffDelay(0, () => 0.5)).toBe(500);
    expect(backoffDelay(3, () => 0.5)).toBe(4000);
    expect(backoffDelay(10, () => 0.5)).toBe(10_000);
    expect(backoffDelay(0, () => 0)).toBe(400);
    expect(backoffDelay(0, () => 1)).toBe(600);
  });
});

describe('chat window', () => {
  const HISTORY = [
    msg(1, { author: ALICE, content: 'my first' }),
    msg(2, { content: 'reply here', reply_to: { id: 1, author: ALICE, excerpt: 'my first', deleted: false } }),
    msg(3, { deleted: true, content: '' }),
  ];

  function renderChat(canModerate = false) {
    const client = createQueryClient();
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <VirtuosoMockContext.Provider value={{ viewportHeight: 2000, itemHeight: 60 }}>
            <ChatWindow chat={{ type: 'room', id: 1 }} me={{ id: 1, username: 'alice' }} canModerate={canModerate}
              searchOpen={false} onCloseSearch={() => {}} />
          </VirtuosoMockContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('renders history with quotes, edit only on own messages and the deleted placeholder', async () => {
    mockApi({ 'GET /api/rooms/1/messages?limit=50': () => json({ items: HISTORY, has_more: false, next_cursor: null }) });
    renderChat();
    const own = await screen.findByRole('article', { name: /^alice/ });
    expect(within(own).getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(within(own).getByRole('button', { name: 'Delete' })).toBeInTheDocument();
    const theirs = screen.getAllByRole('article', { name: /^bob/ })[0]!;
    expect(within(theirs).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    expect(within(theirs).queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
    expect(within(theirs).getByRole('button', { name: /alice/ })).toHaveTextContent('my first');
    expect(screen.getByText('Message deleted')).toBeInTheDocument();
    expect(screen.getByText('Beginning of conversation')).toBeInTheDocument();
  });

  it('lets admins delete messages of others', async () => {
    mockApi({ 'GET /api/rooms/1/messages?limit=50': () => json({ items: HISTORY, has_more: false, next_cursor: null }) });
    renderChat(true);
    const theirs = (await screen.findAllByRole('article', { name: /^bob/ }))[0]!;
    expect(within(theirs).getByRole('button', { name: 'Delete' })).toBeInTheDocument();
  });

  it('shows a sent message at once, then replaces it with the acknowledged one', async () => {
    mockApi({ 'GET /api/rooms/1/messages?limit=50': () => json({ items: HISTORY, has_more: false, next_cursor: null }) });
    let ack: (value: { type: string; message: Message }) => void = () => {};
    const request = vi.spyOn(socket, 'request').mockImplementation(
      () => new Promise((resolve) => (ack = resolve as typeof ack)),
    );
    renderChat();
    const input = await screen.findByLabelText('Message');
    fireEvent.change(input, { target: { value: 'hello there' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    const pending = await screen.findByText('hello there');
    expect(pending.closest('article')).toHaveClass('pending-sending');
    const sent = request.mock.calls[0]![0] as { type: string; content: string; client_msg_id: string };
    expect(sent).toMatchObject({ type: 'send_message', chat: { type: 'room', id: 1 }, content: 'hello there' });

    await act(async () => ack({ type: 'ack', message: msg(4, { author: ALICE, content: 'hello there', client_msg_id: sent.client_msg_id }) }));
    await waitFor(() => expect(screen.getByText('hello there').closest('article')).not.toHaveClass('pending-sending'));
    expect(screen.getAllByText('hello there')).toHaveLength(1);
  });

  it('marks a rejected message as failed with Retry', async () => {
    mockApi({ 'GET /api/rooms/1/messages?limit=50': () => json({ items: [], has_more: false, next_cursor: null }) });
    vi.spyOn(socket, 'request').mockRejectedValue({ code: 'message_too_large', message: 'x' });
    renderChat();
    const input = await screen.findByLabelText('Message');
    fireEvent.change(input, { target: { value: 'nope' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByRole('alert')).toHaveTextContent('Message is too long (max 3 KB)');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('counts messages arriving while the reader is scrolled up instead of jumping', async () => {
    virtuosoMock.scrolledUp = true;
    mockApi({ 'GET /api/rooms/1/messages?limit=50': () => json({ items: HISTORY, has_more: false, next_cursor: null }) });
    const { container } = renderChat();
    await screen.findAllByText('my first');
    // the simulated scroll-up must have landed before messages arrive
    await waitFor(() => expect(container.querySelector('.feed')).toHaveAttribute('data-at-bottom', 'false'));
    act(() => {
      useMessages.getState().upsert('room:1', msg(4, { content: 'news 1' }));
      useMessages.getState().upsert('room:1', msg(5, { author: ALICE, content: 'mine elsewhere' }));
      useMessages.getState().upsert('room:1', msg(6, { content: 'news 2' }));
    });
    expect(await screen.findByRole('button', { name: '↓ 2 new messages' })).toBeInTheDocument();
  });
});
