import type { Presence } from '../api/auth';

const LABEL: Record<Presence, string> = { online: 'online', AFK: 'AFK', offline: 'offline' };
const GLYPH: Record<Presence, string> = { online: '●', AFK: '◐', offline: '○' };

export function PresenceDot({ presence }: { presence: Presence }) {
  return (
    <span className={`presence ${presence}`} title={LABEL[presence]} aria-label={LABEL[presence]}>
      {GLYPH[presence]}
    </span>
  );
}
