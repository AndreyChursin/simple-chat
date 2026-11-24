import { useQuery } from '@tanstack/react-query';
import { useState, type KeyboardEvent } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { contactKeys, fetchContacts } from '../api/contacts';
import { fetchMyRooms, roomKeys, type RoomSummary } from '../api/rooms';
import { UserPresence } from '../components/Presence';
import { useUnread } from '../stores/unread';
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
  const live = useUnread((s) => s.byChat);
  // Live counters (WS) win over the REST snapshot of /rooms/mine and /contacts.
  const roomUnread = (r: RoomSummary) => live[`room:${r.id}`] ?? r.unread;
  const dmUnread = (dmId: number | null, rest: number) => (dmId === null ? 0 : (live[`dm:${dmId}`] ?? rest));

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

  // Per-section sums (live counters over the REST snapshot), unfiltered by the search box.
  const sectionUnread: Record<NavSection, number> = {
    public: publicRooms.reduce((sum, r) => sum + roomUnread(r), 0),
    private: privateRooms.reduce((sum, r) => sum + roomUnread(r), 0),
    contacts:
      (contacts.data?.friends ?? []).reduce((sum, f) => sum + dmUnread(f.dm_id, f.unread), 0) +
      (contacts.data?.past_dms ?? []).reduce((sum, d) => sum + dmUnread(d.dm_id, d.unread), 0),
  };

  if (collapsed) {
    const ICONS: Record<NavSection, string> = { public: '🌐', private: '🔒', contacts: '👥' };
    return (
      <nav className="nav-sidebar collapsed" aria-label="Chats">
        <button className="btn ghost small" aria-label="Expand navigation" onClick={() => setCollapsedPersist(false)}>
          »
        </button>
        {(['public', 'private', 'contacts'] as NavSection[]).map((section) => (
          <button key={section} className="btn ghost small nav-icon" title={TITLES[section]}
            aria-label={`${TITLES[section]}${sectionUnread[section] ? `, ${unreadBadge(sectionUnread[section])} unread` : ''}`}
            onClick={() => {
              setCollapsedPersist(false);
              if (!isOpen(section)) toggle(section);
            }}>
            <span aria-hidden>{ICONS[section]}</span>
            {sectionUnread[section] > 0 && <span className="badge">{unreadBadge(sectionUnread[section])}</span>}
          </button>
        ))}
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
        ? friends.reduce((sum, f) => sum + dmUnread(f.dm_id, f.unread), 0) +
          past.reduce((sum, d) => sum + dmUnread(d.dm_id, d.unread), 0)
        : rooms.reduce((sum, r) => sum + roomUnread(r), 0);
    return (
      <section key={section} className="nav-section" data-section={section} data-open={open}>
        <button className="nav-section-header" aria-expanded={open} onClick={() => toggle(section)}>
          <span aria-hidden>{open ? '▾' : '▸'}</span> {TITLES[section]}
          {unread > 0 && <span className="badge">{unreadBadge(unread)}</span>}
        </button>
        {open && section !== 'contacts' && (
          <ul className="nav-list">
            {rooms.map((r) => {
              const n = roomUnread(r);
              return (
                <li key={r.id}>
                  <NavLink to={`/rooms/${r.id}`} className={n > 0 ? 'unread' : undefined}>
                    <span className="nav-item-name">#{r.name}</span>
                    {n > 0 && <span className="badge">{unreadBadge(n)}</span>}
                  </NavLink>
                </li>
              );
            })}
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
              {friends.map((f) => {
                const n = dmUnread(f.dm_id, f.unread);
                return (
                  <li key={f.id ?? f.username}>
                    {f.dm_id !== null ? (
                      <NavLink to={`/dm/${f.dm_id}`} className={n > 0 ? 'unread' : undefined}>
                        <span className="nav-item-name">
                          <UserPresence user={f} /> {f.username}
                        </span>
                        {n > 0 && <span className="badge">{unreadBadge(n)}</span>}
                      </NavLink>
                    ) : (
                      <span className="nav-item-name">
                        <UserPresence user={f} /> {f.username}
                      </span>
                    )}
                  </li>
                );
              })}
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
                    {past.map((d) => {
                      const n = dmUnread(d.dm_id, d.unread);
                      return (
                        <li key={d.dm_id}>
                          <NavLink to={`/dm/${d.dm_id}`} className={n > 0 ? 'unread' : undefined}>
                            <span className="nav-item-name">
                              <span aria-hidden>🔒</span>{' '}
                              {d.peer.id === null ? <em>{d.peer.username}</em> : d.peer.username}
                            </span>
                            {n > 0 && <span className="badge">{unreadBadge(n)}</span>}
                          </NavLink>
                        </li>
                      );
                    })}
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
