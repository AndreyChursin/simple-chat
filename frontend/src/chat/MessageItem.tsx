import { memo } from 'react';
import { MessageAttachments } from './Attachments';
import type { ChatMessage } from './messagesStore';
import { dayLabel, initials, isGrouped, linkify, sameDay, timeOf } from './format';

export interface MessageActions {
  onReply: (m: ChatMessage) => void;
  onEdit: (m: ChatMessage) => void;
  onDelete: (m: ChatMessage) => void;
  onJump: (messageId: number) => void;
  onRetry: (m: ChatMessage) => void;
  onDiscard: (m: ChatMessage) => void;
}

interface Props extends MessageActions {
  message: ChatMessage;
  prev: ChatMessage | undefined;
  myId: number;
  canModerate: boolean;
  readOnly: boolean;
  highlighted: boolean;
}

function MessageItemImpl({ message: m, prev, myId, canModerate, readOnly, highlighted, ...actions }: Props) {
  const own = m.author.id === myId;
  const deletedAuthor = m.author.id === null;
  const grouped = isGrouped(prev, m);
  const newDay = !prev || !sameDay(prev.created_at, m.created_at);
  const live = m.id > 0 && !m.deleted;
  const canEdit = live && own && !readOnly;
  const canDelete = live && !readOnly && (own || canModerate);

  return (
    <div className="msg-wrap" data-message-id={m.id}>
      {newDay && (
        <div className="date-sep" role="separator">
          <span>{dayLabel(m.created_at)}</span>
        </div>
      )}
      <article
        className={[
          'msg',
          grouped && !newDay ? 'grouped' : '',
          m.pending ? `pending-${m.pending}` : '',
          highlighted ? 'highlight' : '',
        ].join(' ')}
        aria-label={`${m.author.username} at ${timeOf(m.created_at)}`}
      >
        {!(grouped && !newDay) && (
          <header className="msg-head">
            <span className="avatar" aria-hidden>{deletedAuthor ? '?' : initials(m.author.username)}</span>
            <span className={`msg-author${deletedAuthor ? ' deleted-user' : ''}`}>{m.author.username}</span>
            <time dateTime={m.created_at} title={new Date(m.created_at).toLocaleString()}>{timeOf(m.created_at)}</time>
          </header>
        )}
        {m.reply_to && !m.deleted && (
          <button className="msg-quote" onClick={() => m.reply_to && actions.onJump(m.reply_to.id)}>
            <strong>{m.reply_to.author.username}</strong>
            <span>{m.reply_to.deleted ? <em>Original message deleted</em> : m.reply_to.excerpt}</span>
          </button>
        )}
        {(m.deleted || m.content || m.edited_at) && (
          <div className="msg-body">
            {m.deleted ? <em className="muted">Message deleted</em> : linkify(m.content)}
            {m.edited_at && !m.deleted && <span className="edited" title={new Date(m.edited_at).toLocaleString()}> edited</span>}
          </div>
        )}
        {!m.deleted && <MessageAttachments attachments={m.attachments} />}
        {m.pending === 'failed' && (
          <div className="msg-failed" role="alert">
            {m.error ?? 'Not sent'}{' '}
            <button className="btn small" onClick={() => actions.onRetry(m)}>Retry</button>{' '}
            <button className="btn small" onClick={() => actions.onDiscard(m)}>Delete</button>
          </div>
        )}
        {m.id > 0 && !m.deleted && (
          <div className="msg-menu" role="toolbar" aria-label="Message actions">
            {!readOnly && <button className="btn ghost small" onClick={() => actions.onReply(m)}>Reply</button>}
            {canEdit && <button className="btn ghost small" onClick={() => actions.onEdit(m)}>Edit</button>}
            {canDelete && <button className="btn ghost small" onClick={() => actions.onDelete(m)}>Delete</button>}
            <button className="btn ghost small" onClick={() => void navigator.clipboard?.writeText(m.content)}>
              Copy text
            </button>
          </div>
        )}
      </article>
    </div>
  );
}

export const MessageItem = memo(MessageItemImpl);
