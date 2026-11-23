import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMe } from '../auth/session';
import { CreateRoomModal } from '../rooms/CreateRoomModal';
import { useLayout } from '../stores/layout';

export function NavToggle() {
  const setNavOpen = useLayout((s) => s.setNavOpen);
  return (
    <button className="btn ghost small nav-toggle" aria-label="Open navigation" onClick={() => setNavOpen(true)}>
      ☰
    </button>
  );
}

/** Empty state of the Main Chat Layout: nothing open, nav expanded, no context panel. */
export function HomePage() {
  const me = useMe().data;
  const [creating, setCreating] = useState(false);
  return (
    <div className="chat-empty">
      <NavToggle />
      <h1>Welcome, {me?.display_name || me?.username}</h1>
      <p className="muted">Pick a room on the left, or:</p>
      <div className="quick-links">
        <Link className="btn" to="/rooms/catalog">Browse public rooms</Link>
        <button className="btn" onClick={() => setCreating(true)}>Create a room</button>
        <Link className="btn" to="/contacts">Add a friend</Link>
      </div>
      {creating && <CreateRoomModal onClose={() => setCreating(false)} />}
    </div>
  );
}
