import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { Outlet, useLocation, useMatch } from 'react-router-dom';
import { fetchRoom, roomKeys } from '../api/rooms';
import { useLayout } from '../stores/layout';
import { ContextPanel } from './ContextPanel';
import { NavSidebar } from './NavSidebar';

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

/** Main Chat Layout (FRONTEND_SPEC §4.1): nav | chat | context, the context panel only when a room
 * the user belongs to is open. */
export function ChatLayout() {
  const match = useMatch('/rooms/:roomId');
  const roomId = match ? parseRoomId(match.params.roomId) : null;
  const detail = useRoomDetail(roomId);
  const { navOpen, ctxOpen, setNavOpen, setCtxOpen } = useLayout();
  const location = useLocation();

  useEffect(() => {
    setNavOpen(false);
    setCtxOpen(false);
  }, [location.pathname, setNavOpen, setCtxOpen]);

  const room = roomId !== null && detail.data?.my_role ? detail.data : null;
  const classes = ['chat-layout', room ? 'with-ctx' : '', navOpen ? 'nav-open' : '', ctxOpen ? 'ctx-open' : '']
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} data-mode={match ? 'compact' : 'expanded'}>
      <NavSidebar activeRoomId={match ? roomId : null} />
      <section className="chat-main">
        <Outlet />
      </section>
      {room && <ContextPanel room={room} />}
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
