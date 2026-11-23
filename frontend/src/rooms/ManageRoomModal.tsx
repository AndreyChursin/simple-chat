import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { fetchProfile } from '../api/contacts';
import {
  deleteRoom,
  fetchRoomInvitations,
  revokeInvitation,
  roomKeys,
  updateRoom,
  type RoomDetail,
  type RoomMember,
  type Visibility,
} from '../api/rooms';
import { useMe } from '../auth/session';
import { apiErrorToFields, Field, FormError, FormNotice } from '../components/forms';
import { Modal } from '../components/Modal';
import { UserPresence } from '../components/Presence';
import { usePresenceStore } from '../stores/presence';
import { InviteForm } from './InviteUserModal';
import { BAN_HINT, useModeration, useRoomBans, useRoomMembers } from './moderation';
import { ROOM_DESCRIPTION_MAX, ROOM_NAME_MAX, ROOM_NAME_MIN } from './CreateRoomModal';
import { useRoomGone } from './useRoomGone';

type Tab = 'members' | 'admins' | 'banned' | 'invitations' | 'settings';
const TABS: { id: Tab; label: string }[] = [
  { id: 'members', label: 'Members' },
  { id: 'admins', label: 'Admins' },
  { id: 'banned', label: 'Banned users' },
  { id: 'invitations', label: 'Invitations' },
  { id: 'settings', label: 'Settings' },
];

/** Manage Room (FRONTEND_SPEC §4.6), for admins and the owner. */
export function ManageRoomModal({ room, onClose }: { room: RoomDetail; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('members');
  return (
    <Modal title={`Manage #${room.name}`} onClose={onClose} wide>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'members' && <MembersTab room={room} />}
      {tab === 'admins' && <AdminsTab room={room} />}
      {tab === 'banned' && <BannedTab room={room} />}
      {tab === 'invitations' && <InvitationsTab room={room} />}
      {tab === 'settings' && <SettingsTab room={room} onClose={onClose} />}
    </Modal>
  );
}

const ROLE_LABEL = { owner: 'Owner', admin: 'Admin', member: 'Member' } as const;

/** Inline confirmation of a ban with an optional reason (no nested modal: Esc would close both). */
export function BanConfirm({ username, busy, onConfirm, onCancel }: {
  username: string;
  busy: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState('');
  const id = useId();
  return (
    <div className="confirm-box" role="group" aria-label={`Ban ${username}`}>
      <p>
        <strong>Ban {username}?</strong> {BAN_HINT}
      </p>
      <div className="field">
        <label htmlFor={id}>Reason (optional)</label>
        <input id={id} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <div className="row-actions">
        <button type="button" className="btn small" onClick={onCancel}>Cancel</button>
        <button type="button" className="btn small danger" disabled={busy} onClick={() => onConfirm(reason)}>
          Ban
        </button>
      </div>
    </div>
  );
}

function MembersTab({ room }: { room: RoomDetail }) {
  const me = useMe().data;
  const members = useRoomMembers(room.id);
  const live = usePresenceStore((s) => s.byUser);
  const { ban } = useModeration(room.id);
  const [filter, setFilter] = useState('');
  const [confirm, setConfirm] = useState<RoomMember | null>(null);
  const q = filter.trim().toLowerCase();
  const rows = (members.data ?? []).filter((m) => !q || m.username.toLowerCase().includes(q));
  const presenceOf = (m: RoomMember) => (m.id !== null ? live[m.id] : undefined) ?? m.presence;

  return (
    <div>
      <FormError message={ban.error ? apiErrorToFields(ban.error).form : null} />
      <input className="nav-search" type="search" placeholder="Search members…" aria-label="Search members"
        value={filter} onChange={(e) => setFilter(e.target.value)} />
      {confirm && (
        <BanConfirm username={confirm.username} busy={ban.isPending} onCancel={() => setConfirm(null)}
          onConfirm={(reason) => {
            ban.mutate({ userId: confirm.id!, reason });
            setConfirm(null);
          }} />
      )}
      {members.isPending ? (
        <p className="muted">Loading…</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>User</th><th>Status</th><th>Role</th><th>Joined</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id ?? m.username}>
                <td>
                  {m.username}
                  {m.display_name && <span className="muted"> · {m.display_name}</span>}
                </td>
                <td><UserPresence user={m} /> {presenceOf(m)}</td>
                <td>{ROLE_LABEL[m.role]}</td>
                <td>{new Date(m.joined_at).toLocaleDateString()}</td>
                <td className="right">
                  {m.role !== 'owner' && m.id !== null && m.id !== me?.id && (
                    <button className="btn small danger-outline" onClick={() => setConfirm(m)}>Remove (ban)</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AdminsTab({ room }: { room: RoomDetail }) {
  const isOwner = room.my_role === 'owner';
  const members = useRoomMembers(room.id);
  const { grant, revoke } = useModeration(room.id);
  const [candidate, setCandidate] = useState('');
  const all = members.data ?? [];
  const admins = all.filter((m) => m.role !== 'member');
  const candidates = all.filter((m) => m.role === 'member' && m.id !== null);
  const error = grant.error ?? revoke.error;

  return (
    <div>
      <FormError message={error ? apiErrorToFields(error).form : null} />
      {isOwner && (
        <form className="inline-form" onSubmit={(e) => {
          e.preventDefault();
          if (candidate) grant.mutate(Number(candidate), { onSuccess: () => setCandidate('') });
        }}>
          <label htmlFor="f-add-admin">Add admin</label>
          <select id="f-add-admin" value={candidate} onChange={(e) => setCandidate(e.target.value)}>
            <option value="">Choose a member…</option>
            {candidates.map((m) => <option key={m.id} value={m.id!}>{m.username}</option>)}
          </select>
          <button type="submit" className="btn small primary" disabled={!candidate || grant.isPending}>Make admin</button>
        </form>
      )}
      {members.isPending ? (
        <p className="muted">Loading…</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>User</th><th>Role</th><th /></tr>
          </thead>
          <tbody>
            {admins.map((m) => (
              <tr key={m.id ?? m.username}>
                <td><UserPresence user={m} /> {m.username}</td>
                <td>{ROLE_LABEL[m.role]}</td>
                <td className="right">
                  {m.role === 'admin' && m.id !== null && (
                    <button className="btn small" disabled={revoke.isPending} onClick={() => revoke.mutate(m.id!)}>
                      Remove admin
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function BannedTab({ room }: { room: RoomDetail }) {
  const bans = useRoomBans(room.id);
  const { ban, unban } = useModeration(room.id);
  const [username, setUsername] = useState('');
  const [reason, setReason] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const items = bans.data ?? [];

  async function onBan(e: FormEvent) {
    e.preventDefault();
    const name = username.trim();
    if (!name) return;
    setFormError(null);
    setNotice(null);
    setBusy(true);
    try {
      const profile = await fetchProfile(name);
      await ban.mutateAsync({ userId: profile.id!, reason });
      setNotice(`${profile.username} is banned`);
      setUsername('');
      setReason('');
    } catch (err) {
      setFormError(err instanceof ApiError && err.status === 404 ? 'User not found' : apiErrorToFields(err).form);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <form className="ban-form" onSubmit={onBan} noValidate>
        <h3>Ban user by username</h3>
        <FormError message={formError} />
        <FormNotice message={notice} />
        <Field label="Username" name="ban-username" value={username} maxLength={32}
          onChange={(e) => setUsername(e.target.value)} />
        <Field label="Reason (optional)" name="ban-reason" value={reason} maxLength={500}
          onChange={(e) => setReason(e.target.value)} />
        <button type="submit" className="btn danger" disabled={!username.trim() || busy}>Ban user</button>
      </form>
      <FormError message={unban.error ? apiErrorToFields(unban.error).form : null} />
      {bans.isPending ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">Nobody is banned.</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>User</th><th>Banned by</th><th>Date</th><th>Reason</th><th /></tr>
          </thead>
          <tbody>
            {items.map((b) => (
              <tr key={b.user.id ?? b.user.username}>
                <td>{b.user.username}</td>
                <td>{b.banned_by.id === null ? <em>{b.banned_by.username}</em> : b.banned_by.username}</td>
                <td>{new Date(b.banned_at).toLocaleString()}</td>
                <td>{b.reason ?? <span className="muted">—</span>}</td>
                <td className="right">
                  <button className="btn small" disabled={unban.isPending} onClick={() => unban.mutate(b.user.id!)}>
                    Unban
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function InvitationsTab({ room }: { room: RoomDetail }) {
  const qc = useQueryClient();
  const invitations = useQuery({
    queryKey: roomKeys.invitations(room.id),
    queryFn: () => fetchRoomInvitations(room.id),
    staleTime: 0,
  });
  const revoke = useMutation({
    mutationFn: (id: number) => revokeInvitation(room.id, id),
    onSettled: () => qc.invalidateQueries({ queryKey: roomKeys.invitations(room.id) }),
  });
  const items = invitations.data ?? [];
  return (
    <div>
      <InviteForm roomId={room.id} />
      <FormError message={revoke.error ? apiErrorToFields(revoke.error).form : null} />
      {invitations.isPending ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">No pending invitations.</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>Invitee</th><th>Invited by</th><th>Date</th><th /></tr>
          </thead>
          <tbody>
            {items.map((inv) => (
              <tr key={inv.id}>
                <td>{inv.invitee.username}</td>
                <td>{inv.inviter.username}</td>
                <td>{new Date(inv.created_at).toLocaleString()}</td>
                <td className="right">
                  <button className="btn small" disabled={revoke.isPending} onClick={() => revoke.mutate(inv.id)}>
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function SettingsTab({ room, onClose }: { room: RoomDetail; onClose: () => void }) {
  const qc = useQueryClient();
  const roomGone = useRoomGone();
  const isOwner = room.my_role === 'owner';
  const [name, setName] = useState(room.name);
  const [description, setDescription] = useState(room.description);
  const [visibility, setVisibility] = useState<Visibility>(room.visibility);
  const [nameError, setNameError] = useState<string | undefined>();
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmName, setConfirmName] = useState('');

  const save = useMutation({
    mutationFn: () => updateRoom(room.id, { name: name.trim(), description: description.trim(), visibility }),
    onMutate: () => {
      setNotice(null);
      setNameError(undefined);
    },
    onSuccess: async () => {
      setNotice('Settings saved');
      await qc.invalidateQueries({ queryKey: roomKeys.detail(room.id) });
      await qc.invalidateQueries({ queryKey: roomKeys.mine });
      await qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === 'room_name_taken') setNameError(err.message);
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteRoom(room.id),
    onSuccess: () => {
      onClose();
      roomGone(room.id, `Room #${room.name} was deleted`);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length < ROOM_NAME_MIN || trimmed.length > ROOM_NAME_MAX) {
      setNameError(`Name must be ${ROOM_NAME_MIN}–${ROOM_NAME_MAX} characters`);
      return;
    }
    save.mutate();
  }

  const saveError =
    save.error && !(save.error instanceof ApiError && save.error.code === 'room_name_taken')
      ? apiErrorToFields(save.error).form
      : null;

  return (
    <div>
      <form onSubmit={onSubmit} noValidate>
        {!isOwner && <p className="muted">Only the owner can change room settings.</p>}
        <FormError message={saveError} />
        <FormNotice message={notice} />
        <Field label="Name" name="room-name" value={name} maxLength={ROOM_NAME_MAX} disabled={!isOwner}
          onChange={(e) => setName(e.target.value)} error={nameError} />
        <div className="field">
          <label htmlFor="f-room-description">Description</label>
          <textarea id="f-room-description" rows={3} maxLength={ROOM_DESCRIPTION_MAX} value={description}
            disabled={!isOwner} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <fieldset className="segmented" disabled={!isOwner}>
          <legend>Visibility</legend>
          <label>
            <input type="radio" name="room-visibility" checked={visibility === 'public'}
              onChange={() => setVisibility('public')} /> 🌐 Public
          </label>
          <label>
            <input type="radio" name="room-visibility" checked={visibility === 'private'}
              onChange={() => setVisibility('private')} /> 🔒 Private
          </label>
        </fieldset>
        {isOwner && (
          <button type="submit" className="btn primary" disabled={save.isPending}>Save</button>
        )}
      </form>

      {isOwner && (
        <section className="danger-zone">
          <h3>Danger zone</h3>
          <p>All messages, files and images will be permanently deleted.</p>
          <FormError message={remove.error ? apiErrorToFields(remove.error).form : null} />
          <Field label={`Type the room name (${room.name}) to confirm`} name="confirm-room-name" value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)} />
          <button className="btn danger" disabled={confirmName !== room.name || remove.isPending}
            onClick={() => remove.mutate()}>
            Delete room
          </button>
        </section>
      )}
    </div>
  );
}
