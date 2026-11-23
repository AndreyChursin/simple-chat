import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso';
import {
  chatKey,
  deleteMessage,
  editMessage,
  fetchAround,
  fetchMessages,
  searchMessages,
  type ChatRef,
  type Message,
} from '../api/messages';
import { ApiError } from '../api/client';
import { Modal } from '../components/Modal';
import { toast } from '../stores/toasts';
import { socket } from '../ws/socket';
import { AT_BOTTOM_THRESHOLD_PX, countNewAtTail, newAtTail, shouldFollow } from './autoscroll';
import { Composer } from './Composer';
import { timeOf } from './format';
import { MessageItem } from './MessageItem';
import { emptyChat, useMessages, type ChatMessage } from './messagesStore';
import { typingText, useTyping } from './typingStore';

let tempIds = 0;
const HIGHLIGHT_MS = 2000;

const ERROR_TEXT: Record<string, string> = {
  message_too_large: 'Message is too long (max 3 KB)',
  empty_message: 'Message is empty',
  rate_limited: 'Too many messages, slow down',
  forbidden: 'You can no longer write here',
  invalid_reply_target: 'The quoted message is not in this chat',
};

interface Props {
  chat: ChatRef;
  me: { id: number; username: string };
  canModerate: boolean;
  readOnly?: boolean;
  searchOpen: boolean;
  onCloseSearch: () => void;
}

/** Chat Window (FRONTEND_SPEC §4.3–4.4): virtualized feed + composer. Shared by rooms and DMs. */
export function ChatWindow({ chat, me, canModerate, readOnly = false, searchOpen, onCloseSearch }: Props) {
  const key = chatKey(chat);
  const state = useMessages((s) => s.chats[key]) ?? emptyChat();
  const store = useMessages.getState;
  const virtuoso = useRef<VirtuosoHandle>(null);
  const [generation, setGeneration] = useState(0);
  const [jumpTarget, setJumpTarget] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [replyTo, setReplyTo] = useState<ChatMessage | null>(null);
  const [editing, setEditing] = useState<ChatMessage | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ChatMessage | null>(null);
  const tailRef = useRef(0);
  const messages = state.messages;

  // --- loading -------------------------------------------------------------------------------------
  const loadLatest = useCallback(async () => {
    setLoadError(null);
    try {
      const page = await fetchMessages(chat);
      store().setLatest(key, page.items, page.has_more);
      setJumpTarget(null);
      setGeneration((g) => g + 1);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : 'Could not load messages');
    }
  }, [chat.type, chat.id]);

  useEffect(() => {
    setReplyTo(null);
    setEditing(null);
    setUnseen(0);
    setAtBottom(true);
    if (!store().get(key).loaded) void loadLatest();
    else setGeneration((g) => g + 1);
  }, [key, loadLatest, store]);

  const loadOlder = useCallback(async () => {
    const cur = store().get(key);
    const oldest = cur.messages.find((m) => m.id > 0);
    if (!cur.hasMoreBefore || cur.loadingOlder || !oldest) return;
    store().setLoadingOlder(key, true);
    try {
      const page = await fetchMessages(chat, { before_id: oldest.id });
      store().prependOlder(key, page.items, page.has_more);
    } catch {
      store().setLoadingOlder(key, false);
    }
  }, [key]);

  const loadNewer = useCallback(async () => {
    const cur = store().get(key);
    const newest = cur.messages.filter((m) => m.id > 0).at(-1);
    if (!cur.hasMoreAfter || !newest) return;
    const page = await fetchMessages(chat, { after_id: newest.id });
    store().appendNewer(key, page.items, page.has_more);
  }, [key]);

  // --- following the tail ----------------------------------------------------------------------------------
  // Virtuoso's followOutput lands a few px short of the real bottom when the new item is taller than its
  // estimate; after layout, finish the scroll on the actual scroller element.
  const scroller = useRef<HTMLElement | null>(null);
  const atBottomRef = useRef(true);
  atBottomRef.current = atBottom;
  const settleAtBottom = useCallback(() => {
    let frames = 0;
    const step = () => {
      const el = scroller.current;
      if (!el) return;
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 1) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
      if (++frames < 3) requestAnimationFrame(step);
    };
    requestAnimationFrame(() => requestAnimationFrame(step));
  }, []);
  const prevLast = useRef<ChatMessage | undefined>(undefined);
  useEffect(() => {
    const last = messages.at(-1);
    const prev = prevLast.current;
    prevLast.current = last;
    if (newAtTail(prev, last) && shouldFollow(atBottomRef.current, last!.author.id === me.id)) settleAtBottom();
  }, [messages, me.id, settleAtBottom]);

  // --- unseen counter ("↓ N new messages") -------------------------------------------------------------
  useEffect(() => {
    const tail = messages.filter((m) => m.id > 0).at(-1)?.id ?? 0;
    // Count now: a state updater runs later, after tailRef has moved on.
    const fresh = !atBottom && tailRef.current ? countNewAtTail(tailRef.current, messages, me.id) : 0;
    if (fresh > 0) setUnseen((n) => n + fresh);
    tailRef.current = tail;
  }, [messages, atBottom, me.id]);

  useEffect(() => {
    if (atBottom) setUnseen(0);
  }, [atBottom]);

  const scrollToBottom = useCallback(() => {
    if (store().get(key).hasMoreAfter) {
      void loadLatest();
      return;
    }
    virtuoso.current?.scrollToIndex({ index: 'LAST', behavior: 'smooth' });
  }, [key, loadLatest, store]);

  // --- jump to a message (quote / search hit) ------------------------------------------------------------
  const jumpTo = useCallback(
    async (messageId: number) => {
      const idx = store().get(key).messages.findIndex((m) => m.id === messageId);
      if (idx >= 0) {
        virtuoso.current?.scrollToIndex({ index: idx, align: 'center', behavior: 'smooth' });
      } else {
        try {
          const res = await fetchAround(chat, messageId);
          store().setWindow(key, res.items, res.has_more_before, res.has_more_after);
          setJumpTarget(messageId);
          setGeneration((g) => g + 1);
        } catch {
          toast('That message is no longer available', 'error');
          return;
        }
      }
      setHighlight(messageId);
      setTimeout(() => setHighlight((h) => (h === messageId ? null : h)), HIGHLIGHT_MS);
    },
    [key],
  );

  // --- sending -----------------------------------------------------------------------------------------
  const deliver = useCallback(
    (pending: ChatMessage) => {
      const clientMsgId = pending.client_msg_id!;
      socket
        .request({
          type: 'send_message',
          chat,
          client_msg_id: clientMsgId,
          content: pending.content,
          reply_to_message_id: pending.reply_to_message_id ?? null,
        })
        .then((ack) => store().upsert(key, ack.message as Message))
        .catch((err: { code: string; message: string }) =>
          store().failPending(key, clientMsgId, ERROR_TEXT[err.code] ?? err.message),
        );
    },
    [key],
  );

  const send = useCallback(
    (text: string) => {
      const pending: ChatMessage = {
        id: --tempIds,
        chat,
        author: { id: me.id, username: me.username, display_name: null, presence: 'online' },
        content: text.replace(/\r\n/g, '\n'),
        created_at: new Date().toISOString(),
        edited_at: null,
        deleted: false,
        reply_to: replyTo ? { id: replyTo.id, author: replyTo.author, excerpt: replyTo.content.slice(0, 200), deleted: false } : null,
        reply_to_message_id: replyTo?.id ?? null,
        attachments: [],
        client_msg_id: crypto.randomUUID(),
        pending: 'sending',
      };
      setReplyTo(null);
      if (store().get(key).hasMoreAfter) void loadLatest(); // own message: show the live tail
      store().addPending(key, pending);
      deliver(pending);
      if (shouldFollow(atBottom, true)) {
        requestAnimationFrame(() => virtuoso.current?.scrollToIndex({ index: 'LAST', behavior: 'smooth' }));
      }
    },
    [key, me.id, me.username, replyTo, atBottom, deliver, loadLatest],
  );

  const actions = useMemo(
    () => ({
      onReply: (m: ChatMessage) => {
        setEditing(null);
        setReplyTo(m);
      },
      onEdit: (m: ChatMessage) => {
        setReplyTo(null);
        setEditing(m);
      },
      onDelete: (m: ChatMessage) => setConfirmDelete(m),
      onJump: (id: number) => void jumpTo(id),
      onRetry: (m: ChatMessage) => {
        store().retryPending(key, m.client_msg_id!);
        deliver(m);
      },
      onDiscard: (m: ChatMessage) => store().removePending(key, m.client_msg_id!),
    }),
    [key, jumpTo, deliver, store],
  );

  async function saveEdit(m: ChatMessage, text: string) {
    setEditing(null);
    try {
      store().upsert(key, await editMessage(m.id, text));
    } catch (e) {
      toast(e instanceof ApiError ? (ERROR_TEXT[e.code] ?? e.message) : 'Could not edit the message', 'error');
    }
  }

  async function doDelete(m: ChatMessage) {
    setConfirmDelete(null);
    try {
      await deleteMessage(m.id);
      store().markDeleted(key, m.id);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not delete the message', 'error');
    }
  }

  function editLast() {
    const last = [...store().get(key).messages].reverse().find((m) => m.id > 0 && m.author.id === me.id && !m.deleted);
    if (last) actions.onEdit(last);
  }

  // --- typing --------------------------------------------------------------------------------------------
  const typists = useTyping((s) => s.byChat[key]);
  const typing = typingText(Object.entries(typists ?? {}).filter(([id]) => Number(id) !== me.id).map(([, t]) => t.username));

  // --- render ----------------------------------------------------------------------------------------------
  const jumpIndex = jumpTarget === null ? -1 : messages.findIndex((m) => m.id === jumpTarget);
  const Header = useCallback(
    () => (
      <div className="feed-edge muted">
        {state.loadingOlder ? 'Loading…' : state.hasMoreBefore ? '' : state.loaded ? 'Beginning of conversation' : ''}
      </div>
    ),
    [state.loadingOlder, state.hasMoreBefore, state.loaded],
  );

  return (
    <div className="chat-body">
      {searchOpen && <SearchPanel chat={chat} onClose={onCloseSearch} onPick={(id) => void jumpTo(id)} />}
      <div className="feed" data-at-bottom={atBottom} data-unseen={unseen}>
        {loadError ? (
          <div className="chat-empty">
            <p className="form-error">{loadError}</p>
            <button className="btn" onClick={() => void loadLatest()}>Retry</button>
          </div>
        ) : !state.loaded ? (
          <div className="chat-empty muted">Loading…</div>
        ) : messages.length === 0 ? (
          <div className="chat-empty muted">No messages yet. Say hello!</div>
        ) : (
          <Virtuoso
            key={`${key}:${generation}`}
            ref={virtuoso}
            className="feed-list"
            data={messages}
            firstItemIndex={state.firstItemIndex}
            initialTopMostItemIndex={jumpIndex >= 0 ? { index: jumpIndex, align: 'center' } : messages.length - 1}
            computeItemKey={(_, m) => m.client_msg_id ?? m.id}
            startReached={() => void loadOlder()}
            endReached={() => void loadNewer()}
            followOutput={(bottom) => (bottom ? 'smooth' : false)}
            atBottomStateChange={setAtBottom}
            scrollerRef={(el) => {
              scroller.current = el instanceof HTMLElement ? el : null;
            }}
            atBottomThreshold={AT_BOTTOM_THRESHOLD_PX}
            increaseViewportBy={{ top: 400, bottom: 200 }}
            components={{ Header }}
            itemContent={(index, m) => (
              <MessageItem
                message={m}
                prev={messages[index - state.firstItemIndex - 1]}
                myId={me.id}
                canModerate={canModerate}
                readOnly={readOnly}
                highlighted={highlight === m.id}
                {...actions}
              />
            )}
          />
        )}
        {(unseen > 0 || (state.hasMoreAfter && !atBottom)) && (
          <button className="new-messages" onClick={scrollToBottom}>
            ↓ {unseen > 0 ? `${unseen} new ${unseen === 1 ? 'message' : 'messages'}` : 'Jump to latest'}
          </button>
        )}
      </div>
      <div className="typing-line" aria-live="polite">{typing}</div>
      {readOnly ? (
        <div className="composer-frozen">🔒 This conversation is read-only</div>
      ) : (
        <Composer
          replyTo={replyTo}
          editing={editing}
          onSend={send}
          onSaveEdit={(m, text) => void saveEdit(m, text)}
          onCancel={() => {
            setReplyTo(null);
            setEditing(null);
          }}
          onEditLast={editLast}
          onTyping={() => socket.send({ type: 'typing', chat })}
        />
      )}
      {confirmDelete && (
        <Modal title="Delete message?" onClose={() => setConfirmDelete(null)}>
          <p>This cannot be undone.</p>
          <blockquote className="msg-quote static">{confirmDelete.content.slice(0, 200)}</blockquote>
          <div className="modal-actions">
            <button className="btn" onClick={() => setConfirmDelete(null)}>Cancel</button>
            <button className="btn danger" onClick={() => void doDelete(confirmDelete)}>Delete</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function SearchPanel({ chat, onClose, onPick }: { chat: ChatRef; onClose: () => void; onPick: (id: number) => void }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<Message[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setResults([]);
      setStatus('idle');
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      setStatus('loading');
      try {
        const page = await searchMessages(chat, query);
        if (cancelled) return;
        setResults(page.items);
        setCursor(page.next_cursor);
        setStatus('done');
      } catch {
        if (!cancelled) setStatus('error');
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q, chat.type, chat.id]);

  async function more() {
    if (cursor === null) return;
    const page = await searchMessages(chat, q.trim(), cursor);
    setResults((r) => [...r, ...page.items]);
    setCursor(page.next_cursor);
  }

  return (
    <section className="search-panel" aria-label="Search messages">
      <div className="search-panel-top">
        <input type="search" autoFocus placeholder="Search in this chat…" aria-label="Search in this chat" value={q}
          onChange={(e) => setQ(e.target.value)} />
        <button className="btn ghost small" aria-label="Close search" onClick={onClose}>✕</button>
      </div>
      {status === 'loading' && <p className="muted small">Searching…</p>}
      {status === 'error' && <p className="form-error small">Search failed</p>}
      {status === 'done' && results.length === 0 && <p className="muted small">Nothing found</p>}
      <ul className="search-results">
        {results.map((m) => (
          <li key={m.id}>
            <button className="search-hit" onClick={() => onPick(m.id)}>
              <span className="muted small">
                {m.author.username} · {new Date(m.created_at).toLocaleDateString()} {timeOf(m.created_at)}
              </span>
              <span className="search-hit-text">{m.content}</span>
            </button>
          </li>
        ))}
      </ul>
      {cursor !== null && (
        <button className="btn small wide" onClick={() => void more()}>More results</button>
      )}
    </section>
  );
}
