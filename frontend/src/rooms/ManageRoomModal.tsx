import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import {
  deleteRoom,
  fetchRoomInvitations,
  revokeInvitation,
  roomKeys,
  updateRoom,
  type RoomDetail,
  type Visibility,
} from '../api/rooms';
import { apiErrorToFields, Field, FormError, FormNotice } from '../components/forms';
import { Modal } from '../components/Modal';
import { ROOM_DESCRIPTION_MAX, ROOM_NAME_MAX, ROOM_NAME_MIN } from './CreateRoomModal';
import { useRoomGone } from './useRoomGone';

/** Tabs available so far. Members / Admins / Banned users arrive with moderation (roadmap stage 8). */
type Tab = 'invitations' | 'settings';

export function ManageRoomModal({ room, onClose }: { room: RoomDetail; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('invitations');
  return (
    <Modal title={`Manage #${room.name}`} onClose={onClose} wide>
      <div className="tabs" role="tablist">
        {(['invitations', 'settings'] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab${tab === t ? ' active' : ''}`}
            onClick={() => setTab(t)}>
            {t === 'invitations' ? 'Invitations' : 'Settings'}
          </button>
        ))}
      </div>
      {tab === 'invitations' ? <InvitationsTab room={room} /> : <SettingsTab room={room} onClose={onClose} />}
    </Modal>
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
