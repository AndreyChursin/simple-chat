import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import {
  acceptInvitation,
  declineInvitation,
  fetchMyInvitations,
  fetchMyRooms,
  roomKeys,
  type MyInvitation,
} from '../api/rooms';
import { apiErrorToFields, FormError } from '../components/forms';
import { CreateRoomModal } from '../rooms/CreateRoomModal';
import { toast } from '../stores/toasts';
import { useUnread } from '../stores/unread';
import { unreadBadge } from '../layout/NavSidebar';

export function useMyInvitations() {
  return useQuery({ queryKey: roomKeys.myInvitations, queryFn: fetchMyInvitations, staleTime: 0 });
}

/** Private Rooms (FRONTEND_SPEC §6): my private rooms + incoming invitations. */
export function PrivateRoomsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const mine = useQuery({ queryKey: roomKeys.mine, queryFn: fetchMyRooms });
  const invitations = useMyInvitations();
  const [creating, setCreating] = useState(false);

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: roomKeys.myInvitations });
    await qc.invalidateQueries({ queryKey: roomKeys.mine });
  };
  const accept = useMutation({
    mutationFn: (inv: MyInvitation) => acceptInvitation(inv.id),
    onSuccess: async (room) => {
      await refresh();
      navigate(`/rooms/${room.id}`);
    },
    onError: async (err) => {
      if (err instanceof ApiError && err.code === 'room_banned') toast('You are banned from this room', 'error');
      await refresh();
    },
  });
  const decline = useMutation({
    mutationFn: (inv: MyInvitation) => declineInvitation(inv.id),
    onSettled: refresh,
  });

  const rooms = mine.data?.private ?? [];
  const live = useUnread((s) => s.byChat);
  const invites = invitations.data ?? [];
  const error = accept.error ?? decline.error;

  return (
    <main className="page">
      <div className="page-header">
        <h1>Private rooms</h1>
        <button className="btn" onClick={() => setCreating(true)}>+ Create room</button>
      </div>

      <section className="card">
        <h2>
          Invitations {invites.length > 0 && <span className="badge">{invites.length}</span>}
        </h2>
        <FormError message={error && !(error instanceof ApiError && error.code === 'room_banned') ? apiErrorToFields(error).form : null} />
        {invitations.isPending ? (
          <p className="muted">Loading…</p>
        ) : invites.length === 0 ? (
          <p className="muted">No pending invitations.</p>
        ) : (
          <ul className="room-cards">
            {invites.map((inv) => (
              <li key={inv.id} className="room-card">
                <div className="room-card-main">
                  <h3>
                    {inv.room.visibility === 'private' ? '🔒' : '#'} {inv.room.name}
                  </h3>
                  {inv.room.description && <p className="room-card-description">{inv.room.description}</p>}
                  <small className="muted">
                    Invited by {inv.inviter.username} · {new Date(inv.created_at).toLocaleDateString()}
                  </small>
                </div>
                <div className="row-actions">
                  <button className="btn primary" disabled={accept.isPending} onClick={() => accept.mutate(inv)}>
                    Accept
                  </button>
                  <button className="btn" disabled={decline.isPending} onClick={() => decline.mutate(inv)}>
                    Decline
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>My private rooms</h2>
        {mine.isPending ? (
          <p className="muted">Loading…</p>
        ) : rooms.length === 0 ? (
          <p className="muted">You are not in any private room.</p>
        ) : (
          <ul className="room-cards">
            {rooms.map((r) => {
              const unread = live[`room:${r.id}`] ?? r.unread;
              return (
              <li key={r.id} className="room-card">
                <div className="room-card-main">
                  <h3>
                    🔒 {r.name}
                    {unread > 0 && <span className="badge" title="Unread messages">{unreadBadge(unread)}</span>}
                  </h3>
                  {r.description && <p className="room-card-description">{r.description}</p>}
                  <small className="muted">
                    {r.member_count} {r.member_count === 1 ? 'member' : 'members'} · {r.my_role}
                  </small>
                </div>
                <Link className="btn" to={`/rooms/${r.id}`}>Open</Link>
              </li>
              );
            })}
          </ul>
        )}
      </section>
      {creating && <CreateRoomModal defaultVisibility="private" onClose={() => setCreating(false)} />}
    </main>
  );
}
