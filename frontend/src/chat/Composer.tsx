import {
  forwardRef,
  lazy,
  Suspense,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from 'react';
import {
  cancelAttachment,
  formatSize,
  MAX_ATTACHMENTS,
  pastedName,
  precheck,
  setAttachmentComment,
  uploadAttachment,
  type Attachment,
} from '../api/attachments';
import { byteLength, MESSAGE_MAX_BYTES, type ChatRef } from '../api/messages';
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

/** One file being attached: uploads right away, sent with the message once done. */
export interface Chip {
  key: number;
  name: string;
  size: number;
  previewUrl: string | null;
  progress: number;
  status: 'uploading' | 'done' | 'error';
  error?: string;
  attachment?: Attachment;
  comment: string;
  abort?: () => void;
}

export interface ComposerHandle {
  addFiles: (files: File[]) => void;
}

let chipSeq = 0;

interface ComposerProps {
  chat: ChatRef;
  replyTo: ChatMessage | null;
  editing: ChatMessage | null;
  onSend: (text: string, attachments: Attachment[]) => void;
  onSaveEdit: (message: ChatMessage, text: string) => void;
  onCancel: () => void;
  onEditLast: () => void;
  onTyping: () => void;
}

/** FRONTEND_SPEC §4.4: multiline, Enter sends, Shift+Enter newline, Esc cancels reply/edit, ↑ edits
 * the last own message, byte counter, emoji, attachments (button, Ctrl+V, drag-and-drop via `addFiles`). */
export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer(
  { chat, replyTo, editing, onSend, onSaveEdit, onCancel, onEditLast, onTyping },
  ref,
) {
  const [text, setText] = useState('');
  const [chips, setChips] = useState<Chip[]>([]);
  const chipsRef = useRef(chips);
  chipsRef.current = chips;
  const fileInput = useRef<HTMLInputElement>(null);
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

  // Leaving the chat (unmount) cancels unsent uploads: they belong to this chat only.
  useEffect(
    () => () => {
      for (const chip of chipsRef.current) {
        chip.abort?.();
        if (chip.attachment) void cancelAttachment(chip.attachment.id).catch(() => {});
        if (chip.previewUrl) URL.revokeObjectURL(chip.previewUrl);
      }
    },
    [],
  );

  const patchChip = (key: number, patch: Partial<Chip>) =>
    setChips((list) => list.map((c) => (c.key === key ? { ...c, ...patch } : c)));

  function addFiles(files: File[]) {
    const room = MAX_ATTACHMENTS - chipsRef.current.length;
    const accepted = files.slice(0, Math.max(0, room));
    const fresh: Chip[] = accepted.map((file) => {
      const key = ++chipSeq;
      const name = pastedName(file);
      const problem = precheck(file);
      const previewUrl = file.type.startsWith('image/') && !problem ? URL.createObjectURL(file) : null;
      const chip: Chip = { key, name, size: file.size, previewUrl, progress: 0, status: problem ? 'error' : 'uploading', error: problem ?? undefined, comment: '' };
      if (!problem) {
        const upload = uploadAttachment(chat, file, name, (f) => patchChip(key, { progress: f }));
        chip.abort = upload.abort;
        upload.promise.then(
          (attachment) => patchChip(key, { status: 'done', progress: 1, attachment, abort: undefined }),
          (err: { code?: string; message?: string }) => {
            if (err.code !== 'aborted') patchChip(key, { status: 'error', error: err.message ?? 'Upload failed', abort: undefined });
          },
        );
      }
      return chip;
    });
    if (files.length > accepted.length) {
      fresh.push({ key: ++chipSeq, name: `${files.length - accepted.length} more`, size: 0, previewUrl: null, progress: 0, status: 'error', error: `At most ${MAX_ATTACHMENTS} attachments per message`, comment: '' });
    }
    setChips((list) => [...list, ...fresh]);
  }

  useImperativeHandle(ref, () => ({ addFiles }));

  function removeChip(chip: Chip) {
    chip.abort?.();
    if (chip.attachment) void cancelAttachment(chip.attachment.id).catch(() => {});
    if (chip.previewUrl) URL.revokeObjectURL(chip.previewUrl);
    setChips((list) => list.filter((c) => c.key !== chip.key));
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const items = Array.from(e.clipboardData?.items ?? []);
    const files = items.filter((i) => i.kind === 'file').map((i) => i.getAsFile()).filter((f): f is File => f !== null);
    const fallback = files.length ? files : Array.from(e.clipboardData?.files ?? []);
    if (!fallback.length) return; // plain text pastes as usual
    e.preventDefault();
    addFiles(fallback);
  }

  const { bytes, showCounter, tooLong, empty } = composerLimits(text);
  const uploading = chips.some((c) => c.status === 'uploading');
  const ready = chips.filter((c) => c.status === 'done');
  const canSubmit = editing
    ? !tooLong && (!empty || editing.attachments.length > 0)
    : !tooLong && !uploading && (!empty || ready.length > 0);

  function submit() {
    if (!canSubmit) return;
    const message = text;
    lastTyping.current = 0; // the next message's typing is announced at once
    setText('');
    setEmojiOpen(false);
    if (editing) {
      onSaveEdit(editing, message);
      return;
    }
    const sent = ready;
    for (const c of chips) if (c.previewUrl) URL.revokeObjectURL(c.previewUrl);
    setChips([]);
    const changed = sent.filter((c) => (c.attachment!.comment ?? '') !== c.comment.trim());
    if (!changed.length) {
      onSend(message, sent.map((c) => c.attachment!));
      return;
    }
    // comments typed after the upload finished are saved before sending
    void Promise.all(
      sent.map((c) =>
        changed.includes(c)
          ? setAttachmentComment(c.attachment!.id, c.comment).catch(() => c.attachment!)
          : Promise.resolve(c.attachment!),
      ),
    ).then((attachments) => onSend(message, attachments));
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
                Replying to: <strong>{replyTo.author.username}</strong> —{' '}
                <span className="muted">{replyTo.content.slice(0, REPLY_PREVIEW_CHARS)}</span>
              </span>
            )
          )}
          <button className="btn ghost small" aria-label={editing ? 'Cancel editing' : 'Cancel reply'} onClick={onCancel}>
            ✕
          </button>
        </div>
      )}
      {chips.length > 0 && (
        <ul className="chips" aria-label="Attachments">
          {chips.map((c) => (
            <li key={c.key} className={`chip ${c.status}`}>
              {c.previewUrl ? <img src={c.previewUrl} alt="" className="chip-thumb" /> : <span className="chip-icon" aria-hidden>📄</span>}
              <div className="chip-main">
                <span className="chip-name" title={c.name}>{c.name}</span>
                <span className="chip-meta">
                  {c.status === 'error' ? <span className="chip-error" role="alert">{c.error}</span> : c.size ? formatSize(c.size) : ''}
                  {c.status === 'uploading' && ` · ${Math.round(c.progress * 100)}%`}
                </span>
                {c.status === 'uploading' && (
                  <progress className="chip-progress" max={1} value={c.progress} aria-label={`Uploading ${c.name}`} />
                )}
                {c.status === 'done' && (
                  <input className="chip-comment" placeholder="Add comment" aria-label={`Comment for ${c.name}`} maxLength={1000}
                    value={c.comment} onChange={(e) => patchChip(c.key, { comment: e.target.value })} />
                )}
              </div>
              <button className="btn ghost small" aria-label={`Remove ${c.name}`} onClick={() => removeChip(c)}>✕</button>
            </li>
          ))}
        </ul>
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
        {!editing && (
          <>
            <button className="btn ghost composer-icon" aria-label="Attach files" title="Attach files"
              onClick={() => fileInput.current?.click()} disabled={chips.length >= MAX_ATTACHMENTS}>
              📎
            </button>
            <input ref={fileInput} type="file" multiple hidden data-testid="file-input"
              onChange={(e) => {
                addFiles(Array.from(e.target.files ?? []));
                e.target.value = '';
              }} />
          </>
        )}
        <textarea
          ref={area}
          className="composer-input"
          aria-label="Message"
          placeholder="Write a message…"
          rows={1}
          value={text}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
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
});
