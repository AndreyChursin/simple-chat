import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { fetchAllMembers, leaveRoom, roomKeys, type RoomDetail, type RoomMember } from '../api/rooms';
import { apiErrorToFields, FormError } from '../components/forms';
import { PresenceDot } from '../components/Presence';
import { InviteUserModal } from '../rooms/InviteUserModal';
import { ManageRoomModal } from '../rooms/ManageRoomModal';
import { useRoomGone } from '../rooms/useRoomGone';
import { useLayout } from '../stores/layout';

const MEMBER_SEARCH_THRESHOLD = 50;

function MemberList({ title, members }: { title: string; members: RoomMember[] }) {
  if (!members.length) return null;
  return (
    <section className="ctx-group">
      <h3>
        {title} <span className="muted">({members.length})</span>
      </h3>
      <ul className="member-list">
        {members.map((m) => (
          <li key={m.id ?? m.username}>
            <PresenceDot presence={m.presence} />
            <span className="member-name">{m.username}</span>
            {m.display_name && <span className="muted member-display"> {m.display_name}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Right panel of a room (FRONTEND_SPEC §4.5): info, owner, admins, members, actions. */
export function ContextPanel({ room }: { room: RoomDetail }) {
  const roomGone = useRoomGone();
  const setCtxOpen = useLayout((s) => s.setCtxOpen);
  const [dialog, setDialog] = useState<'invite' | 'manage' | null>(null);
  const [filter, setFilter] = useState('');
  const members = useQuery({ queryKey: roomKeys.members(room.id), queryFn: () => fetchAllMembers(room.id) });
  const leave = useMutation({
    mutationFn: () => leaveRoom(room.id),
    onSuccess: () => roomGone(room.id, `You left #${room.name}`),
  });

  const all = members.data ?? [];
  const q = filter.trim().toLowerCase();
  const visible = q ? all.filter((m) => m.username.toLowerCase().includes(q)) : all;
  const isAdmin = room.my_role === 'owner' || room.my_role === 'admin';

  return (
    <aside className="ctx-panel" aria-label="Room details">
      <button className="btn ghost small ctx-close" aria-label="Close details" onClick={() => setCtxOpen(false)}>
        ✕
      </button>
      <section className="ctx-group">
        <h2 className="ctx-title">
          <span title={room.visibility === 'public' ? 'Public room' : 'Private room'}>
            {room.visibility === 'public' ? '🌐' : '🔒'}
          </span>{' '}
          #{room.name}
        </h2>
        {room.description && <p className="ctx-description">{room.description}</p>}
        <p className="muted small">Created {new Date(room.created_at).toLocaleDateString()}</p>
      </section>

      <div className="ctx-actions">
        <button className="btn small" onClick={() => setDialog('invite')}>Invite user</button>
        {isAdmin && (
          <button className="btn small" onClick={() => setDialog('manage')}>Manage room</button>
        )}
      </div>

      {all.length > MEMBER_SEARCH_THRESHOLD && (
        <input className="nav-search" type="search" placeholder="Search members…" aria-label="Search members"
          value={filter} onChange={(e) => setFilter(e.target.value)} />
      )}
      {members.isPending && <p className="muted">Loading members…</p>}
      <MemberList title="Owner" members={visible.filter((m) => m.role === 'owner')} />
      <MemberList title="Admins" members={visible.filter((m) => m.role === 'admin')} />
      <MemberList title="Members" members={visible.filter((m) => m.role === 'member')} />

      <div className="ctx-footer">
        <FormError message={leave.error ? apiErrorToFields(leave.error).form : null} />
        {room.my_role === 'owner' ? (
          <p className="muted small">Owner can't leave — delete the room in Manage room → Settings.</p>
        ) : (
          <button className="btn small" disabled={leave.isPending} onClick={() => leave.mutate()}>
            Leave room
          </button>
        )}
      </div>

      {dialog === 'invite' && (
        <InviteUserModal roomId={room.id} roomName={room.name} onClose={() => setDialog(null)} />
      )}
      {dialog === 'manage' && <ManageRoomModal room={room} onClose={() => setDialog(null)} />}
    </aside>
  );
}
