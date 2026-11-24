import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { contactKeys, fetchProfile, openDm } from '../api/contacts';
import { useMe } from '../auth/session';
import { FriendRequestForm } from '../pages/ContactsPage';
import { forwardRef, useState, type HTMLAttributes, type Ref } from 'react';
import { Virtuoso } from 'react-virtuoso';
import { leaveRoom, type RoomDetail, type RoomMember } from '../api/rooms';
import { apiErrorToFields, FormError } from '../components/forms';
import type { Presence } from '../api/auth';
import { UserPresence } from '../components/Presence';
import { PRESENCE_RANK, usePresenceStore } from '../stores/presence';
import { InviteUserModal } from '../rooms/InviteUserModal';
import { BanConfirm, ManageRoomModal } from '../rooms/ManageRoomModal';
import { useModeration, useRoomMembers } from '../rooms/moderation';
import { useRoomGone } from '../rooms/useRoomGone';
import { useLayout } from '../stores/layout';

const MEMBER_SEARCH_THRESHOLD = 50;

/** online → AFK → offline, then username; re-sorted live as presence changes. */
export function sortByPresence(members: RoomMember[], live: Record<number, Presence>): RoomMember[] {
  const presenceOf = (m: RoomMember) => (m.id !== null ? live[m.id] : undefined) ?? m.presence;
  return [...members].sort(
    (a, b) =>
      PRESENCE_RANK[presenceOf(a)] - PRESENCE_RANK[presenceOf(b)] ||
      a.username.localeCompare(b.username, undefined, { sensitivity: 'base' }),
  );
}

/** Popover of a member (FRONTEND_SPEC §4.5): friend request or a link to the DM; for admins and the owner
 * also Make admin (owner) / Remove admin / Ban from room. The owner is never a moderation target. */
function MemberCard({ member, room, onClose }: { member: RoomMember; room: RoomDetail; onClose: () => void }) {
  const profile = useQuery({ queryKey: contactKeys.profile(member.username), queryFn: () => fetchProfile(member.username) });
  const navigate = useNavigate();
  const message = useMutation({
    mutationFn: () => openDm(member.id!),
    onSuccess: (dm) => navigate(`/dm/${dm.dm_id}`),
  });
  const rel = profile.data;
  const { grant, revoke, ban } = useModeration(room.id);
  const [confirmBan, setConfirmBan] = useState(false);
  const canModerate = (room.my_role === 'owner' || room.my_role === 'admin') && member.role !== 'owner';
  const modError = grant.error ?? revoke.error ?? ban.error;
  return (
    <div className="member-card" role="dialog" aria-label={`Actions for ${member.username}`}>
      <div className="member-card-head">
        <strong>{member.username}</strong>
        {member.display_name && <span className="muted"> {member.display_name}</span>}
        <button className="btn ghost small" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      {!rel ? (
        <p className="muted small">Loading…</p>
      ) : rel.banned_by_me || rel.has_banned_me ? (
        <p className="muted small">{rel.banned_by_me ? 'You blocked this user.' : 'This user blocked you.'}</p>
      ) : rel.friendship === 'friends' ? (
        <button className="btn small primary" disabled={message.isPending} onClick={() => message.mutate()}>Message</button>
      ) : rel.friendship === 'pending_out' ? (
        <p className="muted small">Friend request sent.</p>
      ) : rel.friendship === 'pending_in' ? (
        <p className="small">
          They sent you a friend request — <Link to="/contacts?tab=requests">review it</Link>.
        </p>
      ) : (
        <FriendRequestForm initialUsername={member.username} />
      )}
      {canModerate && (
        <div className="member-card-mod">
          <FormError message={modError ? apiErrorToFields(modError).form : null} />
          <div className="row-actions">
            {room.my_role === 'owner' && member.role === 'member' && (
              <button className="btn small" disabled={grant.isPending} onClick={() => grant.mutate(member.id!)}>
                Make admin
              </button>
            )}
            {member.role === 'admin' && (
              <button className="btn small" disabled={revoke.isPending} onClick={() => revoke.mutate(member.id!)}>
                Remove admin
              </button>
            )}
            {!confirmBan && (
              <button className="btn small danger-outline" onClick={() => setConfirmBan(true)}>Ban from room</button>
            )}
          </div>
          {confirmBan && (
            <BanConfirm username={member.username} busy={ban.isPending} onCancel={() => setConfirmBan(false)}
              onConfirm={(reason) => ban.mutate({ userId: member.id!, reason }, { onSuccess: onClose })} />
          )}
        </div>
      )}
    </div>
  );
}

/** Above this a group is virtualized (rooms hold up to 1000 members); small ones render plainly. */
const VIRTUALIZE_FROM = 100;

// Virtuoso types its list as a div; a <ul> keeps the list semantics of the small groups.
const MemberUl = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>((props, ref) => (
  <ul {...(props as unknown as HTMLAttributes<HTMLUListElement>)} ref={ref as unknown as Ref<HTMLUListElement>} className="member-list" />
));
MemberUl.displayName = 'MemberUl';

function MemberList({ title, members, myId, room, scrollParent }: {
  title: string;
  members: RoomMember[];
  myId: number | undefined;
  room: RoomDetail;
  scrollParent: HTMLElement | null;
}) {
  const live = usePresenceStore((s) => s.byUser);
  const [open, setOpen] = useState<number | null>(null);
  members = sortByPresence(members, live);
  if (!members.length) return null;
  const row = (m: RoomMember) => (
    <>
      <UserPresence user={m} />
      {m.id !== null && m.id !== myId ? (
        <button className="member-name link-button" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}>
          {m.username}
        </button>
      ) : (
        <span className="member-name">{m.username}</span>
      )}
      {m.display_name && <span className="muted member-display"> {m.display_name}</span>}
      {open === m.id && <MemberCard member={m} room={room} onClose={() => setOpen(null)} />}
    </>
  );
  return (
    <section className="ctx-group">
      <h3>
        {title} <span className="muted">({members.length})</span>
      </h3>
      {members.length >= VIRTUALIZE_FROM && scrollParent ? (
        <Virtuoso
          customScrollParent={scrollParent}
          data={members}
          computeItemKey={(_, m) => m.id ?? m.username}
          components={{ List: MemberUl, Item: (props) => <li {...props} /> }}
          itemContent={(_, m) => row(m)}
        />
      ) : (
        <ul className="member-list">
          {members.map((m) => (
            <li key={m.id ?? m.username}>{row(m)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Right panel of a room (FRONTEND_SPEC §4.5): info, owner, admins, members, actions. */
export function ContextPanel({ room }: { room: RoomDetail }) {
  const roomGone = useRoomGone();
  const me = useMe().data;
  const setCtxOpen = useLayout((s) => s.setCtxOpen);
  const [dialog, setDialog] = useState<'invite' | 'manage' | null>(null);
  const [panel, setPanel] = useState<HTMLElement | null>(null);
  const [filter, setFilter] = useState('');
  const members = useRoomMembers(room.id);
  const leave = useMutation({
    mutationFn: () => leaveRoom(room.id),
    onSuccess: () => roomGone(room.id, `You left #${room.name}`),
  });

  const all = members.data ?? [];
  const q = filter.trim().toLowerCase();
  const visible = q ? all.filter((m) => m.username.toLowerCase().includes(q)) : all;
  const isAdmin = room.my_role === 'owner' || room.my_role === 'admin';

  return (
    <aside className="ctx-panel" aria-label="Room details" ref={setPanel}>
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
      <MemberList title="Owner" room={room} scrollParent={panel} myId={me?.id} members={visible.filter((m) => m.role === 'owner')} />
      <MemberList title="Admins" room={room} scrollParent={panel} myId={me?.id} members={visible.filter((m) => m.role === 'admin')} />
      <MemberList title="Members" room={room} scrollParent={panel} myId={me?.id} members={visible.filter((m) => m.role === 'member')} />

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
      {dialog === 'manage' && isAdmin && <ManageRoomModal room={room} onClose={() => setDialog(null)} />}
    </aside>
  );
}
