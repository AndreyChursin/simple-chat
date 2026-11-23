import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { createRoom, roomKeys, type Visibility } from '../api/rooms';
import { apiErrorToFields, Field, FormError, type FieldErrors } from '../components/forms';
import { Modal } from '../components/Modal';

export const ROOM_NAME_MIN = 3;
export const ROOM_NAME_MAX = 64;
export const ROOM_DESCRIPTION_MAX = 1000;

export function CreateRoomModal({
  onClose,
  defaultVisibility = 'public',
}: {
  onClose: () => void;
  defaultVisibility?: Visibility;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<Visibility>(defaultVisibility);
  const [fields, setFields] = useState<FieldErrors>({});

  const create = useMutation({
    mutationFn: createRoom,
    onSuccess: async (room) => {
      await qc.invalidateQueries({ queryKey: roomKeys.mine });
      await qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
      onClose();
      navigate(`/rooms/${room.id}`);
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === 'room_name_taken') setFields({ name: err.message });
      else setFields(apiErrorToFields(err).fields);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length < ROOM_NAME_MIN || trimmed.length > ROOM_NAME_MAX) {
      setFields({ name: `Name must be ${ROOM_NAME_MIN}–${ROOM_NAME_MAX} characters` });
      return;
    }
    setFields({});
    create.mutate({ name: trimmed, description: description.trim(), visibility });
  }

  const formError =
    create.error && !(create.error instanceof ApiError && create.error.code === 'room_name_taken')
      ? apiErrorToFields(create.error).form
      : null;

  return (
    <Modal title="Create room" onClose={onClose}>
      <form onSubmit={onSubmit} noValidate>
        <FormError message={formError} />
        <Field label="Name" name="name" value={name} maxLength={ROOM_NAME_MAX} autoFocus
          onChange={(e) => setName(e.target.value)} error={fields.name} />
        <div className="field">
          <label htmlFor="f-description">Description</label>
          <textarea id="f-description" rows={3} maxLength={ROOM_DESCRIPTION_MAX} value={description}
            onChange={(e) => setDescription(e.target.value)} />
        </div>
        <fieldset className="segmented">
          <legend>Visibility</legend>
          <label>
            <input type="radio" name="visibility" checked={visibility === 'public'}
              onChange={() => setVisibility('public')} />
            🌐 Public <small className="muted">— listed in the catalog, anyone can join</small>
          </label>
          <label>
            <input type="radio" name="visibility" checked={visibility === 'private'}
              onChange={() => setVisibility('private')} />
            🔒 Private <small className="muted">— invitation only</small>
          </label>
        </fieldset>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={create.isPending}>Create</button>
        </div>
      </form>
    </Modal>
  );
}
