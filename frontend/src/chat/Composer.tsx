import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { byteLength, MESSAGE_MAX_BYTES } from '../api/messages';
import type { ChatMessage } from './messagesStore';

const EmojiPicker = lazy(() => import('./EmojiPicker'));

export const COUNTER_FROM_BYTES = 2800;
const MAX_ROWS = 10;
const TYPING_THROTTLE_MS = 3000;
const REPLY_PREVIEW_CHARS = 80;

export function composerLimits(text: string) {
  const bytes = byteLength(text);
  return {
    bytes,
    showCounter: bytes > COUNTER_FROM_BYTES,
    tooLong: bytes > MESSAGE_MAX_BYTES,
    empty: text.trim().length === 0,
  };
}

interface ComposerProps {
  replyTo: ChatMessage | null;
  editing: ChatMessage | null;
  onSend: (text: string) => void;
  onSaveEdit: (message: ChatMessage, text: string) => void;
  onCancel: () => void;
  onEditLast: () => void;
  onTyping: () => void;
}

/** FRONTEND_SPEC §4.4: multiline, Enter sends, Shift+Enter newline, Esc cancels reply/edit, ↑ edits
 * the last own message, byte counter, emoji. */
export function Composer({ replyTo, editing, onSend, onSaveEdit, onCancel, onEditLast, onTyping }: ComposerProps) {
  const [text, setText] = useState('');
  const [emojiOpen, setEmojiOpen] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const lastTyping = useRef(0);
  const draftBeforeEdit = useRef('');

  useEffect(() => {
    if (editing) {
      draftBeforeEdit.current = text;
      setText(editing.content);
    } else {
      setText(draftBeforeEdit.current);
      draftBeforeEdit.current = '';
    }
    area.current?.focus();
  }, [editing?.id]);

  useEffect(() => {
    if (replyTo) area.current?.focus();
  }, [replyTo]);

  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = 'auto';
    const line = parseFloat(getComputedStyle(el).lineHeight) || 20;
    el.style.height = `${Math.min(el.scrollHeight, line * MAX_ROWS + 16)}px`;
  }, [text]);

  const { bytes, showCounter, tooLong, empty } = composerLimits(text);
  const canSubmit = !empty && !tooLong;

  function submit() {
    if (!canSubmit) return;
    if (editing) onSaveEdit(editing, text);
    else onSend(text);
    lastTyping.current = 0; // the next message's typing is announced at once
    setText('');
    setEmojiOpen(false);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    } else if (e.key === 'Escape') {
      if (editing || replyTo) {
        e.preventDefault();
        onCancel();
      }
      setEmojiOpen(false);
    } else if (e.key === 'ArrowUp' && text === '' && !editing) {
      e.preventDefault();
      onEditLast();
    }
  }

  function onChange(value: string) {
    setText(value);
    const now = Date.now();
    if (!editing && value && now - lastTyping.current > TYPING_THROTTLE_MS) {
      lastTyping.current = now;
      onTyping();
    }
  }

  function insertEmoji(emoji: string) {
    const el = area.current;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    const next = text.slice(0, start) + emoji + text.slice(end);
    setText(next);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + emoji.length, start + emoji.length);
    });
  }

  return (
    <div className="composer">
      {(replyTo || editing) && (
        <div className="composer-context">
          {editing ? (
            <span>Editing message</span>
          ) : (
            replyTo && (
              <span>
                Replying to <strong>{replyTo.author.username}</strong> —{' '}
                <span className="muted">{replyTo.content.slice(0, REPLY_PREVIEW_CHARS)}</span>
              </span>
            )
          )}
          <button className="btn ghost small" aria-label={editing ? 'Cancel editing' : 'Cancel reply'} onClick={onCancel}>
            ✕
          </button>
        </div>
      )}
      <div className="composer-row">
        <div className="emoji-anchor">
          <button className="btn ghost composer-icon" aria-label="Insert emoji" aria-expanded={emojiOpen}
            onClick={() => setEmojiOpen(!emojiOpen)}>
            😊
          </button>
          {emojiOpen && (
            <div className="emoji-popover">
              <Suspense fallback={<div className="muted emoji-loading">Loading…</div>}>
                <EmojiPicker onSelect={insertEmoji} />
              </Suspense>
            </div>
          )}
        </div>
        <textarea
          ref={area}
          className="composer-input"
          aria-label="Message"
          placeholder="Write a message…"
          rows={1}
          value={text}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button className="btn primary" onClick={submit} disabled={!canSubmit}>
          {editing ? 'Save' : 'Send'}
        </button>
      </div>
      {showCounter && (
        <div className={`byte-counter${tooLong ? ' over' : ''}`} role={tooLong ? 'alert' : undefined}>
          {bytes} / {MESSAGE_MAX_BYTES} bytes{tooLong && ' — Message is too long (max 3 KB)'}
        </div>
      )}
    </div>
  );
}
