import { useEffect, useRef, useState } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { useMe, useSignOut } from '../auth/session';
import { useContacts } from '../pages/ContactsPage';
import { useMyInvitations } from '../pages/PrivateRoomsPage';

/** Logo | Public Rooms | Private Rooms | Contacts | Sessions | Profile ▼ | Sign out (FRONTEND_SPEC §2). */
export function TopMenu() {
  const me = useMe().data;
  const signOut = useSignOut();
  const [open, setOpen] = useState(false);
  const invitationCount = useMyInvitations().data?.length ?? 0;
  const requestCount = useContacts().data?.incoming.length ?? 0;
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !menuRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);

  return (
    <header className="topmenu">
      <Link to="/" className="logo" aria-label="Home">
        💬 <span>Chat</span>
      </Link>
      <nav className="topmenu-links">
        <NavLink to="/rooms/catalog">Public Rooms</NavLink>
        <NavLink to="/rooms/private">
          Private Rooms
          {invitationCount > 0 && (
            <span className="badge" aria-label={`${invitationCount} pending invitations`}>
              {invitationCount}
            </span>
          )}
        </NavLink>
        <NavLink to="/contacts">
          Contacts
          {requestCount > 0 && (
            <span className="badge" aria-label={`${requestCount} friend requests`}>
              {requestCount}
            </span>
          )}
        </NavLink>
        <NavLink to="/sessions">Sessions</NavLink>
      </nav>
      <div className="topmenu-right">
        <div className="dropdown" ref={menuRef}>
          <button className="btn ghost" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
            {me?.display_name || me?.username} ▾
          </button>
          {open && (
            <div className="dropdown-menu" role="menu">
              <div className="dropdown-header">
                <strong>{me?.username}</strong>
                {me?.display_name && <span>{me.display_name}</span>}
              </div>
              <Link role="menuitem" to="/profile" onClick={() => setOpen(false)}>
                Profile settings
              </Link>
              <Link role="menuitem" to="/profile#password" onClick={() => setOpen(false)}>
                Change password
              </Link>
            </div>
          )}
        </div>
        <button className="btn ghost" onClick={() => void signOut()}>
          Sign out
        </button>
      </div>
    </header>
  );
}
