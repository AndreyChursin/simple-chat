import type { Presence, UserPublic } from '../api/auth';
import { usePresence } from '../stores/presence';

const LABEL: Record<Presence, string> = { online: 'online', AFK: 'AFK', offline: 'offline' };
const GLYPH: Record<Presence, string> = { online: '●', AFK: '◐', offline: '○' };

export function PresenceDot({ presence }: { presence: Presence }) {
  return (
    <span className={`presence ${presence}`} title={LABEL[presence]} aria-label={LABEL[presence]}>
      {GLYPH[presence]}
    </span>
  );
}

/** Presence dot of a user, live from the WebSocket when known. */
export function UserPresence({ user }: { user: Pick<UserPublic, 'id' | 'presence'> }) {
  return <PresenceDot presence={usePresence(user)} />;
}
