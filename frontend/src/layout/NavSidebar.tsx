import { useQuery } from '@tanstack/react-query';
import { useState, type KeyboardEvent } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { contactKeys, fetchContacts } from '../api/contacts';
import { fetchMyRooms, roomKeys, type RoomSummary } from '../api/rooms';
import { UserPresence } from '../components/Presence';
import { CreateRoomModal } from '../rooms/CreateRoomModal';
import { useNavAccordion, type NavSection } from './navAccordion';

const COLLAPSED_KEY = 'chat.nav.collapsed';

const TITLES: Record<NavSection, string> = { public: 'Public Rooms', private: 'Private Rooms', contacts: 'Contacts' };

export function unreadBadge(n: number): string {
  return n > 99 ? '99+' : String(n);
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

export interface ActiveChat {
  type: 'room' | 'dm';
  id: number;
}

export function NavSidebar({ active }: { active: ActiveChat | null }) {
  const navigate = useNavigate();
  const mine = useQuery({ queryKey: roomKeys.mine, queryFn: fetchMyRooms });
  const contacts = useQuery({ queryKey: contactKeys.all, queryFn: fetchContacts });
  const [pastOpen, setPastOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [creating, setCreating] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);

  const publicRooms = mine.data?.public ?? [];
  const privateRooms = mine.data?.private ?? [];
  const activeSection: NavSection | null =
    active === null
      ? null
      : active.type === 'dm'
        ? 'contacts'
        : privateRooms.some((r) => r.id === active.id)
          ? 'private'
          : 'public';
  const { isOpen, toggle } = useNavAccordion(active === null ? 'expanded' : 'compact', activeSection);

  const q = search.trim().toLowerCase();
  const filter = (rooms: RoomSummary[]) => (q ? rooms.filter((r) => r.name.toLowerCase().includes(q)) : rooms);
  const lists: Record<'public' | 'private', RoomSummary[]> = { public: filter(publicRooms), private: filter(privateRooms) };
  const byName = <T extends { username: string }>(list: T[]) =>
    q ? list.filter((u) => u.username.toLowerCase().includes(q)) : list;
  const friends = byName(contacts.data?.friends ?? []);
  const past = (contacts.data?.past_dms ?? []).filter((d) => !q || d.peer.username.toLowerCase().includes(q));

  function setCollapsedPersist(value: boolean) {
    setCollapsed(value);
    try {
      localStorage.setItem(COLLAPSED_KEY, value ? '1' : '0');
    } catch {
      /* ignore */
    }
  }

  function onSearchKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && q && lists.public.length + lists.private.length + friends.length === 0) {
      navigate(`/rooms/catalog?search=${encodeURIComponent(search.trim())}`);
    }
  }

  const creationModal = creating && <CreateRoomModal onClose={() => setCreating(false)} />;

  if (collapsed) {
    return (
      <nav className="nav-sidebar collapsed" aria-label="Chats">
        <button className="btn ghost small" aria-label="Expand navigation" onClick={() => setCollapsedPersist(false)}>
          »
        </button>
        <button className="btn ghost small" aria-label="Create room" title="Create room" onClick={() => setCreating(true)}>
          ＋
        </button>
        {creationModal}
      </nav>
    );
  }

  const renderSection = (section: NavSection) => {
    const open = q ? true : isOpen(section);
    const rooms = section === 'contacts' ? [] : lists[section];
    const unread =
      section === 'contacts'
        ? friends.reduce((sum, f) => sum + f.unread, 0)
        : rooms.reduce((sum, r) => sum + r.unread, 0);
    return (
      <section key={section} className="nav-section" data-section={section} data-open={open}>
        <button className="nav-section-header" aria-expanded={open} onClick={() => toggle(section)}>
          <span aria-hidden>{open ? '▾' : '▸'}</span> {TITLES[section]}
          {unread > 0 && <span className="badge">{unreadBadge(unread)}</span>}
        </button>
        {open && section !== 'contacts' && (
          <ul className="nav-list">
            {rooms.map((r) => (
              <li key={r.id}>
                <NavLink to={`/rooms/${r.id}`} className={r.unread > 0 ? 'unread' : undefined}>
                  <span className="nav-item-name">#{r.name}</span>
                  {r.unread > 0 && <span className="badge">{unreadBadge(r.unread)}</span>}
                </NavLink>
              </li>
            ))}
            {rooms.length === 0 && (
              <li className="muted nav-empty">
                {q ? 'No matches' : section === 'public' ? <Link to="/rooms/catalog">Browse rooms</Link> : 'None yet'}
              </li>
            )}
          </ul>
        )}
        {open && section === 'contacts' && (
          <>
            <ul className="nav-list" aria-label="Friends">
              {friends.map((f) => (
                <li key={f.id ?? f.username}>
                  {f.dm_id !== null ? (
                    <NavLink to={`/dm/${f.dm_id}`} className={f.unread > 0 ? 'unread' : undefined}>
                      <span className="nav-item-name">
                        <UserPresence user={f} /> {f.username}
                      </span>
                      {f.unread > 0 && <span className="badge">{unreadBadge(f.unread)}</span>}
                    </NavLink>
                  ) : (
                    <span className="nav-item-name">
                      <UserPresence user={f} /> {f.username}
                    </span>
                  )}
                </li>
              ))}
              {friends.length === 0 && (
                <li className="muted nav-empty">{q ? 'No matches' : <Link to="/contacts">Add a friend</Link>}</li>
              )}
            </ul>
            {past.length > 0 && (
              <div className="nav-past">
                <button className="nav-subheader" aria-expanded={pastOpen || !!q} onClick={() => setPastOpen(!pastOpen)}>
                  <span aria-hidden>{pastOpen || q ? '▾' : '▸'}</span> Past conversations
                </button>
                {(pastOpen || !!q) && (
                  <ul className="nav-list" aria-label="Past conversations">
                    {past.map((d) => (
                      <li key={d.dm_id}>
                        <NavLink to={`/dm/${d.dm_id}`}>
                          <span className="nav-item-name">
                            <span aria-hidden>🔒</span>{' '}
                            {d.peer.id === null ? <em>{d.peer.username}</em> : d.peer.username}
                          </span>
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </section>
    );
  };

  return (
    <nav className="nav-sidebar" aria-label="Chats">
      <div className="nav-top">
        <input className="nav-search" type="search" placeholder="Search rooms and contacts…" aria-label="Search rooms"
          value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={onSearchKey} />
        <button className="btn ghost small" aria-label="Collapse navigation" onClick={() => setCollapsedPersist(true)}>
          «
        </button>
      </div>
      <div className="nav-sections">{(['public', 'private', 'contacts'] as NavSection[]).map(renderSection)}</div>
      <button className="btn wide" onClick={() => setCreating(true)}>
        + Create room
      </button>
      {creationModal}
    </nav>
  );
}
