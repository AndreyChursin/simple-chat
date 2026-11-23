import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { Outlet, useLocation, useMatch } from 'react-router-dom';
import { contactKeys, fetchDm } from '../api/contacts';
import { fetchRoom, roomKeys } from '../api/rooms';
import { useLayout } from '../stores/layout';
import { ContextPanel } from './ContextPanel';
import { NavSidebar, type ActiveChat } from './NavSidebar';
import { PeerPanel } from './PeerPanel';

export function parseRoomId(raw: string | undefined): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

/** Hook for the open room's detail; shared by the layout (context panel) and the room screen. */
export function useRoomDetail(roomId: number | null) {
  return useQuery({
    queryKey: roomKeys.detail(roomId ?? 0),
    queryFn: () => fetchRoom(roomId ?? 0),
    enabled: roomId !== null,
  });
}

export function useDmDetail(dmId: number | null) {
  return useQuery({ queryKey: contactKeys.dm(dmId ?? 0), queryFn: () => fetchDm(dmId ?? 0), enabled: dmId !== null });
}

/** Main Chat Layout (FRONTEND_SPEC §4.1): nav | chat | context. The context panel shows the members of
 * a room the user belongs to, or the peer card of a DM. */
export function ChatLayout() {
  const roomMatch = useMatch('/rooms/:roomId');
  const dmMatch = useMatch('/dm/:dmId');
  const roomId = roomMatch ? parseRoomId(roomMatch.params.roomId) : null;
  const dmId = dmMatch ? parseRoomId(dmMatch.params.dmId) : null;
  const detail = useRoomDetail(roomId);
  const dmDetail = useDmDetail(dmId);
  const { navOpen, ctxOpen, setNavOpen, setCtxOpen } = useLayout();
  const location = useLocation();

  useEffect(() => {
    setNavOpen(false);
    setCtxOpen(false);
  }, [location.pathname, setNavOpen, setCtxOpen]);

  const room = roomId !== null && detail.data?.my_role ? detail.data : null;
  const dm = dmId !== null ? (dmDetail.data ?? null) : null;
  const active: ActiveChat | null =
    roomId !== null ? { type: 'room', id: roomId } : dmId !== null ? { type: 'dm', id: dmId } : null;
  const classes = ['chat-layout', room || dm ? 'with-ctx' : '', navOpen ? 'nav-open' : '', ctxOpen ? 'ctx-open' : '']
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} data-mode={active ? 'compact' : 'expanded'}>
      <NavSidebar active={active} />
      <section className="chat-main">
        <Outlet />
      </section>
      {room && <ContextPanel room={room} />}
      {dm && <PeerPanel dm={dm} />}
      {(navOpen || ctxOpen) && (
        <div
          className="drawer-backdrop"
          onClick={() => {
            setNavOpen(false);
            setCtxOpen(false);
          }}
        />
      )}
    </div>
  );
}
