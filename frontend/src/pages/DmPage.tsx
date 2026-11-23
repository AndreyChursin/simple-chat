import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { FROZEN_TEXT } from '../api/contacts';
import { useMe } from '../auth/session';
import { ChatWindow } from '../chat/ChatWindow';
import { UserPresence } from '../components/Presence';
import { parseRoomId, useDmDetail } from '../layout/ChatLayout';
import { usePresence } from '../stores/presence';
import { useLayout } from '../stores/layout';
import { NavToggle } from './HomePage';

/** A direct conversation: the chat window of a room, with the peer instead of members. */
export function DmPage() {
  const dmId = parseRoomId(useParams().dmId);
  const dm = useDmDetail(dmId);
  const me = useMe().data;
  const [searchOpen, setSearchOpen] = useState(false);
  const setCtxOpen = useLayout((s) => s.setCtxOpen);
  const peerPresence = usePresence(dm.data?.peer ?? { id: null, presence: 'offline' });

  if (dmId === null || dm.isError) {
    return (
      <div className="chat-empty">
        <NavToggle />
        <h1>Conversation not found</h1>
      </div>
    );
  }
  if (dm.isPending || !me) return <div className="chat-empty muted">Loading…</div>;
  const { peer } = dm.data;

  return (
    <div className="chat-window">
      <header className="chat-header">
        <NavToggle />
        <div className="chat-header-main">
          <h1>
            {peer.id !== null && !dm.data.frozen && <UserPresence user={peer} />}{' '}
            {peer.id === null ? <em>{peer.username}</em> : peer.username}
          </h1>
          <p className="chat-header-description">
            {peer.id === null ? 'Account deleted' : dm.data.frozen ? 'Read-only conversation' : peerPresence}
          </p>
        </div>
        <button className="btn ghost small" aria-label="Search messages" title="Search messages" onClick={() => setSearchOpen((v) => !v)}>
          🔍
        </button>
        <button className="btn ghost small ctx-toggle" aria-label="Show details" onClick={() => setCtxOpen(true)}>
          👤
        </button>
      </header>
      <ChatWindow
        chat={{ type: 'dm', id: dm.data.dm_id }}
        me={me}
        canModerate={false}
        readOnly={dm.data.frozen}
        frozenText={dm.data.frozen_reason ? FROZEN_TEXT[dm.data.frozen_reason] : undefined}
        searchOpen={searchOpen}
        onCloseSearch={() => setSearchOpen(false)}
      />
    </div>
  );
}
